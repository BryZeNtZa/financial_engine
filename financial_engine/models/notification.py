import uuid
from datetime import datetime, timezone

from financial_engine.extensions import db


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), nullable=False, index=True)
    channel = db.Column(db.String(10), nullable=False)  # EMAIL or SMS
    recipient = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=True)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(
        db.String(20), nullable=False, default="PENDING"
    )  # PENDING, SENT, FAILED
    correlation_id = db.Column(db.String(36), nullable=True, index=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self):
        return f"<Notification {self.id} channel={self.channel} status={self.status}>"
