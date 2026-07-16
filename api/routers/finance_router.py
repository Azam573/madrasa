"""
api/routers/finance_router.py — Finance API  v9.0

SQL Injection fix:
- status whitelist validate করা হয়েছে
- f-string WHERE → parameterized string join
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from datetime import date
from api.core.database import get_db
from api.core.security import get_tenant_id, require_role
from api.core.rate_limit import limiter, RATE_LIMITS
from api.models.schemas import VoucherCreate, PaymentCreate, PaymentOut, SuccessResponse

router = APIRouter(prefix="/finance", tags=["Finance"])

_ALLOWED_STATUS = {"paid", "unpaid", "partial", "cancelled"}


@router.get("/vouchers")
@limiter.limit(RATE_LIMITS["read"])
def list_vouchers(
    request: Request,
    response: Response,
    student_id: int | None = None,
    status:     str | None = None,
    year:       int        = Query(default=date.today().year),
    page:       int        = Query(1, ge=1),
    per_page:   int        = Query(20, ge=1, le=100),
    tid: int = Depends(get_tenant_id),
):
    # SQL Injection fix: status whitelist validate
    if status and status not in _ALLOWED_STATUS:
        raise HTTPException(400, f"Invalid status '{status}'. Allowed: {_ALLOWED_STATUS}")

    offset = (page - 1) * per_page
    params = [tid, year]
    where  = ["v.tenant_id=%s", "v.year=%s"]

    if student_id:
        where.append("v.student_id=%s"); params.append(student_id)
    if status:
        where.append("v.status=%s"); params.append(status)

    where_sql = " AND ".join(where)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT v.*, s.name AS student_name, c.class_name
                    FROM fee_vouchers v
                    JOIN students s ON s.id=v.student_id
                    JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
                    JOIN classes c ON c.id=e.class_id
                    WHERE {where_sql}
                    ORDER BY v.issue_date DESC
                    LIMIT %s OFFSET %s""",
                tuple(params) + (per_page, offset),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@router.post("/vouchers", status_code=201)
@limiter.limit(RATE_LIMITS["payment"])
def create_voucher(
    request: Request,
    response: Response,
    body: VoucherCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin","staff","accountant")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            # Check duplicate
            cur.execute(
                """SELECT id FROM fee_vouchers
                   WHERE tenant_id=%s AND student_id=%s AND month_name=%s
                     AND year=%s AND fund_type=%s""",
                (tid, body.student_id, body.month_name, body.year, body.fund_type),
            )
            if cur.fetchone():
                raise HTTPException(400, f"{body.month_name} {body.year}-এর ভাউচার আগেই আছে।")

            # Generate voucher_no
            cur.execute("SELECT COUNT(*)+1 AS n FROM fee_vouchers WHERE tenant_id=%s", (tid,))
            n = cur.fetchone()["n"]
            voucher_no = f"VCH-{tid:03d}-{n:05d}"

            cur.execute(
                """INSERT INTO fee_vouchers
                   (tenant_id, enrollment_id, student_id, voucher_no, issue_date,
                    due_date, month_name, year, amount, fund_type, status, remarks)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unpaid',%s) RETURNING id, voucher_no""",
                (tid, body.enrollment_id, body.student_id, voucher_no,
                 date.today(), body.due_date, body.month_name, body.year,
                 body.amount, body.fund_type, body.remarks),
            )
            row = cur.fetchone()

    return SuccessResponse(
        message="ভাউচার তৈরি হয়েছে।",
        data={"id": row["id"], "voucher_no": row["voucher_no"]},
    )


@router.post("/payments", response_model=SuccessResponse, status_code=201)
@limiter.limit(RATE_LIMITS["payment"])
def collect_payment(
    request: Request,
    response: Response,
    body: PaymentCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin","staff","accountant")),
):
    from datetime import datetime
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT amount, status FROM fee_vouchers WHERE id=%s AND tenant_id=%s",
                (body.voucher_id, tid),
            )
            v = cur.fetchone()
            if not v:
                raise HTTPException(404, "ভাউচার পাওয়া যায়নি।")
            if v["status"] == "paid":
                raise HTTPException(400, "ভাউচার ইতিমধ্যে পরিশোধিত।")

            receipt_no = f"RCP-{tid:03d}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            cur.execute(
                """INSERT INTO fee_payments
                   (tenant_id, voucher_id, amount_paid, payment_date,
                    payment_method, receipt_no, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, body.voucher_id, body.amount_paid, date.today(),
                 body.payment_method, receipt_no, body.notes),
            )
            new_id = cur.fetchone()["id"]

            new_status = "paid" if body.amount_paid >= float(v["amount"]) else "partial"
            cur.execute(
                "UPDATE fee_vouchers SET status=%s, paid_at=NOW() WHERE id=%s AND tenant_id=%s",
                (new_status, body.voucher_id, tid),
            )

    return SuccessResponse(
        message="পেমেন্ট রেকর্ড হয়েছে।",
        data={"id": new_id, "receipt_no": receipt_no, "status": new_status},
    )


@router.get("/summary")
@limiter.limit(RATE_LIMITS["report"])
def finance_summary(
    request: Request,
    response: Response,
    year: int = Query(default=date.today().year),
    tid: int = Depends(get_tenant_id),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                   COALESCE(SUM(CASE WHEN status='paid'   THEN amount END), 0) AS collected,
                   COALESCE(SUM(CASE WHEN status='unpaid' THEN amount END), 0) AS outstanding,
                   COALESCE(SUM(CASE WHEN status='partial' THEN amount END), 0) AS partial,
                   COUNT(*) AS total_vouchers,
                   COUNT(DISTINCT student_id) AS students_billed
                   FROM fee_vouchers WHERE tenant_id=%s AND year=%s""",
                (tid, year),
            )
            return {"success": True, "year": year, "data": dict(cur.fetchone())}
