"""Third-party payment provider clients.

All clients implement :class:`PaymentProviderClientInterface`. Use
:func:`get_provider_client` to build a configured client by name.
"""

from financial_engine.providers.base import (
    PaymentProviderClientInterface,
    PaymentProviderError,
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
)
from financial_engine.providers.momo.client import MomoClient
from financial_engine.providers.om.client import OmClient

# Registry of provider name -> client class.
PROVIDER_REGISTRY: dict[str, type[PaymentProviderClientInterface]] = {
    MomoClient.name: MomoClient,
    OmClient.name: OmClient,
}


def get_provider_client(name: str, config) -> PaymentProviderClientInterface:
    """Build a configured client for ``name`` from a Flask/dict config mapping."""
    try:
        client_cls = PROVIDER_REGISTRY[name]
    except KeyError as exc:
        raise PaymentProviderError(
            f"Unknown payment provider: {name}", provider=name
        ) from exc
    return client_cls.from_config(config)


__all__ = [
    "PaymentProviderClientInterface",
    "PaymentProviderError",
    "PaymentRequest",
    "PaymentResult",
    "PaymentStatus",
    "WebhookEvent",
    "MomoClient",
    "OmClient",
    "PROVIDER_REGISTRY",
    "get_provider_client",
]
