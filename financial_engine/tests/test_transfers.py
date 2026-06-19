from decimal import Decimal

from financial_engine.tests import deposit_funds
from financial_engine.models.ledger_entry import LedgerEntry


class TestTransfers:
    """Tests for fund transfer operations."""

    def test_transfer_creates_balanced_ledger_entries(self, app, client, alice_account, bob_account):
        """Every transfer must produce entries that sum to zero."""
        deposit_funds(client, alice_account, 100)

        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "50",
        })
        assert resp.status_code == 201
        txn_id = resp.get_json()["transaction_id"]

        with app.app_context():
            entries = LedgerEntry.query.filter_by(transaction_id=txn_id).all()
            total = sum(e.amount for e in entries)
            assert total == Decimal("0"), f"Ledger entries do not balance: {total}"

    def test_transfer_fails_when_balance_insufficient(self, client, alice_account, bob_account):
        """Transfer must fail if sender lacks funds."""
        deposit_funds(client, alice_account, 30)

        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "50",
        })
        assert resp.status_code == 422
        assert "Insufficient funds" in resp.get_json()["error"]

    def test_transfer_fails_with_zero_amount(self, client, alice_account, bob_account):
        """Transfer of zero or negative amount should be rejected."""
        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "0",
        })
        assert resp.status_code == 400

    def test_transfer_fails_with_nonexistent_sender(self, client, bob_account):
        """Transfer from non-existent account returns 404."""
        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": "nonexistent",
            "receiver_account_number": bob_account,
            "amount": "10",
        })
        assert resp.status_code == 404

    def test_transfer_fails_currency_mismatch(self, client, alice_account, alice_eur_account):
        """Transfer between different currencies should be rejected."""
        deposit_funds(client, alice_account, 100)

        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": alice_eur_account,
            "amount": "50",
        })
        assert resp.status_code == 422
        assert "Currency mismatch" in resp.get_json()["error"]

    def test_two_phase_transfer(self, app, client, alice_account, bob_account):
        """Test Phase 1 (reserve) + Phase 2 (commit) transfer."""
        deposit_funds(client, alice_account, 100)

        # Phase 1: Initiate
        resp = client.post("/api/v1/transfers/initiate", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "50",
        })
        assert resp.status_code == 201
        txn_id = resp.get_json()["transaction_id"]
        assert resp.get_json()["status"] == "PENDING"

        # Phase 2: Commit
        resp = client.post(f"/api/v1/transfers/{txn_id}/commit")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "SUCCESS"

        # Verify balances
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert Decimal(resp.get_json()["balance"]) == Decimal("50.0000")

        resp = client.get(f"/api/v1/accounts/{bob_account}/balance")
        assert Decimal(resp.get_json()["balance"]) == Decimal("50.0000")

        # Verify ledger balance
        with app.app_context():
            entries = LedgerEntry.query.filter_by(
                transaction_id=txn_id, status="SUCCESS"
            ).all()
            total = sum(e.amount for e in entries)
            assert total == Decimal("0")

    def test_fail_pending_transfer(self, client, alice_account, bob_account):
        """Failing a pending transfer releases reserved funds."""
        deposit_funds(client, alice_account, 100)

        resp = client.post("/api/v1/transfers/initiate", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "50",
        })
        txn_id = resp.get_json()["transaction_id"]

        # Fail the transfer
        resp = client.post(f"/api/v1/transfers/{txn_id}/fail")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "FAILED"

        # Available balance should be restored
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        data = resp.get_json()
        assert Decimal(data["balance"]) == Decimal("100.0000")
        assert Decimal(data["available_balance"]) == Decimal("100.0000")

    def test_cannot_commit_already_completed_transfer(self, client, alice_account, bob_account):
        """Committing a non-PENDING transfer is rejected."""
        deposit_funds(client, alice_account, 100)

        resp = client.post("/api/v1/transfers/initiate", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "50",
        })
        txn_id = resp.get_json()["transaction_id"]

        # Commit once
        client.post(f"/api/v1/transfers/{txn_id}/commit")

        # Try to commit again
        resp = client.post(f"/api/v1/transfers/{txn_id}/commit")
        assert resp.status_code == 409

    def test_transfer_status_in_response(self, client, alice_account, bob_account):
        """Transfer response includes correct status."""
        deposit_funds(client, alice_account, 100)

        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "25",
        })
        data = resp.get_json()
        assert data["status"] == "SUCCESS"
        assert data["type"] == "TRANSFER"
        assert "transaction_id" in data
        assert "correlation_id" in data
