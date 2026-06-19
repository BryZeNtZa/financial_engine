"""Tests for Bonus 3 — Email / SMS notifications.

The Twilio path is exercised with a fake Twilio client (no network); the
provider selection falls back to logging when Twilio is not configured.
"""

from decimal import Decimal

from financial_engine.tests import deposit_funds
from financial_engine.models.notification import Notification
from financial_engine.domain.events import (
    event_bus,
    TRANSFER_COMPLETED,
    DEPOSIT_COMPLETED,
)
from financial_engine.services.notification_service import NotificationService
from financial_engine.services.sms_provider import (
    build_sms_provider,
    LogSmsProvider,
    TwilioSmsProvider,
)
from financial_engine.services.email_provider import (
    build_email_provider,
    LogEmailProvider,
    SmtpEmailProvider,
)


class FakeTwilioMessage:
    def __init__(self, sid="SM123"):
        self.sid = sid


class FakeTwilioMessages:
    def __init__(self, raises=False):
        self._raises = raises
        self.created = []

    def create(self, to, from_, body):
        if self._raises:
            raise RuntimeError("twilio boom")
        self.created.append({"to": to, "from_": from_, "body": body})
        return FakeTwilioMessage()


class FakeTwilioClient:
    def __init__(self, raises=False):
        self.messages = FakeTwilioMessages(raises=raises)


class TestSmsProviderSelection:
    def test_falls_back_to_log_without_credentials(self):
        provider = build_sms_provider({})
        assert isinstance(provider, LogSmsProvider)

    def test_selects_twilio_when_configured(self):
        provider = build_sms_provider({
            "TWILIO_ACCOUNT_SID": "AC123",
            "TWILIO_AUTH_TOKEN": "tok",
            "TWILIO_FROM_NUMBER": "+15550000000",
        })
        assert isinstance(provider, TwilioSmsProvider)


class TestTwilioSmsProvider:
    def test_send_calls_twilio_client(self):
        fake = FakeTwilioClient()
        provider = TwilioSmsProvider("AC", "tok", "+15550000000", client=fake)

        assert provider.send("+237670000000", "You sent 50 USD to John") is True
        assert fake.messages.created == [{
            "to": "+237670000000",
            "from_": "+15550000000",
            "body": "You sent 50 USD to John",
        }]

    def test_send_failure_returns_false(self):
        fake = FakeTwilioClient(raises=True)
        provider = TwilioSmsProvider("AC", "tok", "+15550000000", client=fake)
        assert provider.send("+237670000000", "hi") is False


class FakeSMTP:
    def __init__(self, raises=False):
        self._raises = raises
        self.tls = False
        self.logged_in = None
        self.sent = []
        self.quit_called = False

    def starttls(self, context=None):
        self.tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        if self._raises:
            raise RuntimeError("smtp boom")
        self.sent.append(message)

    def quit(self):
        self.quit_called = True


class TestEmailProviderSelection:
    def test_falls_back_to_log_without_smtp_host(self):
        assert isinstance(build_email_provider({}), LogEmailProvider)

    def test_selects_smtp_when_host_configured(self):
        provider = build_email_provider({
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "u@example.com",
            "SMTP_PASSWORD": "pw",
        })
        assert isinstance(provider, SmtpEmailProvider)


class TestSmtpEmailProvider:
    def test_send_builds_and_dispatches_message(self):
        fake = FakeSMTP()
        provider = SmtpEmailProvider(
            host="smtp.example.com", username="u@example.com", password="pw",
            from_address="u@example.com", smtp_factory=lambda: fake,
        )

        assert provider.send("to@example.com", "Deposit confirmed", "You received 100 USD") is True
        assert fake.tls is True
        assert fake.logged_in == ("u@example.com", "pw")
        assert fake.quit_called is True
        assert len(fake.sent) == 1
        msg = fake.sent[0]
        assert msg["To"] == "to@example.com"
        assert msg["From"] == "u@example.com"
        assert msg["Subject"] == "Deposit confirmed"
        assert "You received 100 USD" in msg.get_content()

    def test_send_failure_returns_false(self):
        fake = FakeSMTP(raises=True)
        provider = SmtpEmailProvider(
            host="smtp.example.com", from_address="u@example.com",
            smtp_factory=lambda: fake,
        )
        assert provider.send("to@example.com", "Subj", "Body") is False
        # Connection is still closed on failure.
        assert fake.quit_called is True


class TestNotificationService:
    def test_records_and_sends_sms(self, app, db):
        fake = FakeTwilioClient()
        sms = TwilioSmsProvider("AC", "tok", "+15550000000", client=fake)
        service = NotificationService(sms_provider=sms)

        with app.app_context():
            notif = service.send_sms(
                user_id="u1", recipient="+237670000000",
                body="Deposit of 100 USD confirmed.",
            )
            assert notif.status == "SENT"
            assert notif.channel == "SMS"
            assert notif.recipient == "+237670000000"

        assert len(fake.messages.created) == 1

    def test_uses_default_recipient_when_none(self, app, db):
        fake = FakeTwilioClient()
        sms = TwilioSmsProvider("AC", "tok", "+15550000000", client=fake)
        service = NotificationService(
            config={"NOTIFICATION_DEFAULT_RECIPIENT": "+10000000000"},
            sms_provider=sms,
        )
        with app.app_context():
            notif = service.send_sms(user_id="u1", recipient=None, body="hi")
            assert notif.recipient == "+10000000000"

    def test_records_and_sends_email(self, app, db):
        fake = FakeSMTP()
        email = SmtpEmailProvider(
            host="smtp.example.com", username="u@example.com", password="pw",
            from_address="u@example.com", smtp_factory=lambda: fake,
        )
        service = NotificationService(email_provider=email)

        with app.app_context():
            notif = service.send_email(
                user_id="u1", recipient="to@example.com",
                subject="Deposit confirmed", body="You received 100 USD",
            )
            assert notif.status == "SENT"
            assert notif.channel == "EMAIL"
            assert notif.subject == "Deposit confirmed"

        assert len(fake.sent) == 1


class TestNotificationEventWiring:
    """The conftest clears the event bus between tests, so each test subscribes
    its own NotificationService (log SMS provider) to the events it asserts on."""

    def test_transfer_emits_sms_notifications(self, client, alice_account, bob_account):
        notifier = NotificationService()
        event_bus.subscribe(TRANSFER_COMPLETED, notifier.handle_transfer_completed)

        deposit_funds(client, alice_account, 100)
        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "50",
        })
        assert resp.status_code == 201

        # Sender + receiver SMS were recorded by the subscribed NotificationService.
        sms = Notification.query.filter_by(channel="SMS").all()
        bodies = [n.body for n in sms]
        assert any("You sent 50" in b for b in bodies)
        assert any("You received 50" in b for b in bodies)

    def test_deposit_notification_routes_to_payer_phone(self, client, alice_account):
        notifier = NotificationService()
        event_bus.subscribe(DEPOSIT_COMPLETED, notifier.handle_deposit_completed)

        # provider "stripe" runs in simulation mode but still carries a payer.
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100",
            "provider": "stripe", "payer": "+237670000000",
        })
        txn_id = resp.get_json()["transaction_id"]
        client.post("/api/v1/payments/webhook", json={
            "transaction_id": txn_id, "amount": "100", "provider": "stripe",
        })

        notif = Notification.query.filter_by(channel="SMS").filter(
            Notification.body.like("Deposit of 100%")
        ).first()
        assert notif is not None
        assert notif.recipient == "+237670000000"
