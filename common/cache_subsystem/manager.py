"""CacheManager — global registry of CacheModules (singleton)."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Callable, Optional

from common.cache_subsystem.module import CacheModule

logger = logging.getLogger(__name__)


class CacheManager:
    """Thread-safe singleton that owns all CacheModules.

    Usage::

        cache_mgr = get_cache_manager()
        cache_mgr.register_module("my:cache", ttl=60)
        ...
        module = cache_mgr.get("my:cache")
        value = module.get_or_compute("k1", expensive_query)
    """

    def __init__(self):
        self._modules: dict[str, CacheModule] = {}

    # ── register / unregister ───────────────────────────────────────────────

    def register_module(
        self,
        name: str,
        ttl: float,
        max_size: int = 500,
        warmup_fn: Optional[Callable[..., Any]] = None,
    ) -> CacheModule:
        """Create and register a new cache module.

        Raises *ValueError* if *name* already exists.
        """
        if name in self._modules:
            raise ValueError(f"CacheModule '{name}' already registered")
        mod = CacheModule(name=name, ttl=ttl, max_size=max_size, warmup_fn=warmup_fn)
        self._modules[name] = mod
        logger.info("cache:register  %s  ttl=%ds  max_size=%d", name, int(ttl), max_size)
        return mod

    def unregister(self, name: str) -> None:
        """Remove a cache module.  Silent if *name* doesn't exist."""
        if name in self._modules:
            del self._modules[name]
            logger.info("cache:unregister  %s", name)

    # ── access ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[CacheModule]:
        """Return a registered module, or *None*."""
        return self._modules.get(name)

    def list_modules(self) -> list[str]:
        """Return sorted list of registered module names."""
        return sorted(self._modules.keys())

    # ── invalidation ────────────────────────────────────────────────────────

    def invalidate(self, pattern: str) -> dict:
        """Invalidate matching modules.

        *pattern* can be an exact name (``"dashboard:experiments"``),
        a glob (``"dashboard:*"``), or ``"*"`` for all modules.

        Returns counts per affected module.
        """
        affected: dict[str, int] = {}
        for name, mod in self._modules.items():
            if pattern == "*" or fnmatch.fnmatch(name, pattern):
                count = mod.invalidate()
                affected[name] = count
        logger.info("cache:invalidate  pattern=%r  affected=%d modules", pattern, len(affected))
        return affected

    # ── stats ───────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Aggregate stats across all modules."""
        modules = [m.stats() for m in self._modules.values()]
        total_hits = sum(m["hits"] for m in modules)
        total_misses = sum(m["misses"] for m in modules)
        total_size = sum(m["current_size"] for m in modules)
        total = total_hits + total_misses
        return {
            "modules": modules,
            "summary": {
                "module_count": len(modules),
                "total_hits": total_hits,
                "total_misses": total_misses,
                "total_entries": total_size,
                "hit_rate": round(total_hits / total, 4) if total else 0.0,
            },
        }


# ── module-level singleton ──────────────────────────────────────────────────

_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """Return the process-wide CacheManager singleton."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager
