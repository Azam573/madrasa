"""
api/core/database.py — Connection Pool with psycopg2
ThreadedConnectionPool দিয়ে ১০০+ concurrent user সাপোর্ট।
"""

import os
import psycopg2
import psycopg2.extras
from psycopg2 import pool
from contextlib import contextmanager


# ── Connection Pool ──────────────────────────────────────────────
# minconn=5  → সবসময় ৫টি connection খোলা থাকবে
# maxconn=50 → একসাথে সর্বোচ্চ ৫০টি connection (৫০ concurrent users)

_pool: pool.ThreadedConnectionPool | None = None


def init_pool(dsn: str | None = None):
    global _pool
    if _pool is not None:
        return

    dsn = dsn or os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable not set.")

    _pool = pool.ThreadedConnectionPool(
        minconn=5,
        maxconn=50,
        dsn=dsn,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


@contextmanager
def get_db():
    """
    FastAPI dependency এবং context manager হিসেবে ব্যবহার করুন।

    Usage (FastAPI):
        @router.get("/students")
        def list_students(db=Depends(get_db)):
            with db.cursor() as cur:
                cur.execute("SELECT * FROM students")
                return cur.fetchall()

    Usage (context manager):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """
    global _pool
    if _pool is None:
        init_pool()

    conn = _pool.getconn()
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def close_pool():
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
