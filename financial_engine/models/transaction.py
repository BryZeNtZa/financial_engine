import uuid
from datetime import datetime, timezone

from financial_engine.extensions import db


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type = db.Column(
        db.String(20), nullable=False
    )  # TRANSFER, DEPOSIT, FX_TRANSFER
    status = db.Column(
        db.String(20), nullable=False, default="PENDING"
    )  # PENDING, SUCCESS, FAILED, REVERSED
    reference = db.Column(db.String(100), nullable=True, unique=True)
    correlation_id = db.Column(db.String(36), nullable=True, index=True)
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    entries = db.relationship("LedgerEntry", backref="transaction", lazy="select")

    def __repr__(self):
        return f"<Transaction {self.id} type={self.type} status={self.status}>"
