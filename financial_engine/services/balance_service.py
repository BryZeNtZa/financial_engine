from decimal import Decimal
from datetime import datetime, timezone

from sqlalchemy import func

from financial_engine.domain.value_objects import Money
from financial_engine.extensions import db
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.models.balance_snapshot import BalanceSnapshot
from financial_engine.models.account import Account
from financial_engine.domain.exceptions import AccountNotFoundError
from financial_engine.services.balance_cache import balance_cache


class BalanceService:
    """Computes account balances from the ledger with snapshot optimization."""

    SNAPSHOT_THRESHOLD = 100  # Create snapshot every N entries

    @staticmethod
    def get_balance(account_id: str) -> Money:
        """Return the settled balance, using the cache-aside flow.

        Cache lookup → ledger calculation on miss → cache update.
        """
        cached = balance_cache.get(account_id)
        if cached is not None:
            return Money(cached["amount"], cached["currency"])

        money = BalanceService._compute_balance(account_id)
        balance_cache.set(account_id, str(money.amount), money.currency)
        return money

    @staticmethod
    def _compute_balance(account_id: str) -> Money:
        """Compute balance from the ledger using snapshot + delta. Uncached."""
        account = db.session.get(Account, account_id)
        if not account:
            raise AccountNotFoundError(account_id)

        # Try to find latest snapshot
        snapshot = (
            BalanceSnapshot.query.filter_by(account_id=account_id)
            .order_by(BalanceSnapshot.created_at.desc())
            .first()
        )

        if snapshot:
            # Sum entries created after the snapshot
            delta = (
                db.session.query(func.coalesce(func.sum(LedgerEntry.amount), 0))
                .filter(
                    LedgerEntry.account_id == account_id,
                    LedgerEntry.status == "SUCCESS",
                    LedgerEntry.created_at > snapshot.snapshot_at,
                )
                .scalar()
            )
            raw = Decimal(str(snapshot.balance)) + Decimal(str(delta))
        else:
            # No snapshot — compute from entire ledger
            raw = (
                db.session.query(func.coalesce(func.sum(LedgerEntry.amount), 0))
                .filter(
                    LedgerEntry.account_id == account_id,
                    LedgerEntry.status == "SUCCESS",
                )
                .scalar()
            )
            raw = Decimal(str(raw))

        return Money(raw, account.currency)

    @staticmethod
    def get_available_balance(account_id: str) -> Money:
        """Available balance = settled balance (cached) minus PENDING debits.

        The expensive part — the settled balance — comes from the cache.
        Only the (typically tiny) set of PENDING debits is summed live, since
        reservations change frequently and must always reflect the latest state.
        """
        settled = BalanceService.get_balance(account_id)

        pending_debits = (
            db.session.query(func.coalesce(func.sum(LedgerEntry.amount), 0))
            .filter(
                LedgerEntry.account_id == account_id,
                LedgerEntry.status == "PENDING",
                LedgerEntry.entry_type == "DEBIT",
            )
            .scalar()
        )

        raw = Decimal(str(settled.amount)) + Decimal(str(pending_debits))
        return Money(raw, settled.currency)

    @classmethod
    def maybe_create_snapshot(cls, account_id: str):
        """Create a snapshot if entry count exceeds threshold since last snapshot."""
        latest = (
            BalanceSnapshot.query.filter_by(account_id=account_id)
            .order_by(BalanceSnapshot.created_at.desc())
            .first()
        )

        last_count = latest.entry_count if latest else 0

        current_count = (
            LedgerEntry.query.filter_by(account_id=account_id, status="SUCCESS").count()
        )

        if current_count - last_count >= cls.SNAPSHOT_THRESHOLD:
            # Use the uncached path: this runs mid-write-transaction and must
            # reflect the freshly-flushed entries, not a stale cached value.
            balance_money = cls._compute_balance(account_id)
            now = datetime.now(timezone.utc)
            snapshot = BalanceSnapshot(
                account_id=account_id,
                balance=balance_money.amount,
                entry_count=current_count,
                snapshot_at=now,
            )
            db.session.add(snapshot)

    @staticmethod
    def get_entry_count(account_id: str) -> int:
        return LedgerEntry.query.filter_by(
            account_id=account_id, status="SUCCESS"
        ).count()
