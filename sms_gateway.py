"""
sms_gateway.py — Shared SMS sending helper (Green Web Bangladesh gateway)

সমস্যা (আগে): ৪টি আলাদা ফাইলে (whatsapp_module.py, workers/tasks.py,
security_2fa.py, password_reset.py) হুবহু একই কোড কপি-পেস্ট করা ছিল —
http:// দিয়ে GET request, token query-string-এ (TLS না থাকা ছাড়াও
access/proxy log-এ token leak হওয়ার আলাদা ঝুঁকি)। payment_gateway.py-এর
env-var + https:// default pattern অনুসরণ করে একটাই POST transport-এ
consolidate করা হলো, যাতে এই বাগ আবার ৪ জায়গার কোনো একটাতে মিস না হয়।
"""

import os
import logging
import urllib.request
import urllib.parse

logger = logging.getLogger("madrasa.sms")

SMS_API_BASE_URL = os.environ.get("SMS_API_BASE_URL", "https://api.greenweb.com.bd/api.php")


def _post(phone: str, message: str, api_key: str, sender: str) -> str:
    """HTTPS POST করে raw provider response text রিটার্ন করে (ব্যর্থ হলে exception raise করে)।"""
    data = urllib.parse.urlencode({
        "token": api_key, "to": phone, "message": message, "sender": sender,
    }).encode()
    req = urllib.request.Request(SMS_API_BASE_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode()


def send_sms_raw(phone: str, message: str, *, api_key: str = "", sender: str = "") -> str:
    """Raw response string — whatsapp_module.py-এর পুরনো contract বজায় রাখতে। Caller নিজে exception handle করে।"""
    api_key = api_key or os.environ.get("SMS_API_KEY", "")
    sender  = sender or os.environ.get("SMS_SENDER_ID", "SmartMadrasa")
    return _post(phone, message, api_key, sender)


def send_sms(phone: str, message: str, *, api_key: str = "", sender: str = "") -> bool:
    """সফল হলে True — OTP/alert flow-এর জন্য (workers/tasks.py, security_2fa.py, password_reset.py)।"""
    key = api_key or os.environ.get("SMS_API_KEY", "")
    if not key:
        logger.warning("SMS API key নেই — SMS পাঠানো যাচ্ছে না।")
        return False
    try:
        return "success" in send_sms_raw(phone, message, api_key=key, sender=sender).lower()
    except Exception as ex:
        logger.error(f"SMS send failed: {ex}")
        return False
