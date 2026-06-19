import pytest

from financial_engine import create_app
from financial_engine.extensions import db as _db
from financial_engine.models.account import Account
from financial_engine.domain.events import event_bus


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
        yield _db
        _db.session.rollback()
        _db.drop_all()

    # Clear event bus between tests
    event_bus.clear()


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


def deposit_funds(client, number, amount):
    """Helper: deposit funds into an account via API."""
    # Initiate deposit
    resp = client.post("/api/v1/deposits", json={
        "number": number,
        "amount": str(amount),
        "provider": "stripe",
    })
    assert resp.status_code == 201
    txn_id = resp.get_json()["transaction_id"]

    # Confirm via webhook
    resp = client.post("/api/v1/payments/webhook", json={
        "transaction_id": txn_id,
        "amount": str(amount),
        "provider": "stripe",
    })
    assert resp.status_code == 200
    return txn_id
