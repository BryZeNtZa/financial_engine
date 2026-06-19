from decimal import Decimal
from datetime import datetime, timezone

from sqlalchemy import func

from financial_engine.domain.value_objects import Money
from financial_engine.extensions import db
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.models.balance_snapshot import BalanceSnapshot
from financial_engine.models.account import Account
from financial_engine.domain.exceptions import AccountNotFoundError


class BalanceService:
    """Computes account balances from the ledger with snapshot optimization."""

    SNAPSHOT_THRESHOLD = 100  # Create snapshot every N entries

    @staticmethod
    def get_balance(account_id: str) -> Money:
        """Compute balance using snapshot + delta pattern. Returns a Money value object."""
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
        """Compute available balance (SUCCESS entries minus PENDING debits). Returns Money."""
        account = db.session.get(Account, account_id)
        if not account:
            raise AccountNotFoundError(account_id)

        settled = (
            db.session.query(func.coalesce(func.sum(LedgerEntry.amount), 0))
            .filter(
                LedgerEntry.account_id == account_id,
                LedgerEntry.status == "SUCCESS",
            )
            .scalar()
        )

        pending_debits = (
            db.session.query(func.coalesce(func.sum(LedgerEntry.amount), 0))
            .filter(
                LedgerEntry.account_id == account_id,
                LedgerEntry.status == "PENDING",
                LedgerEntry.entry_type == "DEBIT",
            )
            .scalar()
        )

        raw = Decimal(str(settled)) + Decimal(str(pending_debits))
        return Money(raw, account.currency)

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
            balance_money = cls.get_balance(account_id)
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
