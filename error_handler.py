"""
error_handler.py — Centralized Safe Error Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
সমস্যা: str(ex) দিয়ে raw exception message সরাসরি user-কে দেখানো হলে
  - DB schema details (table/column names) leak হয়
  - Internal file paths leak হয়
  - Stack trace info leak হয় (security risk)

সমাধান: safe_error() — পুরো error log করো (admin/Sentry-র জন্য),
user-কে শুধু generic, নিরাপদ বার্তা দাও।
"""

import logging
import os

logger = logging.getLogger("madrasa.error")

ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

SAFE_MESSAGES = {
    "db":       "ডেটাবেস সংযোগে সমস্যা হয়েছে। আবার চেষ্টা করুন।",
    "auth":     "লগইন যাচাই করা সম্ভব হয়নি।",
    "payment":  "পেমেন্ট প্রক্রিয়া ব্যর্থ হয়েছে। আবার চেষ্টা করুন।",
    "upload":   "ফাইল আপলোড ব্যর্থ হয়েছে।",
    "sms":      "বার্তা পাঠানো সম্ভব হয়নি।",
    "export":   "ডেটা এক্সপোর্ট ব্যর্থ হয়েছে।",
    "default":  "একটি সমস্যা হয়েছে। সিস্টেম অ্যাডমিনকে জানান।",
}


def safe_error(ex: Exception, category: str = "default", context: str = "") -> str:
    """
    Exception-কে নিরাপদভাবে handle করুন।
    Usage:
        except Exception as ex:
            return False, safe_error(ex, "db", "create_student")
    """
    full_msg = f"[{category.upper()}] {context}: {type(ex).__name__}: {ex}"
    logger.error(full_msg, exc_info=True)

    try:
        import sentry_sdk
        sentry_sdk.capture_exception(ex)
    except ImportError:
        pass

    if ENVIRONMENT == "development":
        return f"{SAFE_MESSAGES.get(category, SAFE_MESSAGES['default'])} (Dev: {type(ex).__name__})"
    return SAFE_MESSAGES.get(category, SAFE_MESSAGES["default"])


def safe_db_error(ex: Exception, context: str = "") -> str:
    return safe_error(ex, "db", context)


def safe_auth_error(ex: Exception, context: str = "") -> str:
    return safe_error(ex, "auth", context)


def safe_payment_error(ex: Exception, context: str = "") -> str:
    return safe_error(ex, "payment", context)


def log_warning(message: str, context: str = ""):
    logger.warning(f"[{context}] {message}")
