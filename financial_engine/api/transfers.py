from decimal import Decimal, InvalidOperation

from flask import request, g
from flask_restx import Namespace, Resource, fields

from financial_engine.models.account import Account
from financial_engine.services.transfer_service import TransferService
from financial_engine.middleware.idempotency import idempotent
from financial_engine.domain.exceptions import (
    AccountNotFoundError,
    InsufficientFundsError,
    CurrencyMismatchError,
    InvalidTransactionStateError,
    TransactionNotFoundError,
)

api = Namespace("transfers", description="Fund transfer operations")

transfer_request_model = api.model("TransferRequest", {
    "sender_account_number": fields.String(required=True, description="Sender account number"),
    "receiver_account_number": fields.String(required=True, description="Receiver account number"),
    "amount": fields.String(required=True, description="Transfer amount (decimal string)"),
})

transfer_initiate_model = api.model("TransferInitiateRequest", {
    "sender_account_number": fields.String(required=True, description="Sender account number"),
    "receiver_account_number": fields.String(required=True, description="Receiver account number"),
    "amount": fields.String(required=True, description="Transfer amount (decimal string)"),
})

transfer_response_model = api.model("TransferResponse", {
    "transaction_id": fields.String(description="Transaction ID"),
    "type": fields.String(description="Transaction type"),
    "status": fields.String(description="Transaction status"),
    "correlation_id": fields.String(description="Correlation ID"),
    "created_at": fields.DateTime(description="Creation timestamp"),
})


def _resolve_account(number: str) -> Account:
    """Resolve an account number to an Account, or abort 404."""
    account = Account.query.filter_by(number=number).first()
    if not account:
        raise AccountNotFoundError(number)
    return account


@api.route("")
class TransferExecute(Resource):
    @api.expect(transfer_request_model)
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(201, "Transfer completed", transfer_response_model)
    @api.response(400, "Validation error")
    @api.response(404, "Account not found")
    @api.response(422, "Insufficient funds or currency mismatch")
    @idempotent
    def post(self):
        """Execute an atomic transfer between two accounts."""
        data = request.json
        try:
            amount = Decimal(data["amount"])
        except (InvalidOperation, KeyError):
            return {"error": "Invalid amount"}, 400

        correlation_id = getattr(g, "correlation_id", None)

        try:
            sender = _resolve_account(data["sender_account_number"])
            receiver = _resolve_account(data["receiver_account_number"])
            txn = TransferService.execute_transfer(
                sender_account_id=sender.id,
                receiver_account_id=receiver.id,
                amount=amount,
                correlation_id=correlation_id,
            )
        except AccountNotFoundError as e:
            return {"error": e.message}, 404
        except InsufficientFundsError as e:
            return {"error": e.message}, 422
        except CurrencyMismatchError as e:
            return {"error": e.message}, 422
        except ValueError as e:
            return {"error": str(e)}, 400

        return {
            "transaction_id": txn.id,
            "type": txn.type,
            "status": txn.status,
            "correlation_id": txn.correlation_id,
            "created_at": txn.created_at.isoformat(),
        }, 201


@api.route("/initiate")
class TransferInitiate(Resource):
    @api.expect(transfer_initiate_model)
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(201, "Transfer initiated (funds reserved)", transfer_response_model)
    @api.response(400, "Validation error")
    @api.response(422, "Insufficient funds")
    @idempotent
    def post(self):
        """Phase 1: Initiate a two-phase transfer (reserve funds)."""
        data = request.json
        try:
            amount = Decimal(data["amount"])
        except (InvalidOperation, KeyError):
            return {"error": "Invalid amount"}, 400

        correlation_id = getattr(g, "correlation_id", None)

        try:
            sender = _resolve_account(data["sender_account_number"])
            receiver = _resolve_account(data["receiver_account_number"])
            txn = TransferService.initiate_transfer(
                sender_account_id=sender.id,
                receiver_account_id=receiver.id,
                amount=amount,
                correlation_id=correlation_id,
            )
        except AccountNotFoundError as e:
            return {"error": e.message}, 404
        except InsufficientFundsError as e:
            return {"error": e.message}, 422
        except CurrencyMismatchError as e:
            return {"error": e.message}, 422
        except ValueError as e:
            return {"error": str(e)}, 400

        return {
            "transaction_id": txn.id,
            "type": txn.type,
            "status": txn.status,
            "correlation_id": txn.correlation_id,
            "created_at": txn.created_at.isoformat(),
        }, 201


@api.route("/<string:transaction_id>/commit")
class TransferCommit(Resource):
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(200, "Transfer committed", transfer_response_model)
    @api.response(404, "Transaction not found")
    @api.response(409, "Invalid state transition")
    @idempotent
    def post(self, transaction_id):
        """Phase 2: Commit a pending two-phase transfer."""
        try:
            txn = TransferService.commit_transfer(transaction_id)
        except TransactionNotFoundError as e:
            return {"error": e.message}, 404
        except InvalidTransactionStateError as e:
            return {"error": e.message}, 409
        except AccountNotFoundError as e:
            return {"error": e.message}, 404

        return {
            "transaction_id": txn.id,
            "type": txn.type,
            "status": txn.status,
            "correlation_id": txn.correlation_id,
            "created_at": txn.created_at.isoformat(),
        }


@api.route("/<string:transaction_id>/fail")
class TransferFail(Resource):
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(200, "Transfer failed", transfer_response_model)
    @api.response(404, "Transaction not found")
    @api.response(409, "Invalid state transition")
    @idempotent
    def post(self, transaction_id):
        """Fail a pending transfer and release reserved funds."""
        try:
            txn = TransferService.fail_transfer(transaction_id)
        except TransactionNotFoundError as e:
            return {"error": e.message}, 404
        except InvalidTransactionStateError as e:
            return {"error": e.message}, 409

        return {
            "transaction_id": txn.id,
            "type": txn.type,
            "status": txn.status,
            "correlation_id": txn.correlation_id,
            "created_at": txn.created_at.isoformat(),
        }
