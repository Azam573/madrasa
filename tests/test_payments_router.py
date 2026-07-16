"""
tests/test_payments_router.py — Online Payments API tests

যাচাই করে:
1. Router সঠিকভাবে register হয়েছে (routes আছে)
2. Schema validation — gateway whitelist, callback_url https enforcement
3. Auth ছাড়া protected endpoint 401/403 দেয়
4. Callback endpoint auth-free কিন্তু unknown gateway 404 দেয়
5. Callback signature-fail হলে success=False (200 idempotent response)
6. Rate limit decorator প্রতিটি endpoint-এ আছে
"""
import inspect
import pytest
from fastapi.testclient import TestClient

import api.main as main_module
from api.main import app

client = TestClient(app, raise_server_exceptions=False)


# ── 1. Wiring ────────────────────────────────────────────────────

class TestRouterWiring:

    def test_routes_registered(self):
        # নতুন FastAPI-তে include_router lazy হয় (_IncludedRouter) —
        # তাই route object নয়, OpenAPI schema থেকে path যাচাই করা হচ্ছে
        paths = set(app.openapi()["paths"].keys())
        assert "/api/v1/payments/online/initiate" in paths
        assert "/api/v1/payments/online/callback/{gateway}" in paths
        assert "/api/v1/payments/online" in paths
        assert "/api/v1/payments/online/{payment_id}" in paths

    def test_all_endpoints_have_rate_limit(self):
        """slowapi decorator মানে function signature-এ request: Request থাকবে।"""
        import api.routers.payments_router as pr
        for fn in (pr.initiate_online_payment, pr.payment_gateway_callback,
                   pr.list_online_payments, pr.get_online_payment):
            src = inspect.getsource(fn)
            assert "request: Request" in src, f"{fn.__name__} missing Request param"
            assert "@limiter.limit" in src, f"{fn.__name__} missing rate limit"


# ── 2. Schema validation ─────────────────────────────────────────

class TestSchemas:

    def test_gateway_whitelist(self):
        from api.models.schemas import OnlinePaymentInitiate
        ok = OnlinePaymentInitiate(voucher_id=1, gateway="BKASH")
        assert ok.gateway == "bkash"  # normalize হয়

        with pytest.raises(ValueError):
            OnlinePaymentInitiate(voucher_id=1, gateway="paypal")

    def test_callback_url_https_only(self):
        from api.models.schemas import OnlinePaymentInitiate
        # https — allowed
        OnlinePaymentInitiate(voucher_id=1, gateway="nagad",
                              callback_url="https://app.example.com/cb")
        # localhost dev — allowed
        OnlinePaymentInitiate(voucher_id=1, gateway="nagad",
                              callback_url="http://localhost:8501/cb")
        # plain http — open redirect ঝুঁকি, reject
        with pytest.raises(ValueError):
            OnlinePaymentInitiate(voucher_id=1, gateway="nagad",
                                  callback_url="http://evil.example.com/cb")

    def test_voucher_id_positive(self):
        from api.models.schemas import OnlinePaymentInitiate
        with pytest.raises(ValueError):
            OnlinePaymentInitiate(voucher_id=0, gateway="bkash")


# ── 3. Auth enforcement ──────────────────────────────────────────

class TestAuth:

    def test_initiate_requires_auth(self):
        r = client.post("/api/v1/payments/online/initiate",
                        json={"voucher_id": 1, "gateway": "bkash"})
        assert r.status_code in (401, 403)

    def test_list_requires_auth(self):
        r = client.get("/api/v1/payments/online")
        assert r.status_code in (401, 403)

    def test_detail_requires_auth(self):
        r = client.get("/api/v1/payments/online/1")
        assert r.status_code in (401, 403)


# ── 4. Callback endpoint (public webhook) ────────────────────────

class TestCallback:

    def test_unknown_gateway_404(self):
        r = client.post("/api/v1/payments/online/callback/paypal", json={})
        assert r.status_code == 404

    def test_invalid_json_400(self):
        r = client.post(
            "/api/v1/payments/online/callback/bkash",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_bad_signature_rejected_gracefully(self, monkeypatch):
        """Forged callback → success=False, কিন্তু HTTP 200 (idempotent)।"""
        import payment_gateway as pg
        monkeypatch.setattr(
            pg, "handle_payment_callback",
            lambda gateway, payload, sig: (False, "Invalid signature"),
        )
        r = client.post(
            "/api/v1/payments/online/callback/bkash",
            json={"trxID": "FAKE123", "statusCode": "0000", "amount": "500"},
            headers={"X-Signature": "forged"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert "signature" in body["message"].lower()

    def test_valid_callback_success(self, monkeypatch):
        import payment_gateway as pg
        monkeypatch.setattr(
            pg, "handle_payment_callback",
            lambda gateway, payload, sig: (True, "Payment successful"),
        )
        r = client.post(
            "/api/v1/payments/online/callback/nagad",
            json={"issuerPaymentRefNo": "NGD1", "status": "Success",
                  "amount": "500", "orderId": "VCH-001-00001-260702"},
            headers={"X-Signature": "valid-sig"},
        )
        assert r.status_code == 200
        assert r.json()["success"] is True


# ── 5. Tenant-scoped behavior (dependency override দিয়ে) ─────────

class TestTenantScoping:

    def test_cross_tenant_voucher_404(self, monkeypatch):
        """
        অন্য tenant-এর voucher_id দিলে 404 — voucher exists কি না
        তা leak হয় না। DB layer mock করে যাচাই।
        """
        from api.core.security import get_tenant_id, require_role

        app.dependency_overrides[get_tenant_id] = lambda: 999

        # require_role একটি factory — override করতে হবে যে instance
        # route-এ ব্যবহৃত হয়েছে সেটি নয়, বরং সব call — তাই route-এর
        # dependant থেকে exact dependency ধরার বদলে DB mock করাই যথেষ্ট:
        # get_db → এমন cursor যার fetchone() None দেয় (voucher নেই)।
        class FakeCursor:
            def execute(self, *a, **k): pass
            def fetchone(self): return None
            def fetchall(self): return []
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class FakeConn:
            def cursor(self): return FakeCursor()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        from contextlib import contextmanager

        @contextmanager
        def fake_get_db():
            yield FakeConn()

        import api.routers.payments_router as pr
        monkeypatch.setattr(pr, "get_db", fake_get_db)

        # require_role bypass: current_user dependency-ও override দরকার —
        # payments_router-এ require_role("admin","staff","accountant")-এর
        # ফলে তৈরি nested dependency get_current_user-এ গিয়ে ঠেকে
        from api.core.security import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": 1, "tenant_id": 999, "role": "admin",
        }

        try:
            r = client.post(
                "/api/v1/payments/online/initiate",
                json={"voucher_id": 12345, "gateway": "bkash"},
            )
            assert r.status_code == 404
        finally:
            app.dependency_overrides.clear()
