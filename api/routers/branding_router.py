"""
api/routers/branding_router.py — Branding settings + Printable Documents

BRANDING (প্রতিষ্ঠানভিত্তিক customization):
    GET  /settings/branding            — বর্তমান branding (auth)
    PUT  /settings/branding            — আপডেট (admin)
    POST /settings/branding/logo       — লোগো আপলোড, base64 (admin)
    POST /settings/branding/signature  — অধ্যক্ষের স্বাক্ষর আপলোড (admin)

PRINT (branded, print-ready HTML — ব্রাউজারে খুলে সরাসরি প্রিন্ট):
    GET /print/receipt/{payment_id}    — টাকা প্রাপ্তির রসিদ
    GET /print/tc/{tc_id}              — ছাড়পত্র (Transfer Certificate)

Security:
- Image upload: server-side magic-byte sniff (PNG/JPEG only, ≤200KB) —
  Content-Type spoofing-এ কাজ হবে না; SVG নিষিদ্ধ (embedded-JS XSS ঝুঁকি)।
- সব branding text HTML-escape হয়ে document-এ বসে (stored-XSS guard)।
- Print endpoint tenant-scoped — অন্য মাদ্রাসার receipt/TC 404।
- GET /settings/branding-এর response-এ base64 image bytes বাদ (payload
  ছোট রাখতে); has_logo/has_signature flag থাকে।
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

import branding as br
from api.core.database import get_db
from api.core.security import get_tenant_id, get_current_user, require_role
from api.core.rate_limit import limiter, RATE_LIMITS
from api.core.audit import audit
from api.models.schemas import BrandingUpdate, ImageUpload, SuccessResponse

logger = logging.getLogger("madrasa_api.branding")

router       = APIRouter(prefix="/settings/branding", tags=["Branding"])
print_router = APIRouter(prefix="/print",             tags=["Print Documents"])


# ═══════════════════════════════ BRANDING ═══════════════════════

@router.get("")
@limiter.limit(RATE_LIMITS["read"])
def get_branding_settings(
    request: Request,
    response: Response,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            brand = br.get_branding(cur, tid)
    # base64 blob response থেকে বাদ — শুধু আছে/নেই flag
    brand["has_logo"]      = bool(brand.pop("logo_base64", None))
    brand["has_signature"] = bool(brand.pop("signature_base64", None))
    brand.pop("logo_mime", None)
    brand.pop("signature_mime", None)
    return {"success": True, "data": brand}


@router.put("")
@limiter.limit(RATE_LIMITS["write"])
def update_branding_settings(
    request: Request,
    response: Response,
    body: BrandingUpdate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin")),
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "আপডেট করার মতো কিছু পাঠানো হয়নি।")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                updated = br.upsert_branding(cur, tid, fields)
                audit(cur, tid, current_user, "UPDATE", "Branding",
                      f"Branding পরিবর্তন: {', '.join(updated)}")
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return SuccessResponse(
        message="Branding আপডেট হয়েছে।",
        data={"updated_fields": updated},
    )


def _upload_image(kind: str, body: ImageUpload, tid: int) -> SuccessResponse:
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                mime = br.set_image(cur, tid, kind, body.image_base64)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    label = "লোগো" if kind == "logo" else "স্বাক্ষর"
    return SuccessResponse(message=f"{label} সংরক্ষিত হয়েছে।", data={"mime": mime})


@router.post("/logo")
@limiter.limit(RATE_LIMITS["write"])
def upload_logo(
    request: Request,
    response: Response,
    body: ImageUpload,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin")),
):
    return _upload_image("logo", body, tid)


@router.post("/signature")
@limiter.limit(RATE_LIMITS["write"])
def upload_signature(
    request: Request,
    response: Response,
    body: ImageUpload,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin")),
):
    return _upload_image("signature", body, tid)


# ═════════════════════════════ PRINT DOCUMENTS ══════════════════

@print_router.get("/receipt/{payment_id}", response_class=HTMLResponse)
@limiter.limit(RATE_LIMITS["read"])
def print_receipt(
    request: Request,
    response: Response,
    payment_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(get_current_user),
):
    """Branded, print-ready রসিদ — ব্রাউজারে খুলে 🖨 বাটনে ক্লিক।"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.id, p.amount_paid, p.payment_date, p.payment_method,
                          p.receipt_no, p.voucher_id,
                          v.voucher_no, v.month_name, v.year,
                          s.name, s.father_name,
                          c.class_name, e.roll_no
                   FROM fee_payments p
                   JOIN fee_vouchers v ON v.id=p.voucher_id
                   JOIN students s     ON s.id=v.student_id
                   LEFT JOIN student_enrollments e
                          ON e.id=v.enrollment_id
                   LEFT JOIN classes c ON c.id=e.class_id
                   WHERE p.id=%s AND p.tenant_id=%s""",
                (payment_id, tid),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "পেমেন্ট পাওয়া যায়নি।")
            row = dict(row)
            brand = br.get_branding(cur, tid)

    return HTMLResponse(br.receipt_html(
        brand,
        student={"name": row["name"], "father_name": row["father_name"],
                 "class_name": row["class_name"], "roll_no": row["roll_no"]},
        payment={"receipt_no": row["receipt_no"], "payment_date": row["payment_date"],
                 "payment_method": row["payment_method"],
                 "amount_paid": row["amount_paid"]},
        voucher={"voucher_no": row["voucher_no"], "month_name": row["month_name"],
                 "year": row["year"]},
    ))


@print_router.get("/tc/{tc_id}", response_class=HTMLResponse)
@limiter.limit(RATE_LIMITS["read"])
def print_tc(
    request: Request,
    response: Response,
    tc_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(get_current_user),
):
    """Branded ছাড়পত্র (Transfer Certificate)।"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT tc.*, s.name, s.father_name
                   FROM tc_records tc
                   JOIN students s ON s.id=tc.student_id
                   WHERE tc.id=%s AND tc.tenant_id=%s""",
                (tc_id, tid),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "TC রেকর্ড পাওয়া যায়নি।")
            row = dict(row)
            brand = br.get_branding(cur, tid)

    return HTMLResponse(br.tc_html(
        brand,
        student={"name": row["name"], "father_name": row["father_name"]},
        tc=row,
    ))
