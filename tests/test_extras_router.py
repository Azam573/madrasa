"""
tests/test_extras_router.py — Notices/Timetable/Zakat/QR API unit tests
"""
import inspect
import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app, raise_server_exceptions=False)


class TestWiring:

    def test_routes_registered(self):
        paths = set(app.openapi()["paths"].keys())
        for p in (
            "/api/v1/notices",
            "/api/v1/notices/{notice_id}",
            "/api/v1/timetable",
            "/api/v1/zakat/summary",
            "/api/v1/zakat/collections",
            "/api/v1/zakat/distributions",
            "/api/v1/attendance/qr-punch",
        ):
            assert p in paths, f"Missing route: {p}"

    def test_all_endpoints_rate_limited(self):
        import api.routers.extras_router as xr
        fns = [
            xr.list_notices, xr.create_notice, xr.delete_notice,
            xr.get_timetable, xr.upsert_timetable_entry,
            xr.zakat_summary, xr.create_zakat_collection,
            xr.list_zakat_collections, xr.create_zakat_distribution,
            xr.list_zakat_distributions, xr.qr_punch,
        ]
        for fn in fns:
            assert "@limiter.limit" in inspect.getsource(fn), fn.__name__


class TestSchemas:

    def test_notice_category_whitelist(self):
        from api.models.schemas import NoticeCreate
        n = NoticeCreate(title="ছুটির নোটিশ", body="আগামীকাল বন্ধ", category="HOLIDAY")
        assert n.category == "holiday"
        with pytest.raises(ValueError):
            NoticeCreate(title="X Y Z", body="A B C", category="spam")

    def test_timetable_day_and_time(self):
        from api.models.schemas import TimetableEntryCreate
        t = TimetableEntryCreate(class_id=1, session_id=1,
                                 day_of_week="saturday", period_no=1,
                                 start_time="08:00", end_time="08:45")
        assert t.day_of_week == "Saturday"
        with pytest.raises(ValueError):
            TimetableEntryCreate(class_id=1, session_id=1,
                                 day_of_week="Someday", period_no=1)
        with pytest.raises(ValueError):
            TimetableEntryCreate(class_id=1, session_id=1,
                                 day_of_week="Sunday", period_no=1,
                                 start_time="8am")

    def test_zakat_distribution_needs_target(self):
        from api.models.schemas import ZakatDistributionCreate
        ZakatDistributionCreate(recipient_name="গরিব পরিবার", amount=1000)
        ZakatDistributionCreate(student_id=5, amount=1000)
        with pytest.raises(ValueError):
            ZakatDistributionCreate(amount=1000)   # কোনো target নেই

    def test_qr_punch_status_whitelist(self):
        from api.models.schemas import QRPunch
        q = QRPunch(enrollment_id=1, status="PRESENT")
        assert q.status == "present"
        with pytest.raises(ValueError):
            QRPunch(enrollment_id=1, status="vanished")


class TestAuth:

    @pytest.mark.parametrize("method,path,payload", [
        ("get",    "/api/v1/notices", None),
        ("post",   "/api/v1/notices", {"title": "T i t", "body": "B o d y"}),
        ("delete", "/api/v1/notices/1", None),
        ("get",    "/api/v1/timetable?class_id=1&session_id=1", None),
        ("get",    "/api/v1/zakat/summary", None),
        ("post",   "/api/v1/zakat/collections", {"amount": 100}),
        ("post",   "/api/v1/attendance/qr-punch", {"enrollment_id": 1}),
    ])
    def test_requires_auth(self, method, path, payload):
        fn = getattr(client, method)
        r = fn(path, json=payload) if payload else fn(path)
        assert r.status_code in (401, 403), f"{method} {path} → {r.status_code}"

    def test_zakat_role_restricted(self):
        import api.routers.extras_router as xr
        assert xr._ZAKAT_ROLES == ("admin", "accountant")
        for fn in (xr.zakat_summary, xr.create_zakat_collection,
                   xr.create_zakat_distribution):
            assert "_ZAKAT_ROLES" in inspect.getsource(fn)


class TestZakatOverdrawGuard:

    def test_distribution_exceeding_balance_rejected(self, monkeypatch):
        """ফান্ডে ৫০০০ থাকলে ৯০০০ বিতরণ 400 দেয় — শরিয়াহ্‌ fund guard।"""
        from contextlib import contextmanager
        from api.core.security import get_current_user

        class FakeCursor:
            def execute(self, sql, params=None): self.sql = sql
            def fetchone(self):
                if "balance" in getattr(self, "sql", ""):
                    return {"balance": 5000.0}
                return {"id": 1, "name": "X"}
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

        import api.routers.extras_router as xr
        monkeypatch.setattr(xr, "get_db", fake_get_db)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": 1, "tenant_id": 1, "role": "admin", "type": "access",
        }
        try:
            r = client.post("/api/v1/zakat/distributions", json={
                "recipient_name": "কেউ", "amount": 9000,
            })
            assert r.status_code == 400
            assert "পর্যাপ্ত" in r.json()["detail"]
        finally:
            app.dependency_overrides.clear()


class TestStructuredLogging:

    def test_json_formatter_includes_context(self):
        import json as _json
        import logging
        from logging_config import JSONFormatter, set_request_context, clear_request_context

        rec = logging.LogRecord("t", logging.INFO, "f.py", 1, "hello", None, None)
        set_request_context(request_id="req-123", tenant_id=7)
        try:
            out = _json.loads(JSONFormatter().format(rec))
            assert out["msg"] == "hello"
            assert out["request_id"] == "req-123"
            assert out["tenant_id"] == 7
        finally:
            clear_request_context()

    def test_context_cleared_between_requests(self):
        from logging_config import (
            _request_id, set_request_context, clear_request_context,
        )
        set_request_context(request_id="abc")
        clear_request_context()
        assert _request_id.get() is None

    def test_response_carries_request_id_header(self):
        r = client.get("/api/health")
        assert "x-request-id" in {k.lower() for k in r.headers}


class TestQRTokenSecurity:

    def test_sign_verify_roundtrip(self):
        import qr_token
        tok = qr_token.sign(5, 1042)
        assert qr_token.verify(tok, 5) == 1042

    def test_forged_token_rejected(self):
        import qr_token
        tok = qr_token.sign(5, 1042)
        # শেষ অক্ষর বদলালে signature ভাঙে
        forged = tok[:-1] + ("0" if tok[-1] != "0" else "1")
        assert qr_token.verify(forged, 5) is None

    def test_cross_tenant_token_rejected(self):
        import qr_token
        tok = qr_token.sign(5, 1042)
        assert qr_token.verify(tok, 6) is None

    def test_plain_id_rejected(self):
        import qr_token
        assert qr_token.verify("STU-00042-001-TID5", 5) is None
        assert qr_token.verify("MQR1.5.1042.deadbeefdeadbeef", 5) is None

    def test_money_fields_are_decimal(self):
        """Fix #1 regression guard — টাকার field float নয়।"""
        from decimal import Decimal
        from api.models.schemas import (
            ZakatCollectionCreate, SalaryPayment,
        )
        z = ZakatCollectionCreate(amount="100.10")
        assert isinstance(z.amount, Decimal)
        s = SalaryPayment(month_name="January", year=2026, basic_salary="15000.50")
        assert isinstance(s.basic_salary, Decimal)
        # float artifact আসে না
        assert str(z.amount) == "100.10"
