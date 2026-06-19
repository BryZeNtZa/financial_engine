"""Domain aggregates.

Two aggregate roots own the domain invariants that were previously enforced by
convention across the service layer:

- ``TransactionAggregate`` — the consistency boundary for a transaction and its
  ledger entries. It owns entry creation (propagating the correlation_id),
  the double-entry invariant (entries sum to zero per currency), and the
  transaction state machine (PENDING → SUCCESS/FAILED, SUCCESS → REVERSED).

- ``AccountAggregate`` — a thin root over an account, owning the optimistic-lock
  ``version`` invariant plus the funds-availability and currency-match checks.
  It deliberately does NOT hold the account's ledger entries: balances are
  derived/projected, so loading millions of rows into the aggregate would be
  unworkable.

Application services still orchestrate persistence, locking and cross-aggregate
coordination; the aggregates make the invariants impossible to bypass.
"""

import json
from decimal import Decimal

from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.domain.value_objects import Money
from financial_engine.domain.exceptions import (
    InvalidTransactionStateError,
    LedgerImbalanceError,
    InsufficientFundsError,
    CurrencyMismatchError,
)


class TransactionAggregate:
    """Aggregate root over a transaction and its ledger entries."""

    # Legal state transitions. Completed transactions are immutable except for
    # the terminal SUCCESS -> REVERSED move (done via a compensating transaction).
    _TRANSITIONS = {
        "PENDING": {"SUCCESS", "FAILED"},
        "SUCCESS": {"REVERSED"},
        "FAILED": set(),
        "REVERSED": set(),
    }

    def __init__(self, transaction: Transaction):
        self._txn = transaction

    @classmethod
    def open(
        cls,
        *,
        type: str,
        correlation_id: str | None,
        status: str = "PENDING",
        reverses_transaction_id: str | None = None,
        metadata: dict | None = None,
    ) -> "TransactionAggregate":
        """Create a new transaction and assign its id (ready for entries/events)."""
        txn = Transaction(
            type=type,
            status=status,
            correlation_id=correlation_id,
            reverses_transaction_id=reverses_transaction_id,
            metadata_json=json.dumps(metadata) if metadata is not None else None,
        )
        db.session.add(txn)
        db.session.flush()  # assign txn.id for downstream references/events
        return cls(txn)

    @classmethod
    def load(cls, transaction: Transaction) -> "TransactionAggregate":
        return cls(transaction)

    # -------------------------------------------------------------- accessors
    @property
    def transaction(self) -> Transaction:
        return self._txn

    @property
    def id(self) -> str:
        return self._txn.id

    @property
    def correlation_id(self) -> str | None:
        return self._txn.correlation_id

    @property
    def status(self) -> str:
        return self._txn.status

    @property
    def entries(self) -> list[LedgerEntry]:
        return list(self._txn.entries)

    def metadata(self) -> dict:
        return json.loads(self._txn.metadata_json) if self._txn.metadata_json else {}

    # ---------------------------------------------------------- entry creation
    def add_entry(
        self, account_id: str, amount: Decimal, entry_type: str, currency: str,
        status: str = "SUCCESS",
    ) -> LedgerEntry:
        """Append a ledger entry, propagating the transaction's correlation_id."""
        entry = LedgerEntry(
            account_id=account_id,
            amount=amount,
            entry_type=entry_type,
            status=status,
            currency=currency,
            correlation_id=self._txn.correlation_id,
        )
        # Associate via the relationship so the FK is set on flush.
        self._txn.entries.append(entry)
        return entry

    def debit(self, account_id: str, money: Money, status: str = "SUCCESS") -> LedgerEntry:
        return self.add_entry(account_id, -money.amount, "DEBIT", money.currency, status)

    def credit(self, account_id: str, money: Money, status: str = "SUCCESS") -> LedgerEntry:
        return self.add_entry(account_id, money.amount, "CREDIT", money.currency, status)

    def reserve_debit(self, account_id: str, money: Money) -> LedgerEntry:
        """Phase-1 reservation: a PENDING debit."""
        return self.debit(account_id, money, status="PENDING")

    def record_double_entry(
        self, *, debit_account_id: str, credit_account_id: str, money: Money,
        status: str = "SUCCESS",
    ) -> None:
        """A balanced debit/credit pair of equal magnitude in one currency."""
        self.debit(debit_account_id, money, status)
        self.credit(credit_account_id, money, status)

    # --------------------------------------------------------------- invariant
    def assert_balanced(self) -> None:
        """Double-entry invariant: SUCCESS entries sum to zero per currency."""
        totals: dict[str, Decimal] = {}
        for entry in self._txn.entries:
            if entry.status == "SUCCESS":
                totals[entry.currency] = totals.get(entry.currency, Decimal("0")) + entry.amount
        for currency, total in totals.items():
            if total != Decimal("0"):
                raise LedgerImbalanceError(self._txn.id, currency, str(total))

    # ----------------------------------------------------------- state machine
    def transition_to(self, new_status: str) -> None:
        current = self._txn.status
        if new_status not in self._TRANSITIONS.get(current, set()):
            raise InvalidTransactionStateError(self._txn.id, current, new_status)
        self._txn.status = new_status

    def mark_success(self) -> None:
        self.transition_to("SUCCESS")

    def mark_failed(self) -> None:
        self.transition_to("FAILED")

    def mark_reversed(self) -> None:
        self.transition_to("REVERSED")

    def settle_pending(self) -> None:
        """Flip this transaction's PENDING entries to SUCCESS (phase-2 commit)."""
        for entry in self._txn.entries:
            if entry.status == "PENDING":
                entry.status = "SUCCESS"

    def fail_pending(self) -> None:
        """Flip this transaction's PENDING entries to FAILED (release reservation)."""
        for entry in self._txn.entries:
            if entry.status == "PENDING":
                entry.status = "FAILED"


class AccountAggregate:
    """Thin aggregate root over an account: optimistic-lock + guard invariants."""

    def __init__(self, account: Account):
        self._account = account

    @property
    def account(self) -> Account:
        return self._account

    @property
    def id(self) -> str:
        return self._account.id

    @property
    def currency(self) -> str:
        return self._account.currency

    @property
    def version(self) -> int:
        return self._account.version

    def touch(self) -> None:
        """Bump the optimistic-lock version on every state-changing write."""
        self._account.version = (self._account.version or 0) + 1

    def assert_same_currency_as(self, currency: str) -> None:
        if self._account.currency != currency:
            raise CurrencyMismatchError(self._account.currency, currency)

    def assert_sufficient(self, available: Money, requested: Money) -> None:
        if available < requested:
            raise InsufficientFundsError(
                self._account.id, str(available.amount), str(requested.amount)
            )
