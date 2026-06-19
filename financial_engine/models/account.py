import uuid
import random
from datetime import datetime, timezone

from sqlalchemy.ext.hybrid import hybrid_property

from financial_engine.extensions import db


def _generate_account_number():
    """Generate a unique 10-digit account number."""
    return f"20{random.randint(10000000, 99999999)}"


class Account(db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    number = db.Column(
        db.String(10), unique=True, nullable=False, index=True,
        default=_generate_account_number,
    )
    user_id = db.Column(db.String(36), nullable=False, index=True)
    _currency = db.Column("currency", db.String(3), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    version = db.Column(db.Integer, nullable=False, default=0)

    ledger_entries = db.relationship("LedgerEntry", backref="account", lazy="dynamic")
    snapshots = db.relationship("BalanceSnapshot", backref="account", lazy="dynamic")

    @hybrid_property
    def currency(self):
        return self._currency

    @currency.setter
    def currency(self, value):
        self._currency = value.upper() if value else value

    def __repr__(self):
        return f"<Account {self.number} user={self.user_id} currency={self.currency}>"
