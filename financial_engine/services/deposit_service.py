import json
import uuid
from decimal import Decimal

from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.services.balance_service import BalanceService
from financial_engine.domain.exceptions import AccountNotFoundError
from financial_engine.domain.value_objects import Money
from financial_engine.domain.events import (
    DomainEvent,
    event_bus,
    DEPOSIT_COMPLETED,
    DEPOSIT_INITIATED,
)
from financial_engine.domain.exceptions import TransactionNotFoundError, InvalidTransactionStateError


# The platform clearing account is a special internal account
CLEARING_ACCOUNT_CURRENCY = {}  # currency -> clearing account id (populated at runtime)


class DepositService:
    """Handles deposits from external payment providers."""

    @staticmethod
    def get_or_create_clearing_account(currency: str) -> Account:
        """Get or create the platform clearing account for a currency."""
        clearing = Account.query.filter_by(
            user_id="PLATFORM_CLEARING", currency=currency
        ).first()
        if not clearing:
            clearing = Account(
                user_id="PLATFORM_CLEARING",
                currency=currency,
            )
            db.session.add(clearing)
            db.session.flush()
        return clearing

    @staticmethod
    def initiate_deposit(
        account_id: str,
        amount: Decimal,
        provider: str = "stripe",
        correlation_id: str | None = None,
    ) -> Transaction:
        """Create a pending deposit transaction."""
        account = db.session.get(Account, account_id)
        if not account:
            raise AccountNotFoundError(account_id)

        deposit_money = Money(amount, account.currency)
        if not deposit_money.is_positive():
            raise ValueError("Deposit amount must be positive")

        corr_id = correlation_id or str(uuid.uuid4())

        txn = Transaction(
            type="DEPOSIT",
            status="PENDING",
            correlation_id=corr_id,
            metadata_json=f'{{"provider": "{provider}", "account_id": "{account_id}"}}',
        )
        db.session.add(txn)
        db.session.commit()

        event_bus.publish(
            DomainEvent(
                DEPOSIT_INITIATED,
                {
                    "transaction_id": txn.id,
                    "account_id": account_id,
                    "amount": str(deposit_money.amount),
                    "currency": deposit_money.currency,
                    "provider": provider,
                },
                correlation_id=corr_id,
            )
        )

        return txn

    @staticmethod
    def confirm_deposit(
        transaction_id: str,
        amount: Decimal,
    ) -> Transaction:
        """Confirm a deposit (called when webhook confirms payment)."""
        txn = db.session.get(Transaction, transaction_id)
        if not txn:
            raise TransactionNotFoundError(transaction_id)

        if txn.status != "PENDING":
            raise InvalidTransactionStateError(transaction_id, txn.status, "SUCCESS")

        meta = json.loads(txn.metadata_json) if txn.metadata_json else {}
        account_id = meta.get("account_id")

        account = db.session.query(Account).filter_by(id=account_id).with_for_update().first()
        if not account:
            raise AccountNotFoundError(account_id)

        clearing = DepositService.get_or_create_clearing_account(account.currency)

        deposit_money = Money(amount, account.currency)

        # Create balanced ledger entries
        debit = LedgerEntry(
            account_id=clearing.id,
            transaction_id=txn.id,
            amount=-deposit_money.amount,
            entry_type="DEBIT",
            status="SUCCESS",
            currency=deposit_money.currency,
        )
        credit = LedgerEntry(
            account_id=account_id,
            transaction_id=txn.id,
            amount=deposit_money.amount,
            entry_type="CREDIT",
            status="SUCCESS",
            currency=deposit_money.currency,
        )
        db.session.add_all([debit, credit])

        txn.status = "SUCCESS"
        account.version += 1

        BalanceService.maybe_create_snapshot(account_id)

        db.session.commit()

        event_bus.publish(
            DomainEvent(
                DEPOSIT_COMPLETED,
                {
                    "transaction_id": txn.id,
                    "account_id": account_id,
                    "amount": str(deposit_money.amount),
                    "currency": deposit_money.currency,
                },
                correlation_id=txn.correlation_id,
            )
        )

        return txn
