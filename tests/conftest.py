"""
tests/conftest.py — Pytest fixtures for Smart Madrasa ERP v9.0
DB mock, test tenant, test user তৈরি করে।
"""
import pytest
import sys
import os

# Project root path যোগ
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def mock_db(monkeypatch):
    """DB calls mock করে — real DB ছাড়াই test চলে।"""
    def fake_fetchone(sql, params=()):
        return None
    def fake_fetchall(sql, params=()):
        return []
    def fake_execute(sql, params=()):
        return True
    def fake_get_connection():
        return None

    monkeypatch.setattr("db.fetchone",       fake_fetchone)
    monkeypatch.setattr("db.fetchall",       fake_fetchall)
    monkeypatch.setattr("db.execute",        fake_execute)
    monkeypatch.setattr("db.get_connection", fake_get_connection)
    return True


@pytest.fixture
def sample_tenant():
    return {"id": 1, "madrasa_name": "Test Madrasa", "slug": "test"}


@pytest.fixture
def sample_user():
    from auth import hash_password
    return {
        "id":            1,
        "tenant_id":     1,
        "username":      "testadmin",
        "password_hash": hash_password("SecurePass123"),
        "role":          "admin",
        "full_name":     "Test Admin",
        "is_active":     True,
    }
