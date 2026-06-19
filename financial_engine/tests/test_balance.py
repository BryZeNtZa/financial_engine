from decimal import Decimal

from financial_engine.tests import deposit_funds


class TestBalance:
    """Tests for ledger-based balance computation."""

    def test_balance_computed_correctly_from_ledger(self, client, alice_account):
        """Balance must be the sum of all SUCCESS ledger entries."""
        deposit_funds(client, alice_account, 100)
        deposit_funds(client, alice_account, 50)

        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert resp.status_code == 200
        data = resp.get_json()
        assert Decimal(data["balance"]) == Decimal("150.0000")
        assert data["currency"] == "USD"

    def test_balance_zero_for_new_account(self, client, alice_account):
        """New account should have zero balance."""
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert resp.status_code == 200
        data = resp.get_json()
        assert Decimal(data["balance"]) == Decimal("0")

    def test_balance_after_transfer(self, client, alice_account, bob_account):
        """Balance reflects deposits and transfers."""
        deposit_funds(client, alice_account, 100)

        client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "30",
        })

        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        data = resp.get_json()
        assert Decimal(data["balance"]) == Decimal("70.0000")

        resp = client.get(f"/api/v1/accounts/{bob_account}/balance")
        data = resp.get_json()
        assert Decimal(data["balance"]) == Decimal("30.0000")

    def test_balance_not_found(self, client):
        """Non-existent account returns 404."""
        resp = client.get("/api/v1/accounts/nonexistent/balance")
        assert resp.status_code == 404

    def test_available_balance_excludes_pending(self, client, alice_account, bob_account):
        """Available balance should account for pending debits."""
        deposit_funds(client, alice_account, 100)

        # Initiate a two-phase transfer (reserves funds)
        resp = client.post("/api/v1/transfers/initiate", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "60",
        })
        assert resp.status_code == 201

        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        data = resp.get_json()
        # Balance is still 100 (pending not settled)
        assert Decimal(data["balance"]) == Decimal("100.0000")
        # Available balance should be 40 (100 - 60 pending)
        assert Decimal(data["available_balance"]) == Decimal("40.0000")
