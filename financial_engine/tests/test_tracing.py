"""Tests for Bonus 5 — distributed tracing.

A correlation_id from the API request must flow through the transaction, every
ledger entry, the domain events, and the notifications, so the full lifecycle
can be reconstructed by querying on that single id.
"""

from decimal import Decimal

from financial_engine.tests import deposit_funds
from financial_engine.extensions import db
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.models.notification import Notification
from financial_engine.domain.events import (
    event_bus,
    TRANSFER_COMPLETED,
    DEPOSIT_COMPLETED,
)
from financial_engine.services.notification_service import NotificationService


class TestDistributedTracing:
    def test_request_correlation_id_flows_through_transfer(
        self, app, client, alice_account, bob_account
    ):
        corr = "trace-transfer-001"

        # Capture notification correlation (bus is cleared between tests).
        notifier = NotificationService()
        event_bus.subscribe(TRANSFER_COMPLETED, notifier.handle_transfer_completed)

        deposit_funds(client, alice_account, 100)

        resp = client.post(
            "/api/v1/transfers",
            json={
                "sender_account_number": alice_account,
                "receiver_account_number": bob_account,
                "amount": "50",
            },
            headers={"X-Correlation-ID": corr},
        )
        assert resp.status_code == 201

        # 1. API request/response carries the id.
        assert resp.headers.get("X-Correlation-ID") == corr
        body = resp.get_json()
        assert body["correlation_id"] == corr
        txn_id = body["transaction_id"]

        with app.app_context():
            # 2. Transaction.
            txn = db.session.get(Transaction, txn_id)
            assert txn.correlation_id == corr

            # 3. Every ledger entry of the transaction.
            entries = LedgerEntry.query.filter_by(transaction_id=txn_id).all()
            assert entries
            assert all(e.correlation_id == corr for e in entries)

            # 4. Notifications.
            notifs = Notification.query.filter_by(correlation_id=corr).all()
            assert len(notifs) >= 1

            # Auditability: all of this transfer's entries are reachable by id.
            assert LedgerEntry.query.filter_by(correlation_id=corr).count() == len(entries)

    def test_generated_correlation_id_when_header_absent(self, client, alice_account, bob_account):
        deposit_funds(client, alice_account, 100)
        resp = client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "10",
        })
        assert resp.status_code == 201
        # Middleware always assigns one and echoes it back.
        corr = resp.headers.get("X-Correlation-ID")
        assert corr
        assert resp.get_json()["correlation_id"] == corr

    def test_correlation_id_flows_through_deposit(self, app, client, alice_account):
        corr = "trace-deposit-001"
        notifier = NotificationService()
        event_bus.subscribe(DEPOSIT_COMPLETED, notifier.handle_deposit_completed)

        resp = client.post(
            "/api/v1/deposits",
            json={"number": alice_account, "amount": "100", "provider": "stripe"},
            headers={"X-Correlation-ID": corr},
        )
        assert resp.get_json()["correlation_id"] == corr
        txn_id = resp.get_json()["transaction_id"]

        # The webhook is a separate request; the deposit's lifecycle keeps the
        # correlation_id assigned at initiation.
        client.post("/api/v1/payments/webhook", json={
            "transaction_id": txn_id, "amount": "100", "provider": "stripe",
        })

        with app.app_context():
            entries = LedgerEntry.query.filter_by(transaction_id=txn_id).all()
            assert entries
            assert all(e.correlation_id == corr for e in entries)
            assert Notification.query.filter_by(correlation_id=corr).count() >= 1

    def test_correlation_id_flows_through_fx_transfer(self, app, client, alice_account, bob_eur_account):
        corr = "trace-fx-001"
        deposit_funds(client, alice_account, 100)

        resp = client.post(
            "/api/v1/fx/transfer",
            json={
                "sender_account_number": alice_account,
                "receiver_account_number": bob_eur_account,
                "amount": "100",
            },
            headers={"X-Correlation-ID": corr},
        )
        assert resp.status_code == 201
        txn_id = resp.get_json()["transaction_id"]

        with app.app_context():
            entries = LedgerEntry.query.filter_by(transaction_id=txn_id).all()
            assert len(entries) == 4  # four-legged FX entry set
            assert all(e.correlation_id == corr for e in entries)
