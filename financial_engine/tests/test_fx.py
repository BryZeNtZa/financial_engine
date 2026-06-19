from decimal import Decimal

from financial_engine.tests import deposit_funds
from financial_engine.models.ledger_entry import LedgerEntry


class TestFX:
    """Tests for foreign exchange operations."""

    def test_fx_rate_endpoint(self, client):
        """FX rate endpoint returns valid rate."""
        resp = client.get("/api/v1/fx/rate?from=USD&to=EUR")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["from_currency"] == "USD"
        assert data["to_currency"] == "EUR"
        assert Decimal(data["rate"]) > 0

    def test_fx_convert_endpoint(self, client):
        """FX convert endpoint computes correct amount."""
        resp = client.get("/api/v1/fx/convert?from=USD&to=EUR&amount=100")
        assert resp.status_code == 200
        data = resp.get_json()
        assert Decimal(data["converted_amount"]) > 0
        assert data["from_currency"] == "USD"
        assert data["to_currency"] == "EUR"

    def test_fx_transfer(self, app, client, alice_account, bob_eur_account):
        """Cross-currency transfer creates balanced ledger entries per currency."""
        deposit_funds(client, alice_account, 100)

        resp = client.post("/api/v1/fx/transfer", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_eur_account,
            "amount": "100",
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "SUCCESS"
        assert data["from_currency"] == "USD"
        assert data["to_currency"] == "EUR"
        assert Decimal(data["to_amount"]) > 0

        # Verify sender balance
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert Decimal(resp.get_json()["balance"]) == Decimal("0.0000")

        # Verify receiver got EUR
        resp = client.get(f"/api/v1/accounts/{bob_eur_account}/balance")
        assert Decimal(resp.get_json()["balance"]) > 0

        # Verify all entries for the txn sum to zero per currency
        with app.app_context():
            entries = LedgerEntry.query.filter_by(
                transaction_id=data["transaction_id"]
            ).all()

            # Group by currency and verify each sums to zero
            by_currency = {}
            for e in entries:
                by_currency.setdefault(e.currency, Decimal("0"))
                by_currency[e.currency] += e.amount

            for curr, total in by_currency.items():
                assert total == Decimal("0"), (
                    f"Entries for {curr} don't balance: {total}"
                )

    def test_fx_transfer_insufficient_funds(self, client, alice_account, bob_eur_account):
        """FX transfer with insufficient funds should fail."""
        resp = client.post("/api/v1/fx/transfer", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_eur_account,
            "amount": "100",
        })
        assert resp.status_code == 422

    def test_fx_unsupported_currency(self, client):
        """Unsupported currency pair returns error."""
        resp = client.get("/api/v1/fx/rate?from=USD&to=XYZ")
        assert resp.status_code == 400
