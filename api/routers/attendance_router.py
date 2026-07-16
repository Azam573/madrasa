"""
api/routers/attendance_router.py — Attendance API
"""
from fastapi import APIRouter, Depends, Query, Request, Response
from datetime import date
from api.core.database import get_db
from api.core.security import get_tenant_id, require_role
from api.core.rate_limit import limiter, RATE_LIMITS
from api.models.schemas import BulkAttendanceRequest, SuccessResponse

router = APIRouter(prefix="/attendance", tags=["Attendance"])


@router.post("/bulk", response_model=SuccessResponse)
@limiter.limit(RATE_LIMITS["bulk"])
def save_bulk_attendance(
    request: Request,
    response: Response,
    body: BulkAttendanceRequest,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin","staff","teacher")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            for rec in body.records:
                cur.execute(
                    """INSERT INTO attendance (tenant_id, enrollment_id, date, status)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (enrollment_id, date)
                       DO UPDATE SET status=EXCLUDED.status""",
                    (tid, rec.enrollment_id, body.date, rec.status),
                )
    return SuccessResponse(
        message=f"{len(body.records)} জনের হাজিরা সেভ হয়েছে।",
        data={"date": str(body.date), "count": len(body.records)},
    )


@router.get("/class/{class_id}")
@limiter.limit(RATE_LIMITS["read"])
def get_class_attendance(
    request: Request,
    response: Response,
    class_id:   int,
    att_date:   date  = Query(default=date.today()),
    session_id: int | None = None,
    tid: int = Depends(get_tenant_id),
):
    # SQL Injection fix (v9.0): sess_clause শুধু hardcoded placeholder,
    # value সব parameterized। session_id int type — injection সম্ভব নয়।
    params = [tid, str(att_date), class_id]
    sess_clause = ""
    if session_id:
        sess_clause = "AND e.session_id=%s"
        params.append(session_id)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.id AS student_id, s.name, e.roll_no, e.id AS enrollment_id,"
                " COALESCE(a.status, 'not_marked') AS status"
                " FROM student_enrollments e"
                " JOIN students s ON s.id=e.student_id"
                " LEFT JOIN attendance a ON a.enrollment_id=e.id"
                "   AND a.tenant_id=%s AND a.date=%s"
                f" WHERE e.class_id=%s {sess_clause}"
                "   AND e.enrollment_status='active' AND s.status='active'"
                " ORDER BY e.roll_no NULLS LAST, s.name",
                tuple(params),
            )
            return {
                "success":  True,
                "date":     str(att_date),
                "class_id": class_id,
                "data":     [dict(r) for r in cur.fetchall()],
            }


@router.get("/student/{enrollment_id}/summary")
@limiter.limit(RATE_LIMITS["read"])
def attendance_summary(
    request: Request,
    response: Response,
    enrollment_id: int,
    tid: int = Depends(get_tenant_id),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                   COUNT(*) AS total,
                   COUNT(CASE WHEN status='present' THEN 1 END) AS present,
                   COUNT(CASE WHEN status='absent'  THEN 1 END) AS absent,
                   COUNT(CASE WHEN status='late'    THEN 1 END) AS late,
                   ROUND(
                     COUNT(CASE WHEN status='present' THEN 1 END)::numeric /
                     NULLIF(COUNT(*),0) * 100, 1
                   ) AS percentage
                   FROM attendance
                   WHERE tenant_id=%s AND enrollment_id=%s""",
                (tid, enrollment_id),
            )
            return {"success": True, "data": dict(cur.fetchone())}


# ─────────────────────────────────────────────
# Academics router
# ─────────────────────────────────────────────
from api.models.schemas import BulkMarksRequest

academics_router = APIRouter(prefix="/academics", tags=["Academics"])


@academics_router.post("/marks/bulk", response_model=SuccessResponse)
@limiter.limit(RATE_LIMITS["bulk"])
def save_bulk_marks(
    request: Request,
    response: Response,
    body: BulkMarksRequest,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role("admin","teacher")),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            for m in body.marks:
                cur.execute(
                    """INSERT INTO student_marks
                       (tenant_id, enrollment_id, exam_id, subject_id,
                        written_obtained, mcq_obtained, practical_obtained, is_absent)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (enrollment_id, exam_id, subject_id)
                       DO UPDATE SET written_obtained=%s, mcq_obtained=%s,
                         practical_obtained=%s, is_absent=%s""",
                    (tid, m.enrollment_id, body.exam_id, m.subject_id,
                     m.written_obtained, m.mcq_obtained, m.practical_obtained, m.is_absent,
                     m.written_obtained, m.mcq_obtained, m.practical_obtained, m.is_absent),
                )
    return SuccessResponse(
        message=f"{len(body.marks)} টি নম্বর সেভ হয়েছে।",
        data={"exam_id": body.exam_id, "count": len(body.marks)},
    )


@academics_router.get("/results/{exam_id}")
@limiter.limit(RATE_LIMITS["report"])
def exam_results(
    request: Request,
    response: Response,
    exam_id:  int,
    class_id: int | None = None,
    tid: int = Depends(get_tenant_id),
):
    params = [tid, exam_id]
    # SQL Injection fix (v9.0): cls_clause hardcoded placeholder, class_id int type
    cls_clause = ""
    if class_id:
        cls_clause = "AND e.class_id=%s"
        params.append(class_id)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.name, e.roll_no, c.class_name,"
                "       SUM(sm.total_obtained) AS obtained,"
                "       SUM(subj.full_marks)   AS full_marks,"
                "       ROUND(SUM(sm.total_obtained)::numeric /"
                "         NULLIF(SUM(subj.full_marks),0)*100, 1) AS percentage"
                " FROM student_marks sm"
                " JOIN student_enrollments e ON e.id=sm.enrollment_id"
                " JOIN students s ON s.id=e.student_id"
                " JOIN subjects subj ON subj.id=sm.subject_id"
                " JOIN classes c ON c.id=e.class_id"
                f" WHERE sm.tenant_id=%s AND sm.exam_id=%s {cls_clause}"
                " GROUP BY s.name, e.roll_no, c.class_name, c.class_numeric"
                " ORDER BY percentage DESC NULLS LAST",
                tuple(params),
            )
            rows = [dict(r) for r in cur.fetchall()]
            for i, r in enumerate(rows):
                r["rank"] = i + 1
            return {"success": True, "data": rows}
