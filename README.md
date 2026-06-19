# Financial Engine — FinTech Ledger Infrastructure

A double-entry accounting ledger system built with Flask, implementing a digital wallet platform with multi-currency support, deposits, transfers, FX operations, and full auditability.

## Architecture Decisions

### Double-Entry Ledger (No Mutable Balance)

The system **never stores a mutable balance column**. All balances are derived from the ledger:

```
Balance = SUM(amount) FROM ledger_entries WHERE account_id = ? AND status = 'SUCCESS'
```

Every financial operation creates at least two ledger entries that sum to zero, guaranteeing accounting integrity.

### Layered Architecture

```
API Layer (Flask-RESTX)        ← Swagger-documented REST endpoints
  │
Middleware                     ← Idempotency, distributed tracing
  │
Service Layer                  ← Business logic, orchestration
  │
Domain Layer                   ← Value objects, events, exceptions
  │
Data Layer (SQLAlchemy)        ← Models, database access
```

### Domain-Driven Design

- **Value Objects**: `Money` — immutable, uses `Decimal` (never float), enforces currency matching
- **Entities**: `Account`, `Transaction`, `LedgerEntry`, `BalanceSnapshot`
- **Aggregates**: Account (with entries + snapshots), Transaction (with entries)
- **Domain Events**: `FundsReserved`, `TransferCompleted`, `TransferFailed`, `DepositCompleted` — published via in-process event bus and consumed by `NotificationService`

### Two-Phase Transfers (Reservation System)

Transfers support a two-phase commit:

1. **Phase 1 — Reserve**: Creates a `PENDING` debit entry on the sender, reducing available balance
2. **Phase 2 — Commit**: Settles the debit to `SUCCESS` and creates the credit entry

This enables risk checks between reservation and settlement.

### Transaction State Machine

```
PENDING  →  SUCCESS   (commit)
PENDING  →  FAILED    (reject / timeout)
SUCCESS  →  REVERSED  (compensating transaction)
```

Transactions are **immutable once completed**. Corrections use compensating entries.

## Performance Optimization Strategy

### Snapshot Pattern

Balance computation is optimized via periodic snapshots:

```
BalanceSnapshot { account_id, balance, entry_count, snapshot_at }

Balance = snapshot.balance + SUM(entries created after snapshot)
```

A snapshot is created automatically every 100 entries per account. This reduces query scope from millions of rows to only the delta since the last snapshot.

### Database Indexes

Composite indexes on:
- `(account_id, status)` — for balance queries
- `(account_id, created_at)` — for snapshot delta queries
- `(account_id, created_at)` on snapshots — for latest snapshot lookup

## API Endpoints

| Method | Endpoint                              | Description                        |
|--------|---------------------------------------|------------------------------------|
| POST   | `/api/v1/accounts`                    | Create an account                  |
| GET    | `/api/v1/accounts/{id}`               | Get account details                |
| GET    | `/api/v1/accounts/{id}/balance`       | Get balance (derived from ledger)  |
| GET    | `/api/v1/accounts/{id}/transactions`  | Transaction history (paginated)    |
| POST   | `/api/v1/transfers`                   | Execute atomic transfer            |
| POST   | `/api/v1/transfers/initiate`          | Phase 1: Reserve funds             |
| POST   | `/api/v1/transfers/{id}/commit`       | Phase 2: Commit transfer           |
| POST   | `/api/v1/transfers/{id}/fail`         | Fail pending transfer              |
| POST   | `/api/v1/deposits`                    | Initiate deposit                   |
| POST   | `/api/v1/payments/webhook`            | Payment provider webhook           |
| GET    | `/api/v1/fx/rate`                     | Get exchange rate                  |
| GET    | `/api/v1/fx/convert`                  | Convert amount                     |
| POST   | `/api/v1/fx/transfer`                 | Cross-currency transfer            |

**Swagger UI** available at: `/api/v1/docs`

## Concurrency Safety

- **Optimistic locking**: `version` column on `Account` — prevents stale reads during concurrent updates
- **Available balance check**: Includes `PENDING` debits in balance computation to prevent over-spending during two-phase transfers
- **Atomic transactions**: All ledger entries for a transfer are written in a single database transaction

## Idempotency

All `POST` endpoints support `Idempotency-Key` header:

```
POST /api/v1/transfers
Idempotency-Key: unique-request-id-123
```

- Same key + same body → returns cached response
- Same key + different body → returns `409 Conflict`
- No key → normal processing

## Distributed Tracing

Every request gets a `correlation_id` (from `X-Correlation-ID` header or auto-generated). This ID propagates through:
- API request/response headers
- Domain events
- Ledger entries (via transaction)
- Notifications

## Cross-Currency Transfers

FX transfers route through an FX Pool account:

```
Alice (USD)  -100 USD  →  FX Pool (USD)  +100 USD
FX Pool (EUR) -92 EUR  →  Bob (EUR)       +92 EUR

Per-currency totals:
  USD: -100 + 100 = 0 ✓
  EUR:  -92 +  92 = 0 ✓
```

Exchange rates are provided by a stub service (easily replaceable with a real API).

## Notifications

Domain events trigger notifications via:
- `EmailProvider` (stub)
- `SMSProvider` (stub)

Events handled: `TransferCompleted`, `TransferFailed`, `DepositCompleted`.

## Tradeoffs

| Decision | Tradeoff |
|----------|----------|
| SQLite default | Simple setup but no true `SELECT FOR UPDATE`; swap to PostgreSQL for production |
| In-process event bus | Simple, synchronous; replace with message broker (RabbitMQ/Kafka) for scale |
| Snapshot every 100 entries | Balances writes vs read performance; tunable threshold |
| Stub payment providers | Always return success; integration tests need real provider sandboxes |
| Decimal(19,4) precision | Covers most currencies; some crypto may need higher precision |

## Setup & Running

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
flask --app financial_engine run --debug

# Run tests
pytest -v

# Run tests with coverage
pytest --cov=financial_engine -v
```

## Project Structure

```
financial_engine/
├── __init__.py                 # App factory
├── config.py                   # Configuration
├── extensions.py               # Flask extensions (db, migrate)
├── api/                        # REST API layer (Flask-RESTX)
│   ├── accounts.py
│   ├── transfers.py
│   ├── deposits.py
│   ├── webhooks.py
│   └── fx.py
├── domain/                     # Domain layer (DDD)
│   ├── value_objects.py        # Money value object
│   ├── events.py               # Domain events + event bus
│   └── exceptions.py           # Domain exceptions
├── middleware/                  # Cross-cutting concerns
│   ├── idempotency.py          # Idempotency key support
│   └── tracing.py              # Correlation ID propagation
├── models/                     # SQLAlchemy models
│   ├── account.py
│   ├── transaction.py
│   ├── ledger_entry.py
│   ├── balance_snapshot.py
│   ├── idempotency.py
│   └── notification.py
├── services/                   # Business logic
│   ├── balance_service.py      # Ledger-based balance computation
│   ├── transfer_service.py     # Transfer orchestration
│   ├── deposit_service.py      # Deposit flow
│   ├── fx_service.py           # Foreign exchange
│   ├── notification_service.py # Email/SMS notifications
│   └── payment_provider.py     # Payment provider stubs
└── tests/                      # Test suite
    ├── test_balance.py
    ├── test_transfers.py
    ├── test_deposits.py
    ├── test_idempotency.py
    ├── test_concurrency.py
    ├── test_fx.py
    ├── test_accounts.py
    └── test_domain.py
```
