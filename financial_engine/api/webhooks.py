from decimal import Decimal, InvalidOperation

from flask import request
from flask_restx import Namespace, Resource, fields

from financial_engine.services.deposit_service import DepositService
from financial_engine.services.payment_provider import PaymentProviderStub
from financial_engine.middleware.idempotency import idempotent
from financial_engine.domain.exceptions import (
    TransactionNotFoundError,
    InvalidTransactionStateError,
)

api = Namespace("payments", description="Payment webhook operations")

webhook_model = api.model("WebhookPayload", {
    "transaction_id": fields.String(required=True, description="Platform transaction ID"),
    "amount": fields.String(required=True, description="Confirmed amount"),
    "provider": fields.String(required=True, description="Payment provider name"),
    "provider_reference": fields.String(
        required=False, description="Provider-specific reference"
    ),
})


@api.route("/webhook")
class PaymentWebhook(Resource):
    @api.expect(webhook_model)
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(200, "Deposit confirmed")
    @api.response(400, "Invalid payload")
    @api.response(401, "Webhook verification failed")
    @api.response(404, "Transaction not found")
    @idempotent
    def post(self):
        """Handle payment provider webhook to confirm deposits."""
        data = request.json
        provider = data.get("provider", "")
        signature = request.headers.get("X-Webhook-Signature", "")

        # Verify webhook signature
        if not PaymentProviderStub.verify_webhook(provider, data, signature):
            return {"error": "Webhook verification failed"}, 401

        try:
            amount = Decimal(data["amount"])
        except (InvalidOperation, KeyError):
            return {"error": "Invalid amount"}, 400

        transaction_id = data.get("transaction_id")
        if not transaction_id:
            return {"error": "Missing transaction_id"}, 400

        try:
            txn = DepositService.confirm_deposit(
                transaction_id=transaction_id,
                amount=amount,
            )
        except TransactionNotFoundError as e:
            return {"error": e.message}, 404
        except InvalidTransactionStateError as e:
            return {"error": e.message}, 409

        return {
            "transaction_id": txn.id,
            "status": txn.status,
            "message": "Deposit confirmed",
        }
