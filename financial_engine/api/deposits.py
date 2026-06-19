from decimal import Decimal, InvalidOperation

from flask import request, g
from flask_restx import Namespace, Resource, fields

from financial_engine.models.account import Account
from financial_engine.services.deposit_service import DepositService
from financial_engine.middleware.idempotency import idempotent
from financial_engine.domain.exceptions import AccountNotFoundError

api = Namespace("deposits", description="Deposit operations")

deposit_request_model = api.model("DepositRequest", {
    "number": fields.String(required=True, description="Target account number"),
    "amount": fields.String(required=True, description="Deposit amount (decimal string)"),
    "provider": fields.String(
        required=False, default="stripe",
        description="Payment provider (stripe, paypal, mtn, orange)",
    ),
})

deposit_response_model = api.model("DepositResponse", {
    "transaction_id": fields.String(description="Transaction ID"),
    "type": fields.String(description="Transaction type"),
    "status": fields.String(description="Transaction status"),
    "correlation_id": fields.String(description="Correlation ID"),
    "created_at": fields.DateTime(description="Creation timestamp"),
})


@api.route("")
class DepositCreate(Resource):
    @api.expect(deposit_request_model)
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(201, "Deposit initiated", deposit_response_model)
    @api.response(400, "Validation error")
    @api.response(404, "Account not found")
    @idempotent
    def post(self):
        """Initiate a deposit from an external payment provider."""
        data = request.json
        try:
            amount = Decimal(data["amount"])
        except (InvalidOperation, KeyError):
            return {"error": "Invalid amount"}, 400

        provider = data.get("provider", "stripe")
        correlation_id = getattr(g, "correlation_id", None)

        try:
            account = Account.query.filter_by(number=data["number"]).first()
            if not account:
                raise AccountNotFoundError(data["number"])
            txn = DepositService.initiate_deposit(
                account_id=account.id,
                amount=amount,
                provider=provider,
                correlation_id=correlation_id,
            )
        except AccountNotFoundError as e:
            return {"error": e.message}, 404
        except ValueError as e:
            return {"error": str(e)}, 400

        return {
            "transaction_id": txn.id,
            "type": txn.type,
            "status": txn.status,
            "correlation_id": txn.correlation_id,
            "created_at": txn.created_at.isoformat(),
        }, 201
