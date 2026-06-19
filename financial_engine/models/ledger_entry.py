import uuid
from datetime import datetime, timezone
from decimal import Decimal

from financial_engine.extensions import db


class LedgerEntry(db.Model):
    __tablename__ = "ledger_entries"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = db.Column(
        db.String(36), db.ForeignKey("accounts.id"), nullable=False, index=True
    )
    transaction_id = db.Column(
        db.String(36), db.ForeignKey("transactions.id"), nullable=False, index=True
    )
    amount = db.Column(db.Numeric(precision=19, scale=4), nullable=False)
    entry_type = db.Column(
        db.String(10), nullable=False
    )  # DEBIT or CREDIT
    status = db.Column(
        db.String(20), nullable=False, default="PENDING"
    )  # PENDING, SUCCESS, FAILED
    currency = db.Column(db.String(3), nullable=False)
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        db.Index("ix_ledger_account_status", "account_id", "status"),
        db.Index("ix_ledger_account_created", "account_id", "created_at"),
    )

    @property
    def signed_amount(self) -> Decimal:
        """Return the amount with sign based on entry type."""
        if self.entry_type == "DEBIT":
            return -abs(self.amount)
        return abs(self.amount)

    def __repr__(self):
        return (
            f"<LedgerEntry {self.id} account={self.account_id} "
            f"amount={self.amount} type={self.entry_type} status={self.status}>"
        )
