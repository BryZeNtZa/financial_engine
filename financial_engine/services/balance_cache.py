"""Balance cache layer.

Implements the cache-aside flow from the specification:

    API Request
      ↓
    Cache Lookup
      ↓
    Ledger Calculation (if cache miss)
      ↓
    Cache Update

A Redis backend is used when ``REDIS_URL`` is configured and reachable;
otherwise the cache gracefully falls back to an in-process backend so the
application (and the test suite) runs without an external Redis server.

The ledger remains the source of truth: cached balances are invalidated on
every write that changes an account's settled balance, so a miss always
recomputes from the ledger.
"""

import json
import logging
import threading
import time

logger = logging.getLogger(__name__)


class _InMemoryBackend:
    """Process-local cache backend with TTL. Used as a fallback."""

    def __init__(self):
        self._store: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            value, expires_at = item
            if expires_at is not None and expires_at < time.time():
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl: int):
        with self._lock:
            expires_at = time.time() + ttl if ttl else None
            self._store[key] = (value, expires_at)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        with self._lock:
            self._store.clear()


class _RedisBackend:
    """Redis-backed cache backend."""

    def __init__(self, client):
        self._client = client

    def get(self, key: str):
        value = self._client.get(key)
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    def set(self, key: str, value: str, ttl: int):
        self._client.set(key, value, ex=ttl or None)

    def delete(self, key: str):
        self._client.delete(key)

    def clear(self):
        # Only clear our own namespace, never flush the whole DB.
        for key in self._client.scan_iter(f"{BalanceCache.KEY_PREFIX}*"):
            self._client.delete(key)


class BalanceCache:
    """Cache-aside store for derived account balances."""

    KEY_PREFIX = "balance:"

    def __init__(self):
        self._backend = _InMemoryBackend()
        self._ttl = 300
        self._enabled = True

    def init_app(self, app):
        """Configure the cache from Flask config, selecting a backend."""
        self._ttl = int(app.config.get("BALANCE_CACHE_TTL", 300))
        self._enabled = bool(app.config.get("BALANCE_CACHE_ENABLED", True))

        url = app.config.get("REDIS_URL")
        if url:
            try:
                import redis

                client = redis.Redis.from_url(url)
                client.ping()
                self._backend = _RedisBackend(client)
                logger.info("Balance cache backend: Redis (%s)", url)
                return
            except Exception:
                logger.warning(
                    "Redis unavailable — balance cache falling back to in-memory",
                    exc_info=True,
                )

        self._backend = _InMemoryBackend()
        logger.info("Balance cache backend: in-memory")

    def _key(self, account_id: str) -> str:
        return f"{self.KEY_PREFIX}{account_id}"

    def get(self, account_id: str) -> dict | None:
        """Return cached ``{'amount', 'currency'}`` or ``None`` on miss."""
        if not self._enabled:
            return None
        raw = self._backend.get(self._key(account_id))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    def set(self, account_id: str, amount: str, currency: str):
        """Store a freshly computed balance."""
        if not self._enabled:
            return
        payload = json.dumps({"amount": amount, "currency": currency})
        self._backend.set(self._key(account_id), payload, self._ttl)

    def invalidate(self, account_id: str):
        """Evict a cached balance after a write that changes it."""
        self._backend.delete(self._key(account_id))

    def invalidate_many(self, *account_ids: str):
        for account_id in account_ids:
            if account_id:
                self.invalidate(account_id)

    def clear(self):
        """Drop all cached balances (used in tests)."""
        self._backend.clear()


# Module-level singleton, wired up in the application factory.
balance_cache = BalanceCache()
