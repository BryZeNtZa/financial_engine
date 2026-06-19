from decimal import Decimal

from financial_engine.tests import deposit_funds


class TestIdempotency:
    """Tests for idempotency key support."""

    def test_idempotent_transfer_does_not_duplicate_entries(self, client, alice_account, bob_account):
        """Same idempotency key must return same response without duplicating."""
        deposit_funds(client, alice_account, 200)

        headers = {"Idempotency-Key": "unique-key-123"}
        payload = {
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "50",
        }

        # First request
        resp1 = client.post("/api/v1/transfers", json=payload, headers=headers)
        assert resp1.status_code == 201

        # Second request with same key
        resp2 = client.post("/api/v1/transfers", json=payload, headers=headers)
        # Should return the stored response (201)
        assert resp2.status_code == 201

        # Balance should only reflect one transfer
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert Decimal(resp.get_json()["balance"]) == Decimal("150.0000")

    def test_idempotency_key_reuse_different_body(self, client, alice_account, bob_account):
        """Reusing a key with different payload returns 409."""
        deposit_funds(client, alice_account, 200)

        headers = {"Idempotency-Key": "reused-key-456"}

        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "50",
        }, headers=headers)
        assert resp.status_code == 201

        # Different body with same key
        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "75",
        }, headers=headers)
        assert resp.status_code == 409

    def test_no_idempotency_key_processes_normally(self, client, alice_account, bob_account):
        """Requests without idempotency key are processed each time."""
        deposit_funds(client, alice_account, 200)

        payload = {
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "30",
        }

        resp1 = client.post("/api/v1/transfers", json=payload)
        assert resp1.status_code == 201

        resp2 = client.post("/api/v1/transfers", json=payload)
        assert resp2.status_code == 201

        # Both should have gone through
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert Decimal(resp.get_json()["balance"]) == Decimal("140.0000")

    def test_idempotent_deposit(self, client, alice_account):
        """Deposits also support idempotency."""
        headers = {"Idempotency-Key": "deposit-key-789"}
        payload = {
            "number": alice_account,
            "amount": "100",
            "provider": "stripe",
        }

        resp1 = client.post("/api/v1/deposits", json=payload, headers=headers)
        assert resp1.status_code == 201

        resp2 = client.post("/api/v1/deposits", json=payload, headers=headers)
        assert resp2.status_code == 201
        assert resp1.get_json()["transaction_id"] == resp2.get_json()["transaction_id"]
