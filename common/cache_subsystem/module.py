"""CacheModule — a single named cache with its own TTL and backend."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from common.cache_subsystem.backends import MemoryBackend

logger = logging.getLogger(__name__)


class CacheModule:
    """One named cache namespace.

    Each module owns an isolated backend (MemoryBackend by default).
    Modules are created via ``CacheManager.register_module()`` — never
    instantiated directly.
    """

    def __init__(
        self,
        name: str,
        ttl: float,
        max_size: int = 500,
        warmup_fn: Optional[Callable[..., Any]] = None,
    ):
        self.name = name
        self.ttl = ttl
        self._max_size = max_size
        self._warmup_fn = warmup_fn
        self._backend = MemoryBackend(max_size=max_size, ttl=ttl)

    # ── public API ──────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        """Synchronous cache read (fast, <1 ms)."""
        return self._backend.get(key)

    def set(self, key: str, value: Any) -> None:
        """Synchronous cache write."""
        self._backend.set(key, value)

    def set_ttl(self, new_ttl: float) -> None:
        """Update the module's TTL. Existing cache entries are dropped."""
        self.ttl = new_ttl
        self._backend = MemoryBackend(max_size=self._max_size, ttl=new_ttl)
        logger.info("cache:set_ttl  %s  → %ss", self.name, new_ttl)

    def invalidate(self, key: Optional[str] = None) -> int:
        """Remove *key* (or *all* keys when None). Returns count removed."""
        return self._backend.invalidate(key)

    def get_or_compute(
        self,
        key: str,
        factory: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Return cached value or compute via *factory* (sync) and store.

        The factory is called *outside* the lock to avoid blocking other
        cache operations during expensive computation.
        """
        cached = self._backend.get(key)
        if cached is not None:
            return cached
        value = factory(*args, **kwargs)
        self._backend.set(key, value)
        return value

    async def get_or_compute_async(
        self,
        key: str,
        factory: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Async variant: factory can be an async function.

        Cache hit path is sync (fast).  Miss path goes through the
        async factory and stores the result synchronously.
        """
        cached = self._backend.get(key)
        if cached is not None:
            return cached
        value = await factory(*args, **kwargs)
        self._backend.set(key, value)
        return value

    async def refresh(self, **params: Any) -> Optional[Any]:
        """Invalidate the module and optionally re-warm via ``warmup_fn``.

        Returns the freshly computed data, or *None* if no warmup_fn was
        registered.
        """
        if self._warmup_fn is None:
            self.invalidate()
            logger.info("cache:refresh  %s  (invalidated only, no warmup)", self.name)
            return None

        self.invalidate()
        t0 = time.monotonic()
        try:
            result = self._warmup_fn(**params)
            # Allow warmup_fn to be either sync or async
            if asyncio.iscoroutine(result):
                result = await result
        except Exception:
            logger.exception("cache:refresh  %s  warmup failed", self.name)
            raise

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "cache:refresh  %s  warmed in %d ms  params=%s",
            self.name, elapsed_ms, params,
        )

        # warmup_fn returns (key, value) tuple or just value
        if isinstance(result, tuple) and len(result) == 2:
            key, value = result
            self._backend.set(key, value)
            return value
        self._backend.set("__warmed__", result)
        return result

    def stats(self) -> dict:
        """Return hit/miss/size stats from the backend."""
        base = self._backend.stats()
        base["name"] = self.name
        return base
