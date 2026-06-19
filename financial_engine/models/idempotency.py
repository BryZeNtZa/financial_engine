import uuid
from datetime import datetime, timezone

from financial_engine.extensions import db


class IdempotencyRecord(db.Model):
    __tablename__ = "idempotency_records"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = db.Column(db.String(255), nullable=False, unique=True, index=True)
    request_hash = db.Column(db.String(64), nullable=False)
    response_code = db.Column(db.Integer, nullable=False)
    response_body = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self):
        return f"<IdempotencyRecord key={self.key}>"
