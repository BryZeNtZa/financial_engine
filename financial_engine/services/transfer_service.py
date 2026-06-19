import uuid
from decimal import Decimal

from financial_engine.domain.value_objects import Money
from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.domain.aggregates import TransactionAggregate, AccountAggregate
from financial_engine.services.balance_service import BalanceService
from financial_engine.services.balance_cache import balance_cache
from financial_engine.domain.exceptions import (
    AccountNotFoundError,
    InvalidTransactionStateError,
    TransactionNotFoundError,
)
from financial_engine.domain.events import (
    DomainEvent,
    event_bus,
    FUNDS_RESERVED,
    TRANSFER_COMPLETED,
    TRANSFER_FAILED,
)


class TransferService:
    """Orchestrates fund transfers; domain invariants live in the aggregates."""

    @staticmethod
    def initiate_transfer(
        sender_account_id: str,
        receiver_account_id: str,
        amount: Decimal,
        correlation_id: str | None = None,
    ) -> Transaction:
        """Phase 1: Reserve funds (create PENDING debit on sender)."""
        # Pessimistic lock: SELECT ... FOR UPDATE prevents concurrent transfers
        # from reading stale balances on the same sender account.
        sender_row = (
            db.session.query(Account).filter_by(id=sender_account_id).with_for_update().first()
        )
        if not sender_row:
            raise AccountNotFoundError(sender_account_id)
        receiver_row = db.session.get(Account, receiver_account_id)
        if not receiver_row:
            raise AccountNotFoundError(receiver_account_id)

        sender = AccountAggregate(sender_row)
        sender.assert_same_currency_as(receiver_row.currency)

        transfer_money = Money(amount, sender.currency)
        if not transfer_money.is_positive():
            raise ValueError("Transfer amount must be positive")
        sender.assert_sufficient(
            BalanceService.get_available_balance(sender_account_id), transfer_money
        )

        txn = TransactionAggregate.open(
            type="TRANSFER",
            correlation_id=correlation_id or str(uuid.uuid4()),
            status="PENDING",
            metadata={"receiver_account_id": receiver_account_id},
        )
        txn.reserve_debit(sender_account_id, transfer_money)
        sender.touch()

        db.session.commit()

        event_bus.publish(
            DomainEvent(
                FUNDS_RESERVED,
                {
                    "transaction_id": txn.id,
                    "sender_account_id": sender_account_id,
                    "receiver_account_id": receiver_account_id,
                    "amount": str(transfer_money.amount),
                    "currency": transfer_money.currency,
                },
                correlation_id=txn.correlation_id,
            )
        )

        return txn.transaction

    @staticmethod
    def commit_transfer(transaction_id: str) -> Transaction:
        """Phase 2: Settle the transfer — finalize debit, create credit."""
        txn_row = db.session.get(Transaction, transaction_id)
        if not txn_row:
            raise TransactionNotFoundError(transaction_id)

        txn = TransactionAggregate.load(txn_row)
        if txn.status != "PENDING":
            raise InvalidTransactionStateError(transaction_id, txn.status, "SUCCESS")

        debit_entry = LedgerEntry.query.filter_by(
            transaction_id=transaction_id, entry_type="DEBIT", status="PENDING"
        ).first()
        if not debit_entry:
            raise InvalidTransactionStateError(
                transaction_id, "NO_PENDING_DEBIT", "SUCCESS"
            )

        sender_row = (
            db.session.query(Account).filter_by(id=debit_entry.account_id).with_for_update().first()
        )
        sender = AccountAggregate(sender_row)

        receiver_account_id = txn.metadata().get("receiver_account_id")
        if not receiver_account_id:
            raise ValueError(
                "Receiver account not found in transaction metadata. "
                "Use execute_transfer for single-phase transfers."
            )

        receiver_row = db.session.get(Account, receiver_account_id)
        if not receiver_row:
            txn.fail_pending()
            txn.mark_failed()
            db.session.commit()
            raise AccountNotFoundError(receiver_account_id)
        receiver = AccountAggregate(receiver_row)

        settled = Money(abs(debit_entry.amount), sender.currency)

        # Settle the reserved debit and add the matching credit, then enforce
        # the double-entry invariant before completing.
        txn.settle_pending()
        txn.credit(receiver_account_id, settled)
        txn.mark_success()
        txn.assert_balanced()

        BalanceService.maybe_create_snapshot(sender.id)
        BalanceService.maybe_create_snapshot(receiver.id)
        sender.touch()
        receiver.touch()

        db.session.commit()

        balance_cache.invalidate_many(sender.id, receiver.id)

        event_bus.publish(
            DomainEvent(
                TRANSFER_COMPLETED,
                {
                    "transaction_id": txn.id,
                    "sender_account_id": sender.id,
                    "receiver_account_id": receiver.id,
                    "amount": str(settled.amount),
                    "currency": settled.currency,
                },
                correlation_id=txn.correlation_id,
            )
        )

        return txn.transaction

    @staticmethod
    def execute_transfer(
        sender_account_id: str,
        receiver_account_id: str,
        amount: Decimal,
        correlation_id: str | None = None,
    ) -> Transaction:
        """Single-phase atomic transfer (both entries at once)."""
        sender_row = (
            db.session.query(Account).filter_by(id=sender_account_id).with_for_update().first()
        )
        if not sender_row:
            raise AccountNotFoundError(sender_account_id)
        receiver_row = db.session.get(Account, receiver_account_id)
        if not receiver_row:
            raise AccountNotFoundError(receiver_account_id)

        sender = AccountAggregate(sender_row)
        receiver = AccountAggregate(receiver_row)
        sender.assert_same_currency_as(receiver.currency)

        transfer_money = Money(amount, sender.currency)
        if not transfer_money.is_positive():
            raise ValueError("Transfer amount must be positive")
        sender.assert_sufficient(
            BalanceService.get_available_balance(sender_account_id), transfer_money
        )

        txn = TransactionAggregate.open(
            type="TRANSFER",
            correlation_id=correlation_id or str(uuid.uuid4()),
            status="SUCCESS",
        )
        txn.record_double_entry(
            debit_account_id=sender_account_id,
            credit_account_id=receiver_account_id,
            money=transfer_money,
        )
        txn.assert_balanced()

        sender.touch()
        receiver.touch()
        BalanceService.maybe_create_snapshot(sender_account_id)
        BalanceService.maybe_create_snapshot(receiver_account_id)

        db.session.commit()

        balance_cache.invalidate_many(sender_account_id, receiver_account_id)

        event_bus.publish(
            DomainEvent(
                TRANSFER_COMPLETED,
                {
                    "transaction_id": txn.id,
                    "sender_account_id": sender_account_id,
                    "receiver_account_id": receiver_account_id,
                    "amount": str(transfer_money.amount),
                    "currency": transfer_money.currency,
                },
                correlation_id=txn.correlation_id,
            )
        )

        return txn.transaction

    @staticmethod
    def fail_transfer(transaction_id: str) -> Transaction:
        """Fail a pending transfer, releasing reserved funds."""
        txn_row = db.session.get(Transaction, transaction_id)
        if not txn_row:
            raise TransactionNotFoundError(transaction_id)

        txn = TransactionAggregate.load(txn_row)
        if txn.status != "PENDING":
            raise InvalidTransactionStateError(transaction_id, txn.status, "FAILED")

        txn.fail_pending()
        txn.mark_failed()
        db.session.commit()

        event_bus.publish(
            DomainEvent(
                TRANSFER_FAILED,
                {"transaction_id": txn.id},
                correlation_id=txn.correlation_id,
            )
        )

        return txn.transaction
