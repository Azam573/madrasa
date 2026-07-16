"""
api/routers/students_router.py — Student API endpoints  v9.0

SQL Injection fix:
- status query param → whitelist validate করা হয়েছে
- where_sql string join — values সব parameterized (%s)
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from api.core.database import get_db
from api.core.security import get_current_user, get_tenant_id, require_role
from api.core.rate_limit import limiter, RATE_LIMITS
from api.models.schemas import (
    StudentCreate, StudentOut, EnrollmentCreate,
    PaginatedResponse, SuccessResponse
)
import math

router = APIRouter(prefix="/students", tags=["Students"])

_ALLOWED_STUDENT_STATUS = {"active", "pending", "inactive", "expelled"}


@router.get("", response_model=PaginatedResponse)
@limiter.limit(RATE_LIMITS["read"])
def list_students(
    request: Request,
    response: Response,
    page:     int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    class_id: int | None = None,
    status:   str        = "active",
    search:   str | None = None,
    tid: int = Depends(get_tenant_id),
):
    # SQL Injection fix: status whitelist validate
    if status not in _ALLOWED_STUDENT_STATUS:
        raise HTTPException(400, f"Invalid status '{status}'. Allowed: {_ALLOWED_STUDENT_STATUS}")

    offset = (page - 1) * per_page
    params = [tid, status]
    where  = ["s.tenant_id=%s", "s.status=%s"]

    if class_id:
        where.append("e.class_id=%s"); params.append(class_id)
    if search:
        where.append("(s.name ILIKE %s OR s.mobile_no ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]

    where_sql = " AND ".join(where)
    params_count = params.copy()

    with get_db() as conn:
        with conn.cursor() as cur:
            # SQL Injection fix (v9.0): where_sql = " AND ".join(where) — শুধু
            # hardcoded column names (%s placeholder)। কোনো user value f-string-এ নেই।
            cur.execute(
                "SELECT COUNT(*) AS n FROM students s"
                " LEFT JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id"
                f" WHERE {where_sql}",
                tuple(params_count),
            )
            total = int(cur.fetchone()["n"])

            cur.execute(
                "SELECT s.id, s.name, s.father_name, s.mobile_no, s.gender,"
                "       s.status, s.created_at,"
                "       e.roll_no, e.monthly_fee, e.enrollment_status,"
                "       c.class_name, sess.session_name"
                " FROM students s"
                " LEFT JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id"
                " LEFT JOIN classes c ON c.id=e.class_id"
                " LEFT JOIN academic_sessions sess ON sess.id=e.session_id"
                f" WHERE {where_sql}"
                " ORDER BY c.class_numeric NULLS LAST, e.roll_no NULLS LAST"
                " LIMIT %s OFFSET %s",
                tuple(params) + (per_page, offset),
            )
            data = [dict(r) for r in cur.fetchall()]

    return PaginatedResponse(
        data=data, total=total, page=page,
        per_page=per_page,
        total_pages=math.ceil(total / per_page),
    )


@router.get("/{student_id}")
@limiter.limit(RATE_LIMITS["read"])
def get_student(request: Request, student_id: int, tid: int = Depends(get_tenant_id)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.*, e.roll_no, e.monthly_fee, e.enrollment_status,
                          c.class_name, sess.session_name
                   FROM students s
                   LEFT JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
                   LEFT JOIN classes c ON c.id=e.class_id
                   LEFT JOIN academic_sessions sess ON sess.id=e.session_id
                   WHERE s.id=%s AND s.tenant_id=%s LIMIT 1""",
                (student_id, tid),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(404, "ছাত্র পাওয়া যায়নি।")
    return {"success": True, "data": dict(row)}


@router.post("", response_model=SuccessResponse, status_code=201)
@limiter.limit(RATE_LIMITS["write"])
def create_student(
    request: Request,
    response: Response,
    body: StudentCreate,
    current_user: dict = Depends(require_role("admin","staff")),
    tid: int = Depends(get_tenant_id),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO students
                   (tenant_id, name, father_name, mother_name, mobile_no,
                    date_of_birth, gender, blood_group, present_address, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING id""",
                (tid, body.name, body.father_name, body.mother_name,
                 body.mobile_no, body.date_of_birth, body.gender,
                 body.blood_group, body.present_address),
            )
            new_id = cur.fetchone()["id"]

    return SuccessResponse(message="ছাত্র সফলভাবে তৈরি হয়েছে।", data={"id": new_id})


@router.patch("/{student_id}/activate")
@limiter.limit(RATE_LIMITS["write"])
def activate_student(
    request: Request,
    response: Response,
    student_id: int,
    roll_no: int,
    enrollment_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE students SET status='active' WHERE id=%s AND tenant_id=%s",
                (student_id, tid),
            )
            cur.execute(
                "UPDATE student_enrollments SET enrollment_status='active', roll_no=%s WHERE id=%s AND tenant_id=%s",
                (roll_no, enrollment_id, tid),
            )
    return SuccessResponse(message="ছাত্র সক্রিয় করা হয়েছে।")


@router.delete("/{student_id}")
@limiter.limit(RATE_LIMITS["write"])
def deactivate_student(
    request: Request,
    response: Response,
    student_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE students SET status='inactive' WHERE id=%s AND tenant_id=%s",
                (student_id, tid),
            )
    return SuccessResponse(message="ছাত্র নিষ্ক্রিয় করা হয়েছে।")
