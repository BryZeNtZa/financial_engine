import logging

from financial_engine.extensions import db
from financial_engine.models.notification import Notification
from financial_engine.domain.events import DomainEvent
from financial_engine.services.sms_provider import build_sms_provider
from financial_engine.services.email_provider import build_email_provider

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends notifications for financial events.

    Architecture:
        NotificationService
          ├ EmailProvider  (SMTP when configured, else a logging fallback)
          └ SmsProvider    (Twilio when configured, else a logging fallback)
    """

    def __init__(self, config=None, sms_provider=None, email_provider=None):
        config = config or {}
        self.email_provider = email_provider or build_email_provider(config)
        self.sms_provider = sms_provider or build_sms_provider(config)
        self.default_recipient = config.get(
            "NOTIFICATION_DEFAULT_RECIPIENT", "stub-phone"
        )

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
        recipient: str | None,
        body: str,
        correlation_id: str | None = None,
    ) -> Notification:
        recipient = recipient or self.default_recipient
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
            recipient=None,
            body=f"You sent {amount} {currency} successfully.",
            correlation_id=event.correlation_id,
        )
        self.send_sms(
            user_id=receiver_id,
            recipient=None,
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
            # Route to the payer's phone when the deposit carried one.
            recipient=payload.get("payer"),
            body=f"Deposit of {amount} {currency} confirmed.",
            correlation_id=event.correlation_id,
        )

    def handle_transfer_failed(self, event: DomainEvent):
        payload = event.payload
        txn_id = payload.get("transaction_id", "")

        self.send_sms(
            user_id="unknown",
            recipient=None,
            body=f"Transfer {txn_id} failed.",
            correlation_id=event.correlation_id,
        )

    def handle_transaction_reversed(self, event: DomainEvent):
        payload = event.payload
        txn_id = payload.get("transaction_id", "")

        self.send_sms(
            user_id="unknown",
            recipient=None,
            body=f"Transaction {txn_id} was reversed.",
            correlation_id=event.correlation_id,
        )
