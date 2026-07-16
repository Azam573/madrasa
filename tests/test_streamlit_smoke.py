"""
tests/test_streamlit_smoke.py — Streamlit app (app.py + modules) smoke tests

Real PostgreSQL, not mocked — same convention as test_integration_e2e.py
(INTEGRATION_DATABASE_URL env var; skipped entirely if unset).

Why this file exists: every other test in tests/ covers the parallel FastAPI
`api/` layer only, using a mocked db.py. The actual user-facing product —
app.py + 40+ Streamlit modules — had zero automated coverage, which is why
these three real bugs were never caught by CI:
  1. Login page querying `tenants` before schema bootstrap had run.
  2. `student_portal.py` selecting an invalid SQL column (e.enrollment_id).
  3. `conn.close()` instead of release_connection() slowly exhausting the pool.

The nav-page crawl runs each page visit in its own subprocess (see
_streamlit_page_smoke.py) — AppTest's internal widget-state tracking was
found to accumulate stale entries across many sequential reruns on one
long-lived instance, and separately, creating a second fresh AppTest
instance later in the same process trips "Forms cannot be nested in other
forms". Both are testing-harness limitations, not product bugs (the real
app works fine — verified manually via a real browser); process-per-page
isolation sidesteps both.

লোকালে চালাতে:
    export INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/madrasa_erp
    pytest tests/test_streamlit_smoke.py -v
"""
import os
import sys

import pytest

INTEGRATION_URL = os.environ.get("INTEGRATION_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not INTEGRATION_URL,
    reason="INTEGRATION_DATABASE_URL সেট নেই — Streamlit smoke tests skip",
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="module", autouse=True)
def _fresh_schema():
    """
    পুরো public schema ড্রপ করে খালি করে দেয়, যাতে bootstrap ordering সত্যিকারের
    ফাঁকা DB-তে যাচাই হয় (টেবিল আগে থেকে থাকলে ভুল ordering-ও কোনো এরর দেখাবে না)।
    """
    import psycopg2

    os.environ["DATABASE_URL"] = INTEGRATION_URL
    conn = psycopg2.connect(INTEGRATION_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.close()
    yield


def _seed_tenant_and_admin():
    """bootstrap_schema() ইতিমধ্যে migration head পর্যন্ত চালানোর পরেই কল করতে হবে।"""
    import psycopg2
    import psycopg2.extras
    from auth import hash_password

    conn = psycopg2.connect(INTEGRATION_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (madrasa_name, slug) VALUES (%s,%s) RETURNING id",
            ("Smoke Test Madrasa", f"smoke-{os.getpid()}"),
        )
        tid = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO app_users (tenant_id, username, password_hash,
                                      role, full_name, is_active, language)
               VALUES (%s,%s,%s,'admin','Smoke Admin',TRUE,'bn') RETURNING id""",
            (tid, "smoke_admin", hash_password("Str0ng!Pass1")),
        )
        cur.execute(
            """INSERT INTO academic_sessions (tenant_id, session_name, is_active)
               VALUES (%s,'2026',TRUE)""",
            (tid,),
        )
        cur.execute(
            """INSERT INTO classes (tenant_id, class_name, class_numeric)
               VALUES (%s,'Hifz-1',1)""",
            (tid,),
        )
    conn.close()
    return {"tenant_id": tid, "username": "smoke_admin", "password": "Str0ng!Pass1"}


def test_fresh_db_bootstrap_before_login():
    """
    Regression test: app.py must run bootstrap_schema() (Alembic migrations)
    BEFORE the login page queries `tenants` — against a genuinely empty
    schema (see _fresh_schema fixture). The old bug crashed here with
    psycopg2.errors.UndefinedTable: relation "tenants" does not exist.
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(os.path.join(ROOT, "app.py"))
    at.run(timeout=60)
    assert not at.exception, f"login page raised on a fresh DB: {at.exception}"
    assert at.session_state["schema_ready"] is True
    assert len(at.text_input) == 0, "login form shouldn't render with zero tenants"


def test_all_nav_pages_render_without_exception():
    """
    Regression test: log in, then visit every registered route (each in its
    own subprocess — see module docstring). Broad net — this class of test
    is exactly what would have caught the e.enrollment_id SQL-column typo in
    student_portal.py.
    """
    import subprocess

    import app as app_module

    creds = _seed_tenant_and_admin()

    helper = os.path.join(ROOT, "tests", "_streamlit_page_smoke.py")
    env = {**os.environ, "DATABASE_URL": INTEGRATION_URL}

    # "Live Dashboard" auto-refreshes by default (time.sleep(15) + st.rerun()
    # in realtime_dashboard.py) — a real, intentional feature, but it never
    # returns control to a synchronous AppTest .run() call, so it can't be
    # smoke-tested this way.
    SKIP_PAGES = {"Live Dashboard"}

    failures = {}
    for page_name in app_module.ROUTES:
        if page_name in SKIP_PAGES:
            continue
        proc = subprocess.run(
            [sys.executable, helper, creds["username"], creds["password"], page_name],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
        )
        out = proc.stdout.strip().splitlines()
        last_line = out[-1] if out else ""
        if not last_line.startswith("OK"):
            failures[page_name] = last_line or proc.stderr[-500:]

    assert not failures, f"pages raised exceptions: {failures}"


def test_connection_pool_survives_sequential_load():
    """
    Regression test for the conn.close() pool-leak bug: many sequential
    get_connection()/release_connection() cycles must not exhaust the pool.
    """
    import psycopg2.pool
    import db

    N = 60
    errors = []
    for i in range(N):
        conn = db.get_connection()
        if conn is None:
            errors.append(f"iteration {i}: get_connection() returned None")
            continue
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        except psycopg2.pool.PoolError as e:
            errors.append(f"iteration {i}: {e}")
        finally:
            db.release_connection(conn)

    assert not errors, f"connection pool errors after {N} sequential cycles: {errors}"
