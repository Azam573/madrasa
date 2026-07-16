"""
api/routers/exams_router.py — Online Exams API

আগে online exam শুধু Streamlit UI থেকে চলত। এখন REST endpoint —
mobile app থেকে exam দেওয়া সম্ভব।

Endpoints:
    POST  /exams                       — exam তৈরি (admin/staff/teacher)
    GET   /exams                       — list (auth)
    PATCH /exams/{id}/status           — activate/deactivate (admin/staff/teacher)
    POST  /exams/{id}/questions        — প্রশ্ন যোগ (admin/staff/teacher)
    GET   /exams/{id}/questions        — উত্তরসহ প্রশ্ন (STAFF ONLY)
    GET   /exams/{id}/paper            — উত্তর-ছাড়া প্রশ্নপত্র (auth — student-facing)
    POST  /exams/{id}/submit           — উত্তরপত্র জমা, server-side scoring
    GET   /exams/{id}/results          — ফলাফল list (admin/staff/teacher)

Security design:
- /paper endpoint কখনো correct_option বা explanation ফেরত দেয় না —
  student client-এ উত্তর leak হওয়ার একমাত্র পথটাই বন্ধ।
- Scoring সম্পূর্ণ server-side; client শুধু answer পাঠায়, score নয়।
- Submit শুধু is_active exam-এ; enrollment tenant-ownership verify হয়।
- Duplicate submit = আগের submission replace (UI-র সাথে সامঞ্জস্যপূর্ণ
  UNIQUE(exam_id, enrollment_id) upsert)।
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from api.core.database import get_db
from api.core.security import get_tenant_id, require_role
from api.core.rate_limit import limiter, RATE_LIMITS
from api.core.audit import audit
from api.models.schemas import (
    ExamCreate, QuestionCreate, ExamSubmission, SuccessResponse,
)

logger = logging.getLogger("madrasa_api.exams")

router = APIRouter(prefix="/exams", tags=["Online Exams"])

_STAFF = ("admin", "staff", "teacher")


def _get_exam_or_404(cur, exam_id: int, tid: int):
    cur.execute(
        "SELECT * FROM online_exams WHERE id=%s AND tenant_id=%s",
        (exam_id, tid),
    )
    exam = cur.fetchone()
    if not exam:
        raise HTTPException(404, "Exam পাওয়া যায়নি।")
    return exam


# ── Create / list / status ──────────────────────────────────────

@router.post("", status_code=201)
@limiter.limit(RATE_LIMITS["write"])
def create_exam(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    body: ExamCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_STAFF)),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO online_exams
                   (tenant_id, title, class_id, session_id, subject_id,
                    duration_mins, instructions, shuffle_questions, is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,FALSE)
                   RETURNING id""",
                (tid, body.title, body.class_id, body.session_id,
                 body.subject_id, body.duration_mins,
                 body.instructions, body.shuffle_questions),
            )
            exam_id = cur.fetchone()["id"]
    return SuccessResponse(message="Exam তৈরি হয়েছে।", data={"id": exam_id})


@router.get("")
@limiter.limit(RATE_LIMITS["read"])
def list_exams(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    active_only: bool = False,
    page:     int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    tid: int = Depends(get_tenant_id),
):
    where = ["oe.tenant_id=%s"]
    params: list = [tid]
    if active_only:
        where.append("oe.is_active=TRUE")
    where_sql = " AND ".join(where)
    offset = (page - 1) * per_page

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT oe.id, oe.title, oe.class_id, oe.subject_id,
                           oe.duration_mins, oe.is_active, oe.created_at,
                           c.class_name, sub.subject_name,
                           (SELECT COUNT(*) FROM exam_questions q
                             WHERE q.exam_id=oe.id)  AS question_count,
                           (SELECT COUNT(*) FROM exam_submissions s
                             WHERE s.exam_id=oe.id)  AS submission_count
                    FROM online_exams oe
                    LEFT JOIN classes  c   ON c.id=oe.class_id
                    LEFT JOIN subjects sub ON sub.id=oe.subject_id
                    WHERE {where_sql}
                    ORDER BY oe.id DESC
                    LIMIT %s OFFSET %s""",
                tuple(params) + (per_page, offset),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@router.patch("/{exam_id}/status")
@limiter.limit(RATE_LIMITS["write"])
def set_exam_status(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    exam_id: int,
    active: bool = Query(...),
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_STAFF)),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            _get_exam_or_404(cur, exam_id, tid)
            if active:
                # প্রশ্ন-শূন্য exam activate করা যাবে না
                cur.execute(
                    "SELECT COUNT(*) AS n FROM exam_questions "
                    "WHERE exam_id=%s AND tenant_id=%s",
                    (exam_id, tid),
                )
                if cur.fetchone()["n"] == 0:
                    raise HTTPException(400, "প্রশ্ন ছাড়া exam activate করা যাবে না।")
            cur.execute(
                "UPDATE online_exams SET is_active=%s WHERE id=%s AND tenant_id=%s",
                (active, exam_id, tid),
            )
            audit(cur, tid, current_user,
                  "UPDATE", "OnlineExam",
                  f"Exam {'activate' if active else 'deactivate'}: #{exam_id}",
                  record_id=exam_id)
    return SuccessResponse(
        message=f"Exam {'activate' if active else 'deactivate'} হয়েছে।",
        data={"id": exam_id, "is_active": active},
    )


# ── Questions ────────────────────────────────────────────────────

@router.post("/{exam_id}/questions", status_code=201)
@limiter.limit(RATE_LIMITS["write"])
def add_question(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    exam_id: int,
    body: QuestionCreate,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_STAFF)),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            _get_exam_or_404(cur, exam_id, tid)
            cur.execute(
                """INSERT INTO exam_questions
                   (tenant_id, exam_id, question_text, option_a, option_b,
                    option_c, option_d, correct_option, marks, explanation)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, exam_id, body.question_text, body.option_a, body.option_b,
                 body.option_c, body.option_d, body.correct_option,
                 body.marks, body.explanation),
            )
            qid = cur.fetchone()["id"]
            # total_marks sync
            cur.execute(
                """UPDATE online_exams SET total_marks =
                     (SELECT COALESCE(SUM(marks),0) FROM exam_questions
                       WHERE exam_id=%s AND tenant_id=%s)
                   WHERE id=%s AND tenant_id=%s""",
                (exam_id, tid, exam_id, tid),
            )
    return SuccessResponse(message="প্রশ্ন যোগ হয়েছে।", data={"id": qid})


@router.get("/{exam_id}/questions")
@limiter.limit(RATE_LIMITS["read"])
def list_questions_with_answers(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    exam_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_STAFF)),  # ⚠️ উত্তরসহ — staff only
):
    with get_db() as conn:
        with conn.cursor() as cur:
            _get_exam_or_404(cur, exam_id, tid)
            cur.execute(
                """SELECT id, question_text, option_a, option_b, option_c,
                          option_d, correct_option, marks, explanation, order_no
                   FROM exam_questions
                   WHERE exam_id=%s AND tenant_id=%s ORDER BY order_no, id""",
                (exam_id, tid),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}


@router.get("/{exam_id}/paper")
@limiter.limit(RATE_LIMITS["read"])
def get_exam_paper(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    exam_id: int,
    enrollment_id: int = Query(..., gt=0, description="পরীক্ষার্থীর enrollment id"),
    tid: int = Depends(get_tenant_id),
):
    """
    Student-facing প্রশ্নপত্র — correct_option ও explanation
    কখনোই এই response-এ থাকে না (client-side answer leak রোধ)।

    Fix (brutal review #2): paper খোলার মুহূর্তে exam_starts-এ টাইমার
    চালু হয় — refresh করলে reset হয় না (প্রথম started_at-ই স্থায়ী)।
    জমা দেওয়ার পর paper আর খোলা যায় না।
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            exam = _get_exam_or_404(cur, exam_id, tid)
            if not exam["is_active"]:
                raise HTTPException(403, "Exam-টি এখন active নয়।")

            cur.execute(
                "SELECT id FROM student_enrollments WHERE id=%s AND tenant_id=%s",
                (enrollment_id, tid),
            )
            if not cur.fetchone():
                raise HTTPException(404, "Enrollment পাওয়া যায়নি।")

            cur.execute(
                "SELECT id FROM exam_submissions "
                "WHERE exam_id=%s AND enrollment_id=%s",
                (exam_id, enrollment_id),
            )
            if cur.fetchone():
                raise HTTPException(409, "আপনি ইতিমধ্যে এই পরীক্ষা জমা দিয়েছেন।")

            cur.execute(
                """INSERT INTO exam_starts (tenant_id, exam_id, enrollment_id)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (exam_id, enrollment_id) DO NOTHING""",
                (tid, exam_id, enrollment_id),
            )
            cur.execute(
                """SELECT started_at,
                          started_at + (%s * interval \'1 minute\') AS deadline
                   FROM exam_starts WHERE exam_id=%s AND enrollment_id=%s""",
                (exam["duration_mins"], exam_id, enrollment_id),
            )
            timing = dict(cur.fetchone())

            order_clause = "RANDOM()" if exam["shuffle_questions"] else "order_no, id"
            cur.execute(
                f"""SELECT id, question_text, option_a, option_b,
                           option_c, option_d, marks
                    FROM exam_questions
                    WHERE exam_id=%s AND tenant_id=%s
                    ORDER BY {order_clause}""",
                (exam_id, tid),
            )
            questions = [dict(r) for r in cur.fetchall()]
    return {
        "success": True,
        "exam": {
            "id":            exam["id"],
            "title":         exam["title"],
            "duration_mins": exam["duration_mins"],
            "total_marks":   exam["total_marks"],
            "instructions":  exam["instructions"],
            "started_at":    timing["started_at"],
            "deadline":      timing["deadline"],
        },
        "questions": questions,
    }


# ── Submit / results ────────────────────────────────────────────

@router.post("/{exam_id}/submit")
@limiter.limit(RATE_LIMITS["write"])
def submit_exam(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    exam_id: int,
    body: ExamSubmission,
    tid: int = Depends(get_tenant_id),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            exam = _get_exam_or_404(cur, exam_id, tid)
            if not exam["is_active"]:
                raise HTTPException(403, "Exam-টি এখন active নয় — জমা নেওয়া বন্ধ।")

            # Enrollment tenant-ownership verify — অন্য মাদ্রাসার
            # enrollment_id দিয়ে জমা দেওয়া যাবে না
            cur.execute(
                """SELECT e.id, s.name
                   FROM student_enrollments e
                   JOIN students s ON s.id=e.student_id
                   WHERE e.id=%s AND e.tenant_id=%s""",
                (body.enrollment_id, tid),
            )
            enrollment = cur.fetchone()
            if not enrollment:
                raise HTTPException(404, "Enrollment পাওয়া যায়নি।")

            # Fix (brutal review #2a): প্রথম submission-ই চূড়ান্ত —
            # resubmit-replace মানে ছিল সীমাহীন retake দিয়ে score বানানো
            cur.execute(
                "SELECT id FROM exam_submissions "
                "WHERE exam_id=%s AND enrollment_id=%s",
                (exam_id, body.enrollment_id),
            )
            if cur.fetchone():
                raise HTTPException(409, "উত্তরপত্র আগেই জমা হয়েছে — পুনরায় জমা সম্ভব নয়।")

            # Fix (brutal review #2b): server-side সময়সীমা।
            # paper না খুলে সরাসরি submit-ও নিষেধ (start রেকর্ড লাগবেই)।
            cur.execute(
                """SELECT started_at,
                          NOW() > started_at
                              + (%s * interval '1 minute')
                              + interval '2 minutes' AS expired
                   FROM exam_starts
                   WHERE exam_id=%s AND enrollment_id=%s""",
                (exam["duration_mins"], exam_id, body.enrollment_id),
            )
            start = cur.fetchone()
            if not start:
                raise HTTPException(403, "আগে প্রশ্নপত্র খুলুন — টাইমার ছাড়া জমা নেওয়া হয় না।")
            if start["expired"]:
                raise HTTPException(403, "সময় শেষ — নির্ধারিত সময়ের মধ্যে জমা দেওয়া হয়নি।")

            # Server-side scoring — client-supplied score বলে কিছু নেই
            cur.execute(
                "SELECT id, correct_option, marks FROM exam_questions "
                "WHERE tenant_id=%s AND exam_id=%s",
                (tid, exam_id),
            )
            questions = cur.fetchall()
            score = sum(
                int(q["marks"]) for q in questions
                if body.answers.get(str(q["id"])) == q["correct_option"]
            )
            total = sum(int(q["marks"]) for q in questions)
            pct   = round(score / total * 100, 2) if total else 0

            client_ip = request.client.host if request.client else None
            cur.execute(
                """INSERT INTO exam_submissions
                   (tenant_id, exam_id, enrollment_id, student_name,
                    answers, score, total_marks, percentage, submitted_at, ip_address)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
                   ON CONFLICT (exam_id, enrollment_id) DO NOTHING
                   RETURNING id""",
                (tid, exam_id, body.enrollment_id, enrollment["name"],
                 json.dumps(body.answers), score, total, pct, client_ip),
            )
            if not cur.fetchone():
                # একই মুহূর্তে দুটি submit এলে দ্বিতীয়টি এখানে ধরা পড়ে
                raise HTTPException(409, "উত্তরপত্র আগেই জমা হয়েছে।")

    return SuccessResponse(
        message="উত্তরপত্র জমা হয়েছে।",
        data={"score": score, "total_marks": total, "percentage": pct},
    )


@router.get("/{exam_id}/results")
@limiter.limit(RATE_LIMITS["report"])
def exam_results(
    request: Request,
    response: Response,  # slowapi rate-limit header injection-এর জন্য আবশ্যক
    exam_id: int,
    tid: int = Depends(get_tenant_id),
    current_user: dict = Depends(require_role(*_STAFF)),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            _get_exam_or_404(cur, exam_id, tid)
            cur.execute(
                """SELECT es.id, es.enrollment_id, es.student_name,
                          es.score, es.total_marks, es.percentage,
                          es.submitted_at
                   FROM exam_submissions es
                   WHERE es.exam_id=%s AND es.tenant_id=%s
                   ORDER BY es.score DESC, es.submitted_at ASC""",
                (exam_id, tid),
            )
            return {"success": True, "data": [dict(r) for r in cur.fetchall()]}
