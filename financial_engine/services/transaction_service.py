import uuid

from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.domain.aggregates import TransactionAggregate, AccountAggregate
from financial_engine.services.balance_service import BalanceService
from financial_engine.services.balance_cache import balance_cache
from financial_engine.domain.exceptions import (
    TransactionNotFoundError,
    InvalidTransactionStateError,
)
from financial_engine.domain.events import (
    DomainEvent,
    event_bus,
    TRANSACTION_REVERSED,
)


class TransactionService:
    """Lifecycle operations on transactions.

    Implements correction of completed transactions through *compensating
    transactions* (the spec's reversal mechanism), never by mutating the
    original financial records.
    """

    @staticmethod
    def reverse_transaction(
        transaction_id: str,
        correlation_id: str | None = None,
    ) -> Transaction:
        """Reverse a settled transaction with a compensating transaction.

        Rules enforced:
        - Only a ``SUCCESS`` transaction can be reversed. ``PENDING`` transfers
          must be cancelled via ``fail_transfer``; ``FAILED`` / ``REVERSED``
          transactions cannot be reversed (prevents double-reversal).
        - The original transaction and its ledger entries are **immutable**:
          the only change is the terminal ``SUCCESS -> REVERSED`` status flag.
        - A new ``REVERSAL`` transaction is created whose entries are the exact
          inverse of the original's ``SUCCESS`` entries, restoring balances.
          The compensating entries sum to zero, preserving double-entry.

        Reversals are administrative corrections and are **not** subject to a
        funds-availability check — they must always succeed to keep the ledger
        consistent (a clawback may legitimately push a balance negative).
        """
        original_row = db.session.get(Transaction, transaction_id)
        if not original_row:
            raise TransactionNotFoundError(transaction_id)

        original = TransactionAggregate.load(original_row)
        if original.status != "SUCCESS":
            # SUCCESS is the only state from which a reversal is valid.
            raise InvalidTransactionStateError(
                transaction_id, original.status, "REVERSED"
            )

        original_entries = LedgerEntry.query.filter_by(
            transaction_id=transaction_id, status="SUCCESS"
        ).all()
        if not original_entries:
            raise InvalidTransactionStateError(
                transaction_id, "NO_SUCCESS_ENTRIES", "REVERSED"
            )

        # Lock affected accounts in a deterministic order to avoid deadlocks
        # with concurrent writes/reversals.
        affected_account_ids = sorted({e.account_id for e in original_entries})
        for account_id in affected_account_ids:
            db.session.query(Account).filter_by(id=account_id).with_for_update().first()

        corr_id = correlation_id or original.correlation_id or str(uuid.uuid4())

        reversal = TransactionAggregate.open(
            type="REVERSAL",
            correlation_id=corr_id,
            status="SUCCESS",
            reverses_transaction_id=original.id,
            metadata={"reverses_transaction_id": original.id},
        )

        # Inverse of every original entry: flip the sign and the DEBIT/CREDIT
        # type. Amounts are stored signed, so -amount with the opposite type
        # keeps the convention consistent and nets the account back to zero.
        for entry in original_entries:
            reversal.add_entry(
                account_id=entry.account_id,
                amount=-entry.amount,
                entry_type="CREDIT" if entry.entry_type == "DEBIT" else "DEBIT",
                currency=entry.currency,
                status="SUCCESS",
            )
        reversal.assert_balanced()

        # Terminal, documented transition. Original entries are left untouched.
        original.mark_reversed()

        for account_id in affected_account_ids:
            account = db.session.get(Account, account_id)
            if account:
                AccountAggregate(account).touch()
            BalanceService.maybe_create_snapshot(account_id)

        db.session.commit()

        # Settled balances changed on every affected account — evict caches.
        balance_cache.invalidate_many(*affected_account_ids)

        event_bus.publish(
            DomainEvent(
                TRANSACTION_REVERSED,
                {
                    "transaction_id": original.id,
                    "reversal_transaction_id": reversal.id,
                    "type": original.transaction.type,
                    "affected_account_ids": affected_account_ids,
                },
                correlation_id=corr_id,
            )
        )

        return reversal.transaction
