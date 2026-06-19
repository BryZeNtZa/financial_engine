from decimal import Decimal

from financial_engine.tests import deposit_funds
from financial_engine.extensions import db
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.services.transaction_service import TransactionService


def _balance(client, number):
    resp = client.get(f"/api/v1/accounts/{number}/balance")
    return Decimal(resp.get_json()["balance"])


class TestReversal:
    """Requirement 2B — REVERSED state via compensating transactions."""

    def _make_transfer(self, client, alice_account, bob_account, amount="30"):
        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": amount,
        })
        assert resp.status_code == 201
        return resp.get_json()["transaction_id"]

    def test_reversal_restores_balances(self, client, alice_account, bob_account):
        deposit_funds(client, alice_account, 100)
        txn_id = self._make_transfer(client, alice_account, bob_account, "30")

        assert _balance(client, alice_account) == Decimal("70.0000")
        assert _balance(client, bob_account) == Decimal("30.0000")

        resp = client.post(f"/api/v1/transactions/{txn_id}/reverse")
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["reversed_transaction_id"] == txn_id
        assert body["type"] == "REVERSAL"

        # Balances are restored to their pre-transfer state.
        assert _balance(client, alice_account) == Decimal("100.0000")
        assert _balance(client, bob_account) == Decimal("0.0000")

    def test_original_marked_reversed_and_entries_immutable(
        self, app, client, alice_account, bob_account
    ):
        deposit_funds(client, alice_account, 100)
        txn_id = self._make_transfer(client, alice_account, bob_account, "30")

        with app.app_context():
            before = {
                e.id: (e.amount, e.entry_type, e.status)
                for e in LedgerEntry.query.filter_by(transaction_id=txn_id).all()
            }

        client.post(f"/api/v1/transactions/{txn_id}/reverse")

        with app.app_context():
            original = db.session.get(Transaction, txn_id)
            assert original.status == "REVERSED"

            # Original entries are untouched (immutability).
            after = {
                e.id: (e.amount, e.entry_type, e.status)
                for e in LedgerEntry.query.filter_by(transaction_id=txn_id).all()
            }
            assert before == after

    def test_compensating_entries_sum_to_zero(self, app, client, alice_account, bob_account):
        deposit_funds(client, alice_account, 100)
        txn_id = self._make_transfer(client, alice_account, bob_account, "30")
        resp = client.post(f"/api/v1/transactions/{txn_id}/reverse")
        reversal_id = resp.get_json()["reversal_transaction_id"]

        with app.app_context():
            entries = LedgerEntry.query.filter_by(transaction_id=reversal_id).all()
            assert len(entries) == 2
            assert sum(e.amount for e in entries) == Decimal("0")
            # The reversal links back to the original.
            reversal = db.session.get(Transaction, reversal_id)
            assert reversal.reverses_transaction_id == txn_id
            assert reversal.type == "REVERSAL"

    def test_reversal_of_deposit_restores_balance(self, client, alice_account):
        txn_id = deposit_funds(client, alice_account, 100)
        assert _balance(client, alice_account) == Decimal("100.0000")

        resp = client.post(f"/api/v1/transactions/{txn_id}/reverse")
        assert resp.status_code == 201
        assert _balance(client, alice_account) == Decimal("0.0000")

    def test_cannot_reverse_pending_transaction(self, client, alice_account, bob_account):
        deposit_funds(client, alice_account, 100)
        # Two-phase initiate leaves the transaction PENDING.
        resp = client.post("/api/v1/transfers/initiate", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "30",
        })
        txn_id = resp.get_json()["transaction_id"]

        resp = client.post(f"/api/v1/transactions/{txn_id}/reverse")
        assert resp.status_code == 409

    def test_cannot_double_reverse(self, client, alice_account, bob_account):
        deposit_funds(client, alice_account, 100)
        txn_id = self._make_transfer(client, alice_account, bob_account, "30")

        assert client.post(f"/api/v1/transactions/{txn_id}/reverse").status_code == 201
        # Second attempt: original is now REVERSED, not SUCCESS.
        assert client.post(f"/api/v1/transactions/{txn_id}/reverse").status_code == 409

    def test_reverse_unknown_transaction_returns_404(self, client):
        resp = client.post("/api/v1/transactions/does-not-exist/reverse")
        assert resp.status_code == 404
