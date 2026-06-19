import logging

from financial_engine.extensions import db
from financial_engine.models.notification import Notification
from financial_engine.domain.events import DomainEvent

logger = logging.getLogger(__name__)


class EmailProvider:
    """Stub email provider."""

    @staticmethod
    def send(recipient: str, subject: str, body: str) -> bool:
        logger.info(f"[EMAIL] To: {recipient} Subject: {subject} Body: {body}")
        return True


class SMSProvider:
    """Stub SMS provider."""

    @staticmethod
    def send(recipient: str, body: str) -> bool:
        logger.info(f"[SMS] To: {recipient} Body: {body}")
        return True


class NotificationService:
    """Sends notifications for financial events."""

    def __init__(self):
        self.email_provider = EmailProvider()
        self.sms_provider = SMSProvider()

    def send_email(
        self,
        user_id: str,
        recipient: str,
        subject: str,
        body: str,
        correlation_id: str | None = None,
    ) -> Notification:
        success = self.email_provider.send(recipient, subject, body)
        notif = Notification(
            user_id=user_id,
            channel="EMAIL",
            recipient=recipient,
            subject=subject,
            body=body,
            status="SENT" if success else "FAILED",
            correlation_id=correlation_id,
        )
        db.session.add(notif)
        db.session.commit()
        return notif

    def send_sms(
        self,
        user_id: str,
        recipient: str,
        body: str,
        correlation_id: str | None = None,
    ) -> Notification:
        success = self.sms_provider.send(recipient, body)
        notif = Notification(
            user_id=user_id,
            channel="SMS",
            recipient=recipient,
            body=body,
            status="SENT" if success else "FAILED",
            correlation_id=correlation_id,
        )
        db.session.add(notif)
        db.session.commit()
        return notif

    def handle_transfer_completed(self, event: DomainEvent):
        payload = event.payload
        amount = payload.get("amount", "0")
        currency = payload.get("currency", "")
        sender_id = payload.get("sender_account_id", "")
        receiver_id = payload.get("receiver_account_id", "")

        self.send_sms(
            user_id=sender_id,
            recipient="stub-phone",
            body=f"You sent {amount} {currency} successfully.",
            correlation_id=event.correlation_id,
        )
        self.send_sms(
            user_id=receiver_id,
            recipient="stub-phone",
            body=f"You received {amount} {currency}.",
            correlation_id=event.correlation_id,
        )

    def handle_deposit_completed(self, event: DomainEvent):
        payload = event.payload
        amount = payload.get("amount", "0")
        currency = payload.get("currency", "")
        account_id = payload.get("account_id", "")

        self.send_sms(
            user_id=account_id,
            recipient="stub-phone",
            body=f"Deposit of {amount} {currency} confirmed.",
            correlation_id=event.correlation_id,
        )

    def handle_transfer_failed(self, event: DomainEvent):
        payload = event.payload
        txn_id = payload.get("transaction_id", "")

        self.send_sms(
            user_id="unknown",
            recipient="stub-phone",
            body=f"Transfer {txn_id} failed.",
            correlation_id=event.correlation_id,
        )
