from financial_engine.tests import deposit_funds


class TestTransactionHistory:
    """Tests for transaction history retrieval."""

    def test_transaction_history(self, client, alice_account, bob_account):
        """Transaction history returns all transactions for an account."""
        deposit_funds(client, alice_account, 100)

        client.post("/api/v1/transfers", json={
            "sender_account_number": alice_account,
            "receiver_account_number": bob_account,
            "amount": "30",
        })

        resp = client.get(f"/api/v1/accounts/{alice_account}/transactions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 2  # deposit + transfer
        assert len(data["transactions"]) >= 2

    def test_transaction_history_pagination(self, client, alice_account):
        """Transaction history supports pagination."""
        for _ in range(5):
            deposit_funds(client, alice_account, 10)

        resp = client.get(
            f"/api/v1/accounts/{alice_account}/transactions?page=1&per_page=2"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["transactions"]) == 2
        assert data["per_page"] == 2
        assert data["total"] == 5

    def test_transaction_history_empty(self, client, alice_account):
        """New account has empty transaction history."""
        resp = client.get(f"/api/v1/accounts/{alice_account}/transactions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0

    def test_transaction_history_not_found(self, client):
        """Non-existent account returns 404."""
        resp = client.get("/api/v1/accounts/nonexistent/transactions")
        assert resp.status_code == 404


class TestAccounts:
    """Tests for account operations."""

    def test_create_account(self, client):
        """Creating an account returns 201."""
        resp = client.post("/api/v1/accounts", json={
            "user_id": "test_user",
            "currency": "USD",
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["user_id"] == "test_user"
        assert data["currency"] == "USD"

    def test_create_multiple_accounts_same_currency(self, client, alice_account):
        """Creating multiple accounts for same user+currency is allowed."""
        resp = client.post("/api/v1/accounts", json={
            "user_id": "alice",
            "currency": "USD",
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["number"] != alice_account

    def test_get_account(self, client, alice_account):
        """Retrieve account by account number."""
        resp = client.get(f"/api/v1/accounts/{alice_account}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["number"] == alice_account


class TestCorrelationId:
    """Tests for distributed tracing."""

    def test_correlation_id_propagated(self, client, alice_account, bob_account):
        """Correlation ID from request header is echoed in response."""
        deposit_funds(client, alice_account, 100)

        resp = client.post(
            "/api/v1/transfers",
            json={
                "sender_account_number": alice_account,
                "receiver_account_number": bob_account,
                "amount": "10",
            },
            headers={"X-Correlation-ID": "my-trace-123"},
        )
        assert resp.status_code == 201
        assert resp.headers.get("X-Correlation-ID") == "my-trace-123"

    def test_correlation_id_generated_if_missing(self, client, alice_account):
        """If no correlation ID is sent, one is generated."""
        resp = client.get(f"/api/v1/accounts/{alice_account}/balance")
        assert resp.headers.get("X-Correlation-ID") is not None
