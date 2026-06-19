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

    # MTN Mobile Money (Collections / Request to Pay)
    MOMO_SUBSCRIPTION_KEY = os.environ.get("MOMO_SUBSCRIPTION_KEY")
    MOMO_API_USER = os.environ.get("MOMO_API_USER")
    MOMO_API_KEY = os.environ.get("MOMO_API_KEY")
    MOMO_TARGET_ENVIRONMENT = os.environ.get("MOMO_TARGET_ENVIRONMENT", "sandbox")
    MOMO_BASE_URL = os.environ.get(
        "MOMO_BASE_URL", "https://sandbox.momodeveloper.mtn.com"
    )
    MOMO_CALLBACK_URL = os.environ.get("MOMO_CALLBACK_URL")
    MOMO_WEBHOOK_SECRET = os.environ.get("MOMO_WEBHOOK_SECRET")

    # Orange Money (Web Payment)
    OM_AUTHORIZATION_HEADER = os.environ.get("OM_AUTHORIZATION_HEADER")
    OM_MERCHANT_KEY = os.environ.get("OM_MERCHANT_KEY")
    OM_ENVIRONMENT = os.environ.get("OM_ENVIRONMENT", "dev")
    OM_BASE_URL = os.environ.get("OM_BASE_URL", "https://api.orange.com")
    OM_CURRENCY = os.environ.get("OM_CURRENCY", "XOF")
    OM_RETURN_URL = os.environ.get("OM_RETURN_URL")
    OM_CANCEL_URL = os.environ.get("OM_CANCEL_URL")
    OM_NOTIF_URL = os.environ.get("OM_NOTIF_URL")
    OM_LANG = os.environ.get("OM_LANG", "fr")


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SECRET_KEY = "test-secret"
