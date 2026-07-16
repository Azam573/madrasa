"""
tests/test_payment_security.py — Payment Gateway Security Tests
HMAC signature verification, amount validation, idempotency।
"""
import pytest
import sys, os, json, hmac, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Test secret keys
TEST_BKASH_SECRET = "test_bkash_secret_key"
TEST_NAGAD_KEY    = "test_nagad_merchant_key"


def _make_bkash_sig(payload: dict, secret: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


class TestBkashCallbackVerification:

    def setup_method(self):
        os.environ["BKASH_APP_SECRET"] = TEST_BKASH_SECRET

    def test_valid_signature_accepted(self):
        import payment_gateway as pg
        payload = {"trxID": "TXN001", "amount": "500", "statusCode": "0000"}
        sig = _make_bkash_sig(payload, TEST_BKASH_SECRET)
        assert pg.verify_bkash_callback(payload, sig) is True

    def test_fake_signature_rejected(self):
        import payment_gateway as pg
        payload = {"trxID": "TXN001", "amount": "500"}
        assert pg.verify_bkash_callback(payload, "fake_signature_xyz") is False

    def test_tampered_payload_rejected(self):
        """Payload tamper করলে signature মিলবে না"""
        import payment_gateway as pg
        payload = {"trxID": "TXN001", "amount": "500"}
        sig = _make_bkash_sig(payload, TEST_BKASH_SECRET)
        # amount পরিবর্তন করলে signature invalid
        tampered = {"trxID": "TXN001", "amount": "50000"}
        assert pg.verify_bkash_callback(tampered, sig) is False

    def test_empty_signature_rejected(self):
        import payment_gateway as pg
        payload = {"trxID": "TXN001"}
        assert pg.verify_bkash_callback(payload, "") is False

    def test_timing_safe_comparison(self):
        """hmac.compare_digest ব্যবহার হচ্ছে কিনা verify"""
        import payment_gateway
        import inspect
        source = inspect.getsource(payment_gateway.verify_bkash_callback)
        assert "compare_digest" in source


class TestApiSchemaValidation:

    def test_password_min_8_chars(self):
        from api.models.schemas import LoginRequest
        with pytest.raises(Exception):
            LoginRequest(tenant_id=1, username="admin", password="short")

    def test_password_exactly_8_accepted(self):
        from api.models.schemas import LoginRequest
        req = LoginRequest(tenant_id=1, username="admin", password="12345678")
        assert req.password == "12345678"

    def test_voucher_negative_amount_rejected(self):
        from api.models.schemas import VoucherCreate
        with pytest.raises(Exception):
            VoucherCreate(
                enrollment_id=1, student_id=1,
                month_name="january", year=2024,
                amount=-500,
            )

    def test_voucher_zero_amount_rejected(self):
        from api.models.schemas import VoucherCreate
        with pytest.raises(Exception):
            VoucherCreate(
                enrollment_id=1, student_id=1,
                month_name="january", year=2024,
                amount=0,
            )

    def test_voucher_max_amount_enforced(self):
        from api.models.schemas import VoucherCreate
        with pytest.raises(Exception):
            VoucherCreate(
                enrollment_id=1, student_id=1,
                month_name="january", year=2024,
                amount=999_999,  # > 500_000 limit
            )

    def test_invalid_fund_type_rejected(self):
        from api.models.schemas import VoucherCreate
        with pytest.raises(Exception):
            VoucherCreate(
                enrollment_id=1, student_id=1,
                month_name="january", year=2024,
                amount=500, fund_type="invalid_fund",
            )

    def test_invalid_student_status_rejected(self):
        from api.models.schemas import StudentCreate
        with pytest.raises(Exception):
            StudentCreate(name="Test", gender="Unknown")

    def test_invalid_mobile_rejected(self):
        from api.models.schemas import StudentCreate
        with pytest.raises(Exception):
            StudentCreate(name="Test", mobile_no="12345")

    def test_valid_mobile_accepted(self):
        from api.models.schemas import StudentCreate
        s = StudentCreate(name="Test Student", mobile_no="01712345678")
        assert s.mobile_no == "01712345678"
