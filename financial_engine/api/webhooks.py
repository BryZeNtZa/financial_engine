import json
from decimal import Decimal, InvalidOperation

from flask import request
from flask_restx import Namespace, Resource, fields

from financial_engine.models.transaction import Transaction
from financial_engine.services.deposit_service import DepositService
from financial_engine.services.payment_gateway import PaymentGateway
from financial_engine.services.payment_provider import PaymentProviderStub
from financial_engine.middleware.idempotency import idempotent
from financial_engine.providers.base import PaymentStatus
from financial_engine.domain.exceptions import (
    TransactionNotFoundError,
    InvalidTransactionStateError,
    DepositAmountMismatchError,
)

api = Namespace("payments", description="Payment webhook operations")

webhook_model = api.model("WebhookPayload", {
    "transaction_id": fields.String(
        required=False, description="Platform transaction ID (simulation mode)"
    ),
    "amount": fields.String(required=False, description="Confirmed amount"),
    "provider": fields.String(required=True, description="Payment provider name"),
    "provider_reference": fields.String(
        required=False, description="Provider-specific reference (real-provider mode)"
    ),
})


@api.route("/webhook")
class PaymentWebhook(Resource):
    @api.expect(webhook_model)
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(200, "Deposit confirmed / acknowledged")
    @api.response(400, "Invalid payload")
    @api.response(401, "Webhook verification failed")
    @api.response(404, "Transaction not found")
    @api.response(422, "Amount mismatch")
    @idempotent
    def post(self):
        """Handle a payment provider webhook to confirm deposits."""
        data = request.json or {}
        provider = data.get("provider", "")

        client = PaymentGateway.get_client(provider)
        if client is None:
            return self._handle_simulation(data, provider)
        return self._handle_provider(data, client)

    # ----------------------------------------------------- simulation mode
    def _handle_simulation(self, data, provider):
        """Dev/test path: the webhook carries our transaction_id directly."""
        signature = request.headers.get("X-Webhook-Signature", "")
        if not PaymentProviderStub.verify_webhook(provider, data, signature):
            return {"error": "Webhook verification failed"}, 401

        try:
            amount = Decimal(data["amount"])
        except (InvalidOperation, KeyError):
            return {"error": "Invalid amount"}, 400

        transaction_id = data.get("transaction_id")
        if not transaction_id:
            return {"error": "Missing transaction_id"}, 400

        return self._confirm(transaction_id, amount)

    # --------------------------------------------------- real provider mode
    def _handle_provider(self, data, client):
        """Production path: verify the provider webhook, then re-query status.

        The callback body is never trusted to credit funds on its own; the
        deposit is only confirmed after the provider's own status endpoint
        reports SUCCESSFUL.
        """
        event = client.parse_webhook(data)

        txn = Transaction.query.filter_by(
            provider_reference=event.provider_reference, type="DEPOSIT"
        ).first()
        if not txn:
            return {"error": "Unknown provider reference"}, 404

        meta = json.loads(txn.metadata_json) if txn.metadata_json else {}
        context = {
            "notif_token": meta.get("notif_token"),
            "order_id": txn.id,
            "amount": meta.get("amount"),
        }

        if not client.verify_webhook(
            data,
            headers=dict(request.headers),
            raw_body=request.get_data(),
            context=context,
        ):
            return {"error": "Webhook verification failed"}, 401

        status = client.get_payment_status(event.provider_reference, context=context)

        if status == PaymentStatus.SUCCESSFUL:
            amount = (
                event.amount
                if event.amount is not None
                else Decimal(str(meta.get("amount", "0")))
            )
            return self._confirm(txn.id, amount)

        if status in (PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED):
            try:
                DepositService.fail_deposit(txn.id)
            except InvalidTransactionStateError as e:
                return {"error": e.message}, 409
            return {"transaction_id": txn.id, "status": "FAILED", "message": "Deposit failed"}

        # Still pending — acknowledge without crediting (provider may retry).
        return {"transaction_id": txn.id, "status": "PENDING", "message": "Pending confirmation"}

    # ----------------------------------------------------------- shared
    @staticmethod
    def _confirm(transaction_id, amount):
        try:
            txn = DepositService.confirm_deposit(
                transaction_id=transaction_id, amount=amount
            )
        except TransactionNotFoundError as e:
            return {"error": e.message}, 404
        except InvalidTransactionStateError as e:
            return {"error": e.message}, 409
        except DepositAmountMismatchError as e:
            return {"error": e.message}, 422

        return {
            "transaction_id": txn.id,
            "status": txn.status,
            "message": "Deposit confirmed",
        }
