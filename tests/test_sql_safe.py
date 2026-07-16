"""
tests/test_sql_safe.py — SQL Injection Prevention Tests
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestSafeTable:

    def test_valid_table_returns_name(self):
        from sql_safe import safe_table
        assert safe_table("students") == "students"
        assert safe_table("fee_vouchers") == "fee_vouchers"
        assert safe_table("attendance") == "attendance"

    def test_injection_attempt_raises(self):
        from sql_safe import safe_table
        with pytest.raises(ValueError):
            safe_table("students; DROP TABLE students")

    def test_unknown_table_raises(self):
        from sql_safe import safe_table
        with pytest.raises(ValueError):
            safe_table("evil_table")

    def test_empty_string_raises(self):
        from sql_safe import safe_table
        with pytest.raises(ValueError):
            safe_table("")

    def test_union_injection_raises(self):
        from sql_safe import safe_table
        with pytest.raises(ValueError):
            safe_table("students UNION SELECT password FROM app_users")

    def test_case_sensitive(self):
        """Table names case-sensitive — STUDENTS ≠ students"""
        from sql_safe import safe_table
        with pytest.raises(ValueError):
            safe_table("STUDENTS")


class TestBuildWhere:

    def test_no_optional_params(self):
        from sql_safe import build_where
        where, params = build_where(["tenant_id=%s"], {})
        assert where == "tenant_id=%s"
        assert params == []

    def test_with_optional_params(self):
        from sql_safe import build_where
        where, params = build_where(
            ["tenant_id=%s"],
            {"session_id": 3, "class_id": 5},
        )
        assert "session_id=%s" in where
        assert "class_id=%s" in where
        assert 3 in params
        assert 5 in params

    def test_none_values_skipped(self):
        from sql_safe import build_where
        where, params = build_where(
            ["tenant_id=%s"],
            {"session_id": None, "class_id": None},
        )
        assert "session_id" not in where
        assert params == []

    def test_empty_fixed_clauses(self):
        from sql_safe import build_where
        where, params = build_where([], {})
        assert where == "TRUE"

    def test_mixed_none_and_value(self):
        from sql_safe import build_where
        where, params = build_where(
            ["tenant_id=%s"],
            {"session_id": 1, "class_id": None},
        )
        assert "session_id=%s" in where
        assert "class_id" not in where
        assert 1 in params
