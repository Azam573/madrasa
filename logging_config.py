"""
logging_config.py — Structured (JSON) logging + request context

Multi-tenant SaaS-এ "কোন মাদ্রাসার কোন request-এ কী ঘটেছে" খুঁজতে
প্রতিটি log line-এ tenant_id ও request_id থাকা দরকার — সেটাই এখানে।

ব্যবহার:
    # API startup-এ (api/main.py):
    from logging_config import setup_logging
    setup_logging()

    # Middleware-এ প্রতিটি request-এ:
    from logging_config import set_request_context, clear_request_context
    set_request_context(request_id="...", tenant_id=5)

Output (এক লাইনে এক JSON object — CloudWatch/Loki/ELK সরাসরি পড়ে):
    {"ts":"2026-07-02T18:00:01+00:00","level":"INFO","logger":"madrasa_api",
     "msg":"Payment completed","request_id":"a1b2...","tenant_id":5}

LOG_FORMAT=text দিলে আগের মতো human-readable output (local dev-এর জন্য)।
contextvars ব্যবহার করায় async ও multi-thread — দুই ক্ষেত্রেই প্রতিটি
request-এর context আলাদা থাকে, লিক করে না।
"""
import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

# ── Request context (per-request, async-safe) ───────────────────
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_tenant_id:  ContextVar[int | None] = ContextVar("tenant_id",  default=None)


def set_request_context(request_id: str | None = None,
                        tenant_id: int | None = None) -> None:
    if request_id is not None:
        _request_id.set(request_id)
    if tenant_id is not None:
        _tenant_id.set(tenant_id)


def clear_request_context() -> None:
    _request_id.set(None)
    _tenant_id.set(None)


class JSONFormatter(logging.Formatter):
    """এক লাইনে এক JSON object — log aggregator-বান্ধব।"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        rid = _request_id.get()
        tid = _tenant_id.get()
        if rid:
            entry["request_id"] = rid
        if tid:
            entry["tenant_id"] = tid
        if record.exc_info and record.exc_info[0]:
            entry["exc_type"] = record.exc_info[0].__name__
            entry["exc"]      = self.formatException(record.exc_info)
        # logger.info("...", extra={"order_id": 5}) জাতীয় কাস্টম field
        for key, val in record.__dict__.items():
            if key.startswith("ctx_"):
                entry[key[4:]] = val
        return json.dumps(entry, ensure_ascii=False, default=str)


class ContextTextFormatter(logging.Formatter):
    """Dev-এর জন্য human-readable, তবু request/tenant context সহ।"""

    def format(self, record: logging.LogRecord) -> str:
        rid = _request_id.get()
        tid = _tenant_id.get()
        prefix = ""
        if rid or tid:
            parts = []
            if tid:
                parts.append(f"t={tid}")
            if rid:
                parts.append(f"rid={rid[:8]}")
            prefix = f"[{' '.join(parts)}] "
        base = super().format(record)
        return f"{prefix}{base}"


def setup_logging(level: str | None = None) -> None:
    """
    Root logger configure করে। Idempotent — একাধিকবার ডাকলে
    duplicate handler হবে না।

    Env:
        LOG_LEVEL  — DEBUG/INFO/WARNING (default: INFO)
        LOG_FORMAT — json (default production) | text (default development)
    """
    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    env        = os.environ.get("ENVIRONMENT", "development").lower()
    fmt        = os.environ.get(
        "LOG_FORMAT", "json" if env == "production" else "text"
    ).lower()

    root = logging.getLogger()
    root.setLevel(getattr(logging, level_name, logging.INFO))

    # আগের handler সরিয়ে fresh setup — duplicate log রোধ
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(ContextTextFormatter(
            "%(asctime)s %(levelname)-7s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        ))
    root.addHandler(handler)

    # গোলমেলে third-party logger শান্ত রাখা
    for noisy in ("urllib3", "botocore", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
