"""
sql_safe.py — SQL Injection Prevention Utilities  v9.0

সমস্যা: Table name ও column name parameterize করা যায় না psycopg2-তে।
সমাধান: Strict whitelist দিয়ে validate করা।

যেকোনো dynamic table/column name ব্যবহারের আগে এই module থেকে
validate করতে হবে। Whitelist-এ না থাকলে ValueError raise হবে।

ব্যবহার:
    from sql_safe import safe_table, safe_clauses

    # Table name validate
    tbl = safe_table("students")  # OK
    tbl = safe_table("evil; DROP TABLE")  # ValueError!

    # Dynamic WHERE clause তৈরি
    sql, params = safe_clauses("fee_vouchers", {"tenant_id": 1, "year": 2024})
"""

from typing import Any

# ── Allowed Tables ────────────────────────────────────────────────────────────
ALLOWED_TABLES: frozenset[str] = frozenset({
    "tenants",
    "academic_sessions",
    "classes",
    "students",
    "student_enrollments",
    "fee_vouchers",
    "fee_payments",
    "subjects",
    "exams",
    "marks_distribution",
    "student_marks",
    "attendance",
    "app_users",
    "teachers",
    "teacher_assignments",
    "teacher_attendance",
    "notices",
    "notifications",
    "audit_logs",
    "expenses",
    "expense_categories",
    "hostel_rooms",
    "hostel_allocations",
    "library_books",
    "library_issues",
    "online_payments",
    "zakat_collections",
    "zakat_disbursements",
    "exam_questions",
    "exam_attempts",
    "timetable",
    "documents",
    "branches",
    "backup_logs",
    "holidays",
    "qr_attendance_logs",
    "online_applications",
    "whatsapp_logs",
    "sms_logs",
    "security_otp",
    "totp_secrets",
    "cache_entries",
})

# ── Allowed Columns (tenant isolation column names) ───────────────────────────
ALLOWED_TENANT_COLS: frozenset[str] = frozenset({
    "tenant_id",
    "id",
})


def safe_table(table_name: str) -> str:
    """
    Table name whitelist-এ আছে কিনা check করে।
    না থাকলে ValueError raise করে।

    >>> safe_table("students")
    'students'
    >>> safe_table("evil; DROP TABLE students")
    ValueError: ...
    """
    if table_name not in ALLOWED_TABLES:
        raise ValueError(
            f"SQL Injection প্রতিরোধ: '{table_name}' অনুমোদিত table নয়। "
            f"Allowed: {sorted(ALLOWED_TABLES)}"
        )
    return table_name


def safe_col(col_name: str) -> str:
    """
    Column name whitelist-এ আছে কিনা check করে।
    """
    if col_name not in ALLOWED_TENANT_COLS:
        raise ValueError(
            f"SQL Injection প্রতিরোধ: '{col_name}' অনুমোদিত column নয়।"
        )
    return col_name


def build_where(fixed_clauses: list[str], optional: dict[str, Any]) -> tuple[str, list]:
    """
    Dynamic WHERE clause নিরাপদে তৈরি করে।

    fixed_clauses: সবসময় থাকে এমন clause ["tenant_id=%s", "status='active'"]
    optional: শর্তসাপেক্ষ filter {"session_id": 3, "class_id": None}
              None মান skip হয়।

    Returns: (where_string, params_list)

    উদাহরণ:
        where, params = build_where(
            ["s.tenant_id=%s", "s.status='active'"],
            {"e.session_id": session_id, "e.class_id": class_id}
        )
        sql = f"SELECT ... WHERE {where}"
        fetchall(sql, [tid] + params)
    """
    clauses = list(fixed_clauses)
    params: list[Any] = []

    for col_expr, val in optional.items():
        if val is not None:
            clauses.append(f"{col_expr}=%s")
            params.append(val)

    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


def table_exists_safe(table_name: str) -> bool:
    """
    Table আছে কিনা information_schema দিয়ে check করে।
    f-string দিয়ে SELECT 1 FROM {table} করার পরিবর্তে এটি ব্যবহার করুন।
    """
    from db import fetchone
    # table_name আগেই whitelist-এ আছে কিনা check
    if table_name not in ALLOWED_TABLES:
        return False
    try:
        row = fetchone(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
            (table_name,),
        )
        return row is not None
    except Exception:
        return False
