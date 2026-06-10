"""Cache Subsystem — pluggable, module-level TTL caching for Quant Admin.

Usage::

    from common.cache_subsystem import get_cache_manager

    cache_mgr = get_cache_manager()
    cache_mgr.register_module("my:cache", ttl=300, max_size=100)

    mod = cache_mgr.get("my:cache")
    data = await mod.get_or_compute_async("key", expensive_query)
"""

from common.cache_subsystem.backends import MemoryBackend
from common.cache_subsystem.manager import CacheManager, get_cache_manager
from common.cache_subsystem.module import CacheModule

__all__ = [
    "CacheManager",
    "CacheModule",
    "MemoryBackend",
    "get_cache_manager",
]
