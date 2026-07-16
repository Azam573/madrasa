"""
tests/test_branding.py — Branding & Print Documents tests
"""
import base64
import inspect
import pytest
from fastapi.testclient import TestClient

from api.main import app
import branding as br

client = TestClient(app, raise_server_exceptions=False)

# ১×১ pixel বৈধ PNG (magic bytes সহ)
_PNG_1PX = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d0e660000000049454e44ae426082"
)).decode()


class TestImageValidation:

    def test_valid_png_accepted(self):
        ok, mime, err = br.validate_image_b64(_PNG_1PX)
        assert ok and mime == "image/png"

    def test_fake_extension_rejected(self):
        """Content যেটাই হোক — magic bytes ছাড়া reject (spoofing guard)।"""
        fake = base64.b64encode(b"<script>alert(1)</script> not an image").decode()
        ok, mime, err = br.validate_image_b64(fake)
        assert not ok

    def test_svg_rejected(self):
        """SVG-তে embedded JS থাকতে পারে — নিষিদ্ধ।"""
        svg = base64.b64encode(
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        ).decode()
        ok, _, _ = br.validate_image_b64(svg)
        assert not ok

    def test_oversize_rejected(self):
        big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (br.MAX_IMAGE_BYTES + 10)).decode()
        ok, _, err = br.validate_image_b64(big)
        assert not ok and "KB" in err

    def test_invalid_base64_rejected(self):
        ok, _, _ = br.validate_image_b64("!!!not-base64!!!")
        assert not ok


class TestXSSGuard:

    def test_branding_text_escaped_in_receipt(self):
        """মাদ্রাসার নামে <script> থাকলে HTML-এ escape হয়ে যায়।"""
        brand = dict(br._DEFAULTS)
        brand.update({
            "madrasa_name": "<script>alert('xss')</script>",
            "address": "", "phone": "", "email": "",
            "receipt_footer": "<img src=x onerror=alert(1)>",
        })
        html = br.receipt_html(
            brand,
            student={"name": "X", "father_name": "Y",
                     "class_name": "Z", "roll_no": 1},
            payment={"receipt_no": "R1", "payment_date": "2026-01-01",
                     "payment_method": "cash", "amount_paid": 100},
            voucher={"voucher_no": "V1", "month_name": "Jan", "year": 2026},
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        # Executable ট্যাগ হিসেবে নেই — escaped টেক্সট হিসেবে থাকা নিরাপদ
        assert "<img src=x onerror" not in html
        assert "&lt;img src=x onerror" in html

    def test_tc_text_escaped(self):
        brand = dict(br._DEFAULTS)
        brand.update({"madrasa_name": "Test", "address": "", "phone": "", "email": ""})
        html = br.tc_html(
            brand,
            student={"name": "<b>মালিসিয়াস</b>", "father_name": None},
            tc={"tc_number": "TC-1", "issue_date": "2026-01-01",
                "last_class": None, "last_session": None, "conduct": None,
                "attendance_pct": None, "reason": "<script>x</script>",
                "remarks": None},
        )
        assert "<script>x</script>" not in html


class TestUpsertValidation:

    class _Cur:
        def __init__(self): self.executed = []
        def execute(self, sql, params=None): self.executed.append(sql)
        def fetchone(self): return None

    def test_color_validation(self):
        with pytest.raises(ValueError):
            br.upsert_branding(self._Cur(), 1, {"primary_color": "red"})
        with pytest.raises(ValueError):
            br.upsert_branding(self._Cur(), 1, {"primary_color": "#GGGGGG"})

    def test_non_whitelisted_field_ignored(self):
        """logo_base64 upsert দিয়ে সেট করা যায় না — শুধু set_image দিয়ে।"""
        cur = self._Cur()
        updated = br.upsert_branding(cur, 1, {
            "logo_base64": "hack", "tenant_id": 999, "name_english": "OK Madrasa",
        })
        assert updated == ["name_english"]

    def test_established_year_bounds(self):
        with pytest.raises(ValueError):
            br.upsert_branding(self._Cur(), 1, {"established_year": 3000})


class TestAPIWiring:

    def test_routes_registered(self):
        paths = set(app.openapi()["paths"].keys())
        for p in (
            "/api/v1/settings/branding",
            "/api/v1/settings/branding/logo",
            "/api/v1/settings/branding/signature",
            "/api/v1/print/receipt/{payment_id}",
            "/api/v1/print/tc/{tc_id}",
        ):
            assert p in paths, f"Missing route: {p}"

    def test_branding_write_admin_only(self):
        import api.routers.branding_router as brr
        for fn in (brr.update_branding_settings, brr.upload_logo,
                   brr.upload_signature):
            assert 'require_role("admin")' in inspect.getsource(fn)

    @pytest.mark.parametrize("method,path,payload", [
        ("get", "/api/v1/settings/branding", None),
        ("put", "/api/v1/settings/branding", {"name_english": "X"}),
        ("post", "/api/v1/settings/branding/logo", {"image_base64": "aGVsbG8="}),
        ("get", "/api/v1/print/receipt/1", None),
        ("get", "/api/v1/print/tc/1", None),
    ])
    def test_requires_auth(self, method, path, payload):
        fn = getattr(client, method)
        r = fn(path, json=payload) if payload else fn(path)
        assert r.status_code in (401, 403)

    def test_get_branding_never_returns_blob(self):
        """GET response-এ base64 blob নেই — শুধু has_logo flag।"""
        import api.routers.branding_router as brr
        src = inspect.getsource(brr.get_branding_settings)
        assert 'pop("logo_base64"' in src and "has_logo" in src
