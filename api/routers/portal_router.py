"""
api/routers/portal_router.py — Teacher & Parent Portal API

দুইটি ভিন্ন consumer:

TEACHERS (staff JWT):
    GET  /teachers                      — list (auth)
    POST /teachers                      — নতুন শিক্ষক (admin/staff)
    GET  /teachers/{id}                 — detail (auth; salary মাস্ক নয় শুধু admin/accountant-এ)
    POST /teachers/{id}/assignments     — class assign (admin/staff)
    GET  /teachers/{id}/assignments     — assignment list (auth)
    POST /teachers/{id}/salary          — বেতন প্রদান (admin/accountant ONLY)
    GET  /teachers/{id}/salary          — বেতন history (admin/accountant ONLY)

PARENTS (OTP → scoped JWT, কোনো user account লাগে না):
    POST /portal/parent/request-otp     — mobile-এ OTP (public, কঠোর rate limit)
    POST /portal/parent/verify-otp      — OTP verify → 30-min parent JWT
    GET  /portal/parent/children        — এই mobile-এর সব সন্তান
    GET  /portal/parent/children/{student_id}/summary
                                        — ফি/উপস্থিতি/রেজাল্ট সারাংশ

Parent security design:
- OTP enumeration-safe: mobile-টা কোনো ছাত্রের সাথে match না করলেও
  response একই — "OTP পাঠানো হয়েছে (যদি নম্বরটি নিবন্ধিত থাকে)"।
- Parent JWT-তে role="parent" + mobile claim। প্রতিটি data endpoint
  token-এর mobile দিয়েই filter করে — query param-এ mobile নেওয়া হয় না,
  তাই এক অভিভাবক অন্যের সন্তানের ডেটা দেখতে পারে না।
- Salary endpoints admin/accountant-এ সীমিত — teacher নিজেও অন্যের
  বেতন দেখতে পারবে না।
"""
import os
import secrets
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.core.database import get_db
from api.core.security import (
    get_tenant_id, require_role, get_current_user, create_access_token,
)
from api.core.rate_limit import limiter, RATE_LIMITS
from api.core.audit import audit
from api.models.schemas import (
    TeacherCreate, TeacherAssignment, SalaryPayment,
    ParentOTPRequest, ParentOTPVerify, SuccessResponse,
)

logger = logging.getLogger("madrasa_api.portal")

router        = APIRouter(prefix="/teachers", tags=["Teachers"])
parent_router = APIRouter(prefix="/portal/parent", tags=["Parent Portal"])

_SALARY_ROLES = ("admin", "accountant")
_OTP_TTL_MINUTES     = 5
_OTP_MAX_ATTEMPTS    = 3
_PARENT_JWT_MINUTES  = 30


# ═════════════════════════════════ TEACHERS ═════════════════════

@router.get("")
@limiter.limit(RATE_LIMITS["read"])
def list_teachers(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    status: str | None = None,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(get_current_user),
):
    if status and status not in {"active", "inactive", "resigned"}:
        raise HTTPException(400, "Invalid status।")

    where  = ["tenant_id=%s"]
    params: list = [tid]
    if status:
        where.append("status=%s"); params.append(status)

    # বেতন সংবেদনশীল — admin/accountant ছাড়া list-এ মাস্ক
    can_see_salary = current_user.get("role") in _SALARY_ROLES
    salary_col = "monthly_salary" if can_see_salary else "NULL AS monthly_salary"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, name, designation, mobile_no, email,
                           joining_date, qualification, status, {salary_col}
                    FROM teachers
                    WHERE {' AND '.join(where)}
                    ORDER BY name""",
                tuple(params),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@router.post("", status_code=201)
@limiter.limit(RATE_LIMITS["write"])
def create_teacher(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    body: TeacherCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin", "staff")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO teachers
                   (tenant_id, name, father_name, mobile_no, email, nid_no,
                    designation, joining_date, monthly_salary, qualification,
                    present_address, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')
                   RETURNING id""",
                (tid, body.name, body.father_name, body.mobile_no, body.email,
                 body.nid_no, body.designation, body.joining_date,
                 body.monthly_salary, body.qualification, body.present_address),
            )
            teacher_id = cur.fetchone()["id"]
            audit(cur, tid, current_user, "CREATE", "Teachers",
                  f"নতুন শিক্ষক: {body.name} ({body.designation})",
                  record_id=teacher_id)
    return SuccessResponse(message="শিক্ষক যোগ হয়েছে।", data={"id": teacher_id})


def _get_teacher_or_404(cur, teacher_id: int, tid: int):
    cur.execute(
        "SELECT * FROM teachers WHERE id=%s AND tenant_id=%s",
        (teacher_id, tid),
    )
    t = cur.fetchone()
    if not t:
        raise HTTPException(404, "শিক্ষক পাওয়া যায়নি।")
    return t


@router.get("/{teacher_id}")
@limiter.limit(RATE_LIMITS["read"])
def get_teacher(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    teacher_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            t = dict(_get_teacher_or_404(cur, teacher_id, tid))
    # NID ও salary সংবেদনশীল — role অনুযায়ী মাস্ক
    if current_user.get("role") not in _SALARY_ROLES:
        t["monthly_salary"] = None
        t["nid_no"] = None
    return {"success": True, "data": t}


@router.post("/{teacher_id}/assignments", status_code=201)
@limiter.limit(RATE_LIMITS["write"])
def assign_class(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    teacher_id: int,
    body: TeacherAssignment,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin", "staff")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            _get_teacher_or_404(cur, teacher_id, tid)
            # class tenant-ownership verify
            cur.execute(
                "SELECT id FROM classes WHERE id=%s AND tenant_id=%s",
                (body.class_id, tid),
            )
            if not cur.fetchone():
                raise HTTPException(404, "Class পাওয়া যায়নি।")
            cur.execute(
                """INSERT INTO teacher_assignments
                   (tenant_id, teacher_id, class_id, subject_id,
                    session_id, is_class_teacher)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, teacher_id, body.class_id, body.subject_id,
                 body.session_id, body.is_class_teacher),
            )
            aid = cur.fetchone()["id"]
    return SuccessResponse(message="Class assign হয়েছে।", data={"id": aid})


@router.get("/{teacher_id}/assignments")
@limiter.limit(RATE_LIMITS["read"])
def list_assignments(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    teacher_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(get_current_user),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            _get_teacher_or_404(cur, teacher_id, tid)
            cur.execute(
                """SELECT ta.id, ta.class_id, c.class_name,
                          ta.subject_id, sub.subject_name,
                          ta.session_id, ta.is_class_teacher
                   FROM teacher_assignments ta
                   LEFT JOIN classes  c   ON c.id=ta.class_id
                   LEFT JOIN subjects sub ON sub.id=ta.subject_id
                   WHERE ta.teacher_id=%s AND ta.tenant_id=%s
                   ORDER BY ta.id DESC""",
                (teacher_id, tid),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@router.post("/{teacher_id}/salary", status_code=201)
@limiter.limit(RATE_LIMITS["payment"])
def pay_salary(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    teacher_id: int,
    body: SalaryPayment,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_SALARY_ROLES)),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            _get_teacher_or_404(cur, teacher_id, tid)
            # duplicate month check — UNIQUE constraint-এর আগে বন্ধুসুলভ error
            cur.execute(
                """SELECT id FROM teacher_salary
                   WHERE tenant_id=%s AND teacher_id=%s
                     AND month_name=%s AND year=%s""",
                (tid, teacher_id, body.month_name, body.year),
            )
            if cur.fetchone():
                raise HTTPException(
                    400, f"{body.month_name} {body.year}-এর বেতন আগেই দেওয়া হয়েছে।"
                )
            cur.execute(
                """INSERT INTO teacher_salary
                   (tenant_id, teacher_id, month_name, year, basic_salary,
                    bonus, deduction, payment_date, payment_method,
                    status, remarks)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,CURRENT_DATE,%s,'paid',%s)
                   RETURNING id, net_salary""",
                (tid, teacher_id, body.month_name, body.year,
                 body.basic_salary, body.bonus, body.deduction,
                 body.payment_method, body.remarks),
            )
            row = cur.fetchone()
            audit(cur, tid, current_user, "CREATE", "Salary",
                  f"বেতন প্রদান: teacher#{teacher_id} | {body.month_name} {body.year} "
                  f"| নিট ৳{row['net_salary']}",
                  record_id=row["id"])
    return SuccessResponse(
        message="বেতন রেকর্ড হয়েছে।",
        data={"id": row["id"], "net_salary": float(row["net_salary"])},
    )


@router.get("/{teacher_id}/salary")
@limiter.limit(RATE_LIMITS["read"])
def salary_history(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    teacher_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_SALARY_ROLES)),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            _get_teacher_or_404(cur, teacher_id, tid)
            cur.execute(
                """SELECT id, month_name, year, basic_salary, bonus,
                          deduction, net_salary, payment_date,
                          payment_method, status, remarks
                   FROM teacher_salary
                   WHERE teacher_id=%s AND tenant_id=%s
                   ORDER BY year DESC, id DESC""",
                (teacher_id, tid),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


# ═══════════════════════════════ PARENT PORTAL ══════════════════

def _generate_otp() -> str:
    """Cryptographically secure ৬-সংখ্যার OTP।"""
    return f"{secrets.randbelow(900000) + 100000}"


def _send_parent_otp_sms(phone: str, otp: str) -> bool:
    """password_reset-এর একই SMS provider ব্যবহার করে।"""
    from password_reset import _send_otp_sms
    return _send_otp_sms(phone, otp, "Smart Madrasa")


def get_parent_claims(current_user: dict = Depends(get_current_user)) -> dict:
    """Parent-scoped JWT dependency — role ও mobile claim verify করে।"""
    if current_user.get("role") != "parent":
        raise HTTPException(403, "Parent portal token প্রয়োজন।")
    if not current_user.get("mobile") or not current_user.get("tenant_id"):
        raise HTTPException(403, "Token-এ প্রয়োজনীয় claim নেই।")
    return current_user


@parent_router.post("/request-otp")
@limiter.limit(RATE_LIMITS["password_reset"])   # 3/minute — OTP spam রোধ
def parent_request_otp(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    body: ParentOTPRequest,
):
    """
    Enumeration-safe: নম্বর নিবন্ধিত থাকুক বা না থাকুক response একই।
    OTP কেবল তখনই পাঠানো হয় যখন mobile-টি কোনো active ছাত্রের সাথে যুক্ত।
    """
    generic = SuccessResponse(
        message="নম্বরটি নিবন্ধিত থাকলে OTP পাঠানো হয়েছে। ৫ মিনিটের মধ্যে ব্যবহার করুন।"
    )

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS n FROM students
                   WHERE tenant_id=%s AND mobile_no=%s AND status='active'""",
                (body.tenant_id, body.mobile_no),
            )
            if cur.fetchone()["n"] == 0:
                logger.info("Parent OTP requested for unregistered mobile — silent skip")
                return generic

            otp = _generate_otp()
            cur.execute(
                """INSERT INTO parent_otp_tokens
                   (tenant_id, mobile_no, otp_code, expires_at)
                   VALUES (%s,%s,%s,%s)""",
                (body.tenant_id, body.mobile_no, otp,
                 datetime.utcnow() + timedelta(minutes=_OTP_TTL_MINUTES)),
            )

    sent = _send_parent_otp_sms(body.mobile_no, otp)
    if not sent and os.environ.get("ENVIRONMENT", "development") == "development":
        # Dev-এ SMS provider না থাকলে log-এ OTP — production-এ কখনো নয়
        logger.warning(f"DEV MODE parent OTP for {body.mobile_no}: {otp}")

    return generic


@parent_router.post("/verify-otp")
@limiter.limit(RATE_LIMITS["login"])   # 5/minute — brute-force রোধ
def parent_verify_otp(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    body: ParentOTPVerify,
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, otp_code, expires_at, attempts, used
                   FROM parent_otp_tokens
                   WHERE tenant_id=%s AND mobile_no=%s
                   ORDER BY id DESC LIMIT 1""",
                (body.tenant_id, body.mobile_no),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(400, "OTP পাওয়া যায়নি — আগে request করুন।")
            if row["used"]:
                raise HTTPException(400, "এই OTP আগেই ব্যবহার হয়েছে।")
            if row["expires_at"].replace(tzinfo=None) < datetime.utcnow():
                raise HTTPException(400, "OTP-এর মেয়াদ শেষ। নতুন OTP নিন।")
            if row["attempts"] >= _OTP_MAX_ATTEMPTS:
                raise HTTPException(429, "অনেকবার ভুল OTP। নতুন OTP নিন।")

            if not secrets.compare_digest(row["otp_code"], body.otp):
                cur.execute(
                    "UPDATE parent_otp_tokens SET attempts=attempts+1 WHERE id=%s",
                    (row["id"],),
                )
                remaining = _OTP_MAX_ATTEMPTS - row["attempts"] - 1
                raise HTTPException(400, f"OTP ভুল। আর {max(remaining,0)} বার সুযোগ আছে।")

            cur.execute(
                "UPDATE parent_otp_tokens SET used=TRUE WHERE id=%s",
                (row["id"],),
            )

    token = create_access_token(
        {
            "role":      "parent",
            "mobile":    body.mobile_no,
            "tenant_id": body.tenant_id,
        },
        expires_delta=timedelta(minutes=_PARENT_JWT_MINUTES),
    )
    return {
        "success": True,
        "access_token": token,
        "token_type": "bearer",
        "expires_in_minutes": _PARENT_JWT_MINUTES,
    }


@parent_router.get("/children")
@limiter.limit(RATE_LIMITS["read"])
def parent_children(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    claims: dict = Depends(get_parent_claims),
):
    """Token-এর mobile দিয়েই filter — query param-এ mobile নেওয়া হয় না।"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.id, s.name, s.status,
                          c.class_name, e.roll_no
                   FROM students s
                   LEFT JOIN student_enrollments e
                          ON e.student_id=s.id AND e.tenant_id=s.tenant_id
                   LEFT JOIN classes c ON c.id=e.class_id
                   WHERE s.tenant_id=%s AND s.mobile_no=%s AND s.status='active'
                   ORDER BY s.name""",
                (claims["tenant_id"], claims["mobile"]),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@parent_router.get("/children/{student_id}/summary")
@limiter.limit(RATE_LIMITS["read"])
def parent_child_summary(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    student_id: int,
    claims: dict = Depends(get_parent_claims),
):
    tid    = claims["tenant_id"]
    mobile = claims["mobile"]

    with get_db() as conn:
        with conn.cursor() as cur:
            # Ownership check: ছাত্রটি অবশ্যই এই mobile-এর সাথে যুক্ত হতে হবে
            cur.execute(
                """SELECT id, name FROM students
                   WHERE id=%s AND tenant_id=%s AND mobile_no=%s
                     AND status='active'""",
                (student_id, tid, mobile),
            )
            student = cur.fetchone()
            if not student:
                raise HTTPException(404, "ছাত্র পাওয়া যায়নি।")

            # ফি সারাংশ
            cur.execute(
                """SELECT
                     COUNT(*) FILTER (WHERE status='unpaid')            AS unpaid_count,
                     COALESCE(SUM(amount) FILTER (WHERE status='unpaid'),0) AS unpaid_total,
                     COALESCE(SUM(amount) FILTER (WHERE status='paid'),0)   AS paid_total
                   FROM fee_vouchers
                   WHERE tenant_id=%s AND student_id=%s""",
                (tid, student_id),
            )
            fees = dict(cur.fetchone())

            # সাম্প্রতিক ১২টি ভাউচার
            cur.execute(
                """SELECT voucher_no, month_name, year, amount, status, due_date
                   FROM fee_vouchers
                   WHERE tenant_id=%s AND student_id=%s
                   ORDER BY year DESC, id DESC LIMIT 12""",
                (tid, student_id),
            )
            vouchers = [dict(r) for r in cur.fetchall()]

            # রেজাল্ট সারাংশ
            cur.execute(
                """SELECT ex.exam_name,
                          SUM(sm.obtained_marks) AS obtained,
                          SUM(subj.full_marks)   AS full_marks
                   FROM student_marks sm
                   JOIN exams ex ON ex.id=sm.exam_id
                   JOIN subjects subj ON subj.id=sm.subject_id
                   JOIN student_enrollments e ON e.id=sm.enrollment_id
                   WHERE sm.tenant_id=%s AND e.student_id=%s
                   GROUP BY ex.id, ex.exam_name
                   ORDER BY ex.id DESC LIMIT 5""",
                (tid, student_id),
            )
            results = [dict(r) for r in cur.fetchall()]

    return {
        "success": True,
        "student": dict(student),
        "fees":    fees,
        "recent_vouchers": vouchers,
        "recent_results":  results,
    }
