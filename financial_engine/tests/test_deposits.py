from decimal import Decimal

from financial_engine.models.ledger_entry import LedgerEntry


class TestDeposits:
    """Tests for deposit operations."""

    def test_deposit_flow(self, app, client, alice_account):
        """Full deposit flow: initiate → webhook confirm → ledger balanced."""
        # Initiate
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account,
            "amount": "200",
            "provider": "stripe",
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "PENDING"
        txn_id = data["transaction_id"]

        # Confirm via webhook
        resp = client.post("/api/v1/payments/webhook", json={
            "transaction_id": txn_id,
            "amount": "200",
            "provider": "stripe",
        })
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "SUCCESS"

        # Check balance
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert Decimal(resp.get_json()["balance"]) == Decimal("200.0000")

        # Verify ledger entries balance
        with app.app_context():
            entries = LedgerEntry.query.filter_by(transaction_id=txn_id).all()
            total = sum(e.amount for e in entries)
            assert total == Decimal("0"), f"Deposit entries not balanced: {total}"

    def test_deposit_nonexistent_account(self, client):
        """Deposit to non-existent account should fail."""
        resp = client.post("/api/v1/deposits", json={
            "number": "nonexistent",
            "amount": "100",
        })
        assert resp.status_code == 404

    def test_deposit_invalid_amount(self, client, alice_account):
        """Deposit with invalid amount should fail."""
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account,
            "amount": "abc",
        })
        assert resp.status_code == 400

    def test_deposit_zero_amount(self, client, alice_account):
        """Deposit of zero should fail."""
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account,
            "amount": "0",
        })
        assert resp.status_code == 400

    def test_webhook_duplicate_confirmation(self, client, alice_account):
        """Confirming an already-confirmed deposit should fail."""
        resp = client.post("/api/v1/deposits", json={
            "number": alice_account,
            "amount": "100",
        })
        txn_id = resp.get_json()["transaction_id"]

        # First confirmation
        client.post("/api/v1/payments/webhook", json={
            "transaction_id": txn_id,
            "amount": "100",
            "provider": "stripe",
        })

        # Second confirmation should fail
        resp = client.post("/api/v1/payments/webhook", json={
            "transaction_id": txn_id,
            "amount": "100",
            "provider": "stripe",
        })
        assert resp.status_code == 409
