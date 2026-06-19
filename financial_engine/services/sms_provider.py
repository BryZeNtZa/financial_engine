"""SMS providers for notifications.

Two implementations behind a small interface:
- ``TwilioSmsProvider`` — sends via the Twilio REST API (``twilio`` package).
- ``LogSmsProvider``    — logs the message; the graceful fallback used in dev
                          and tests when Twilio is not configured.

``build_sms_provider(config)`` selects Twilio when its credentials are present
and the package is importable, otherwise falls back to logging — mirroring the
graceful-fallback pattern used by the balance cache and payment gateway.
"""

import abc
import logging

logger = logging.getLogger(__name__)


class SmsProvider(abc.ABC):
    """Sends an SMS to a recipient phone number."""

    name: str = ""

    @abc.abstractmethod
    def send(self, recipient: str, body: str) -> bool:
        """Return True if the message was accepted for delivery."""


class LogSmsProvider(SmsProvider):
    """Fallback provider that logs instead of sending (dev / tests)."""

    name = "log"

    def send(self, recipient: str, body: str) -> bool:
        logger.info("[SMS] To: %s Body: %s", recipient, body)
        return True


class TwilioSmsProvider(SmsProvider):
    """Sends SMS through Twilio's REST API."""

    name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, client=None):
        self._from = from_number
        if client is not None:
            self._client = client
        else:
            # Imported lazily so the twilio package stays an optional dependency.
            from twilio.rest import Client

            self._client = Client(account_sid, auth_token)

    def send(self, recipient: str, body: str) -> bool:
        try:
            message = self._client.messages.create(
                to=recipient, from_=self._from, body=body
            )
            logger.info(
                "Twilio SMS sent sid=%s to=%s", getattr(message, "sid", "?"), recipient
            )
            return True
        except Exception:
            logger.warning("Twilio SMS send failed to=%s", recipient, exc_info=True)
            return False


def build_sms_provider(config) -> SmsProvider:
    """Select an SMS provider from config, falling back to logging."""
    config = config or {}
    sid = config.get("TWILIO_ACCOUNT_SID")
    token = config.get("TWILIO_AUTH_TOKEN")
    from_number = config.get("TWILIO_FROM_NUMBER")

    if sid and token and from_number:
        try:
            provider = TwilioSmsProvider(sid, token, from_number)
            logger.info("SMS provider: Twilio (from %s)", from_number)
            return provider
        except Exception:
            logger.warning(
                "Twilio init failed; SMS provider falling back to log", exc_info=True
            )

    logger.info("SMS provider: log (Twilio not configured)")
    return LogSmsProvider()
