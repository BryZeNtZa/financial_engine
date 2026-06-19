"""Resolution seam between the deposit flow and concrete provider clients.

`get_client` returns a configured :class:`PaymentProviderClientInterface` when
the named provider is known *and* its credentials are configured; otherwise it
returns ``None`` to signal **simulation mode** (used by local dev, tests, and
providers without a real client such as stripe/paypal). This single seam is
also the place tests monkeypatch to inject a fake client.
"""

import logging

from flask import current_app

from financial_engine.providers import get_provider_client
from financial_engine.providers.base import PaymentProviderClientInterface

logger = logging.getLogger(__name__)

# Deposit-facing provider names -> provider registry keys.
PROVIDER_ALIASES = {
    "mtn": "mtn_momo",
    "momo": "mtn_momo",
    "mtn_momo": "mtn_momo",
    "orange": "orange_money",
    "om": "orange_money",
    "orange_money": "orange_money",
}

# Config keys that must all be present for a provider to be "configured".
_REQUIRED_CONFIG = {
    "mtn_momo": ("MOMO_SUBSCRIPTION_KEY", "MOMO_API_USER", "MOMO_API_KEY"),
    "orange_money": ("OM_AUTHORIZATION_HEADER", "OM_MERCHANT_KEY"),
}

# Providers that collect from a customer's mobile wallet and therefore require
# the payer's phone number (MSISDN) to initiate a collection.
MOBILE_MONEY_PROVIDERS = {"mtn_momo", "orange_money"}


class PaymentGateway:
    """Resolves provider clients with a simulation fallback."""

    @staticmethod
    def resolve_name(provider: str | None) -> str | None:
        if not provider:
            return None
        return PROVIDER_ALIASES.get(provider.lower())

    @staticmethod
    def is_configured(registry_name: str, config) -> bool:
        required = _REQUIRED_CONFIG.get(registry_name, ())
        return bool(required) and all(config.get(key) for key in required)

    @staticmethod
    def requires_payer(provider: str | None) -> bool:
        """True when the provider is a mobile-money provider needing an MSISDN."""
        return PaymentGateway.resolve_name(provider) in MOBILE_MONEY_PROVIDERS

    @staticmethod
    def get_client(provider: str | None) -> PaymentProviderClientInterface | None:
        """Return a configured client, or ``None`` for simulation mode."""
        registry_name = PaymentGateway.resolve_name(provider)
        if not registry_name:
            return None  # unknown provider (e.g. stripe/paypal) -> simulation

        config = current_app.config
        if not PaymentGateway.is_configured(registry_name, config):
            logger.info(
                "Provider %s has no credentials configured; using simulation mode",
                registry_name,
            )
            return None

        return get_provider_client(registry_name, config)
