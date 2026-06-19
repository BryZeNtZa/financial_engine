"""Payment provider client interface and shared types.

Every third-party payment provider client under ``financial_engine.providers``
implements :class:`PaymentProviderClientInterface`, exposing a normalized set
of payment operations. This keeps the rest of the system (deposit flow,
webhooks) decoupled from any single provider's API shape.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class PaymentStatus(str, Enum):
    """Normalized payment lifecycle status, mapped from provider-specific values."""

    PENDING = "PENDING"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PaymentProviderError(Exception):
    """Raised when a provider call fails (network, auth, or API-level error)."""

    def __init__(self, message: str, *, provider: str | None = None,
                 status_code: int | None = None, raw=None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.status_code = status_code
        self.raw = raw


@dataclass(frozen=True)
class PaymentRequest:
    """Normalized request to collect funds from a customer."""

    amount: Decimal
    currency: str
    customer_reference: str          # payer MSISDN / phone number
    transaction_reference: str       # our internal id (externalId / order_id)
    description: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentResult:
    """Normalized result of initiating a collection."""

    provider: str
    provider_reference: str          # reference used to poll status later
    status: PaymentStatus
    payment_url: str | None = None   # redirect URL (e.g. Orange Money); None for push (MoMo)
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WebhookEvent:
    """Normalized provider webhook / notification."""

    provider: str
    provider_reference: str
    transaction_reference: str | None
    status: PaymentStatus
    amount: Decimal | None
    currency: str | None
    raw: dict = field(default_factory=dict)


class PaymentProviderClientInterface(abc.ABC):
    """Contract implemented by every payment provider client.

    Implementations are responsible for their own authentication (token
    acquisition/caching) behind these methods, and for mapping their
    provider-specific status codes onto :class:`PaymentStatus`.
    """

    #: short, stable provider identifier, e.g. "mtn_momo", "orange_money"
    name: str = ""

    @abc.abstractmethod
    def initiate_payment(self, request: PaymentRequest) -> PaymentResult:
        """Request a collection from the customer (request-to-pay / web payment)."""

    @abc.abstractmethod
    def get_payment_status(
        self, provider_reference: str, context: dict | None = None
    ) -> PaymentStatus:
        """Query the current status of a previously initiated payment.

        ``context`` carries provider-specific extras some APIs require to look
        up a transaction (e.g. Orange Money needs ``order_id`` and ``amount``
        alongside the ``pay_token``). It is ignored by providers that don't.
        """

    @abc.abstractmethod
    def verify_webhook(
        self,
        payload: dict,
        *,
        headers: dict | None = None,
        raw_body: bytes | None = None,
        context: dict | None = None,
    ) -> bool:
        """Authenticate an incoming webhook/notification before trusting it."""

    @abc.abstractmethod
    def parse_webhook(self, payload: dict) -> WebhookEvent:
        """Parse a verified webhook payload into a normalized event."""


def to_decimal(value) -> Decimal | None:
    """Best-effort conversion of a provider-supplied amount to Decimal."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None
