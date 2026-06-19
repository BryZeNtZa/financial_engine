"""Tests for wiring the provider clients into the deposit flow.

A FakeClient is injected by monkeypatching PaymentGateway.get_client, so the
real-provider path (initiate -> webhook verify -> status re-query -> confirm)
is exercised without any network or credentials.
"""

from decimal import Decimal

import pytest

from financial_engine.services.payment_gateway import PaymentGateway
from financial_engine.providers.base import (
    PaymentProviderClientInterface,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
)


class FakeClient(PaymentProviderClientInterface):
    name = "fake"

    def __init__(self, status=PaymentStatus.SUCCESSFUL, verify=True):
        self._status = status
        self._verify = verify
        self.initiated = []

    def initiate_payment(self, request):
        self.initiated.append(request)
        return PaymentResult(
            provider=self.name,
            provider_reference="PROV-REF-1",
            status=PaymentStatus.PENDING,
            payment_url="https://pay.example/PROV-REF-1",
            raw={"notif_token": "NT-1"},
        )

    def get_payment_status(self, provider_reference, context=None):
        return self._status

    def verify_webhook(self, payload, *, headers=None, raw_body=None, context=None):
        return self._verify

    def parse_webhook(self, payload):
        return WebhookEvent(
            provider=self.name,
            provider_reference=payload.get("referenceId", ""),
            transaction_reference=payload.get("order_id"),
            status=PaymentStatus.SUCCESSFUL,
            amount=Decimal(str(payload.get("amount", "0"))),
            currency=payload.get("currency"),
            raw=payload,
        )


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(PaymentGateway, "get_client", staticmethod(lambda provider: client))
    return client


def _balance(client, number):
    return Decimal(client.get(f"/api/v1/accounts/{number}/balance").get_json()["balance"])


class TestProviderWiring:
    def test_initiate_calls_provider_and_returns_payment_url(self, client, alice_account, fake_client):
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account,
            "amount": "100",
            "provider": "mtn",
            "payer": "237670000000",
        })
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["status"] == "PENDING"
        assert body["payment_url"] == "https://pay.example/PROV-REF-1"

        # The provider client actually received the collection request.
        assert len(fake_client.initiated) == 1
        assert fake_client.initiated[0].customer_reference == "237670000000"

    def test_webhook_confirms_after_status_requery(self, client, alice_account, fake_client):
        client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100",
            "provider": "mtn", "payer": "237670000000",
        })

        # Provider-style webhook: references the provider's id, not ours.
        resp = client.post("/api/v1/payments/webhook", json={
            "provider": "mtn",
            "referenceId": "PROV-REF-1",
            "amount": "100",
            "status": "SUCCESSFUL",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "SUCCESS"
        assert _balance(client, alice_account) == Decimal("100.0000")

    def test_webhook_rejected_when_verification_fails(self, client, alice_account, monkeypatch):
        bad = FakeClient(verify=False)
        monkeypatch.setattr(PaymentGateway, "get_client", staticmethod(lambda provider: bad))

        client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100",
            "provider": "mtn", "payer": "237670000000",
        })
        resp = client.post("/api/v1/payments/webhook", json={
            "provider": "mtn", "referenceId": "PROV-REF-1", "amount": "100",
        })
        assert resp.status_code == 401
        assert _balance(client, alice_account) == Decimal("0.0000")

    def test_webhook_does_not_credit_while_pending(self, client, alice_account, monkeypatch):
        pending = FakeClient(status=PaymentStatus.PENDING)
        monkeypatch.setattr(PaymentGateway, "get_client", staticmethod(lambda provider: pending))

        client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100",
            "provider": "mtn", "payer": "237670000000",
        })
        resp = client.post("/api/v1/payments/webhook", json={
            "provider": "mtn", "referenceId": "PROV-REF-1", "amount": "100",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "PENDING"
        assert _balance(client, alice_account) == Decimal("0.0000")

    def test_webhook_marks_failed_on_provider_failure(self, client, alice_account, monkeypatch):
        failed = FakeClient(status=PaymentStatus.FAILED)
        monkeypatch.setattr(PaymentGateway, "get_client", staticmethod(lambda provider: failed))

        client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100",
            "provider": "mtn", "payer": "237670000000",
        })
        resp = client.post("/api/v1/payments/webhook", json={
            "provider": "mtn", "referenceId": "PROV-REF-1", "amount": "100",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "FAILED"
        assert _balance(client, alice_account) == Decimal("0.0000")

    def test_webhook_unknown_provider_reference_404(self, client, alice_account, fake_client):
        resp = client.post("/api/v1/payments/webhook", json={
            "provider": "mtn", "referenceId": "DOES-NOT-EXIST", "amount": "100",
        })
        assert resp.status_code == 404


class TestPayerValidation:
    """Mobile-money providers require the payer's MSISDN; card providers don't."""

    @pytest.mark.parametrize("provider", ["mtn", "momo", "orange", "om"])
    def test_mobile_money_requires_payer(self, client, alice_account, provider):
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100", "provider": provider,
        })
        assert resp.status_code == 400
        assert "payer" in resp.get_json()["error"].lower()
        # No transaction should have been created/credited.
        assert _balance(client, alice_account) == Decimal("0.0000")

    def test_mobile_money_with_payer_succeeds(self, client, alice_account, fake_client):
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100",
            "provider": "mtn", "payer": "237670000000",
        })
        assert resp.status_code == 201

    @pytest.mark.parametrize("provider", ["stripe", "paypal"])
    def test_card_providers_do_not_require_payer(self, client, alice_account, provider):
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100", "provider": provider,
        })
        assert resp.status_code == 201


class TestDepositAmountValidation:
    """Simulation path: a webhook cannot inflate the initiated amount."""

    def test_amount_mismatch_rejected(self, client, alice_account):
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account, "amount": "100", "provider": "stripe",
        })
        txn_id = resp.get_json()["transaction_id"]

        resp = client.post("/api/v1/payments/webhook", json={
            "transaction_id": txn_id, "amount": "999999", "provider": "stripe",
        })
        assert resp.status_code == 422
        assert _balance(client, alice_account) == Decimal("0.0000")
