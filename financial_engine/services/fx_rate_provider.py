"""Third-party FX rate integration.

A small, pluggable rate-source abstraction sits behind a caching facade:

- ``CurrencyApiSource``      — the free, no-key currency-api (default).
- ``ExchangeRateApiSource`` — ExchangeRate-API v6 (keyed), used when
                              ``FX_PROVIDER=exchangerate`` and ``FX_API_KEY`` set.
- ``FXRateProvider``        — caches rates in-memory (TTL) and falls back to
                              static rates when the third-party API is down.

``build_rate_source(config)`` selects the source from config; the module-level
``fx_rate_provider`` singleton is what the FX service consumes.
"""

import abc
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


class FxRateSource(abc.ABC):
    """A third-party source of FX rates relative to a base currency."""

    name: str = ""

    def __init__(self, session=None, timeout: int = 10):
        # Default to the requests module; injectable for testing.
        self._session = session if session is not None else requests
        self._timeout = timeout

    @abc.abstractmethod
    def fetch_rates(self, base: str) -> dict[str, Decimal]:
        """Return ``{CURRENCY: rate}`` for all currencies relative to ``base``."""

    @staticmethod
    def _to_rate_map(raw: dict, base: str) -> dict[str, Decimal]:
        if not raw:
            raise ValueError("No rates returned by FX API")
        rates = {currency.upper(): Decimal(str(value)) for currency, value in raw.items()}
        rates.setdefault(base.upper(), Decimal("1"))
        return rates


class CurrencyApiSource(FxRateSource):
    """Free, no-key currency-api (https://github.com/fawazahmed0/exchange-api)."""

    name = "currency_api"
    DEFAULT_BASE_URL = "https://latest.currency-api.pages.dev/v1/currencies"

    def __init__(self, base_url: str | None = None, session=None, timeout: int = 10):
        super().__init__(session, timeout)
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def fetch_rates(self, base: str) -> dict[str, Decimal]:
        base_lower = base.lower()
        url = f"{self._base_url}/{base_lower}.json"
        resp = self._session.get(url, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        return self._to_rate_map(data.get(base_lower, {}), base)


class ExchangeRateApiSource(FxRateSource):
    """ExchangeRate-API v6 keyed endpoint.

    GET {base_url}/{api_key}/latest/{BASE}
      -> { "result": "success", "conversion_rates": { ... } }
    """

    name = "exchangerate"
    DEFAULT_BASE_URL = "https://v6.exchangerate-api.com/v6"

    def __init__(self, api_key: str, base_url: str | None = None, session=None, timeout: int = 10):
        super().__init__(session, timeout)
        self._api_key = api_key
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def fetch_rates(self, base: str) -> dict[str, Decimal]:
        url = f"{self._base_url}/{self._api_key}/latest/{base.upper()}"
        resp = self._session.get(url, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("result") != "success":
            raise ValueError(f"exchangerate-api error: {data.get('error-type', 'unknown')}")
        return self._to_rate_map(data.get("conversion_rates", {}), base)


def build_rate_source(config) -> FxRateSource:
    """Select an FX rate source from config, defaulting to the free currency-api."""
    config = config or {}
    provider = str(config.get("FX_PROVIDER", "currency_api")).lower()
    timeout = int(config.get("FX_RATE_TIMEOUT", 10))

    if provider in ("exchangerate", "exchangerate_api", "exchangerate-api"):
        api_key = config.get("FX_API_KEY")
        if api_key:
            return ExchangeRateApiSource(
                api_key, base_url=config.get("FX_API_URL"), timeout=timeout
            )
        logger.warning(
            "FX_PROVIDER=exchangerate but FX_API_KEY is missing; "
            "falling back to currency-api"
        )

    return CurrencyApiSource(base_url=config.get("FX_RATE_API_URL"), timeout=timeout)


class FXRateProvider:
    """Caching facade over a pluggable third-party FX rate source."""

    def __init__(self):
        self._cache = {}          # {base_currency: {currency: Decimal rate, ...}}
        self._cache_ts = {}       # {base_currency: timestamp}

    @staticmethod
    def _cache_ttl() -> int:
        """Cache time-to-live in seconds (default 5 minutes)."""
        return int(current_app.config.get("FX_RATE_CACHE_TTL", 300))

    def _source(self) -> FxRateSource:
        return build_rate_source(current_app.config)

    def get_rate(self, from_currency: str, to_currency: str) -> Decimal:
        """Return the exchange rate from *from_currency* to *to_currency*."""
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        rates = self._get_rates("EUR")  # normalise through a single EUR base
        from_rate = rates.get(from_currency)
        to_rate = rates.get(to_currency)

        if from_rate is None or to_rate is None:
            raise ValueError(
                f"Unsupported currency pair: {from_currency}/{to_currency}"
            )

        return (to_rate / from_rate).quantize(Decimal("0.0001"))

    def _get_rates(self, base: str = "EUR") -> dict[str, Decimal]:
        """Return cached rates or fetch fresh ones from the selected source."""
        now = time.time()
        if base in self._cache and (now - self._cache_ts.get(base, 0)) < self._cache_ttl():
            return self._cache[base]

        try:
            source = self._source()
            rates = source.fetch_rates(base)
            self._cache[base] = rates
            self._cache_ts[base] = now
            logger.info("FX rates refreshed (source=%s base=%s)", source.name, base)
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

    def clear_cache(self):
        """Invalidate the rate cache (useful in testing)."""
        self._cache.clear()
        self._cache_ts.clear()


fx_rate_provider = FXRateProvider()
