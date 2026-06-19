import logging

logger = logging.getLogger(__name__)


class PaymentProviderStub:
    """Mock payment provider that always returns success.

    Supports Stripe, PayPal, and Mobile Money (MTN, Orange).
    """

    SUPPORTED_PROVIDERS = {"stripe", "paypal", "mtn", "orange"}

    @classmethod
    def process_payment(cls, provider: str, amount: str, currency: str, **kwargs) -> dict:
        provider = provider.lower()
        if provider not in cls.SUPPORTED_PROVIDERS:
            return {"success": False, "error": f"Unsupported provider: {provider}"}

        logger.info(
            f"[PAYMENT] Processing {amount} {currency} via {provider}"
        )

        return {
            "success": True,
            "provider": provider,
            "provider_reference": f"{provider}_ref_{amount}_{currency}",
        }

    @classmethod
    def verify_webhook(cls, provider: str, payload: dict, signature: str) -> bool:
        """Stub webhook verification — always returns True."""
        logger.info(f"[WEBHOOK] Verifying {provider} webhook signature")
        return True
