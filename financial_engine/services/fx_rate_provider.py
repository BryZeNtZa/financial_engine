import logging
import time
from decimal import Decimal

import requests
from flask import current_app

logger = logging.getLogger(__name__)

# Fallback rates used when the third-party API is unreachable (base: EUR)
FALLBACK_RATES = {
    "EUR": Decimal("1"),
    "USD": Decimal("1.14487288"),
    "GBP": Decimal("0.863714"),
    "XAF": Decimal("655.95700002"),
    "XOF": Decimal("655.95700002"),
    "NGN": Decimal("1587.41808004"),
    "KES": Decimal("148.20698867"),
    "GHS": Decimal("12.45250479"),
    "ZAR": Decimal("19.29030952"),
}


class FXRateProvider:
    """Fetches live FX rates from a third-party API with in-memory caching."""

    def __init__(self):
        self._cache = {}          # {base_currency: {currency: Decimal rate, ...}}
        self._cache_ts = {}       # {base_currency: timestamp}

    @staticmethod
    def _api_base_url() -> str:
        return current_app.config.get(
            "FX_RATE_API_URL",
            "https://latest.currency-api.pages.dev/v1/currencies",
        )

    @staticmethod
    def _cache_ttl() -> int:
        """Cache time-to-live in seconds (default 5 minutes)."""
        return int(current_app.config.get("FX_RATE_CACHE_TTL", 300))

    @staticmethod
    def _timeout() -> int:
        """HTTP request timeout in seconds."""
        return int(current_app.config.get("FX_RATE_TIMEOUT", 10))

    def get_rate(self, from_currency: str, to_currency: str) -> Decimal:
        """Return the exchange rate from *from_currency* to *to_currency*.

        Fetches live rates from the configured third-party API, falling
        back to static rates if the API is unreachable.
        """
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        rates = self._get_rates("EUR")  # normalise through EUR base
        from_rate = rates.get(from_currency)
        to_rate = rates.get(to_currency)

        if from_rate is None or to_rate is None:
            raise ValueError(
                f"Unsupported currency pair: {from_currency}/{to_currency}"
            )

        return (to_rate / from_rate).quantize(Decimal("0.0001"))

    def _get_rates(self, base: str = "EUR") -> dict[str, Decimal]:
        """Return cached rates or fetch fresh ones from the API."""
        now = time.time()
        if base in self._cache and (now - self._cache_ts.get(base, 0)) < self._cache_ttl():
            return self._cache[base]

        try:
            rates = self._fetch_rates(base)
            self._cache[base] = rates
            self._cache_ts[base] = now
            logger.info("FX rates refreshed from third-party API (base=%s)", base)
            return rates
        except Exception:
            logger.warning(
                "Third-party FX API unavailable — using %s",
                "stale cache" if base in self._cache else "fallback rates",
                exc_info=True,
            )
            if base in self._cache:
                return self._cache[base]
            return dict(FALLBACK_RATES)

    def _fetch_rates(self, base: str) -> dict[str, Decimal]:
        """Call the currency-api and return a dict of {CURRENCY: rate}."""
        base_lower = base.lower()
        url = f"{self._api_base_url()}/{base_lower}.json"

        resp = requests.get(url, timeout=self._timeout())
        resp.raise_for_status()
        data = resp.json()

        raw_rates = data.get(base_lower, {})
        if not raw_rates:
            raise ValueError("No rates returned by FX API")

        rates: dict[str, Decimal] = {}
        for currency, value in raw_rates.items():
            rates[currency.upper()] = Decimal(str(value))

        # Ensure the base currency itself is included
        rates.setdefault(base.upper(), Decimal("1"))

        return rates

    def clear_cache(self):
        """Invalidate the rate cache (useful in testing)."""
        self._cache.clear()
        self._cache_ts.clear()


fx_rate_provider = FXRateProvider()
