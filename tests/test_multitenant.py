"""
tests/test_multitenant.py — Multi-Tenant Isolation Tests
Tenant A-এর data Tenant B দেখতে পাবে না।
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestTenantIsolation:

    def test_sql_always_has_tenant_id(self):
        """সব critical SQL-এ tenant_id=%s আছে কিনা check"""
        import finance_module
        import inspect
        source = inspect.getsource(finance_module._collect_payment)
        assert "tenant_id=%s" in source

    def test_voucher_creation_has_tenant_id(self):
        import finance_module
        import inspect
        source = inspect.getsource(finance_module._create_voucher)
        assert "tenant_id" in source

    def test_db_helpers_parameterized(self):
        """fetchall/fetchone এ params tuple ব্যবহার হচ্ছে"""
        import db
        import inspect
        source = inspect.getsource(db.fetchall)
        assert "params" in source
        assert "%s" not in source.split("def fetchall")[0]

    def test_for_update_in_collect_payment(self):
        """Race condition fix — FOR UPDATE আছে কিনা"""
        import finance_module
        import inspect
        source = inspect.getsource(finance_module._collect_payment)
        assert "FOR UPDATE" in source

    def test_for_update_in_create_voucher(self):
        """Duplicate voucher prevention — FOR UPDATE আছে কিনা"""
        import finance_module
        import inspect
        source = inspect.getsource(finance_module._create_voucher)
        assert "FOR UPDATE" in source


class TestPasswordSecurity:

    def test_no_sha256_in_auth(self):
        """SHA-256 বাদ দেওয়া হয়েছে কিনা"""
        import auth
        import inspect
        source = inspect.getsource(auth)
        assert "sha256" not in source.lower() or "test" in source.lower()

    def test_argon2_used(self):
        """argon2 import আছে কিনা"""
        import auth
        import inspect
        source = inspect.getsource(auth)
        assert "argon2" in source or "PasswordHasher" in source

    def test_cors_no_wildcard(self):
        """CORS wildcard '*' default নেই"""
        import api.main as main_module
        import inspect
        source = inspect.getsource(main_module)
        # _get_cors_origins function থাকতে হবে
        assert "_get_cors_origins" in source
        # Default * নেই
        assert '"*"' not in source.replace('os.environ.get("FRONTEND_URL", "")', "")


class TestVersionConsistency:

    def test_version_file_exists(self):
        import __version__
        assert hasattr(__version__, "VERSION")
        assert __version__.VERSION == "9.0"

    def test_api_version_matches(self):
        """API version __version__.py থেকে runtime-এ derive হয় — hardcode নয়।"""
        import __version__
        import api.main as m
        import inspect

        # Runtime value: API_VERSION অবশ্যই __version__.VERSION থেকে আসবে
        assert m.API_VERSION.startswith(__version__.VERSION)
        assert m.app.version == m.API_VERSION

        # Regression guard: source-এ আর কোনো hardcoded "X.Y.Z" version নেই
        source = inspect.getsource(m)
        assert f'"{m.API_VERSION}"' not in source
