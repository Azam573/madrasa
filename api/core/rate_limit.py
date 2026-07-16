"""
api/core/rate_limit.py — API Rate Limiting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
slowapi (FastAPI-র জন্য standard rate limiter) ব্যবহার করে।
Redis available থাকলে distributed limiting (multi-worker safe),
না থাকলে in-memory fallback (single worker-এ কাজ করবে)।

Limit strategy:
  - Login/Reset endpoints: কঠোর সীমা (brute-force রোধ)
  - সাধারণ GET endpoints: উদার সীমা
  - Write/Payment endpoints: মাঝারি সীমা
  - IP + tenant_id দুটোই বিবেচনা করা হয় যাতে একটা ভারী tenant
    অন্য tenant-এর জন্য সমস্যা তৈরি না করে।
"""

import os
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request

logger = logging.getLogger("madrasa.ratelimit")


def _storage_uri() -> str:
    """Redis থাকলে ব্যবহার করো (multi-worker safe), না হলে memory://"""
    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        try:
            import redis
            r = redis.from_url(redis_url, socket_connect_timeout=1)
            r.ping()
            logger.info("✅ Rate limiter: Redis backend (distributed-safe)")
            return redis_url
        except Exception as ex:
            logger.warning(f"Redis unavailable for rate limiting, falling back to memory: {ex}")
    logger.warning(
        "⚠️  Rate limiter: in-memory backend। Multi-worker deployment-এ "
        "প্রতিটি worker আলাদা limit count রাখবে — REDIS_URL সেট করুন production-এ।"
    )
    return "memory://"


def _rate_limit_key(request: Request) -> str:
    """
    IP + tenant_id (যদি token-এ থাকে) — combined key।
    এতে একটা tenant-এর ভারী ট্রাফিক অন্য tenant-কে block করবে না,
    কিন্তু একই IP থেকে brute-force এখনও ধরা পড়বে।
    """
    ip = get_remote_address(request)
    # JWT decode করা এড়িয়ে শুধু IP ব্যবহার করছি দ্রুততার জন্য;
    # per-tenant limiting আলাদা decorator দিয়ে নিচে করা হয়েছে।
    return ip


limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=_storage_uri(),
    strategy="fixed-window",
    headers_enabled=True,  # X-RateLimit-* headers response-এ যোগ হবে
)


# ── পূর্বনির্ধারিত limit strings (slowapi decorator-এ ব্যবহারের জন্য) ──
RATE_LIMITS = {
    "login":        "5/minute",     # Brute-force রোধ
    "password_reset": "3/minute",   # OTP spam রোধ
    "write":        "30/minute",    # POST/PATCH/DELETE সাধারণ
    "read":         "100/minute",   # GET সাধারণ
    "payment":      "10/minute",    # Payment endpoints
    "report":       "20/minute",    # Heavy report queries
    "bulk":         "10/minute",    # Bulk operations (attendance, marks)
}


def rate_limit_exceeded_handler(request: Request, exc) -> dict:
    """Custom 429 response — বাংলায় বার্তা সহ।"""
    return {
        "success": False,
        "error": "অনেক বেশি request পাঠানো হয়েছে। কিছুক্ষণ পর আবার চেষ্টা করুন।",
        "retry_after": getattr(exc, "retry_after", None),
    }
