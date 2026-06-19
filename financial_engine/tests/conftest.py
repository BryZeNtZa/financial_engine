import pytest
from decimal import Decimal

from financial_engine import create_app
from financial_engine.extensions import db as _db
from financial_engine.models.account import Account
from financial_engine.domain.events import event_bus
from financial_engine.services.fx_rate_provider import fx_rate_provider

# Deterministic rates used across all tests (base: EUR)
TEST_FX_RATES = {
    "EUR": Decimal("1"),
    "USD": Decimal("1.14487288"),
    "GBP": Decimal("0.863714"),
    "XAF": Decimal("655.95700002"),
    "XOF": Decimal("655.95700002"),
    "NGN": Decimal("1587.41808004"),
    "KES": Decimal("148.20698867"),
    "GHS": Decimal("12.45250479"),
    "ZAR": Decimal("19.29030952"),
}


@pytest.fixture(scope="session")
def app():
    """Create application for testing."""
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-secret",
        "SERVER_NAME": "localhost",
    })
    yield app


@pytest.fixture(autouse=True)
def db(app):
    """Create fresh database tables for each test."""
    with app.app_context():
        _db.create_all()

        # Seed the FX rate provider cache so tests never call the real API
        import time
        fx_rate_provider._cache["EUR"] = dict(TEST_FX_RATES)
        fx_rate_provider._cache_ts["EUR"] = time.time() + 999_999

        yield _db
        _db.session.rollback()
        _db.drop_all()

    # Clear event bus and rate cache between tests
    event_bus.clear()
    fx_rate_provider.clear_cache()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def alice_account(app, db):
    """Create Alice's USD account."""
    with app.app_context():
        account = Account(user_id="alice", currency="USD")
        db.session.add(account)
        db.session.commit()
        return account.number


@pytest.fixture
def bob_account(app, db):
    """Create Bob's USD account."""
    with app.app_context():
        account = Account(user_id="bob", currency="USD")
        db.session.add(account)
        db.session.commit()
        return account.number


@pytest.fixture
def alice_eur_account(app, db):
    """Create Alice's EUR account."""
    with app.app_context():
        account = Account(user_id="alice_eur", currency="EUR")
        db.session.add(account)
        db.session.commit()
        return account.number


@pytest.fixture
def bob_eur_account(app, db):
    """Create Bob's EUR account."""
    with app.app_context():
        account = Account(user_id="bob_eur", currency="EUR")
        db.session.add(account)
        db.session.commit()
        return account.number
