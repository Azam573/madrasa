"""
api/routers/extras_router.py — Notices, Timetable, Zakat, QR Attendance

চারটি ছোট কিন্তু mobile-app-এর জন্য অপরিহার্য module-এর REST API।

NOTICES:
    GET    /notices                — list (parent JWT-ও চলে — নোটিশ সবার জন্য)
    POST   /notices                — তৈরি (admin/staff)
    DELETE /notices/{id}           — মুছে ফেলা (admin/staff)

TIMETABLE:
    GET  /timetable                — class+session-এর রুটিন (auth)
    POST /timetable                — entry তৈরি/আপডেট upsert (admin/staff)

ZAKAT:
    GET  /zakat/summary            — বছরের collection/distribution/balance
    POST /zakat/collections        — আদায় রেকর্ড (admin/accountant)
    GET  /zakat/collections        — list (admin/accountant)
    POST /zakat/distributions      — বিতরণ রেকর্ড (admin/accountant)
    GET  /zakat/distributions      — list (admin/accountant)

QR ATTENDANCE:
    POST /attendance/qr-punch      — QR স্ক্যানের punch-in (admin/staff/teacher)

Security notes:
- Notice list parent-token-এও পড়া যায় (role check শিথিল, কিন্তু tenant
  filter token থেকেই) — অভিভাবক অ্যাপে নোটিশ দেখানো এর মূল use case।
- Zakat আর্থিকভাবে সংবেদনশীল ও শরিয়াহ্‌-hisab — শুধু admin/accountant।
- QR punch-এ enrollment tenant-ownership verify হয়; distribution-এ
  student_id দিলে সেটিও verify হয়।
- Zakat distribution collection-এর চেয়ে বেশি হতে পারবে না (fund
  overdraw guard) — শরিয়াহ্‌ অনুযায়ী যাকাত ফান্ড আলাদা থাকতে হয়।
"""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from api.core.database import get_db
from api.core.security import get_tenant_id, get_current_user, require_role
from api.core.rate_limit import limiter, RATE_LIMITS
from api.core.audit import audit
from api.models.schemas import (
    NoticeCreate, TimetableEntryCreate,
    ZakatCollectionCreate, ZakatDistributionCreate,
    QRPunch, SuccessResponse,
)

logger = logging.getLogger("madrasa_api.extras")

notices_router   = APIRouter(prefix="/notices",    tags=["Notices"])
timetable_router = APIRouter(prefix="/timetable",  tags=["Timetable"])
zakat_router     = APIRouter(prefix="/zakat",      tags=["Zakat"])
qr_router        = APIRouter(prefix="/attendance", tags=["QR Attendance"])

_ZAKAT_ROLES = ("admin", "accountant")


# ═════════════════════════════════ NOTICES ══════════════════════

@notices_router.get("")
@limiter.limit(RATE_LIMITS["read"])
def list_notices(
    request: Request,
    response: Response,
    active_only: bool = True,
    page:     int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(get_current_user),   # parent token-ও গ্রহণযোগ্য
):
    where = ["n.tenant_id=%s"]
    params: list = [tid]
    if active_only:
        where.append("(n.expiry_date IS NULL OR n.expiry_date >= CURRENT_DATE)")
    offset = (page - 1) * per_page

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT n.id, n.title, n.body, n.category, n.is_pinned,
                           n.publish_date, n.expiry_date, n.created_by,
                           c.class_name AS target_class_name
                    FROM notices n
                    LEFT JOIN classes c ON c.id=n.target_class
                    WHERE {' AND '.join(where)}
                    ORDER BY n.is_pinned DESC, n.publish_date DESC, n.id DESC
                    LIMIT %s OFFSET %s""",
                tuple(params) + (per_page, offset),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@notices_router.post("", status_code=201)
@limiter.limit(RATE_LIMITS["write"])
def create_notice(
    request: Request,
    response: Response,
    body: NoticeCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin", "staff")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            if body.target_class:
                cur.execute(
                    "SELECT id FROM classes WHERE id=%s AND tenant_id=%s",
                    (body.target_class, tid),
                )
                if not cur.fetchone():
                    raise HTTPException(404, "Class পাওয়া যায়নি।")
            cur.execute(
                """INSERT INTO notices
                   (tenant_id, title, body, category, target_class,
                    is_pinned, expiry_date, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, body.title, body.body, body.category, body.target_class,
                 body.is_pinned, body.expiry_date,
                 current_user.get("full_name") or current_user.get("username", "Admin")),
            )
            nid = cur.fetchone()["id"]
    return SuccessResponse(message="নোটিশ প্রকাশিত হয়েছে।", data={"id": nid})


@notices_router.delete("/{notice_id}")
@limiter.limit(RATE_LIMITS["write"])
def delete_notice(
    request: Request,
    response: Response,
    notice_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin", "staff")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM notices WHERE id=%s AND tenant_id=%s RETURNING id",
                (notice_id, tid),
            )
            if not cur.fetchone():
                raise HTTPException(404, "নোটিশ পাওয়া যায়নি।")
    return SuccessResponse(message="নোটিশ মুছে ফেলা হয়েছে।", data={"id": notice_id})


# ════════════════════════════════ TIMETABLE ═════════════════════

@timetable_router.get("")
@limiter.limit(RATE_LIMITS["read"])
def get_timetable(
    request: Request,
    response: Response,
    class_id:   int = Query(..., gt=0),
    session_id: int = Query(..., gt=0),
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.id, t.day_of_week, t.period_no,
                          t.start_time, t.end_time, t.room_no,
                          t.subject_id, sub.subject_name,
                          t.teacher_id, tc.name AS teacher_name
                   FROM timetable t
                   LEFT JOIN subjects sub ON sub.id=t.subject_id
                   LEFT JOIN teachers tc  ON tc.id=t.teacher_id
                   WHERE t.tenant_id=%s AND t.class_id=%s AND t.session_id=%s
                   ORDER BY t.day_of_week, t.period_no""",
                (tid, class_id, session_id),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@timetable_router.post("", status_code=201)
@limiter.limit(RATE_LIMITS["write"])
def upsert_timetable_entry(
    request: Request,
    response: Response,
    body: TimetableEntryCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin", "staff")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM classes WHERE id=%s AND tenant_id=%s",
                (body.class_id, tid),
            )
            if not cur.fetchone():
                raise HTTPException(404, "Class পাওয়া যায়নি।")
            # একই slot-এ আবার দিলে replace — UI-র builder-এর মতো আচরণ
            cur.execute(
                """INSERT INTO timetable
                   (tenant_id, class_id, session_id, day_of_week, period_no,
                    start_time, end_time, subject_id, teacher_id, room_no)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, class_id, session_id, day_of_week, period_no)
                   DO UPDATE SET start_time=EXCLUDED.start_time,
                                 end_time=EXCLUDED.end_time,
                                 subject_id=EXCLUDED.subject_id,
                                 teacher_id=EXCLUDED.teacher_id,
                                 room_no=EXCLUDED.room_no
                   RETURNING id""",
                (tid, body.class_id, body.session_id, body.day_of_week,
                 body.period_no, body.start_time, body.end_time,
                 body.subject_id, body.teacher_id, body.room_no),
            )
            entry_id = cur.fetchone()["id"]
    return SuccessResponse(message="রুটিন সংরক্ষিত হয়েছে।", data={"id": entry_id})


# ═════════════════════════════════ ZAKAT ════════════════════════

@zakat_router.get("/summary")
@limiter.limit(RATE_LIMITS["report"])
def zakat_summary(
    request: Request,
    response: Response,
    year: int = Query(default=None),
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_ZAKAT_ROLES)),
):
    year = year or date.today().year
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COALESCE(SUM(amount),0) AS n FROM zakat_collections
                   WHERE tenant_id=%s AND collection_year=%s""",
                (tid, year),
            )
            collected = float(cur.fetchone()["n"])
            cur.execute(
                """SELECT COALESCE(SUM(amount),0) AS n FROM zakat_distributions
                   WHERE tenant_id=%s AND distribution_year=%s""",
                (tid, year),
            )
            distributed = float(cur.fetchone()["n"])
    return {
        "success": True, "year": year,
        "data": {
            "collected":   collected,
            "distributed": distributed,
            "balance":     round(collected - distributed, 2),
        },
    }


@zakat_router.post("/collections", status_code=201)
@limiter.limit(RATE_LIMITS["payment"])
def create_zakat_collection(
    request: Request,
    response: Response,
    body: ZakatCollectionCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_ZAKAT_ROLES)),
):
    today = date.today()
    with get_db() as conn:
        with conn.cursor() as cur:
            # Fix (brutal review #4): COUNT(*)+1 concurrent insert-এ duplicate
            # receipt দিত। এখন id-ভিত্তিক — globally race-safe।
            cur.execute(
                """INSERT INTO zakat_collections
                   (tenant_id, donor_name, donor_mobile, amount,
                    collection_date, collection_year, zakat_type, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, body.donor_name, body.donor_mobile, body.amount,
                 today, today.year, body.zakat_type, body.notes),
            )
            zid = cur.fetchone()["id"]
            receipt_no = f"ZKT-{tid:03d}-{zid:05d}"
            cur.execute(
                "UPDATE zakat_collections SET receipt_no=%s WHERE id=%s",
                (receipt_no, zid),
            )
            audit(cur, tid, current_user, "CREATE", "Zakat",
                  f"যাকাত আদায়: ৳{body.amount} | {receipt_no} | দাতা: {body.donor_name or '—'}",
                  record_id=zid)
    return SuccessResponse(
        message="যাকাত আদায় রেকর্ড হয়েছে।",
        data={"id": zid, "receipt_no": receipt_no},
    )


@zakat_router.get("/collections")
@limiter.limit(RATE_LIMITS["read"])
def list_zakat_collections(
    request: Request,
    response: Response,
    year: int = Query(default=None),
    page:     int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_ZAKAT_ROLES)),
):
    year = year or date.today().year
    offset = (page - 1) * per_page
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, donor_name, donor_mobile, amount, collection_date,
                          zakat_type, receipt_no, notes
                   FROM zakat_collections
                   WHERE tenant_id=%s AND collection_year=%s
                   ORDER BY id DESC LIMIT %s OFFSET %s""",
                (tid, year, per_page, offset),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@zakat_router.post("/distributions", status_code=201)
@limiter.limit(RATE_LIMITS["payment"])
def create_zakat_distribution(
    request: Request,
    response: Response,
    body: ZakatDistributionCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_ZAKAT_ROLES)),
):
    today = date.today()
    with get_db() as conn:
        with conn.cursor() as cur:
            if body.student_id:
                cur.execute(
                    "SELECT id, name FROM students WHERE id=%s AND tenant_id=%s",
                    (body.student_id, tid),
                )
                student = cur.fetchone()
                if not student:
                    raise HTTPException(404, "ছাত্র পাওয়া যায়নি।")

            # Fund overdraw guard — যাকাত ফান্ডে যা আছে তার বেশি বিতরণ নয়
            cur.execute(
                """SELECT
                     COALESCE((SELECT SUM(amount) FROM zakat_collections
                               WHERE tenant_id=%s AND collection_year=%s),0)
                   - COALESCE((SELECT SUM(amount) FROM zakat_distributions
                               WHERE tenant_id=%s AND distribution_year=%s),0)
                   AS balance""",
                (tid, today.year, tid, today.year),
            )
            balance = float(cur.fetchone()["balance"])
            if body.amount > balance:
                raise HTTPException(
                    400,
                    f"যাকাত ফান্ডে পর্যাপ্ত অর্থ নেই। বর্তমান ব্যালেন্স: ৳{balance:,.2f}",
                )

            cur.execute(
                """INSERT INTO zakat_distributions
                   (tenant_id, student_id, recipient_name, amount,
                    distribution_date, distribution_year, purpose, approved_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, body.student_id, body.recipient_name, body.amount,
                 today, today.year, body.purpose,
                 current_user.get("full_name") or current_user.get("username", "Admin")),
            )
            did = cur.fetchone()["id"]
            audit(cur, tid, current_user, "CREATE", "Zakat",
                  f"যাকাত বিতরণ: ৳{body.amount} | প্রাপক: "
                  f"{body.recipient_name or ('student#' + str(body.student_id))}",
                  record_id=did)
    return SuccessResponse(message="যাকাত বিতরণ রেকর্ড হয়েছে।", data={"id": did})


@zakat_router.get("/distributions")
@limiter.limit(RATE_LIMITS["read"])
def list_zakat_distributions(
    request: Request,
    response: Response,
    year: int = Query(default=None),
    page:     int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_ZAKAT_ROLES)),
):
    year = year or date.today().year
    offset = (page - 1) * per_page
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT zd.id, zd.student_id, s.name AS student_name,
                          zd.recipient_name, zd.amount, zd.distribution_date,
                          zd.purpose, zd.approved_by
                   FROM zakat_distributions zd
                   LEFT JOIN students s ON s.id=zd.student_id
                   WHERE zd.tenant_id=%s AND zd.distribution_year=%s
                   ORDER BY zd.id DESC LIMIT %s OFFSET %s""",
                (tid, year, per_page, offset),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


# ═══════════════════════════ QR ATTENDANCE ══════════════════════

@qr_router.post("/qr-punch")
@limiter.limit(RATE_LIMITS["bulk"])
def qr_punch(
    request: Request,
    response: Response,
    body: QRPunch,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin", "staff", "teacher")),
):
    """
    QR স্ক্যানার অ্যাপ থেকে punch-in। QR কোডে থাকে enrollment_id —
    scan করে এই endpoint-এ পাঠালেই হাজিরা। একই দিনে আবার punch = update।
    """
    punch_date = body.punch_date or date.today()

    # Fix (brutal review #3): স্ক্যান path — signed token verify;
    # ভুল/জাল/অন্য-tenant-এর token = 403
    enrollment_id = body.enrollment_id
    if body.qr_token:
        import qr_token as qrt
        verified = qrt.verify(body.qr_token, tid)
        if verified is None:
            raise HTTPException(403, "QR token অবৈধ — কার্ডটি জাল বা অন্য প্রতিষ্ঠানের।")
        enrollment_id = verified

    with get_db() as conn:
        with conn.cursor() as cur:
            # Enrollment tenant-ownership + ছাত্রের নাম (response-এর জন্য)
            cur.execute(
                """SELECT e.id, s.name
                   FROM student_enrollments e
                   JOIN students s ON s.id=e.student_id
                   WHERE e.id=%s AND e.tenant_id=%s""",
                (enrollment_id, tid),
            )
            enrollment = cur.fetchone()
            if not enrollment:
                raise HTTPException(404, "Enrollment পাওয়া যায়নি।")

            cur.execute(
                """INSERT INTO attendance (tenant_id, enrollment_id, date, status)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (enrollment_id, date)
                   DO UPDATE SET status=EXCLUDED.status
                   RETURNING id""",
                (tid, enrollment_id, punch_date, body.status),
            )
            att_id = cur.fetchone()["id"]

    return SuccessResponse(
        message=f"✅ {enrollment['name']} — {body.status} ({punch_date})",
        data={"attendance_id": att_id, "student_name": enrollment["name"],
              "date": str(punch_date), "status": body.status},
    )
