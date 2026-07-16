"""
api/core/jwt_revoke.py — JWT Revocation (Blacklist)  v9.0 fix #4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
সমস্যা: একবার access token (8 ঘণ্টা মেয়াদ) ইস্যু হলে, logout করলেও
সেই token পরবর্তী 8 ঘণ্টা সম্পূর্ণ valid থাকে — চুরি গেলেও বাতিল করা যায় না।
Password reset (#1)-এর পরও পুরোনো token কাজ করবে, যা security gap।

সমাধান (Redis-backed):
  1. Per-token revocation: logout → সেই নির্দিষ্ট jti blacklist-এ যায়,
     TTL = token-এর বাকি থাকা expiry সময় (এর বেশি রাখার দরকার নেই,
     কারণ JWT নিজেই তখন expire করে যাবে)।
  2. Per-user revocation: password reset / "log out everywhere" হলে
     ওই user-এর জন্য একটা cutoff timestamp set হয় — তার আগে ইস্যু হওয়া
     সব token (iat < cutoff) invalid গণ্য হবে, যত token-ই থাকুক।

Fail-safe নীতি: Redis unavailable হলে revocation check skip হয়ে যায়
(log করা হয়, কিন্তু request block করা হয় না) — availability bias,
কারণ JWT-র নিজস্ব expiry এমনিতেই একটা upper bound দেয়।
"""

import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("madrasa.jwt_revoke")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_TOKEN_BLACKLIST_PREFIX = "jwt:revoked:token:"
_USER_CUTOFF_PREFIX     = "jwt:revoked:user:"

# Per-user cutoff কতদিন ধরে রাখা হবে (refresh token-এর মেয়াদের সমান)
_USER_CUTOFF_TTL_SECONDS = 7 * 24 * 3600  # 7 দিন

_redis = None
_REDIS_AVAILABLE = True

try:
    import redis as _redis_lib

    def _get_redis():
        global _redis
        if _redis is None:
            _redis = _redis_lib.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return _redis

except ImportError:
    _REDIS_AVAILABLE = False
    logger.warning("redis package নেই — JWT revocation disabled। pip install redis")

    def _get_redis():
        return None


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# ── Per-token revocation (logout) ───────────────────────────────

def revoke_token(jti: str, exp_timestamp: int) -> bool:
    """
    একটি নির্দিষ্ট token-কে blacklist করুন (logout-এ ব্যবহার হয়)।

    Args:
        jti: token-এর unique ID (JWT payload-এর 'jti' claim)
        exp_timestamp: token-এর expiry (UNIX timestamp) — TTL হিসেবে
                       ব্যবহৃত হয়, যাতে expire হওয়ার পর Redis থেকেও
                       স্বয়ংক্রিয়ভাবে মুছে যায়।
    """
    if not _REDIS_AVAILABLE or not jti:
        return False
    try:
        r = _get_redis()
        ttl = max(1, exp_timestamp - _now_ts())
        r.setex(f"{_TOKEN_BLACKLIST_PREFIX}{jti}", ttl, "1")
        logger.info(f"Token revoked: jti={jti[:12]}… ttl={ttl}s")
        return True
    except Exception as ex:
        logger.warning(f"Token revoke ব্যর্থ (fail-open): {ex}")
        return False


def is_token_revoked(jti: str) -> bool:
    """এই নির্দিষ্ট token blacklist-এ আছে কিনা চেক করুন।"""
    if not _REDIS_AVAILABLE or not jti:
        return False
    try:
        r = _get_redis()
        return bool(r.exists(f"{_TOKEN_BLACKLIST_PREFIX}{jti}"))
    except Exception as ex:
        logger.warning(f"Revocation check ব্যর্থ (fail-open, request continue হবে): {ex}")
        return False  # Fail-open: Redis down থাকলে block করব না


# ── Per-user revocation (password reset / "log out everywhere") ──

def revoke_all_user_tokens(user_id: int, tenant_id: int) -> bool:
    """
    একজন user-এর ইস্যু করা সব পুরোনো token অবিলম্বে invalid করুন।
    Password reset (#1), account compromise, বা "সব ডিভাইস থেকে লগআউট"
    এর সময় call করুন।
    """
    if not _REDIS_AVAILABLE:
        return False
    try:
        r = _get_redis()
        key = f"{_USER_CUTOFF_PREFIX}{tenant_id}:{user_id}"
        r.setex(key, _USER_CUTOFF_TTL_SECONDS, str(_now_ts()))
        logger.info(f"সব token revoke করা হলো: user_id={user_id} tenant_id={tenant_id}")
        return True
    except Exception as ex:
        logger.warning(f"Bulk revoke ব্যর্থ (fail-open): {ex}")
        return False


def is_user_globally_revoked(user_id: int, tenant_id: int, issued_at: int) -> bool:
    """
    এই token-টি ইস্যু হওয়ার পর user-এর "সব revoke" হয়েছে কিনা চেক করুন।

    Args:
        issued_at: token payload-এর 'iat' claim (কখন ইস্যু হয়েছিল)
    """
    if not _REDIS_AVAILABLE:
        return False
    try:
        r = _get_redis()
        key = f"{_USER_CUTOFF_PREFIX}{tenant_id}:{user_id}"
        cutoff = r.get(key)
        if cutoff is None:
            return False
        return int(issued_at) < int(cutoff)
    except Exception as ex:
        logger.warning(f"User-cutoff check ব্যর্থ (fail-open): {ex}")
        return False


def revocation_health() -> dict:
    """Health check endpoint-এর জন্য।"""
    if not _REDIS_AVAILABLE:
        return {"available": False, "reason": "redis package নেই"}
    try:
        r = _get_redis()
        r.ping()
        return {"available": True}
    except Exception as ex:
        return {"available": False, "reason": str(ex)}
