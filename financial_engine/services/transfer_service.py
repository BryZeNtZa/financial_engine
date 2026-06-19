import uuid
from decimal import Decimal

from financial_engine.domain.value_objects import Money
from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.services.balance_service import BalanceService
from financial_engine.domain.exceptions import (
    AccountNotFoundError,
    InsufficientFundsError,
    CurrencyMismatchError,
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
    """Handles fund transfers between accounts with two-phase commit."""

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
        sender = db.session.query(Account).filter_by(id=sender_account_id).with_for_update().first()
        if not sender:
            raise AccountNotFoundError(sender_account_id)

        receiver = db.session.get(Account, receiver_account_id)
        if not receiver:
            raise AccountNotFoundError(receiver_account_id)

        if sender.currency != receiver.currency:
            raise CurrencyMismatchError(sender.currency, receiver.currency)

        transfer_money = Money(amount, sender.currency)
        if not transfer_money.is_positive():
            raise ValueError("Transfer amount must be positive")

        # Compute available balance with pessimistic locking
        available = BalanceService.get_available_balance(sender_account_id)
        if available < transfer_money:
            raise InsufficientFundsError(
                sender_account_id, str(available.amount), str(transfer_money.amount)
            )

        # Create transaction
        txn = Transaction(
            type="TRANSFER",
            status="PENDING",
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata_json=f'{{"receiver_account_id": "{receiver_account_id}"}}',
        )
        db.session.add(txn)
        db.session.flush()

        # Phase 1: Create PENDING debit entry for sender
        debit_entry = LedgerEntry(
            account_id=sender_account_id,
            transaction_id=txn.id,
            amount=-transfer_money.amount,
            entry_type="DEBIT",
            status="PENDING",
            currency=transfer_money.currency,
        )
        db.session.add(debit_entry)

        # Bump sender version (optimistic lock)
        sender.version += 1

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

        return txn

    @staticmethod
    def commit_transfer(transaction_id: str) -> Transaction:
        """Phase 2: Settle the transfer — finalize debit, create credit."""
        txn = db.session.get(Transaction, transaction_id)
        if not txn:
            raise TransactionNotFoundError(transaction_id)

        if txn.status != "PENDING":
            raise InvalidTransactionStateError(transaction_id, txn.status, "SUCCESS")

        # Find the pending debit entry
        debit_entry = LedgerEntry.query.filter_by(
            transaction_id=transaction_id,
            entry_type="DEBIT",
            status="PENDING",
        ).first()

        if not debit_entry:
            raise InvalidTransactionStateError(
                transaction_id, "NO_PENDING_DEBIT", "SUCCESS"
            )

        sender = db.session.query(Account).filter_by(id=debit_entry.account_id).with_for_update().first()

        # Determine receiver from transaction metadata or entries
        # For transfers, we need to know the receiver; store it during initiation
        # We'll use a query approach: find entries for this txn
        # The receiver info is passed during initiation and stored
        # For simplicity, we read it from pending entries
        # Actually, let's get it from the initiation context

        # Re-check balance with the pending entry becoming SUCCESS
        available = BalanceService.get_available_balance(debit_entry.account_id)
        pending_amount = abs(debit_entry.amount)

        # The pending debit is already factored into available balance,
        # so we just need to verify it's still valid
        # (available already accounts for pending debits)

        # Settle the debit
        debit_entry.status = "SUCCESS"

        # Create credit entry for receiver
        # We need to find the receiver — store in transaction metadata
        import json
        meta = json.loads(txn.metadata_json) if txn.metadata_json else {}
        receiver_account_id = meta.get("receiver_account_id")

        if not receiver_account_id:
            # Fallback: for two-phase, the caller must provide receiver info
            raise ValueError(
                "Receiver account not found in transaction metadata. "
                "Use execute_transfer for single-phase transfers."
            )

        receiver = db.session.get(Account, receiver_account_id)
        if not receiver:
            debit_entry.status = "FAILED"
            txn.status = "FAILED"
            db.session.commit()
            raise AccountNotFoundError(receiver_account_id)

        credit_entry = LedgerEntry(
            account_id=receiver_account_id,
            transaction_id=txn.id,
            amount=abs(debit_entry.amount),
            entry_type="CREDIT",
            status="SUCCESS",
            currency=receiver.currency,
        )
        db.session.add(credit_entry)

        txn.status = "SUCCESS"

        # Snapshot maintenance
        BalanceService.maybe_create_snapshot(debit_entry.account_id)
        BalanceService.maybe_create_snapshot(receiver_account_id)

        # Bump versions
        sender.version += 1
        receiver.version += 1

        db.session.commit()

        event_bus.publish(
            DomainEvent(
                TRANSFER_COMPLETED,
                {
                    "transaction_id": txn.id,
                    "sender_account_id": debit_entry.account_id,
                    "receiver_account_id": receiver_account_id,
                    "amount": str(abs(debit_entry.amount)),
                    "currency": sender.currency,
                },
                correlation_id=txn.correlation_id,
            )
        )

        return txn

    @staticmethod
    def execute_transfer(
        sender_account_id: str,
        receiver_account_id: str,
        amount: Decimal,
        correlation_id: str | None = None,
    ) -> Transaction:
        """Single-phase atomic transfer (both entries at once)."""
        sender = db.session.query(Account).filter_by(id=sender_account_id).with_for_update().first()
        if not sender:
            raise AccountNotFoundError(sender_account_id)

        receiver = db.session.get(Account, receiver_account_id)
        if not receiver:
            raise AccountNotFoundError(receiver_account_id)

        if sender.currency != receiver.currency:
            raise CurrencyMismatchError(sender.currency, receiver.currency)

        transfer_money = Money(amount, sender.currency)
        if not transfer_money.is_positive():
            raise ValueError("Transfer amount must be positive")

        # Compute balance
        available = BalanceService.get_available_balance(sender_account_id)
        if available < transfer_money:
            raise InsufficientFundsError(
                sender_account_id, str(available.amount), str(transfer_money.amount)
            )

        corr_id = correlation_id or str(uuid.uuid4())

        txn = Transaction(
            type="TRANSFER",
            status="SUCCESS",
            correlation_id=corr_id,
        )
        db.session.add(txn)
        db.session.flush()

        debit = LedgerEntry(
            account_id=sender_account_id,
            transaction_id=txn.id,
            amount=-transfer_money.amount,
            entry_type="DEBIT",
            status="SUCCESS",
            currency=transfer_money.currency,
        )
        credit = LedgerEntry(
            account_id=receiver_account_id,
            transaction_id=txn.id,
            amount=transfer_money.amount,
            entry_type="CREDIT",
            status="SUCCESS",
            currency=receiver.currency,
        )
        db.session.add_all([debit, credit])

        # Bump versions
        sender.version += 1
        receiver.version += 1

        # Snapshot maintenance
        BalanceService.maybe_create_snapshot(sender_account_id)
        BalanceService.maybe_create_snapshot(receiver_account_id)

        db.session.commit()

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
                correlation_id=corr_id,
            )
        )

        return txn

    @staticmethod
    def fail_transfer(transaction_id: str) -> Transaction:
        """Fail a pending transfer, releasing reserved funds."""
        txn = db.session.get(Transaction, transaction_id)
        if not txn:
            raise TransactionNotFoundError(transaction_id)

        if txn.status != "PENDING":
            raise InvalidTransactionStateError(transaction_id, txn.status, "FAILED")

        # Mark all pending entries as FAILED
        pending_entries = LedgerEntry.query.filter_by(
            transaction_id=transaction_id, status="PENDING"
        ).all()

        for entry in pending_entries:
            entry.status = "FAILED"

        txn.status = "FAILED"
        db.session.commit()

        event_bus.publish(
            DomainEvent(
                TRANSFER_FAILED,
                {"transaction_id": txn.id},
                correlation_id=txn.correlation_id,
            )
        )

        return txn
