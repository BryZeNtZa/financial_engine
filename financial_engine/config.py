import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///financial_engine.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    IDEMPOTENCY_KEY_EXPIRY_HOURS = 24

    # Balance cache layer. When REDIS_URL is set and reachable, balances are
    # cached in Redis; otherwise the cache falls back to an in-process store.
    REDIS_URL = os.environ.get("REDIS_URL")  # e.g. redis://localhost:6379/0
    BALANCE_CACHE_ENABLED = os.environ.get("BALANCE_CACHE_ENABLED", "true").lower() == "true"
    BALANCE_CACHE_TTL = int(os.environ.get("BALANCE_CACHE_TTL", 300))

    # Third-party FX rate API (currency-api)
    FX_RATE_API_URL = os.environ.get(
        "FX_RATE_API_URL", "https://latest.currency-api.pages.dev/v1/currencies"
    )
    FX_RATE_CACHE_TTL = int(os.environ.get("FX_RATE_CACHE_TTL", 300))
    FX_RATE_TIMEOUT = int(os.environ.get("FX_RATE_TIMEOUT", 10))


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SECRET_KEY = "test-secret"
