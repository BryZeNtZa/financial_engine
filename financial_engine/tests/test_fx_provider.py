"""Tests for the pluggable third-party FX rate sources.

A FakeSession returns canned API payloads so the currency-api and
exchangerate-api sources are exercised without any network.
"""

from decimal import Decimal

import pytest

from financial_engine.services.fx_rate_provider import (
    CurrencyApiSource,
    ExchangeRateApiSource,
    FXRateProvider,
    FALLBACK_RATES,
    build_rate_source,
)


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class TestSourceSelection:
    def test_default_is_currency_api(self):
        assert isinstance(build_rate_source({}), CurrencyApiSource)

    def test_exchangerate_with_key(self):
        source = build_rate_source({"FX_PROVIDER": "exchangerate", "FX_API_KEY": "k"})
        assert isinstance(source, ExchangeRateApiSource)

    def test_exchangerate_without_key_falls_back_to_currency_api(self):
        source = build_rate_source({"FX_PROVIDER": "exchangerate"})
        assert isinstance(source, CurrencyApiSource)


class TestCurrencyApiSource:
    def test_fetch_parses_rates(self):
        session = FakeSession(FakeResponse({"eur": {"usd": 1.08, "gbp": 0.85}}))
        source = CurrencyApiSource(session=session)

        rates = source.fetch_rates("EUR")
        assert rates["USD"] == Decimal("1.08")
        assert rates["GBP"] == Decimal("0.85")
        assert rates["EUR"] == Decimal("1")  # base always present
        # currency-api URL shape: .../eur.json
        assert session.calls[0][0].endswith("/eur.json")


class TestExchangeRateApiSource:
    def test_fetch_parses_conversion_rates(self):
        session = FakeSession(FakeResponse({
            "result": "success",
            "base_code": "EUR",
            "conversion_rates": {"EUR": 1, "USD": 1.08, "XAF": 655.957},
        }))
        source = ExchangeRateApiSource("APIKEY", session=session)

        rates = source.fetch_rates("EUR")
        assert rates["USD"] == Decimal("1.08")
        assert rates["XAF"] == Decimal("655.957")
        # keyed v6 URL shape: .../v6/APIKEY/latest/EUR
        assert session.calls[0][0].endswith("/v6/APIKEY/latest/EUR")

    def test_error_result_raises(self):
        session = FakeSession(FakeResponse({"result": "error", "error-type": "invalid-key"}))
        source = ExchangeRateApiSource("BAD", session=session)
        with pytest.raises(ValueError):
            source.fetch_rates("EUR")


class TestFacadeFallback:
    def test_falls_back_to_static_rates_when_source_fails(self, app, monkeypatch):
        provider = FXRateProvider()

        class _BoomSource:
            name = "boom"

            def fetch_rates(self, base):
                raise RuntimeError("API down")

        monkeypatch.setattr(provider, "_source", lambda: _BoomSource())

        with app.app_context():
            # No cache, source fails -> fallback rates are used.
            rate = provider.get_rate("USD", "EUR")
            expected = (
                FALLBACK_RATES["EUR"] / FALLBACK_RATES["USD"]
            ).quantize(Decimal("0.0001"))
            assert rate == expected
