"""Cache backends — swappable storage for CacheModule."""

import threading
import time
from typing import Any, Optional

from cachetools import TTLCache


class MemoryBackend:
    """Thread-safe in-memory TTL cache backed by cachetools.TTLCache.

    All public methods are guarded by a reentrant lock so one CacheModule
    can be shared across asyncio tasks / threads without corruption.
    """

    def __init__(self, max_size: int, ttl: float):
        self._cache: TTLCache = TTLCache(maxsize=max_size, ttl=ttl)
        self._lock = threading.RLock()
        self._hits: int = 0
        self._misses: int = 0

    # ── public API ──────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value

    def invalidate(self, key: Optional[str] = None) -> int:
        """Remove *key* (or *all* keys when key is None).  Returns count."""
        with self._lock:
            if key is None:
                count = len(self._cache)
                self._cache.clear()
                return count
            if key in self._cache:
                del self._cache[key]
                return 1
            return 0

    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "current_size": len(self._cache),
                "max_size": self._cache.maxsize,
                "ttl": self._cache.ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total else 0.0,
            }


# Reserved for future use — same interface, different storage.
# class RedisBackend: ...
# class DiskBackend:  ...
