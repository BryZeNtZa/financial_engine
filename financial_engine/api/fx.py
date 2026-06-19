from decimal import Decimal, InvalidOperation

from flask import request, g
from flask_restx import Namespace, Resource, fields

from financial_engine.models.account import Account
from financial_engine.services.fx_service import FXService
from financial_engine.middleware.idempotency import idempotent
from financial_engine.domain.exceptions import (
    AccountNotFoundError,
    InsufficientFundsError,
)

api = Namespace("fx", description="Foreign exchange operations")

fx_rate_model = api.model("FXRate", {
    "from_currency": fields.String(description="Source currency"),
    "to_currency": fields.String(description="Target currency"),
    "rate": fields.String(description="Exchange rate"),
})

fx_convert_model = api.model("FXConvert", {
    "from_currency": fields.String(description="Source currency"),
    "to_currency": fields.String(description="Target currency"),
    "amount": fields.String(description="Original amount"),
    "converted_amount": fields.String(description="Converted amount"),
    "rate": fields.String(description="Applied rate"),
})

fx_transfer_request = api.model("FXTransferRequest", {
    "sender_account_number": fields.String(required=True, description="Sender account number"),
    "receiver_account_number": fields.String(required=True, description="Receiver account number"),
    "amount": fields.String(required=True, description="Amount in sender's currency"),
})

fx_transfer_response = api.model("FXTransferResponse", {
    "transaction_id": fields.String(description="Transaction ID"),
    "type": fields.String(description="Transaction type"),
    "status": fields.String(description="Transaction status"),
    "from_amount": fields.String(description="Debited amount"),
    "from_currency": fields.String(description="Source currency"),
    "to_amount": fields.String(description="Credited amount"),
    "to_currency": fields.String(description="Target currency"),
    "rate": fields.String(description="Applied rate"),
    "correlation_id": fields.String(description="Correlation ID"),
})


@api.route("/rate")
class FXRate(Resource):
    @api.doc(params={
        "from": "Source currency code (e.g. USD)",
        "to": "Target currency code (e.g. EUR)",
    })
    @api.marshal_with(fx_rate_model)
    def get(self):
        """Get current exchange rate between two currencies."""
        from_curr = request.args.get("from", "").upper()
        to_curr = request.args.get("to", "").upper()

        if not from_curr or not to_curr:
            api.abort(400, "Both 'from' and 'to' query parameters are required")

        try:
            rate = FXService.get_rate(from_curr, to_curr)
        except ValueError as e:
            api.abort(400, str(e))

        return {
            "from_currency": from_curr,
            "to_currency": to_curr,
            "rate": str(rate),
        }


@api.route("/convert")
class FXConvert(Resource):
    @api.doc(params={
        "from": "Source currency code",
        "to": "Target currency code",
        "amount": "Amount to convert",
    })
    @api.marshal_with(fx_convert_model)
    def get(self):
        """Convert an amount between currencies."""
        from_curr = request.args.get("from", "").upper()
        to_curr = request.args.get("to", "").upper()

        try:
            amount = Decimal(request.args.get("amount", "0"))
        except InvalidOperation:
            api.abort(400, "Invalid amount")

        if not from_curr or not to_curr:
            api.abort(400, "Both 'from' and 'to' query parameters are required")

        try:
            rate = FXService.get_rate(from_curr, to_curr)
            converted = FXService.convert(amount, from_curr, to_curr)
        except ValueError as e:
            api.abort(400, str(e))

        return {
            "from_currency": from_curr,
            "to_currency": to_curr,
            "amount": str(amount),
            "converted_amount": str(converted),
            "rate": str(rate),
        }


@api.route("/transfer")
class FXTransfer(Resource):
    @api.expect(fx_transfer_request)
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(201, "FX transfer completed", fx_transfer_response)
    @api.response(400, "Validation error")
    @api.response(422, "Insufficient funds")
    @idempotent
    def post(self):
        """Execute a cross-currency transfer."""
        data = request.json
        try:
            amount = Decimal(data["amount"])
        except (InvalidOperation, KeyError):
            return {"error": "Invalid amount"}, 400

        correlation_id = getattr(g, "correlation_id", None)

        try:
            sender = Account.query.filter_by(number=data["sender_account_number"]).first()
            if not sender:
                raise AccountNotFoundError(data["sender_account_number"])
            receiver = Account.query.filter_by(number=data["receiver_account_number"]).first()
            if not receiver:
                raise AccountNotFoundError(data["receiver_account_number"])

            txn = FXService.execute_fx_transfer(
                sender_account_id=sender.id,
                receiver_account_id=receiver.id,
                amount=amount,
                correlation_id=correlation_id,
            )
        except AccountNotFoundError as e:
            return {"error": e.message}, 404
        except InsufficientFundsError as e:
            return {"error": e.message}, 422
        except ValueError as e:
            return {"error": str(e)}, 400

        # Retrieve entry details for response
        from financial_engine.models.ledger_entry import LedgerEntry

        sender_entry = LedgerEntry.query.filter_by(
            transaction_id=txn.id, entry_type="DEBIT"
        ).first()
        receiver_entry = LedgerEntry.query.filter_by(
            transaction_id=txn.id,
            entry_type="CREDIT",
            account_id=receiver.id,
        ).first()

        from_curr = sender_entry.currency if sender_entry else ""
        to_curr = receiver_entry.currency if receiver_entry else ""

        try:
            rate = str(FXService.get_rate(from_curr, to_curr))
        except ValueError:
            rate = "N/A"

        return {
            "transaction_id": txn.id,
            "type": txn.type,
            "status": txn.status,
            "from_amount": str(abs(sender_entry.amount)) if sender_entry else "0",
            "from_currency": from_curr,
            "to_amount": str(receiver_entry.amount) if receiver_entry else "0",
            "to_currency": to_curr,
            "rate": rate,
            "correlation_id": txn.correlation_id,
        }, 201
