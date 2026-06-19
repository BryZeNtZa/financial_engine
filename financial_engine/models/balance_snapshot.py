import uuid
from datetime import datetime, timezone

from financial_engine.extensions import db


class BalanceSnapshot(db.Model):
    __tablename__ = "balance_snapshots"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = db.Column(
        db.String(36), db.ForeignKey("accounts.id"), nullable=False, index=True
    )
    balance = db.Column(db.Numeric(precision=19, scale=4), nullable=False)
    entry_count = db.Column(db.Integer, nullable=False)
    snapshot_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        db.Index("ix_snapshot_account_created", "account_id", "created_at"),
    )

    def __repr__(self):
        return (
            f"<BalanceSnapshot account={self.account_id} "
            f"balance={self.balance} entries={self.entry_count}>"
        )
