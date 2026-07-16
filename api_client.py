"""
api_client.py — Streamlit → FastAPI HTTP Client
Streamlit frontend থেকে FastAPI backend-এ call করার wrapper।
Connection pool-এর সুবিধা নিতে সব DB call এখান থেকে করুন।
"""

import os
import streamlit as st
import httpx
from typing import Any
from error_handler import safe_db_error

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000/api/v1")
TIMEOUT  = 30.0


def _headers() -> dict:
    token = st.session_state.get("api_token")
    if token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}


def _handle(resp: httpx.Response) -> dict:
    if resp.status_code == 401:
        st.session_state.pop("logged_in", None)
        st.session_state.pop("api_token", None)
        st.warning("Session শেষ। আবার লগইন করুন।")
        st.rerun()
    try:
        return resp.json()
    except Exception:
        return {"success": False, "error": resp.text}


class MadrasaAPIClient:
    """
    FastAPI backend-এর সাথে communicate করার client।

    Usage:
        api = MadrasaAPIClient()
        students = api.get("/students", params={"page": 1})
        api.post("/attendance/bulk", json={"date": "2024-01-01", "records": [...]})
    """

    def get(self, path: str, params: dict = None) -> dict:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.get(f"{API_BASE}{path}", headers=_headers(), params=params or {})
            return _handle(resp)
        except httpx.ConnectError:
            return {"success": False, "error": "API server সংযুক্ত নেই।"}
        except Exception as ex:
            return {"success": False, "error": safe_db_error(ex)}

    def post(self, path: str, json: Any = None, data: dict = None) -> dict:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.post(
                    f"{API_BASE}{path}", headers=_headers(), json=json, data=data
                )
            return _handle(resp)
        except httpx.ConnectError:
            return {"success": False, "error": "API server সংযুক্ত নেই।"}
        except Exception as ex:
            return {"success": False, "error": safe_db_error(ex)}

    def patch(self, path: str, json: Any = None) -> dict:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.patch(f"{API_BASE}{path}", headers=_headers(), json=json)
            return _handle(resp)
        except Exception as ex:
            return {"success": False, "error": safe_db_error(ex)}

    def delete(self, path: str) -> dict:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.delete(f"{API_BASE}{path}", headers=_headers())
            return _handle(resp)
        except Exception as ex:
            return {"success": False, "error": safe_db_error(ex)}

    # ── Auth ────────────────────────────────────────────────────
    def login(self, tenant_id: int, username: str, password: str) -> dict:
        return self.post("/auth/login", json={
            "tenant_id": tenant_id,
            "username":  username,
            "password":  password,
        })

    def me(self) -> dict:
        return self.get("/auth/me")

    # ── Students ────────────────────────────────────────────────
    def list_students(self, page=1, per_page=20, class_id=None,
                      status="active", search=None) -> dict:
        params = {"page": page, "per_page": per_page, "status": status}
        if class_id: params["class_id"] = class_id
        if search:   params["search"]   = search
        return self.get("/students", params=params)

    def get_student(self, student_id: int) -> dict:
        return self.get(f"/students/{student_id}")

    def create_student(self, data: dict) -> dict:
        return self.post("/students", json=data)

    def activate_student(self, student_id: int, roll_no: int, enrollment_id: int) -> dict:
        return self.patch(
            f"/students/{student_id}/activate",
            json={"roll_no": roll_no, "enrollment_id": enrollment_id},
        )

    # ── Finance ─────────────────────────────────────────────────
    def finance_summary(self, year: int = None) -> dict:
        params = {}
        if year: params["year"] = year
        return self.get("/finance/summary", params=params)

    def list_vouchers(self, student_id=None, status=None, year=None) -> dict:
        params = {}
        if student_id: params["student_id"] = student_id
        if status:     params["status"]      = status
        if year:       params["year"]        = year
        return self.get("/finance/vouchers", params=params)

    def create_voucher(self, data: dict) -> dict:
        return self.post("/finance/vouchers", json=data)

    def collect_payment(self, voucher_id: int, amount: float,
                        method: str = "cash", notes: str = "") -> dict:
        return self.post("/finance/payments", json={
            "voucher_id":     voucher_id,
            "amount_paid":    amount,
            "payment_method": method,
            "notes":          notes,
        })

    # ── Attendance ──────────────────────────────────────────────
    def get_class_attendance(self, class_id: int, att_date: str,
                              session_id: int = None) -> dict:
        params = {"att_date": att_date}
        if session_id: params["session_id"] = session_id
        return self.get(f"/attendance/class/{class_id}", params=params)

    def save_bulk_attendance(self, att_date: str, records: list) -> dict:
        return self.post("/attendance/bulk", json={
            "date":    att_date,
            "records": records,
        })

    def get_attendance_summary(self, enrollment_id: int) -> dict:
        return self.get(f"/attendance/student/{enrollment_id}/summary")

    # ── Academics ───────────────────────────────────────────────
    def save_bulk_marks(self, exam_id: int, marks: list) -> dict:
        return self.post("/academics/marks/bulk", json={
            "exam_id": exam_id,
            "marks":   marks,
        })

    def get_exam_results(self, exam_id: int, class_id: int = None) -> dict:
        params = {}
        if class_id: params["class_id"] = class_id
        return self.get(f"/academics/results/{exam_id}", params=params)

    # ── Health ──────────────────────────────────────────────────
    def health(self) -> dict:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{API_BASE.replace('/v1','')}/health")
            return resp.json()
        except Exception:
            return {"status": "unreachable"}


# Singleton instance
api = MadrasaAPIClient()
