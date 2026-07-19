"""
api/routers/auth_router.py — Authentication endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from api.core.database import get_db
from api.core.security import (
    verify_password, create_access_token, create_refresh_token,
    verify_token, get_current_user, bearer_scheme
)
from api.core.jwt_revoke import revoke_token, revoke_all_user_tokens
from api.core.rate_limit import limiter, RATE_LIMITS
from api.models.schemas import LoginRequest, TokenResponse, RefreshRequest, SuccessResponse
from fastapi.security import HTTPAuthorizationCredentials
import audit_module as audit

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit(RATE_LIMITS["login"])
def login(request: Request, response: Response, req: LoginRequest):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, username, password_hash, role, full_name, is_active
                   FROM app_users WHERE tenant_id=%s AND username=%s""",
                (req.tenant_id, req.username.strip()),
            )
            user = cur.fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="ব্যবহারকারী পাওয়া যায়নি।")
    if not user["is_active"]:
        raise HTTPException(status_code=401, detail="অ্যাকাউন্ট নিষ্ক্রিয়।")
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="পাসওয়ার্ড ভুল।")

    token_data = {
        "user_id":   user["id"],
        "username":  user["username"],
        "role":      user["role"],
        "tenant_id": req.tenant_id,
        "full_name": user["full_name"],
    }

    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        full_name=user["full_name"],
        tenant_id=req.tenant_id,
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(RATE_LIMITS["write"])
def refresh(request: Request, response: Response, req: RefreshRequest):
    payload = verify_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token.")

    # #4 fix: পুরোনো jti/iat/exp/type বাদ দিয়ে নতুন token বানাই —
    # নাহলে নতুন access token পুরোনো jti বহন করবে এবং revocation
    # logic ভেঙে যাবে (নতুন token-কে পুরোনো হিসেবে track করা হবে)।
    token_data = {
        k: v for k, v in payload.items()
        if k not in ("exp", "type", "jti", "iat")
    }
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        user_id=payload["user_id"],
        username=payload["username"],
        role=payload["role"],
        full_name=payload.get("full_name"),
        tenant_id=payload["tenant_id"],
    )


@router.post("/logout", response_model=SuccessResponse)
@limiter.limit(RATE_LIMITS["write"])
def logout(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    #4 fix: বর্তমান access token-কে blacklist করে — সাথে সাথে invalid হয়ে যায়,
    8 ঘণ্টা অপেক্ষা করতে হয় না। verify_token() নিজেই revocation check করে,
    তাই এখানে duplicate validation এড়াতে raw jwt decode করি।
    """
    payload = verify_token(credentials.credentials)
    jti = payload.get("jti")
    exp = payload.get("exp")

    if jti and exp:
        # exp datetime/timestamp দুই ফরম্যাটই হতে পারে jwt lib ভেদে
        exp_ts = int(exp.timestamp()) if hasattr(exp, "timestamp") else int(exp)
        revoke_token(jti, exp_ts)

    audit.log(
        "LOGOUT", "Auth",
        f"লগআউট — {payload.get('username','')}",
    )
    return SuccessResponse(message="সফলভাবে লগআউট হয়েছে।")


@router.post("/logout-all-devices", response_model=SuccessResponse)
@limiter.limit(RATE_LIMITS["write"])
def logout_all_devices(
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user),
):
    """
    #4 fix: এই user-এর ইস্যু করা সব device-এর token অবিলম্বে invalid করে।
    Password change/reset (#1)-এর পরে, বা অ্যাকাউন্ট compromise সন্দেহ হলে
    ব্যবহার করুন।
    """
    revoke_all_user_tokens(
        current_user["user_id"],
        current_user["tenant_id"],
    )
    audit.log(
        "LOGOUT_ALL", "Auth",
        f"সব ডিভাইস থেকে লগআউট — {current_user.get('username','')}",
    )
    return SuccessResponse(message="সব ডিভাইস থেকে সফলভাবে লগআউট হয়েছে।")


@router.get("/me")
@limiter.limit(RATE_LIMITS["read"])
def me(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    current_user: dict = Depends(get_current_user),
):
    return {"success": True, "user": current_user}
