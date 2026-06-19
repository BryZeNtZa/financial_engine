"""Tests for the domain aggregates — the invariants they centralize."""

from decimal import Decimal

import pytest

from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.domain.value_objects import Money
from financial_engine.domain.aggregates import TransactionAggregate, AccountAggregate
from financial_engine.domain.exceptions import (
    LedgerImbalanceError,
    InvalidTransactionStateError,
    InsufficientFundsError,
    CurrencyMismatchError,
)


class TestTransactionAggregate:
    def test_balanced_double_entry_passes(self, app, db):
        with app.app_context():
            acc_a = Account(user_id="a", currency="USD")
            acc_b = Account(user_id="b", currency="USD")
            db.session.add_all([acc_a, acc_b])
            db.session.flush()

            txn = TransactionAggregate.open(
                type="TRANSFER", correlation_id="c1", status="SUCCESS"
            )
            txn.record_double_entry(
                debit_account_id=acc_a.id,
                credit_account_id=acc_b.id,
                money=Money(Decimal("50"), "USD"),
            )
            txn.assert_balanced()  # does not raise

            # correlation_id is propagated onto every entry.
            assert all(e.correlation_id == "c1" for e in txn.entries)

    def test_unbalanced_entries_raise(self, app, db):
        with app.app_context():
            acc = Account(user_id="a", currency="USD")
            db.session.add(acc)
            db.session.flush()

            txn = TransactionAggregate.open(
                type="TRANSFER", correlation_id="c2", status="SUCCESS"
            )
            # Only a debit — nothing balancing it.
            txn.debit(acc.id, Money(Decimal("50"), "USD"))
            with pytest.raises(LedgerImbalanceError):
                txn.assert_balanced()

    def test_fx_balances_per_currency(self, app, db):
        with app.app_context():
            txn = TransactionAggregate.open(
                type="FX_TRANSFER", correlation_id="c3", status="SUCCESS"
            )
            txn.debit("acc-usd", Money(Decimal("100"), "USD"))
            txn.credit("pool-usd", Money(Decimal("100"), "USD"))
            txn.debit("pool-eur", Money(Decimal("92"), "EUR"))
            txn.credit("acc-eur", Money(Decimal("92"), "EUR"))
            txn.assert_balanced()  # zero per currency

    def test_illegal_state_transition_raises(self, app, db):
        with app.app_context():
            txn = TransactionAggregate.open(
                type="TRANSFER", correlation_id="c4", status="SUCCESS"
            )
            # SUCCESS -> FAILED is not allowed.
            with pytest.raises(InvalidTransactionStateError):
                txn.mark_failed()

    def test_legal_transition_chain(self, app, db):
        with app.app_context():
            txn = TransactionAggregate.open(
                type="TRANSFER", correlation_id="c5", status="PENDING"
            )
            txn.mark_success()      # PENDING -> SUCCESS
            assert txn.status == "SUCCESS"
            txn.mark_reversed()     # SUCCESS -> REVERSED
            assert txn.status == "REVERSED"
            with pytest.raises(InvalidTransactionStateError):
                txn.mark_success()  # REVERSED is terminal


class TestAccountAggregate:
    def test_touch_bumps_version(self, app, db):
        with app.app_context():
            acc = Account(user_id="a", currency="USD")
            db.session.add(acc)
            db.session.flush()
            agg = AccountAggregate(acc)
            assert agg.version == 0
            agg.touch()
            assert agg.version == 1

    def test_assert_sufficient(self, app, db):
        with app.app_context():
            acc = Account(user_id="a", currency="USD")
            db.session.add(acc)
            db.session.flush()
            agg = AccountAggregate(acc)
            agg.assert_sufficient(Money(Decimal("100"), "USD"), Money(Decimal("60"), "USD"))
            with pytest.raises(InsufficientFundsError):
                agg.assert_sufficient(Money(Decimal("50"), "USD"), Money(Decimal("60"), "USD"))

    def test_assert_same_currency(self, app, db):
        with app.app_context():
            acc = Account(user_id="a", currency="USD")
            db.session.add(acc)
            db.session.flush()
            agg = AccountAggregate(acc)
            agg.assert_same_currency_as("USD")
            with pytest.raises(CurrencyMismatchError):
                agg.assert_same_currency_as("EUR")
