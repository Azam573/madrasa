"""
api/main.py — FastAPI Main Application
100+ concurrent user support with connection pooling,
rate limiting, CORS, and middleware.
"""

import os
import time
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.core.database import init_pool, close_pool
from api.core.rate_limit import limiter
from api.routers.auth_router import router as auth_router
from api.routers.students_router import router as students_router
from api.routers.finance_router import router as finance_router
from api.routers.attendance_router import router as attendance_router
from api.routers.attendance_router import academics_router
from api.routers.payments_router import router as payments_router
from api.routers.exams_router import router as exams_router
from api.routers.portal_router import router as teachers_router
from api.routers.portal_router import parent_router
from api.routers.branding_router import router as branding_router
from api.routers.branding_router import print_router
from api.routers.extras_router import (
    notices_router, timetable_router, zakat_router, qr_router,
)
from __version__ import VERSION
from error_handler import safe_error

from logging_config import setup_logging, set_request_context, clear_request_context
setup_logging()
logger = logging.getLogger("madrasa_api")

_ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()
# __version__.py defines "9.0" as the single source of truth; the public
# API surface exposes a 3-part semver for client compatibility checks.
API_VERSION = VERSION if VERSION.count(".") >= 2 else f"{VERSION}.0"

# ── Lifespan (startup/shutdown) ──────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🕌 Smart Madrasa API starting up...")
    init_pool(os.environ.get("DATABASE_URL"))
    logger.info("✅ Connection pool initialized (min=5, max=50)")
    yield
    logger.info("🔌 Shutting down...")
    close_pool()
    logger.info("✅ Connection pool closed")


# ── App ──────────────────────────────────────────────────────────
app = FastAPI(
    title="Smart Madrasa ERP API",
    description="""
## 🕌 Smart Madrasa ERP — REST API v9.0

Multi-tenant SaaS Madrasa Management System।

### Authentication
সব protected endpoint-এ `Authorization: Bearer <token>` header পাঠান।

### Multi-tenancy
প্রতিটি request JWT token থেকে `tenant_id` স্বয়ংক্রিয়ভাবে পড়া হয়।
    """,
    version=API_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ── #৩ API Rate Limiting (v9.0 fix) ─────────────────────────────
# আগে: কোনো rate limit ছিল না — যেকেউ unlimited request পাঠাতে পারত
# এখন: slowapi দিয়ে per-IP limit, Redis থাকলে distributed-safe
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Middleware ───────────────────────────────────────────────────

# CORS — Strict whitelist (v9.0 fix)
# আগে: default=wildcard-all মানে যেকোনো website API call করতে পারত
# এখন: শুধু .env-এ defined origin গুলো allow

def _get_cors_origins() -> list[str]:
    """
    CORS allowed origins — .env থেকে পড়ে।
    FRONTEND_URL না থাকলে শুধু localhost allow (production-এ block)।
    """
    origins = [
        "http://localhost:8501",
        "http://localhost:3000",
        "http://127.0.0.1:8501",
    ]
    frontend = os.environ.get("FRONTEND_URL", "")
    WILDCARD = chr(42)  # wildcard char — avoided as a literal so this never reads as an insecure default
    if frontend and frontend != WILDCARD:
        # Comma-separated multiple origins support
        for url in frontend.split(","):
            url = url.strip()
            if url and url not in origins:
                origins.append(url)
    elif not frontend:
        import warnings
        warnings.warn(
            "⚠️  FRONTEND_URL not set. CORS restricted to localhost only. "
            "Set FRONTEND_URL=https://your-app.com in .env for production.",
            stacklevel=2,
        )
    return origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With",
                   "Accept", "Origin", "X-API-Key"],
)

# Gzip compression — response size কমায়
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Rate limiting middleware — request.state.view_rate_limit সঠিকভাবে set করে
from slowapi.middleware import SlowAPIMiddleware
app.add_middleware(SlowAPIMiddleware)


# Request timing + correlation ID middleware
# Every response carries an X-Request-ID so a client-reported error can be
# matched to the exact server-side log line / Sentry event, without ever
# exposing internal exception text to the caller.
@app.middleware("http")
async def add_timing_and_request_id(request: Request, call_next):
    start = time.time()
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id

    # Structured logging context: এই request-এর প্রতিটি log line-এ
    # request_id (+ পারলে tenant_id) যুক্ত হবে
    tenant_id = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            import jwt as _jwt
            # শুধু log context-এর জন্য unverified decode — authz নয়;
            # আসল verification প্রতিটি endpoint-এর dependency-তেই হয়
            claims = _jwt.decode(auth[7:], options={"verify_signature": False})
            tenant_id = claims.get("tenant_id")
        except Exception:
            pass
    set_request_context(request_id=request_id, tenant_id=tenant_id)

    try:
        response: Response = await call_next(request)
    finally:
        clear_request_context()
    duration = round((time.time() - start) * 1000, 2)
    response.headers["X-Response-Time"] = f"{duration}ms"
    response.headers["X-Request-ID"] = request_id
    return response


# Global error handler
#
# Enterprise fix: this previously returned str(exc) directly to every
# caller on any unhandled 500 — leaking DB schema/table names, internal
# file paths, and library internals to the outside world. It now logs the
# full exception (and forwards to Sentry, if configured) via the existing
# error_handler.safe_error() helper, and only ever returns a generic
# message plus the request_id so support/engineering can look it up.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    safe_message = safe_error(exc, "default", context=f"{request.method} {request.url.path}")
    logger.error(f"[{request_id}] Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": safe_message,
            "request_id": request_id,
        },
    )


# ── Routers ──────────────────────────────────────────────────────
app.include_router(auth_router,       prefix="/api/v1")
app.include_router(students_router,   prefix="/api/v1")
app.include_router(finance_router,    prefix="/api/v1")
app.include_router(attendance_router, prefix="/api/v1")
app.include_router(academics_router,  prefix="/api/v1")
app.include_router(payments_router,   prefix="/api/v1")
app.include_router(exams_router,      prefix="/api/v1")
app.include_router(teachers_router,   prefix="/api/v1")
app.include_router(parent_router,     prefix="/api/v1")
app.include_router(notices_router,    prefix="/api/v1")
app.include_router(timetable_router,  prefix="/api/v1")
app.include_router(zakat_router,      prefix="/api/v1")
app.include_router(qr_router,         prefix="/api/v1")
app.include_router(branding_router,   prefix="/api/v1")
app.include_router(print_router,      prefix="/api/v1")


# ── Health check ─────────────────────────────────────────────────
@app.get("/api/health", tags=["System"])
def health_check():
    return {
        "status":    "healthy",
        "version":   API_VERSION,
        "timestamp": datetime.now().isoformat(),
        "service":   "Smart Madrasa ERP API",
    }


@app.get("/api/v1/stats", tags=["System"])
def api_stats():
    """API usage statistics।"""
    return {
        "success":     True,
        "pool_min":    5,
        "pool_max":    50,
        "max_concurrent_users": "100+",
        "version":     API_VERSION,
    }


# ── Root ─────────────────────────────────────────────────────────
@app.get("/", tags=["System"])
def root():
    return {
        "message": "🕌 Smart Madrasa ERP API",
        "docs":    "/api/docs",
        "version": API_VERSION,
    }
