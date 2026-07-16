"""
tests/test_exams_portal_router.py — Exams + Teacher/Parent Portal API tests

সবচেয়ে গুরুত্বপূর্ণ security invariant-গুলো যাচাই করে:
1. /paper endpoint-এর SQL-এ correct_option/explanation SELECT-ই হয় না
2. Salary endpoints admin/accountant-এ সীমিত
3. Parent data endpoint-এ mobile query param নেই — token claim থেকে আসে
4. OTP enumeration-safe generic response
5. Schema validation (correct_option, mobile format, answers dict)
"""
import inspect
import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app, raise_server_exceptions=False)


# ── 1. Wiring ────────────────────────────────────────────────────

class TestWiring:

    def test_routes_registered(self):
        paths = set(app.openapi()["paths"].keys())
        for p in (
            "/api/v1/exams",
            "/api/v1/exams/{exam_id}/questions",
            "/api/v1/exams/{exam_id}/paper",
            "/api/v1/exams/{exam_id}/submit",
            "/api/v1/exams/{exam_id}/results",
            "/api/v1/teachers",
            "/api/v1/teachers/{teacher_id}/salary",
            "/api/v1/portal/parent/request-otp",
            "/api/v1/portal/parent/verify-otp",
            "/api/v1/portal/parent/children",
            "/api/v1/portal/parent/children/{student_id}/summary",
        ):
            assert p in paths, f"Missing route: {p}"

    def test_all_endpoints_rate_limited(self):
        import api.routers.exams_router as er
        import api.routers.portal_router as pr
        endpoints = [
            er.create_exam, er.list_exams, er.set_exam_status,
            er.add_question, er.list_questions_with_answers,
            er.get_exam_paper, er.submit_exam, er.exam_results,
            pr.list_teachers, pr.create_teacher, pr.get_teacher,
            pr.assign_class, pr.list_assignments, pr.pay_salary,
            pr.salary_history, pr.parent_request_otp,
            pr.parent_verify_otp, pr.parent_children,
            pr.parent_child_summary,
        ]
        for fn in endpoints:
            src = inspect.getsource(fn)
            assert "@limiter.limit" in src, f"{fn.__name__} missing rate limit"


# ── 2. Answer-leak prevention (সবচেয়ে critical) ─────────────────

class TestAnswerLeakPrevention:

    def test_paper_sql_never_selects_answers(self):
        """
        /paper-এর SELECT-এ correct_option ও explanation কলাম-ই নেই —
        অর্থাৎ ভুলবশত serialization-এও leak হওয়া অসম্ভব।
        """
        import api.routers.exams_router as er
        src = inspect.getsource(er.get_exam_paper)
        assert "correct_option" not in src.replace(
            "correct_option ও explanation", ""  # docstring উল্লেখ বাদ
        )
        assert "SELECT id, question_text, option_a, option_b" in src

    def test_questions_with_answers_is_staff_only(self):
        import api.routers.exams_router as er
        src = inspect.getsource(er.list_questions_with_answers)
        assert "require_role" in src

    def test_scoring_is_server_side(self):
        """ExamSubmission schema-তে score field নেই — client score পাঠাতে পারে না।"""
        from api.models.schemas import ExamSubmission
        assert "score" not in ExamSubmission.model_fields


# ── 3. Schema validation ─────────────────────────────────────────

class TestSchemas:

    def test_correct_option_whitelist(self):
        from api.models.schemas import QuestionCreate
        q = QuestionCreate(question_text="২+২=?", option_a="৩", option_b="৪",
                           correct_option="b")
        assert q.correct_option == "B"
        with pytest.raises(ValueError):
            QuestionCreate(question_text="২+২=?", option_a="৩", option_b="৪",
                           correct_option="E")

    def test_correct_option_must_have_matching_option(self):
        from api.models.schemas import QuestionCreate
        with pytest.raises(ValueError):
            # correct=C কিন্তু option_c নেই
            QuestionCreate(question_text="x?", option_a="1", option_b="2",
                           correct_option="C")

    def test_submission_answers_validated(self):
        from api.models.schemas import ExamSubmission
        s = ExamSubmission(enrollment_id=1, answers={"5": "a", "9": "D"})
        assert s.answers == {"5": "A", "9": "D"}
        with pytest.raises(ValueError):
            ExamSubmission(enrollment_id=1, answers={})
        with pytest.raises(ValueError):
            ExamSubmission(enrollment_id=1, answers={"5": "X"})
        with pytest.raises(ValueError):
            ExamSubmission(enrollment_id=1, answers={"abc": "A"})

    def test_teacher_mobile_format(self):
        from api.models.schemas import TeacherCreate
        TeacherCreate(name="Ustad Karim", mobile_no="01712345678")
        with pytest.raises(ValueError):
            TeacherCreate(name="Ustad Karim", mobile_no="12345")

    def test_parent_otp_mobile_format(self):
        from api.models.schemas import ParentOTPRequest
        ParentOTPRequest(tenant_id=1, mobile_no="01812345678")
        with pytest.raises(ValueError):
            ParentOTPRequest(tenant_id=1, mobile_no="+8801812345678")


# ── 4. Auth enforcement ──────────────────────────────────────────

class TestAuth:

    @pytest.mark.parametrize("method,path,payload", [
        ("post", "/api/v1/exams", {"title": "Test Exam"}),
        ("get",  "/api/v1/exams", None),
        ("get",  "/api/v1/exams/1/paper?enrollment_id=1", None),
        ("post", "/api/v1/exams/1/submit",
                 {"enrollment_id": 1, "answers": {"1": "A"}}),
        ("get",  "/api/v1/teachers", None),
        ("post", "/api/v1/teachers", {"name": "X Y"}),
        ("get",  "/api/v1/teachers/1/salary", None),
        ("get",  "/api/v1/portal/parent/children", None),
    ])
    def test_protected_endpoints_require_auth(self, method, path, payload):
        fn = getattr(client, method)
        r = fn(path, json=payload) if payload else fn(path)
        assert r.status_code in (401, 403), f"{method} {path} → {r.status_code}"

    def test_salary_endpoints_role_restricted(self):
        """Salary route-এ require_role শুধু admin/accountant।"""
        import api.routers.portal_router as pr
        for fn in (pr.pay_salary, pr.salary_history):
            src = inspect.getsource(fn)
            assert "_SALARY_ROLES" in src
        assert pr._SALARY_ROLES == ("admin", "accountant")


# ── 5. Parent portal scoping ─────────────────────────────────────

class TestParentScoping:

    def test_children_endpoint_has_no_mobile_param(self):
        """
        Mobile কখনো client input নয় — token claim থেকে আসে।
        Endpoint signature-এ mobile parameter থাকলেই vulnerability।
        """
        import api.routers.portal_router as pr
        for fn in (pr.parent_children, pr.parent_child_summary):
            params = inspect.signature(fn).parameters
            assert "mobile" not in params
            assert "mobile_no" not in params
            src = inspect.getsource(fn)
            assert 'claims["mobile"]' in src or "claims: dict" in src

    def test_parent_token_required_role(self):
        """Staff JWT দিয়েও parent endpoint চলবে না — role='parent' লাগবেই।"""
        from api.core.security import get_current_user
        from api.routers.portal_router import get_parent_claims
        from fastapi import HTTPException

        staff_claims = {"role": "admin", "tenant_id": 1, "mobile": None}
        with pytest.raises(HTTPException) as exc:
            get_parent_claims(staff_claims)
        assert exc.value.status_code == 403

    def test_otp_request_enumeration_safe(self, monkeypatch):
        """নিবন্ধিত/অনিবন্ধিত নম্বরে একই generic response।"""
        from contextlib import contextmanager

        class FakeCursor:
            def execute(self, *a, **k): pass
            def fetchone(self): return {"n": 0}   # নম্বর নিবন্ধিত নয়
            def fetchall(self): return []
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class FakeConn:
            def cursor(self): return FakeCursor()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        @contextmanager
        def fake_get_db():
            yield FakeConn()

        import api.routers.portal_router as pr
        monkeypatch.setattr(pr, "get_db", fake_get_db)

        r = client.post("/api/v1/portal/parent/request-otp",
                        json={"tenant_id": 1, "mobile_no": "01999999999"})
        assert r.status_code == 200
        # Response-এ "নিবন্ধিত নয়" জাতীয় কিছু নেই — generic বার্তা
        assert "নিবন্ধিত থাকলে" in r.json()["message"]

    def test_otp_uses_secure_random(self):
        """OTP-তে random নয়, secrets ব্যবহার হচ্ছে।"""
        import api.routers.portal_router as pr
        src = inspect.getsource(pr._generate_otp)
        assert "secrets" in src
        otp = pr._generate_otp()
        assert len(otp) == 6 and otp.isdigit()

    def test_otp_compare_is_constant_time(self):
        import api.routers.portal_router as pr
        src = inspect.getsource(pr.parent_verify_otp)
        assert "compare_digest" in src


# ── 6. Exam business rules (DB mock দিয়ে) ───────────────────────

class TestExamRules:

    def _fake_db(self, monkeypatch, exam_row):
        from contextlib import contextmanager

        class FakeCursor:
            def execute(self, sql, params=None): self.last_sql = sql
            def fetchone(self): return exam_row
            def fetchall(self): return []
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class FakeConn:
            def cursor(self): return FakeCursor()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        @contextmanager
        def fake_get_db():
            yield FakeConn()

        import api.routers.exams_router as er
        monkeypatch.setattr(er, "get_db", fake_get_db)

    def _as_admin(self):
        from api.core.security import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": 1, "tenant_id": 1, "role": "admin", "type": "access",
        }

    def test_inactive_exam_paper_403(self, monkeypatch):
        self._fake_db(monkeypatch, {
            "id": 1, "tenant_id": 1, "title": "T", "is_active": False,
            "shuffle_questions": True, "duration_mins": 30,
            "total_marks": 10, "instructions": None,
        })
        self._as_admin()
        try:
            r = client.get("/api/v1/exams/1/paper",
                           params={"enrollment_id": 1})
            assert r.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_missing_exam_404(self, monkeypatch):
        self._fake_db(monkeypatch, None)
        self._as_admin()
        try:
            r = client.get("/api/v1/exams/999/paper",
                           params={"enrollment_id": 1})
            assert r.status_code == 404
        finally:
            app.dependency_overrides.clear()
