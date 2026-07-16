"""
monitoring.py — Sentry Error Monitoring + Structured Logging
Production error tracking, performance monitoring, alerts।
"""

import os
import logging
import functools
import traceback
from datetime import datetime
from typing import Callable, Any
from error_handler import safe_db_error

logger = logging.getLogger("madrasa.monitoring")

# ─────────────────────────────────────────────
# Sentry Setup
# ─────────────────────────────────────────────

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
_sentry_initialized = False


def init_sentry():
    global _sentry_initialized
    if _sentry_initialized or not SENTRY_DSN:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_logging = LoggingIntegration(
            level=logging.INFO,
            event_level=logging.ERROR,
        )

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[sentry_logging],
            traces_sample_rate=0.1,      # 10% performance tracing
            profiles_sample_rate=0.1,
            environment=os.environ.get("APP_ENV", "production"),
            release=f"madrasa-erp@7.0.0",
            before_send=_before_send,
        )
        _sentry_initialized = True
        logger.info("✅ Sentry initialized")
    except ImportError:
        logger.warning("sentry-sdk নেই। pip install sentry-sdk")


def _before_send(event, hint):
    """Sensitive data filter করুন।"""
    # Password, token, mobile_no filter
    if "request" in event:
        data = event["request"].get("data", {})
        for key in ["password", "token", "mobile_no", "nid_no"]:
            if key in data:
                data[key] = "[FILTERED]"
    return event


def capture_exception(exc: Exception, context: dict = None):
    """Exception Sentry-তে পাঠান।"""
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            if context:
                for k, v in context.items():
                    scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except ImportError:
        logger.error(f"Exception: {exc}\n{traceback.format_exc()}")


def capture_message(message: str, level: str = "info", context: dict = None):
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            if context:
                for k, v in context.items():
                    scope.set_extra(k, v)
            sentry_sdk.capture_message(message, level=level)
    except ImportError:
        getattr(logger, level, logger.info)(message)


def set_user_context(user_id: int, username: str, tenant_id: int, role: str):
    try:
        import sentry_sdk
        sentry_sdk.set_user({
            "id":        str(user_id),
            "username":  username,
            "tenant_id": tenant_id,
            "role":      role,
        })
    except ImportError:
        pass


# ─────────────────────────────────────────────
# Structured Logger
# ─────────────────────────────────────────────

class MadrasaLogger:
    """Structured JSON logging।"""

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _log(self, level: str, message: str, **kwargs):
        import json
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "level":     level.upper(),
            "message":   message,
            **kwargs,
        }
        log_fn = getattr(self._logger, level, self._logger.info)
        log_fn(json.dumps(record, ensure_ascii=False, default=str))

    def info(self, msg: str, **kw):    self._log("info",    msg, **kw)
    def warning(self, msg: str, **kw): self._log("warning", msg, **kw)
    def error(self, msg: str, **kw):   self._log("error",   msg, **kw)
    def debug(self, msg: str, **kw):   self._log("debug",   msg, **kw)

    def audit(self, action: str, tenant_id: int, user: str, detail: str):
        self._log("info", detail,
                  action=action, tenant_id=tenant_id, user=user, type="audit")

    def perf(self, operation: str, duration_ms: float, tenant_id: int = None):
        self._log("info", f"{operation} took {duration_ms:.1f}ms",
                  operation=operation, duration_ms=duration_ms,
                  tenant_id=tenant_id, type="performance")


# ─────────────────────────────────────────────
# Performance Timer Decorator
# ─────────────────────────────────────────────

import time

def track_performance(operation_name: str = None):
    """Function execution time track করুন।"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            op = operation_name or f"{func.__module__}.{func.__name__}"
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                duration = (time.perf_counter() - start) * 1000
                if duration > 1000:  # 1 second-এর বেশি লাগলে warning
                    logger.warning(f"⚠️ Slow operation: {op} took {duration:.1f}ms")
                else:
                    logger.debug(f"⚡ {op}: {duration:.1f}ms")
                return result
            except Exception as ex:
                duration = (time.perf_counter() - start) * 1000
                capture_exception(ex, {"operation": op, "duration_ms": duration})
                raise
        return wrapper
    return decorator


# ─────────────────────────────────────────────
# Health endpoint data
# ─────────────────────────────────────────────

def get_system_health() -> dict:
    health = {
        "timestamp":  datetime.utcnow().isoformat(),
        "version":    "7.0.0",
        "components": {},
    }

    # DB
    try:
        from db import fetchone
        fetchone("SELECT 1")
        health["components"]["database"] = {"status": "healthy"}
    except Exception as ex:
        health["components"]["database"] = {"status": "error", "error": safe_db_error(ex)}

    # Redis
    try:
        from cache import cache_health
        h = cache_health()
        health["components"]["redis"] = h
    except Exception as ex:
        health["components"]["redis"] = {"status": "error", "error": safe_db_error(ex)}

    # Storage
    try:
        from storage import storage_health
        health["components"]["storage"] = storage_health()
    except Exception as ex:
        health["components"]["storage"] = {"status": "error", "error": safe_db_error(ex)}

    # Sentry
    health["components"]["sentry"] = {
        "status":      "active" if _sentry_initialized else "disabled",
        "configured":  bool(SENTRY_DSN),
    }

    # Overall
    errors = [k for k, v in health["components"].items()
              if v.get("status") == "error"]
    health["status"] = "degraded" if errors else "healthy"
    health["errors"] = errors

    return health
