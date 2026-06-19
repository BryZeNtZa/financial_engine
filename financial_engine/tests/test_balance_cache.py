from decimal import Decimal

from financial_engine.tests import deposit_funds
from financial_engine.services.balance_cache import balance_cache
from financial_engine.services.balance_service import BalanceService
from financial_engine.models.account import Account


class TestBalanceCache:
    """Tests for the balance cache layer (cache-aside flow)."""

    def test_balance_is_cached_after_first_read(self, app, client, alice_account):
        """A miss computes from the ledger, then the value is cached."""
        deposit_funds(client, alice_account, 100)

        with app.app_context():
            account = Account.query.filter_by(number=alice_account).first()

            # Cache empty initially (deposit invalidated it).
            assert balance_cache.get(account.id) is None

            balance = BalanceService.get_balance(account.id)
            assert balance.amount == Decimal("100.0000")

            # Now it is populated.
            cached = balance_cache.get(account.id)
            assert cached is not None
            assert Decimal(cached["amount"]) == Decimal("100.0000")
            assert cached["currency"] == "USD"

    def test_cache_hit_does_not_touch_ledger(self, app, client, alice_account, monkeypatch):
        """On a cache hit, the ledger computation path is not invoked."""
        deposit_funds(client, alice_account, 100)

        with app.app_context():
            account = Account.query.filter_by(number=alice_account).first()

            # Warm the cache.
            BalanceService.get_balance(account.id)

            # Make the uncached path explode — a hit must not call it.
            def _boom(_account_id):
                raise AssertionError("ledger computed on a cache hit")

            monkeypatch.setattr(BalanceService, "_compute_balance", staticmethod(_boom))

            balance = BalanceService.get_balance(account.id)
            assert balance.amount == Decimal("100.0000")

    def test_write_invalidates_cache(self, app, client, alice_account, bob_account):
        """A transfer evicts the cached balances of both accounts."""
        deposit_funds(client, alice_account, 100)

        with app.app_context():
            alice = Account.query.filter_by(number=alice_account).first()
            bob = Account.query.filter_by(number=bob_account).first()
            # Warm both caches.
            BalanceService.get_balance(alice.id)
            BalanceService.get_balance(bob.id)
            assert balance_cache.get(alice.id) is not None

        # Transfer 30 from Alice to Bob.
        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "30",
        })
        assert resp.status_code == 201

        with app.app_context():
            alice = Account.query.filter_by(number=alice_account).first()
            bob = Account.query.filter_by(number=bob_account).first()
            # Caches were evicted by the write.
            assert balance_cache.get(alice.id) is None
            assert balance_cache.get(bob.id) is None

            # Recomputed balances reflect the transfer.
            assert BalanceService.get_balance(alice.id).amount == Decimal("70.0000")
            assert BalanceService.get_balance(bob.id).amount == Decimal("30.0000")

    def test_cached_balance_matches_endpoint_after_multiple_ops(
        self, app, client, alice_account, bob_account
    ):
        """End-to-end: the cached read stays consistent with the ledger."""
        deposit_funds(client, alice_account, 200)

        for amount in ("10", "20", "30"):
            client.post("/api/v1/transfers", json={
                "sender_account_number": alice_account,
                "receiver_account_number": bob_account,
                "amount": amount,
            })

        # Endpoint (served via cache) must equal the ledger truth.
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert Decimal(resp.get_json()["balance"]) == Decimal("140.0000")

        with app.app_context():
            alice = Account.query.filter_by(number=alice_account).first()
            assert BalanceService._compute_balance(alice.id).amount == Decimal("140.0000")
