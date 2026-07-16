"""
ip_throttle.py — পাবলিক, unauthenticated ফর্মের জন্য per-IP rate limiting।

Streamlit-এ ডিফল্টভাবে client IP পাওয়া যায় না — st.context.ip_address
ব্যবহার করা হয়েছে (Streamlit >=1.37 প্রয়োজন; এই প্রজেক্টে 1.58 ইনস্টল
আছে বলে নিশ্চিত করা হয়েছে), আর nginx-কে X-Forwarded-For pass করতে হয়
(nginx.conf-এর Streamlit location block-এ সেট করা আছে)। স্থানীয় dev বা
no-proxy পরিবেশে ip_address None হতে পারে — তখন fail-open করা হয় (honeypot/
timing-এর মতো অন্য সুরক্ষা কার্যকর থাকে, পুরো ফর্মই ব্লক হয়ে যায় না)।
"""
import logging
import streamlit as st
from cache import get_redis, REDIS_AVAILABLE

logger = logging.getLogger("madrasa.ip_throttle")


def get_client_ip() -> str | None:
    try:
        return st.context.ip_address
    except Exception:
        return None


def check_ip_rate_limit(action: str, max_requests: int, window_seconds: int) -> bool:
    """
    True রিটার্ন করে মানে অনুমোদিত (limit-এ পৌঁছায়নি)। Redis অনুপলব্ধ থাকলে
    বা IP পাওয়া না গেলে fail-open করে — availability-কে priority দেওয়া
    হয়েছে, যেহেতু honeypot + timing heuristic ইতিমধ্যে defense-in-depth
    হিসেবে কাজ করছে।
    """
    if not REDIS_AVAILABLE:
        return True
    ip = get_client_ip()
    if not ip:
        return True
    try:
        r = get_redis()
        key = f"ip_throttle:{action}:{ip}"
        count = r.incr(key)
        if count == 1:
            r.expire(key, window_seconds)
        return count <= max_requests
    except Exception as ex:
        logger.warning(f"IP rate limit check failed (fail-open): {ex}")
        return True
