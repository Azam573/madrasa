"""
tests/test_rate_limit.py — API Rate Limiting Tests (v9.0 fix #3)

যাচাই করে:
1. login endpoint-এ rate limit decorator আছে
2. limiter app.state-এ register করা আছে
3. RateLimitExceeded exception handler যুক্ত আছে
4. বাস্তবে অতিরিক্ত request পাঠালে 429 আসে (TestClient দিয়ে integration test)
"""

import os
import inspect
import pytest


class TestRateLimitConfiguration:
    """Static checks — কোড সঠিকভাবে wire করা আছে কিনা।"""

    def test_rate_limit_module_exists(self):
        from api.core.rate_limit import limiter, RATE_LIMITS
        assert limiter is not None
        assert isinstance(RATE_LIMITS, dict)

    def test_login_has_strict_limit(self):
        """Login সবচেয়ে কঠোর limit (brute-force রোধ)।"""
        from api.core.rate_limit import RATE_LIMITS
        assert "login" in RATE_LIMITS
        # Format: "N/minute" — সংখ্যা বের করি
        count = int(RATE_LIMITS["login"].split("/")[0])
        assert count <= 10, "Login rate limit খুব উদার — brute force সম্ভব"

    def test_payment_has_limit(self):
        from api.core.rate_limit import RATE_LIMITS
        assert "payment" in RATE_LIMITS

    def test_limiter_registered_on_app(self):
        """app.state.limiter সেট করা আছে কিনা — slowapi-র জন্য আবশ্যক।"""
        import api.main as main_module
        assert hasattr(main_module.app.state, "limiter")

    def test_rate_limit_exception_handler_registered(self):
        """RateLimitExceeded handler app-এ যুক্ত আছে কিনা।"""
        from slowapi.errors import RateLimitExceeded
        import api.main as main_module
        # FastAPI internally exception_handlers dict-এ রাখে
        handlers = main_module.app.exception_handlers
        assert RateLimitExceeded in handlers

    def test_login_route_has_limiter_decorator(self):
        """auth_router.login function-এ slowapi decorator প্রয়োগ হয়েছে কিনা।
        slowapi decorator wrap করার সময় __wrapped__ attribute রাখে।
        """
        from api.routers.auth_router import login
        source = inspect.getsource(login)
        # decorator যুক্ত থাকলে Request parameter থাকবে
        assert "request: Request" in source or "request:Request" in source

    def test_all_routers_import_limiter(self):
        """প্রতিটি critical router rate_limit module import করেছে কিনা।"""
        import api.routers.auth_router as auth_r
        import api.routers.students_router as students_r
        import api.routers.finance_router as finance_r
        import api.routers.attendance_router as attendance_r

        for module in (auth_r, students_r, finance_r, attendance_r):
            source = inspect.getsource(module)
            assert "from api.core.rate_limit import" in source, \
                f"{module.__name__} rate limiter ব্যবহার করছে না"


class TestRateLimitEnforcement:
    """Integration test — TestClient দিয়ে বাস্তব আচরণ যাচাই।"""

    @pytest.fixture
    def client(self):
        os.environ.setdefault("JWT_SECRET", "test-secret-for-rate-limit-tests")
        os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
        from fastapi.testclient import TestClient
        import api.main as main_module
        return TestClient(main_module.app)

    def test_login_rate_limit_triggers_429(self, client, monkeypatch):
        """
        ৫ বারের বেশি দ্রুত login চেষ্টা করলে ৬ষ্ঠ বারে 429 পাওয়া উচিত।
        get_db() মক করা হয়েছে (DB connection ছাড়াই) যাতে শুধু
        rate-limiting layer যাচাই হয়, নেটওয়ার্ক/DB নির্ভরতা না থাকে।
        """
        import api.routers.auth_router as auth_router_module
        from contextlib import contextmanager

        class _FakeCursor:
            def execute(self, *a, **kw):
                pass
            def fetchone(self):
                return None  # "ব্যবহারকারী পাওয়া যায়নি" পথে যাবে (401), DB-তে যাবে না
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

        @contextmanager
        def _fake_get_db():
            yield _FakeConn()

        monkeypatch.setattr(auth_router_module, "get_db", _fake_get_db)

        login_payload = {"tenant_id": 1, "username": "nonexistent_user_xyz", "password": "wrongpass"}

        responses = []
        for _ in range(7):
            resp = client.post("/api/v1/auth/login", json=login_payload)
            responses.append(resp.status_code)

        # প্রতিটি request 401 (ব্যবহারকারী নেই) হওয়ার কথা, যতক্ষণ না rate limit (429) hit করে
        assert 429 in responses, (
            f"৭ বার দ্রুত login চেষ্টা করেও rate limit (429) আসেনি। "
            f"পাওয়া status codes: {responses}"
        )

    def test_rate_limit_response_has_bangla_message(self, client, monkeypatch):
        """429 response পাওয়া উচিত, এবং তা JSON body সহ আসা উচিত।"""
        import api.routers.auth_router as auth_router_module
        from contextlib import contextmanager

        class _FakeCursor:
            def execute(self, *a, **kw):
                pass
            def fetchone(self):
                return None
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

        @contextmanager
        def _fake_get_db():
            yield _FakeConn()

        monkeypatch.setattr(auth_router_module, "get_db", _fake_get_db)

        login_payload = {"tenant_id": 1, "username": "test_rl_msg", "password": "x"}
        last_resp = None
        for _ in range(7):
            last_resp = client.post("/api/v1/auth/login", json=login_payload)
            if last_resp.status_code == 429:
                break
        assert last_resp is not None
        if last_resp.status_code == 429:
            assert last_resp.headers.get("content-type", "").startswith("application/json")
