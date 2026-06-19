from flask import request, g
from flask_restx import Namespace, Resource, fields

from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.services.balance_service import BalanceService
from financial_engine.middleware.idempotency import idempotent

api = Namespace("accounts", description="Account operations")

# Swagger Models
account_create_model = api.model("CreateAccount", {
    "user_id": fields.String(required=True, description="User ID"),
    "currency": fields.String(required=True, description="ISO 4217 currency code"),
})

account_model = api.model("Account", {
    "number": fields.String(description="Account number"),
    "user_id": fields.String(description="User ID"),
    "currency": fields.String(description="Currency code"),
    "created_at": fields.DateTime(description="Creation timestamp"),
})

balance_model = api.model("Balance", {
    "number": fields.String(description="Account number"),
    "balance": fields.String(description="Current balance"),
    "available_balance": fields.String(description="Available balance (excl. pending debits)"),
    "currency": fields.String(description="Currency code"),
    "entry_count": fields.Integer(description="Total successful ledger entries"),
})

transaction_model = api.model("Transaction", {
    "transaction_id": fields.String(attribute="id"),
    "type": fields.String(description="Transaction type"),
    "amount": fields.String(description="Transaction amount"),
    "status": fields.String(description="Transaction status"),
    "correlation_id": fields.String(description="Correlation ID for tracing"),
    "created_at": fields.DateTime(description="Creation timestamp"),
})

transaction_list_model = api.model("TransactionList", {
    "transactions": fields.List(fields.Nested(transaction_model)),
    "page": fields.Integer(description="Current page"),
    "per_page": fields.Integer(description="Items per page"),
    "total": fields.Integer(description="Total items"),
})

account_list_model = api.model("AccountList", {
    "accounts": fields.List(fields.Nested(account_model)),
    "page": fields.Integer(description="Current page"),
    "per_page": fields.Integer(description="Items per page"),
    "total": fields.Integer(description="Total accounts"),
})


@api.route("")
class AccountList(Resource):
    @api.marshal_with(account_list_model)
    @api.doc(params={
        "page": "Page number (default: 1)",
        "per_page": "Items per page (default: 20, max: 100)",
        "user_id": "Filter by user ID (optional)",
    })
    def get(self):
        """List accounts (paginated)."""
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 100)
        user_id = request.args.get("user_id")

        query = Account.query
        if user_id:
            query = query.filter_by(user_id=user_id)

        pagination = query.order_by(Account.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        return {
            "accounts": pagination.items,
            "page": page,
            "per_page": per_page,
            "total": pagination.total,
        }

    @api.expect(account_create_model)
    @api.doc(params={"Idempotency-Key": {"in": "header", "description": "Unique key to ensure idempotent processing", "required": False}})
    @api.marshal_with(account_model, code=201)
    @api.response(201, "Account created")
    @idempotent
    def post(self):
        """Create a new account."""
        data = request.json
        user_id = data["user_id"]
        currency = data["currency"].upper()

        account = Account(user_id=user_id, currency=currency)
        db.session.add(account)
        db.session.commit()
        return account, 201


@api.route("/<string:number>")
class AccountDetail(Resource):
    @api.marshal_with(account_model)
    @api.response(404, "Account not found")
    def get(self, number):
        """Get account details."""
        account = Account.query.filter_by(number=number).first()
        if not account:
            api.abort(404, f"Account not found: {number}")
        return account


@api.route("/<string:number>/balance")
class AccountBalance(Resource):
    @api.marshal_with(balance_model)
    @api.response(404, "Account not found")
    def get(self, number):
        """Get account balance (derived from ledger entries)."""
        account = Account.query.filter_by(number=number).first()
        if not account:
            api.abort(404, f"Account not found: {number}")

        balance = BalanceService.get_balance(account.id)
        available = BalanceService.get_available_balance(account.id)
        entry_count = BalanceService.get_entry_count(account.id)

        return {
            "number": number,
            "balance": str(balance.amount),
            "available_balance": str(available.amount),
            "currency": balance.currency,
            "entry_count": entry_count,
        }


@api.route("/<string:number>/transactions")
class AccountTransactions(Resource):
    @api.marshal_with(transaction_list_model)
    @api.response(404, "Account not found")
    @api.doc(params={
        "page": "Page number (default: 1)",
        "per_page": "Items per page (default: 20, max: 100)",
    })
    def get(self, number):
        """Get transaction history for an account (paginated)."""
        account = Account.query.filter_by(number=number).first()
        if not account:
            api.abort(404, f"Account not found: {number}")

        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 100)

        # Find transactions that have ledger entries for this account
        txn_ids = (
            db.session.query(LedgerEntry.transaction_id)
            .filter(LedgerEntry.account_id == account.id)
            .distinct()
            .subquery()
        )

        pagination = (
            Transaction.query.filter(Transaction.id.in_(db.session.query(txn_ids)))
            .order_by(Transaction.created_at.desc())
            .paginate(page=page, per_page=per_page, error_out=False)
        )

        # Compute amount per transaction for this account
        transactions = []
        for txn in pagination.items:
            entry_sum = (
                db.session.query(db.func.coalesce(db.func.sum(LedgerEntry.amount), 0))
                .filter(
                    LedgerEntry.transaction_id == txn.id,
                    LedgerEntry.account_id == account.id,
                )
                .scalar()
            )
            transactions.append({
                "id": txn.id,
                "type": txn.type,
                "amount": str(entry_sum),
                "status": txn.status,
                "correlation_id": txn.correlation_id,
                "created_at": txn.created_at,
            })

        return {
            "transactions": transactions,
            "page": page,
            "per_page": per_page,
            "total": pagination.total,
        }
