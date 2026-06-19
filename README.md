# Financial Engine — FinTech Ledger Infrastructure

A double-entry accounting ledger system built with Flask, implementing a digital wallet platform with multi-currency support, deposits, transfers, FX operations, and full auditability.

## Architecture Decisions

### Double-Entry Ledger (No Mutable Balance)

The system **never stores a mutable balance column**. All balances are derived from the ledger:

```sql
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
- **Aggregates** (`domain/aggregates.py`) — own the invariants so they can't be bypassed:
  - **`TransactionAggregate`** is the consistency boundary for a transaction and its entries. It owns entry creation (propagating `correlation_id`), the **double-entry invariant** (`assert_balanced` — SUCCESS entries sum to zero *per currency*), and the **state machine** (`PENDING → SUCCESS/FAILED`, `SUCCESS → REVERSED`). Services call `record_double_entry` / `debit` / `credit` / `mark_success` instead of hand-assembling `LedgerEntry` rows.
  - **`AccountAggregate`** is a thin root owning the optimistic-lock `version` (`touch()`), the funds-availability check (`assert_sufficient`), and currency matching (`assert_same_currency_as`). It deliberately does **not** hold the account's ledger entries — balances are derived/projected, so loading millions of rows into the aggregate would defeat the performance design.
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

Transactions are **immutable once completed**. Corrections are made through **compensating transactions**, never by editing settled records.

### Reversal (Compensating Transactions)

`POST /transactions/{id}/reverse` reverses a settled (`SUCCESS`) transaction:

- A new `REVERSAL` transaction is created whose ledger entries are the exact **inverse** of the original's (sign flipped, `DEBIT`↔`CREDIT`), restoring affected balances. These compensating entries sum to zero, so the double-entry invariant holds.
- The original transaction and its entries are **never mutated** — the only change is the terminal `SUCCESS → REVERSED` status flag. `reverses_transaction_id` links the compensating transaction back to the original for audit.
- Guards: only `SUCCESS` transactions can be reversed (a `PENDING` transfer is cancelled via `/fail`; an already-`REVERSED`/`FAILED` one returns `409`), which also prevents double-reversal.
- Reversals are administrative corrections and intentionally skip the funds-availability check, so a clawback always succeeds (and may push a balance negative, representing a debt).

## Performance Optimization Strategy

Two complementary layers keep balance reads fast at scale (1M users / 10M entries / 1000 qps, target <10ms): a **cache layer** for hot reads and a **snapshot pattern** that bounds the cost of a cold read.

### Cache Layer

Balance reads follow a cache-aside flow:

```
API Request
  ↓
Cache Lookup        ← balance:{account_id} in Redis
  ↓ (miss)
Ledger Calculation  ← snapshot + delta (see below)
  ↓
Cache Update        ← store with TTL
```

- Backed by **Redis** when `REDIS_URL` is configured; otherwise falls back to an in-process store, so the app and test suite run with no external dependency.
- The **ledger stays the source of truth**: any write that changes an account's settled balance (transfer, deposit, FX) invalidates its cache key, so the next read recomputes.
- `available_balance` is derived as `cached_settled_balance + SUM(pending debits)`, where the pending set is tiny — so even the available balance avoids the large ledger scan.

Configured via `REDIS_URL`, `BALANCE_CACHE_ENABLED`, `BALANCE_CACHE_TTL`.

### Snapshot Pattern

On a cache miss, balance computation is optimized via periodic snapshots:

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
| POST   | `/api/v1/transactions/{id}/reverse`   | Reverse a settled txn (compensating) |
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

Every request gets a `correlation_id` (from the `X-Correlation-ID` header or auto-generated by the tracing middleware). This single id propagates through the whole lifecycle:
- **API request/response** — read from / echoed in the `X-Correlation-ID` header
- **Transaction** — `Transaction.correlation_id`
- **Ledger entries** — stamped on every `LedgerEntry.correlation_id` (indexed) at creation
- **Domain events** — `DomainEvent.correlation_id`
- **Notifications** — `Notification.correlation_id`

Because the id is denormalized onto the ledger entries themselves, a full audit trail for any operation is a single indexed query (`LedgerEntry.query.filter_by(correlation_id=...)`), independent of joins.

## Cross-Currency Transfers

FX transfers route through an FX Pool account:

```
Alice (USD)  -100 USD  →  FX Pool (USD)  +100 USD
FX Pool (EUR) -92 EUR  →  Bob (EUR)       +92 EUR

Per-currency totals:
  USD: -100 + 100 = 0 ✓
  EUR:  -92 +  92 = 0 ✓
```

### FX Rate Integration

Rates come from a real third-party API behind a pluggable, cached source layer (`fx_rate_provider.py`):

- **`CurrencyApiSource`** — the free, no-key currency-api (default).
- **`ExchangeRateApiSource`** — ExchangeRate-API v6 keyed endpoint (`GET /v6/{key}/latest/{base}` → `conversion_rates`), used when `FX_PROVIDER=exchangerate` and `FX_API_KEY` are set.

`build_rate_source(config)` selects the source. The `FXRateProvider` facade caches rates in-memory (TTL `FX_RATE_CACHE_TTL`, default 5 min) and **falls back to static rates** when the API is unreachable, so the engine keeps working offline. All pairs are normalized through a single EUR base.

Endpoints: `GET /fx/rate`, `GET /fx/convert?from=USD&to=EUR&amount=100` (→ `converted_amount`), `POST /fx/transfer`.

## Payment Provider Integration (Deposits)

Deposits route through a pluggable provider layer under `financial_engine/providers/`. Every client implements `PaymentProviderClientInterface` (`initiate_payment`, `get_payment_status`, `verify_webhook`, `parse_webhook`) and maps its provider-specific status codes onto a normalized `PaymentStatus`.

Implemented clients:
- **`MomoClient`** — MTN Mobile Money Collections (Request to Pay): OAuth token, push prompt to the payer phone, status polling, HMAC webhook verification.
- **`OmClient`** — Orange Money Web Payment: `client_credentials` token, `webpayment` returning a redirect `payment_url` + `notif_token`, transaction-status lookup, `notif_token` webhook verification.

`PaymentGateway.get_client(provider)` resolves a configured client by name (`mtn`→MoMo, `orange`→Orange), or returns `None` for **simulation mode** when credentials are absent (local dev/tests, and providers without a client such as stripe/paypal).

Mobile-money providers (`mtn`, `orange`) **require** a `payer` MSISDN on `POST /deposits` (`400` otherwise); card/redirect providers (`stripe`, `paypal`) do not.

Deposit flow with a real provider:
1. `POST /deposits` (with `payer` MSISDN) → `initiate_payment` → the provider reference and `payment_url` are persisted on the transaction; the initiated amount is recorded.
2. The provider calls `POST /payments/webhook`. The body is **never trusted to credit on its own**: the handler verifies the webhook, then **re-queries `get_payment_status`**, and only confirms the deposit when the provider reports `SUCCESSFUL`.

Hardening applied to confirmation:
- The transaction row is **locked and its status re-checked under the lock**, so concurrent/duplicate webhooks can never double-credit.
- The confirmed amount is **validated against the initiated amount** (`422` on mismatch), so a webhook cannot inflate a deposit.

## Notifications

Domain events drive notifications through a `NotificationService`:

```
NotificationService
  ├ EmailProvider   (SMTP when configured, else a logging fallback)
  └ SmsProvider     (Twilio when configured, else a logging fallback)
```

- **SMS via Twilio**: `build_sms_provider(config)` returns a `TwilioSmsProvider` (using the `twilio` package) when `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` are set, otherwise a `LogSmsProvider` — so dev/tests run with no credentials or network.
- **Email via SMTP**: `build_email_provider(config)` returns an `SmtpEmailProvider` (stdlib `smtplib`, works with Gmail/SES/SendGrid/Mailgun SMTP) when `SMTP_HOST` is set, otherwise a `LogEmailProvider`.
- Each notification is persisted as a `Notification` row (`SENT` / `FAILED`) with its `correlation_id`.
- Deposit notifications route to the **payer's phone** when the deposit carried one; otherwise `NOTIFICATION_DEFAULT_RECIPIENT` is used.

Events handled: `TransferCompleted`, `TransferFailed`, `DepositCompleted`, `TransactionReversed`.

## Tradeoffs

| Decision | Tradeoff |
|----------|----------|
| SQLite default | Simple setup but no true `SELECT FOR UPDATE`; swap to PostgreSQL for production |
| Balance cache (Redis, write-through invalidation) | Fast reads; cache miss falls back to ledger. In-process fallback when no `REDIS_URL`, so a single-process dev/test run isn't shared across workers |
| In-process event bus | Simple, synchronous; replace with message broker (RabbitMQ/Kafka) for scale |
| Snapshot every 100 entries | Balances writes vs read performance; tunable threshold |
| Provider clients with simulation fallback | Real MoMo/Orange clients are used when credentials are configured; otherwise deposits run in simulation mode (webhook carries the txn id) so dev/tests need no credentials or network |
| Decimal(19,4) precision | Covers most currencies; some crypto may need higher precision |

## Setup & Running

```bash
# Create a virtual environment
python3 -m venv .venv

# activate the project Venv
source .venv/bin/activate # Linix
.venv\Scripts\activate.bat # Windows
```

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
│   ├── transactions.py         # Reversal (compensating transactions)
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
│   ├── balance_cache.py        # Redis/in-memory balance cache layer
│   ├── transfer_service.py     # Transfer orchestration
│   ├── transaction_service.py  # Reversal via compensating transactions
│   ├── deposit_service.py      # Deposit flow (provider-wired)
│   ├── payment_gateway.py      # Provider resolution + simulation fallback
│   ├── fx_service.py           # Foreign exchange
│   ├── notification_service.py # Email/SMS notifications
│   └── payment_provider.py     # Simulation stub (used when no provider configured)
├── providers/                  # Third-party payment provider clients
│   ├── base.py                 # PaymentProviderClientInterface + DTOs
│   ├── momo/                   # MTN Mobile Money (Request to Pay)
│   └── om/                     # Orange Money (Web Payment)
└── tests/                      # Test suite
    ├── test_balance.py
    ├── test_balance_cache.py
    ├── test_transfers.py
    ├── test_reversal.py
    ├── test_deposits.py
    ├── test_idempotency.py
    ├── test_concurrency.py
    ├── test_fx.py
    ├── test_accounts.py
    └── test_domain.py
```
