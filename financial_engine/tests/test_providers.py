"""Tests for payment provider clients.

These never touch the network: a FakeSession routes requests by
method + URL substring and returns canned responses, letting us assert the
request shapes and status mapping each client produces.
"""

from decimal import Decimal

import pytest

from financial_engine.providers import (
    MomoClient,
    OmClient,
    PaymentProviderClientInterface,
    PaymentRequest,
    PaymentStatus,
    get_provider_client,
)


class FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = ""

    def json(self):
        return self._json


class FakeSession:
    """Routes (method, url-substring) -> FakeResponse and records calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _match(self, method, url):
        for m, sub, resp in self.routes:
            if m == method and sub in url:
                return resp
        raise AssertionError(f"no fake route for {method} {url}")

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._match("POST", url)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._match("GET", url)

    def last(self, method, sub):
        for m, url, kwargs in reversed(self.calls):
            if m == method and sub in url:
                return url, kwargs
        raise AssertionError(f"no recorded {method} call matching {sub}")


# --------------------------------------------------------------------- MoMo
class TestMomoClient:
    def _client(self, status="SUCCESSFUL"):
        session = FakeSession([
            ("POST", "/collection/token/", FakeResponse(200, {"access_token": "tok", "expires_in": 3600})),
            ("POST", "/collection/v1_0/requesttopay", FakeResponse(202)),
            ("GET", "/collection/v1_0/requesttopay/", FakeResponse(200, {
                "status": status, "amount": "100", "currency": "XAF", "externalId": "tx1",
            })),
        ])
        client = MomoClient(
            subscription_key="sub", api_user="user", api_key="key",
            target_environment="mtncameroon", session=session,
        )
        return client, session

    def test_implements_interface(self):
        client, _ = self._client()
        assert isinstance(client, PaymentProviderClientInterface)
        assert client.name == "mtn_momo"

    def test_initiate_builds_request_to_pay(self):
        client, session = self._client()
        result = client.initiate_payment(PaymentRequest(
            amount=Decimal("100"), currency="XAF",
            customer_reference="237670000000", transaction_reference="tx1",
            description="Wallet top-up",
        ))

        assert result.provider == "mtn_momo"
        assert result.status == PaymentStatus.PENDING
        assert result.payment_url is None
        assert result.provider_reference  # the generated X-Reference-Id

        _, kwargs = session.last("POST", "requesttopay")
        body = kwargs["json"]
        assert body["amount"] == "100"
        assert body["currency"] == "XAF"
        assert body["externalId"] == "tx1"
        assert body["payer"] == {"partyIdType": "MSISDN", "partyId": "237670000000"}
        # X-Reference-Id header equals the returned provider_reference
        assert kwargs["headers"]["X-Reference-Id"] == result.provider_reference

    def test_status_mapping(self):
        client, _ = self._client(status="SUCCESSFUL")
        assert client.get_payment_status("ref-1") == PaymentStatus.SUCCESSFUL

        client, _ = self._client(status="FAILED")
        assert client.get_payment_status("ref-1") == PaymentStatus.FAILED

    def test_parse_webhook(self):
        client, _ = self._client()
        event = client.parse_webhook({
            "referenceId": "ref-9", "externalId": "tx1",
            "status": "SUCCESSFUL", "amount": "100", "currency": "XAF",
        })
        assert event.provider == "mtn_momo"
        assert event.provider_reference == "ref-9"
        assert event.transaction_reference == "tx1"
        assert event.status == PaymentStatus.SUCCESSFUL
        assert event.amount == Decimal("100")

    def test_webhook_hmac_verification(self):
        import hashlib
        import hmac

        secret = "shh"
        session = FakeSession([])
        client = MomoClient(
            subscription_key="s", api_user="u", api_key="k",
            webhook_secret=secret, session=session,
        )
        raw = b'{"status":"SUCCESSFUL"}'
        good = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        assert client.verify_webhook({}, headers={"X-Callback-Signature": good}, raw_body=raw) is True
        assert client.verify_webhook({}, headers={"X-Callback-Signature": "bad"}, raw_body=raw) is False


# ----------------------------------------------------------------- Orange Money
class TestOmClient:
    def _client(self, status="SUCCESS"):
        session = FakeSession([
            ("POST", "/oauth/v3/token", FakeResponse(200, {"access_token": "tok", "expires_in": 7776000})),
            ("POST", "/webpayment", FakeResponse(201, {
                "status": 201, "message": "OK",
                "pay_token": "PT-123", "payment_url": "https://webpayment.orange-money.com/pay/PT-123",
                "notif_token": "NT-456",
            })),
            ("POST", "/transactionstatus", FakeResponse(200, {"status": status})),
        ])
        client = OmClient(
            authorization_header="Basic abc", merchant_key="MK",
            return_url="https://app/return", cancel_url="https://app/cancel",
            notif_url="https://app/notif", session=session,
        )
        return client, session

    def test_implements_interface(self):
        client, _ = self._client()
        assert isinstance(client, PaymentProviderClientInterface)
        assert client.name == "orange_money"

    def test_initiate_returns_payment_url_and_token(self):
        client, session = self._client()
        result = client.initiate_payment(PaymentRequest(
            amount=Decimal("100"), currency="XOF",
            customer_reference="0700000000", transaction_reference="order-1",
        ))

        assert result.provider == "orange_money"
        assert result.status == PaymentStatus.PENDING
        assert result.provider_reference == "PT-123"
        assert result.payment_url == "https://webpayment.orange-money.com/pay/PT-123"
        assert result.raw["notif_token"] == "NT-456"

        _, kwargs = session.last("POST", "webpayment")
        body = kwargs["json"]
        assert body["merchant_key"] == "MK"
        assert body["order_id"] == "order-1"
        assert body["amount"] == 100  # integral -> int
        assert body["notif_url"] == "https://app/notif"

    def test_status_mapping(self):
        client, _ = self._client(status="SUCCESS")
        status = client.get_payment_status("PT-123", context={"order_id": "order-1", "amount": 100})
        assert status == PaymentStatus.SUCCESSFUL

        client, _ = self._client(status="EXPIRED")
        status = client.get_payment_status("PT-123", context={"order_id": "order-1", "amount": 100})
        assert status == PaymentStatus.EXPIRED

    def test_status_query_sends_paytoken_and_order(self):
        client, session = self._client()
        client.get_payment_status("PT-123", context={"order_id": "order-1", "amount": 100})
        _, kwargs = session.last("POST", "transactionstatus")
        body = kwargs["json"]
        assert body["pay_token"] == "PT-123"
        assert body["order_id"] == "order-1"
        assert body["amount"] == 100

    def test_webhook_notif_token_verification(self):
        client, _ = self._client()
        payload = {"status": "SUCCESS", "notif_token": "NT-456", "order_id": "order-1"}
        assert client.verify_webhook(payload, context={"notif_token": "NT-456"}) is True
        assert client.verify_webhook(payload, context={"notif_token": "WRONG"}) is False

    def test_parse_webhook(self):
        client, _ = self._client()
        event = client.parse_webhook({
            "status": "SUCCESS", "pay_token": "PT-123",
            "order_id": "order-1", "amount": "100", "currency": "XOF",
        })
        assert event.status == PaymentStatus.SUCCESSFUL
        assert event.provider_reference == "PT-123"
        assert event.transaction_reference == "order-1"


# --------------------------------------------------------------------- registry
class TestRegistry:
    def test_get_provider_client(self):
        config = {
            "MOMO_SUBSCRIPTION_KEY": "s", "MOMO_API_USER": "u", "MOMO_API_KEY": "k",
            "OM_AUTHORIZATION_HEADER": "Basic x", "OM_MERCHANT_KEY": "mk",
        }
        assert isinstance(get_provider_client("mtn_momo", config), MomoClient)
        assert isinstance(get_provider_client("orange_money", config), OmClient)

    def test_unknown_provider_raises(self):
        from financial_engine.providers import PaymentProviderError

        with pytest.raises(PaymentProviderError):
            get_provider_client("bitcoin", {})
