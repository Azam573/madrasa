"""
tests/test_auth.py — Authentication & Password Security Tests
Critical path: password hashing, verification, argon2 upgrade।
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestPasswordHashing:
    """argon2 password hashing tests"""

    def test_hash_returns_string(self):
        from auth import hash_password
        hashed = hash_password("TestPassword123")
        assert isinstance(hashed, str)
        assert len(hashed) > 20

    def test_hash_is_not_plaintext(self):
        from auth import hash_password
        hashed = hash_password("MyPassword")
        assert "MyPassword" not in hashed

    def test_two_hashes_are_different(self):
        """argon2 unique salt — একই password দুইবার hash = different result"""
        from auth import hash_password
        h1 = hash_password("SamePassword")
        h2 = hash_password("SamePassword")
        assert h1 != h2  # Different salt each time

    def test_verify_correct_password(self):
        from auth import hash_password, verify_password
        pw     = "CorrectPassword123"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_verify_wrong_password(self):
        from auth import hash_password, verify_password
        hashed = hash_password("CorrectPassword")
        assert verify_password("WrongPassword", hashed) is False

    def test_verify_empty_password(self):
        from auth import hash_password, verify_password
        hashed = hash_password("SomePassword")
        assert verify_password("", hashed) is False

    def test_legacy_demo_hash_correct_password(self):
        """Legacy demo hash backward compatibility"""
        from auth import verify_password, DEMO_LEGACY_HASH, DEMO_LEGACY_PASSWORD
        assert verify_password(DEMO_LEGACY_PASSWORD, DEMO_LEGACY_HASH) is True

    def test_legacy_demo_hash_wrong_password(self):
        from auth import verify_password, DEMO_LEGACY_HASH
        assert verify_password("wrong_password", DEMO_LEGACY_HASH) is False

    def test_sha256_hash_rejected(self):
        """পুরনো SHA-256 hash argon2 দিয়ে verify হবে না — নিরাপদ"""
        import hashlib
        from auth import verify_password
        sha256_hash = hashlib.sha256("password123".encode()).hexdigest()
        assert verify_password("password123", sha256_hash) is False

    def test_needs_rehash_legacy(self):
        """Legacy hash-এর needs_rehash False — migration আলাদাভাবে হয়"""
        from auth import needs_rehash, DEMO_LEGACY_HASH
        assert needs_rehash(DEMO_LEGACY_HASH) is False

    def test_needs_rehash_argon2(self):
        """Fresh argon2 hash-এর rehash দরকার নেই"""
        from auth import hash_password, needs_rehash
        hashed = hash_password("TestPassword")
        assert needs_rehash(hashed) is False


class TestRolePermissions:
    """RBAC permission tests"""

    def test_admin_has_all_permissions(self):
        from auth import can_access, ROLE_PERMISSIONS
        admin_pages = ROLE_PERMISSIONS.get("admin", [])
        assert "Finance" in admin_pages
        assert "User Management" in admin_pages
        assert "Settings" in admin_pages

    def test_staff_cannot_access_settings(self):
        from auth import can_access
        assert can_access("staff", "Settings") is False

    def test_teacher_cannot_access_finance(self):
        from auth import can_access
        assert can_access("teacher", "Finance") is False

    def test_accountant_can_access_finance(self):
        from auth import can_access
        assert can_access("accountant", "Finance") is True

    def test_unknown_role_has_no_access(self):
        from auth import can_access
        assert can_access("hacker", "Dashboard") is False

    def test_empty_role_has_no_access(self):
        from auth import can_access
        assert can_access("", "Finance") is False
