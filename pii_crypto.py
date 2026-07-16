"""
pii_crypto.py — sensitive field-level এনক্রিপশন (আপাতত শুধু students.nid_no)।

শুধু NID নম্বর এনক্রিপ্ট করা হয়েছে, mobile_no নয় — কারণ mobile_no
parent-portal লগইন (parent_portal.py, api/routers/portal_router.py) ও
bulk_import.py-এর duplicate-check-এ WHERE/ILIKE lookup-এ ব্যবহৃত হয়;
plain Fernet (randomized IV) দিয়ে এনক্রিপ্ট করলে ওই lookup-গুলো ভেঙে যেত।
NID কোথাও lookup-এ ব্যবহার হয় না (নিশ্চিত করা হয়েছে) — শুধু সংরক্ষণ ও
প্রদর্শনের জন্য, তাই সহজ ও নিরাপদ।

FIELD_ENCRYPTION_KEY env var না থাকলে (local dev-এ ভুলে বাদ পড়লে) crash না
করে None/placeholder ফেরত দেয় — db.py-এর "if not conn: return False" এর
মতোই graceful-skip কনভেনশন।
"""
import os
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger("madrasa.pii_crypto")

_fernet_instance: Fernet | None = None
_fernet_checked = False


def _get_key() -> str | None:
    """db.py-এর _cfg()-এর মতোই — Streamlit secrets আগে চেক করে (এই প্রজেক্টে
    .env সরাসরি লোড হয় না, .streamlit/secrets.toml-ই আসল উৎস), তারপর
    os.environ (Celery/Alembic migration-এর মতো non-Streamlit প্রসেসের জন্য)।"""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "FIELD_ENCRYPTION_KEY" in st.secrets:
            return st.secrets["FIELD_ENCRYPTION_KEY"]
    except Exception:
        pass
    return os.environ.get("FIELD_ENCRYPTION_KEY")


def _fernet() -> Fernet | None:
    global _fernet_instance, _fernet_checked
    if not _fernet_checked:
        _fernet_checked = True
        key = _get_key()
        if key:
            try:
                _fernet_instance = Fernet(key.encode())
            except Exception as ex:
                logger.error(f"FIELD_ENCRYPTION_KEY অকার্যকর: {ex}")
        else:
            logger.warning("FIELD_ENCRYPTION_KEY সেট নেই — NID এনক্রিপশন disabled থাকবে।")
    return _fernet_instance


def encrypt_nid(plain: str | None) -> str | None:
    """None/খালি স্ট্রিং হলে None রিটার্ন করে। Key কনফিগার না থাকলে None
    রিটার্ন করে (silently — plaintext কখনো লেখা হয় না, খালি থেকে যায়)।"""
    if not plain:
        return None
    f = _fernet()
    if not f:
        return None
    return f.encrypt(plain.encode()).decode()


def decrypt_nid(token: str | None) -> str:
    """ব্যর্থ হলে (key মিসিং/rotate হয়ে গেছে/ভুল ফরম্যাট) raw exception না
    ছুঁড়ে একটা নিরাপদ placeholder রিটার্ন করে — Streamlit render ভাঙবে না।"""
    if not token:
        return "—"
    f = _fernet()
    if not f:
        return "—"
    try:
        return f.decrypt(token.encode()).decode()
    except Exception:
        return "—"
