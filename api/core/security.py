"""
api/core/security.py — JWT Authentication  v9.0
Token-based auth, role verification, tenant isolation।

পরিবর্তন (v9.0):
- SHA-256 বাদ → argon2-cffi
- auth.py-এর একই PasswordHasher instance import করা হয়েছে
  (Single source of truth)
"""

import os
import uuid
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

try:
    import jwt
except ImportError:
    import jose.jwt as jwt

from api.core.database import get_db
from api.core.jwt_revoke import is_token_revoked, is_user_globally_revoked

# ── Config ──────────────────────────────────────────────────────
_ENVIRONMENT   = os.environ.get("ENVIRONMENT", "development").lower()
_DEFAULT_SECRET = "CHANGE-THIS-IN-PRODUCTION-USE-LONG-RANDOM-STRING"
SECRET_KEY     = os.environ.get("JWT_SECRET", _DEFAULT_SECRET)
ALGORITHM      = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480   # 8 ঘণ্টা
REFRESH_TOKEN_EXPIRE_DAYS   = 7

# Enterprise fix: a *warning* is not enough — a production process must
# refuse to start with a known, public default signing key, since that
# key alone would let anyone forge valid JWTs for any tenant/role.
if SECRET_KEY == _DEFAULT_SECRET:
    if _ENVIRONMENT == "production":
        raise RuntimeError(
            "JWT_SECRET is unset (still the default placeholder) while "
            "ENVIRONMENT=production. Refusing to start — set a random "
            "32+ char secret via `openssl rand -hex 32`."
        )
    import warnings
    warnings.warn(
        "⚠️  JWT_SECRET এখনো default value-এ আছে! "
        "Production-এ deploy করার আগে .env-এ random string set করুন।",
        stacklevel=2,
    )

bearer_scheme = HTTPBearer()

# argon2 hasher — auth.py-এর মতো একই parameters
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

DEMO_LEGACY_HASH     = "demo_hash_admin123"
DEMO_LEGACY_PASSWORD = "admin123"
# Demo login must never work once ENVIRONMENT=production (see auth.py).
_DEMO_LOGIN_ALLOWED = _ENVIRONMENT != "production"


# ── Password helpers ────────────────────────────────────────────

def hash_password(password: str) -> str:
    """argon2id দিয়ে hash করে।"""
    return _ph.hash(password)


def verify_password(plain: str, stored_hash: str) -> bool:
    """Legacy demo hash + argon2 — দুটোই handle করে।"""
    if stored_hash == DEMO_LEGACY_HASH:
        return _DEMO_LOGIN_ALLOWED and plain == DEMO_LEGACY_PASSWORD
    try:
        return _ph.verify(stored_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ── Token helpers ────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    issued_at = datetime.utcnow()
    expire = issued_at + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({
        "exp":  expire,
        "iat":  int(issued_at.timestamp()),  # #4 fix: per-user revocation cutoff-এর জন্য
        "jti":  str(uuid.uuid4()),            # #4 fix: per-token revocation-এর জন্য
        "type": "access",
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    issued_at = datetime.utcnow()
    expire = issued_at + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "exp":  expire,
        "iat":  int(issued_at.timestamp()),
        "jti":  str(uuid.uuid4()),
        "type": "refresh",
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token মেয়াদ শেষ। আবার লগইন করুন।",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token।",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # #4 fix: Per-token revocation — logout করা token আর valid নয়
    jti = payload.get("jti")
    if jti and is_token_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="এই session লগআউট করা হয়েছে। আবার লগইন করুন।",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # #4 fix: Per-user revocation — password reset/"log out everywhere"-এর
    # পরে ইস্যু হওয়া পুরোনো token (issued আগে) আর valid নয়
    user_id   = payload.get("user_id")
    tenant_id = payload.get("tenant_id")
    issued_at = payload.get("iat")
    if user_id and tenant_id and issued_at:
        if is_user_globally_revoked(user_id, tenant_id, issued_at):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="নিরাপত্তার জন্য পুনরায় লগইন করুন (পাসওয়ার্ড পরিবর্তিত হয়েছে)।",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return payload


# ── FastAPI Dependencies ─────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    payload = verify_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type.")
    return payload


def get_tenant_id(current_user: dict = Depends(get_current_user)) -> int:
    tid = current_user.get("tenant_id")
    if not tid:
        raise HTTPException(status_code=403, detail="Tenant ID পাওয়া যায়নি।")
    return int(tid)


def require_role(*roles: str):
    def dependency(current_user: dict = Depends(get_current_user)):
        user_role = current_user.get("role", "")
        if user_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"এই কাজের জন্য {' বা '.join(roles)} role প্রয়োজন।",
            )
        return current_user
    return dependency


# Convenience shortcuts
AdminOnly      = Depends(require_role("admin"))
AdminOrStaff   = Depends(require_role("admin", "staff"))
AdminOrTeacher = Depends(require_role("admin", "teacher"))
AnyRole        = Depends(require_role("admin", "staff", "teacher", "accountant"))
