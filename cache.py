"""
cache.py — Redis Cache Layer
ড্যাশবোর্ড KPI, ছাত্র তালিকা, রিপোর্ট ডেটা cache করে।
Cache miss হলে DB থেকে নিয়ে cache-এ রাখে।
"""

import os
import json
import hashlib
import logging
from functools import wraps
from typing import Any, Callable, Optional
from error_handler import safe_db_error

logger = logging.getLogger("madrasa.cache")

# ── Redis connection ──────────────────────────────────────────────
try:
    import redis

    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    _redis: redis.Redis | None = None

    def get_redis() -> redis.Redis:
        global _redis
        if _redis is None:
            _redis = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=True,
                health_check_interval=30,
            )
        return _redis

    REDIS_AVAILABLE = True

except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("redis package নেই। Cache disabled। pip install redis")


# ── TTL presets (seconds) ────────────────────────────────────────
TTL = {
    "dashboard_kpi":    300,    #  5 মিনিট — KPI tiles
    "student_list":     120,    #  2 মিনিট — ছাত্র তালিকা
    "class_list":       3600,   #  1 ঘণ্টা — ক্লাস/সেশন (কমই বদলায়)
    "fee_summary":      180,    #  3 মিনিট — ফি সারসংক্ষেপ
    "attendance_today": 60,     #  1 মিনিট — আজকের হাজিরা
    "exam_results":     600,    # 10 মিনিট — পরীক্ষার ফলাফল
    "report":           300,    #  5 মিনিট — রিপোর্ট
    "teacher_list":     3600,   #  1 ঘণ্টা
    "notice_list":      120,    #  2 মিনিট
    "default":          180,    #  3 মিনিট
}


# ── Key builder ──────────────────────────────────────────────────

def cache_key(namespace: str, tenant_id: int, *args) -> str:
    """
    Format: madrasa:{tenant_id}:{namespace}:{hash_of_args}
    Example: madrasa:1:dashboard_kpi:a3f9c2
    """
    suffix = hashlib.md5(
        ":".join(str(a) for a in args).encode(),
        usedforsecurity=False,  # cache-key hash — security context নয়
    ).hexdigest()[:8] if args else "default"
    return f"madrasa:{tenant_id}:{namespace}:{suffix}"


# ── Core get/set/delete ──────────────────────────────────────────

def cache_get(key: str) -> Any | None:
    """Cache থেকে value নিন। Miss হলে None।"""
    if not REDIS_AVAILABLE:
        return None
    try:
        r   = get_redis()
        val = r.get(key)
        if val is None:
            return None
        return json.loads(val)
    except Exception as ex:
        logger.warning(f"Cache GET failed: {ex}")
        return None


def cache_set(key: str, value: Any, ttl: int = TTL["default"]) -> bool:
    """Cache-এ value সেট করুন।"""
    if not REDIS_AVAILABLE:
        return False
    try:
        r = get_redis()
        r.setex(key, ttl, json.dumps(value, default=str))
        return True
    except Exception as ex:
        logger.warning(f"Cache SET failed: {ex}")
        return False


def cache_delete(key: str) -> bool:
    """একটি key মুছুন।"""
    if not REDIS_AVAILABLE:
        return False
    try:
        get_redis().delete(key)
        return True
    except Exception as ex:
        logger.warning(f"Cache DELETE failed: {ex}")
        return False


def cache_delete_pattern(pattern: str) -> int:
    """
    Pattern দিয়ে সব matching key মুছুন।
    Example: cache_delete_pattern("madrasa:1:*")
             → tenant 1-এর সব cache clear করবে
    """
    if not REDIS_AVAILABLE:
        return 0
    try:
        r    = get_redis()
        keys = list(r.scan_iter(pattern, count=100))
        if keys:
            return r.delete(*keys)
        return 0
    except Exception as ex:
        logger.warning(f"Cache DELETE PATTERN failed: {ex}")
        return 0


def invalidate_tenant(tenant_id: int) -> int:
    """একটি tenant-এর সব cache clear করুন।"""
    deleted = cache_delete_pattern(f"madrasa:{tenant_id}:*")
    logger.info(f"Cache invalidated for tenant {tenant_id}: {deleted} keys")
    return deleted


def invalidate_namespace(tenant_id: int, namespace: str) -> int:
    """নির্দিষ্ট namespace-এর cache clear করুন।"""
    return cache_delete_pattern(f"madrasa:{tenant_id}:{namespace}:*")


# ── Decorator ────────────────────────────────────────────────────

def cached(namespace: str, ttl: int | None = None, tenant_param: str = "tid"):
    """
    Function-এর result cache করার decorator।

    Usage:
        @cached("student_list", ttl=120)
        def get_students(tid: int, class_id: int = None):
            return db.fetchall(...)

    Cache key: madrasa:{tid}:{namespace}:{hash(args+kwargs)}
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # tenant_id বের করা
            tid = kwargs.get(tenant_param) or (args[0] if args else 1)
            cache_ttl = ttl or TTL.get(namespace, TTL["default"])

            # Key তৈরি
            key_args = list(args[1:]) + [f"{k}={v}" for k, v in sorted(kwargs.items())]
            key = cache_key(namespace, tid, *key_args)

            # Cache check
            cached_val = cache_get(key)
            if cached_val is not None:
                logger.debug(f"Cache HIT: {key}")
                return cached_val

            # DB call
            logger.debug(f"Cache MISS: {key}")
            result = func(*args, **kwargs)

            # Cache store
            if result is not None:
                cache_set(key, result, cache_ttl)

            return result
        return wrapper
    return decorator


# ── Health check ─────────────────────────────────────────────────

def cache_health() -> dict:
    """Redis connection status।"""
    if not REDIS_AVAILABLE:
        return {"status": "disabled", "reason": "redis package নেই"}
    try:
        r = get_redis()
        r.ping()
        info = r.info("memory")
        return {
            "status":      "healthy",
            "redis_url":   REDIS_URL.split("@")[-1],  # password hide
            "used_memory": info.get("used_memory_human", "unknown"),
            "connected_clients": r.info("clients").get("connected_clients", 0),
        }
    except Exception as ex:
        return {"status": "error", "error": safe_db_error(ex)}


# ── Stats ────────────────────────────────────────────────────────

def cache_stats(tenant_id: int) -> dict:
    """একটি tenant-এর cache statistics।"""
    if not REDIS_AVAILABLE:
        return {"available": False}
    try:
        r    = get_redis()
        keys = list(r.scan_iter(f"madrasa:{tenant_id}:*", count=1000))
        namespaces = {}
        for k in keys:
            parts = k.split(":")
            ns = parts[2] if len(parts) > 2 else "unknown"
            namespaces[ns] = namespaces.get(ns, 0) + 1
        return {
            "available":    True,
            "total_keys":   len(keys),
            "namespaces":   namespaces,
        }
    except Exception as ex:
        return {"available": False, "error": safe_db_error(ex)}
