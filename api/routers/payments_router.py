"""
api/routers/payments_router.py — Online Payments API (bKash / Nagad)

আগে online payment শুধু Streamlit UI থেকে চলত — কোনো REST endpoint
ছিল না, ফলে mobile app বা external integration সম্ভব ছিল না।

Endpoints:
    POST /payments/online/initiate           — payment session তৈরি (auth)
    POST /payments/online/callback/{gateway} — gateway webhook (public, signature-verified)
    GET  /payments/online                    — transaction list (auth, tenant-scoped)
    GET  /payments/online/{payment_id}       — transaction detail (auth, tenant-scoped)

Security design:
- initiate: JWT আবশ্যক; voucher-এর tenant ownership DB-তে verify হয় —
  অন্য tenant-এর voucher_id দিলে 404 (existence leak হয় না)।
- amount কখনো client থেকে নেওয়া হয় না — সবসময় voucher-এর DB amount।
- callback: JWT নেই (bKash/Nagad-এর সার্ভার ডাকে), কিন্তু
  payment_gateway.handle_payment_callback() ভেতরে signature verify,
  idempotency, ও amount check করে — তাই forged callback টিকবে না।
- payment_gateway module lazy-import করা হয় (এটি utils→streamlit টানে;
  API worker-এ import-time-এ streamlit লোড এড়াতে)।
"""
import os
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, Response

from api.core.database import get_db
from api.core.security import get_tenant_id, require_role
from api.core.rate_limit import limiter, RATE_LIMITS
from api.core.audit import audit
from api.models.schemas import (
    OnlinePaymentInitiate, SuccessResponse, ALLOWED_GATEWAYS,
)

logger = logging.getLogger("madrasa_api.payments")

router = APIRouter(prefix="/payments", tags=["Online Payments"])

_ALLOWED_TXN_STATUS = {"pending", "completed", "failed", "cancelled", "refunded"}


# ── Helpers ──────────────────────────────────────────────────────

def _default_callback_url(gateway: str) -> str:
    base = os.environ.get("APP_URL", "http://localhost:8501").rstrip("/")
    return f"{base}/api/v1/payments/online/callback/{gateway}"


def _get_gateway(name: str):
    """Lazy import — streamlit dependency API import-time-এ এড়ায়।"""
    import payment_gateway as pg
    if name == "bkash":
        return pg.BkashGateway()
    return pg.NagadGateway()


# ── Initiate ─────────────────────────────────────────────────────

@router.post("/online/initiate", status_code=201)
@limiter.limit(RATE_LIMITS["payment"])
def initiate_online_payment(
    request: Request,
    response: Response,
    body: OnlinePaymentInitiate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin", "staff", "accountant")),
):
    """
    Voucher-এর বিপরীতে bKash/Nagad payment session তৈরি করে।
    Amount সবসময় DB-র voucher থেকে আসে — client-supplied amount নেই।
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            # Tenant ownership + status verify — এক query-তেই
            cur.execute(
                """SELECT v.id, v.amount, v.status, v.student_id, v.voucher_no
                   FROM fee_vouchers v
                   WHERE v.id=%s AND v.tenant_id=%s""",
                (body.voucher_id, tid),
            )
            voucher = cur.fetchone()
            if not voucher:
                raise HTTPException(404, "ভাউচার পাওয়া যায়নি।")
            if voucher["status"] == "paid":
                raise HTTPException(400, "ভাউচার ইতিমধ্যে পরিশোধিত।")

            amount = float(voucher["amount"])
            merchant_invoice = (
                f"{voucher['voucher_no']}-{datetime.now().strftime('%y%m%d%H%M%S')}"
            )

            # Pending record আগে তৈরি হয় — callback এলে এটাই match হবে
            cur.execute(
                """INSERT INTO online_payments
                   (tenant_id, voucher_id, student_id, payment_method,
                    merchant_invoice, amount, status)
                   VALUES (%s,%s,%s,%s,%s,%s,'pending') RETURNING id""",
                (tid, voucher["id"], voucher["student_id"],
                 body.gateway, merchant_invoice, amount),
            )
            payment_id = cur.fetchone()["id"]
            audit(cur, tid, current_user, "CREATE", "OnlinePayment",
                  f"Payment initiate: ৳{amount} | {body.gateway} | {merchant_invoice}",
                  record_id=payment_id)

    callback_url = body.callback_url or _default_callback_url(body.gateway)
    gw = _get_gateway(body.gateway)

    if body.gateway == "bkash":
        gw_response = gw.create_payment(amount, merchant_invoice, callback_url)
    else:
        gw_response = gw.initiate_payment(amount, merchant_invoice, callback_url)

    if gw_response.get("error"):
        # Gateway ব্যর্থ হলে pending record fail mark — orphan থাকবে না
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE online_payments
                       SET status='failed', failure_reason=%s
                       WHERE id=%s AND tenant_id=%s""",
                    (str(gw_response["error"])[:500], payment_id, tid),
                )
        raise HTTPException(502, f"Gateway error: {gw_response['error']}")

    return SuccessResponse(
        message="Payment session তৈরি হয়েছে।",
        data={
            "payment_id":       payment_id,
            "merchant_invoice": merchant_invoice,
            "amount":           amount,
            "gateway":          body.gateway,
            # bKash: bkashURL, Nagad: callBackUrl — client redirect করবে
            "redirect_url": gw_response.get("bkashURL")
                            or gw_response.get("callBackUrl"),
            "gateway_payment_id": gw_response.get("paymentID")
                                  or gw_response.get("paymentReferenceId"),
        },
    )


# ── Gateway callback (webhook) ───────────────────────────────────

@router.post("/online/callback/{gateway}")
@limiter.limit(RATE_LIMITS["payment"])
async def payment_gateway_callback(
    request: Request,
    response: Response,   # slowapi async endpoint-এ rate-limit header inject করতে লাগে
    gateway: str,
    x_signature: str = Header(default="", alias="X-Signature"),
):
    """
    bKash/Nagad-এর server-to-server callback।

    ⚠️ ইচ্ছাকৃতভাবে JWT-free — gateway-র সার্ভার token পাঠায় না।
    নিরাপত্তা handle_payment_callback()-এর ভেতরে:
      signature verify → idempotency → amount check → DB update।
    Response সবসময় 200 (idempotent) নয়তো gateway retry-storm করে;
    ব্যর্থতা body-র success flag-এ জানানো হয়।
    """
    gateway = gateway.strip().lower()
    if gateway not in ALLOWED_GATEWAYS:
        raise HTTPException(404, "Unknown gateway")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    import payment_gateway as pg
    ok, message = pg.handle_payment_callback(gateway, payload, x_signature)

    if not ok:
        logger.warning(f"Callback rejected [{gateway}]: {message}")

    return {"success": ok, "message": message}


# ── List / detail ────────────────────────────────────────────────

@router.get("/online")
@limiter.limit(RATE_LIMITS["read"])
def list_online_payments(
    request: Request,
    response: Response,
    status:   str | None = None,
    gateway:  str | None = None,
    page:     int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    tid: int = Depends(get_tenant_id),
):
    if status and status not in _ALLOWED_TXN_STATUS:
        raise HTTPException(400, f"Invalid status. Allowed: {sorted(_ALLOWED_TXN_STATUS)}")
    if gateway and gateway not in ALLOWED_GATEWAYS:
        raise HTTPException(400, f"Invalid gateway. Allowed: {sorted(ALLOWED_GATEWAYS)}")

    where  = ["op.tenant_id=%s"]
    params: list = [tid]
    if status:
        where.append("op.status=%s"); params.append(status)
    if gateway:
        where.append("op.payment_method=%s"); params.append(gateway)

    offset = (page - 1) * per_page
    where_sql = " AND ".join(where)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT op.id, op.voucher_id, op.student_id,
                           op.payment_method, op.transaction_id,
                           op.merchant_invoice, op.amount, op.currency,
                           op.status, op.initiated_at, op.completed_at,
                           op.failure_reason,
                           s.name AS student_name
                    FROM online_payments op
                    LEFT JOIN students s ON s.id=op.student_id
                    WHERE {where_sql}
                    ORDER BY op.initiated_at DESC
                    LIMIT %s OFFSET %s""",
                tuple(params) + (per_page, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"SELECT COUNT(*) AS n FROM online_payments op WHERE {where_sql}",
                tuple(params),
            )
            total = cur.fetchone()["n"]

    return {
        "success": True, "data": rows,
        "total": total, "page": page, "per_page": per_page,
    }


@router.get("/online/{payment_id}")
@limiter.limit(RATE_LIMITS["read"])
def get_online_payment(
    request: Request,
    response: Response,
    payment_id: int,
    tid: int = Depends(get_tenant_id),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            # gateway_response ইচ্ছাকৃতভাবে বাদ — raw gateway payload-এ
            # internal reference/PII থাকতে পারে; শুধু admin debugging-এ দরকার
            cur.execute(
                """SELECT op.id, op.voucher_id, op.student_id,
                          op.payment_method, op.transaction_id,
                          op.merchant_invoice, op.amount, op.currency,
                          op.status, op.initiated_at, op.completed_at,
                          op.failure_reason
                   FROM online_payments op
                   WHERE op.id=%s AND op.tenant_id=%s""",
                (payment_id, tid),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Payment পাওয়া যায়নি।")
    return {"success": True, "data": dict(row)}
