"""
qr_token.py — QR কোডের জন্য HMAC-signed token

Fix (brutal review #3): আগে QR-এ থাকত plain sequential ID
("STU-00042-...") — যে কেউ সংখ্যা অনুমান করে ভুয়া QR বানিয়ে অন্যের
হাজিরা দিতে পারত। এখন token-এ HMAC-SHA256 স্বাক্ষর থাকে — JWT_SECRET
না জানলে বৈধ token বানানো অসম্ভব।

Format:  MQR1.<tenant_id>.<enrollment_id>.<hmac_hex_16>
উদাহরণ:  MQR1.5.1042.9f3ab2c4d1e0f987

Streamlit-এর ID কার্ড জেনারেটর ও API-র /attendance/qr-punch —
দুই জায়গাতেই এই module ব্যবহৃত হয়।
"""
import hmac
import hashlib
import os

_PREFIX = "MQR1"


def _secret() -> bytes:
    return os.environ.get("JWT_SECRET", "dev-secret").encode()


def _sig(tenant_id: int, enrollment_id: int) -> str:
    msg = f"{_PREFIX}.{tenant_id}.{enrollment_id}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:16]


def sign(tenant_id: int, enrollment_id: int) -> str:
    """QR-এ বসানোর জন্য signed token।"""
    return f"{_PREFIX}.{tenant_id}.{enrollment_id}.{_sig(tenant_id, enrollment_id)}"


def verify(token: str, expected_tenant_id: int) -> int | None:
    """
    বৈধ হলে enrollment_id ফেরত দেয়, নয়তো None।
    Token-এর tenant অবশ্যই caller-এর tenant-এর সাথে মিলতে হবে —
    এক মাদ্রাসার কার্ড অন্য মাদ্রাসায় চলবে না।
    """
    try:
        prefix, tid_s, eid_s, sig = token.strip().split(".")
        if prefix != _PREFIX:
            return None
        tid, eid = int(tid_s), int(eid_s)
    except (ValueError, AttributeError):
        return None
    if tid != expected_tenant_id:
        return None
    if not hmac.compare_digest(sig, _sig(tid, eid)):
        return None
    return eid
