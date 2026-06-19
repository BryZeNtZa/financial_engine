from flask import g
from flask_restx import Namespace, Resource, fields

from financial_engine.services.transaction_service import TransactionService
from financial_engine.middleware.idempotency import idempotent
from financial_engine.domain.exceptions import (
    TransactionNotFoundError,
    InvalidTransactionStateError,
)

api = Namespace("transactions", description="Transaction lifecycle operations")

reversal_response_model = api.model("ReversalResponse", {
    "reversal_transaction_id": fields.String(description="Compensating transaction ID"),
    "reversed_transaction_id": fields.String(description="Original transaction ID"),
    "type": fields.String(description="Compensating transaction type (REVERSAL)"),
    "status": fields.String(description="Status of the original transaction (REVERSED)"),
    "correlation_id": fields.String(description="Correlation ID"),
})


@api.route("/<string:transaction_id>/reverse")
class TransactionReverse(Resource):
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.response(201, "Transaction reversed", reversal_response_model)
    @api.response(404, "Transaction not found")
    @api.response(409, "Transaction cannot be reversed")
    @idempotent
    def post(self, transaction_id):
        """Reverse a completed transaction via a compensating transaction."""
        correlation_id = getattr(g, "correlation_id", None)
        try:
            reversal = TransactionService.reverse_transaction(
                transaction_id, correlation_id=correlation_id
            )
        except TransactionNotFoundError as e:
            return {"error": e.message}, 404
        except InvalidTransactionStateError as e:
            return {"error": e.message}, 409

        return {
            "reversal_transaction_id": reversal.id,
            "reversed_transaction_id": reversal.reverses_transaction_id,
            "type": reversal.type,
            "status": "REVERSED",
            "correlation_id": reversal.correlation_id,
        }, 201
