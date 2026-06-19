"""Email providers for notifications.

Two implementations behind a small interface:
- ``SmtpEmailProvider`` — sends via SMTP using the standard library (works with
  Gmail, Amazon SES, SendGrid, Mailgun, etc.; no extra dependency).
- ``LogEmailProvider``  — logs the message; the graceful fallback used in dev
                          and tests when SMTP is not configured.

``build_email_provider(config)`` selects SMTP when ``SMTP_HOST`` is configured,
otherwise falls back to logging — the same pattern used by the SMS provider.
"""

import abc
import logging
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class EmailProvider(abc.ABC):
    """Sends an email to a recipient address."""

    name: str = ""

    @abc.abstractmethod
    def send(self, recipient: str, subject: str, body: str) -> bool:
        """Return True if the message was accepted for delivery."""


class LogEmailProvider(EmailProvider):
    """Fallback provider that logs instead of sending (dev / tests)."""

    name = "log"

    def send(self, recipient: str, subject: str, body: str) -> bool:
        logger.info("[EMAIL] To: %s Subject: %s Body: %s", recipient, subject, body)
        return True


class SmtpEmailProvider(EmailProvider):
    """Sends email over SMTP via the standard library."""

    name = "smtp"

    def __init__(
        self,
        host: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        from_address: str | None = None,
        use_tls: bool = True,
        timeout: int = 15,
        smtp_factory=None,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from = from_address or username
        self._use_tls = use_tls
        self._timeout = timeout
        # Injectable connection factory for testing.
        self._smtp_factory = smtp_factory

    def _connect(self):
        if self._smtp_factory is not None:
            return self._smtp_factory()
        return smtplib.SMTP(self._host, self._port, timeout=self._timeout)

    def send(self, recipient: str, subject: str, body: str) -> bool:
        message = EmailMessage()
        message["From"] = self._from
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        try:
            smtp = self._connect()
            try:
                if self._use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
            finally:
                smtp.quit()
            logger.info("SMTP email sent to=%s subject=%s", recipient, subject)
            return True
        except Exception:
            logger.warning("SMTP email send failed to=%s", recipient, exc_info=True)
            return False


def build_email_provider(config) -> EmailProvider:
    """Select an email provider from config, falling back to logging."""
    config = config or {}
    host = config.get("SMTP_HOST")

    if host:
        provider = SmtpEmailProvider(
            host=host,
            port=int(config.get("SMTP_PORT", 587)),
            username=config.get("SMTP_USERNAME"),
            password=config.get("SMTP_PASSWORD"),
            from_address=config.get("SMTP_FROM_ADDRESS") or config.get("SMTP_USERNAME"),
            use_tls=str(config.get("SMTP_USE_TLS", "true")).lower() == "true",
        )
        logger.info("Email provider: SMTP (%s:%s)", host, provider._port)
        return provider

    logger.info("Email provider: log (SMTP not configured)")
    return LogEmailProvider()
