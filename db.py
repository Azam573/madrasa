"""
db.py — Database connection pool and schema initialization for Smart Madrasa ERP v9.0
Uses psycopg2 ThreadedConnectionPool (min=5, max=30). No ORM.
মোট বাজেট: api/core/database.py-এর maxconn=50 + workers/tasks.py-এর maxconn=5 +
এই pool-এর maxconn=30 = 85, Postgres-এর ডিফল্ট max_connections=100-এর নিচে
(admin/psql-এর জন্য ~15 কানেকশন মার্জিন রেখে)।

পরিবর্তন (v9.0):
- get_connection() → এখন ThreadedConnectionPool থেকে connection নেয়
- release_connection() → pool-এ connection ফেরত দেয়
- fetchall / fetchone / execute — সব pool-aware
- _get_pool() → lazy init, thread-safe singleton
"""

import os
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool
import streamlit as st

# ---------------------------------------------------------------------------
# Connection Pool — Singleton, Thread-Safe
# ---------------------------------------------------------------------------

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _build_dsn() -> str | None:
    """Streamlit secrets বা env থেকে DATABASE_URL পড়ে।"""
    try:
        if hasattr(st, "secrets") and "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass
    return os.environ.get("DATABASE_URL")


def _cfg(key: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool | None:
    """
    Pool lazy-init করে। প্রথম call-এ তৈরি হয়, পরে singleton return করে।
    Thread-safe lock দিয়ে race condition এড়ানো হয়েছে।
    """
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:   # double-check after acquiring lock
            return _pool
        try:
            dsn = _build_dsn()
            if dsn:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=5,
                    maxconn=30,
                    dsn=dsn,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
            else:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=5,
                    maxconn=30,
                    host=_cfg("DB_HOST", "localhost"),
                    port=_cfg("DB_PORT", "5432"),
                    dbname=_cfg("DB_NAME", "madrasa_erp"),
                    user=_cfg("DB_USER", "postgres"),
                    password=_cfg("DB_PASSWORD", ""),
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
        except Exception as e:
            st.error(f"Connection pool তৈরি ব্যর্থ: {e}")
            return None
    return _pool


def get_connection():
    """
    Pool থেকে একটি connection নেয়।
    ব্যবহার শেষে অবশ্যই release_connection() বা context manager দিয়ে ফেরত দিতে হবে।
    """
    pool = _get_pool()
    if not pool:
        return None
    try:
        conn = pool.getconn()
        conn.autocommit = False
        return conn
    except psycopg2.pool.PoolError as e:
        st.error(f"Connection pool exhausted: {e}")
        return None
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        return None


def release_connection(conn):
    """Connection pool-এ ফেরত দেয়।"""
    if conn is None:
        return
    pool = _get_pool()
    if pool:
        try:
            pool.putconn(conn)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Schema Bootstrap
# ---------------------------------------------------------------------------

def _fallback_dsn() -> str:
    """DATABASE_URL না থাকলে individual DB_* settings জোড়া দিয়ে DSN বানায়।"""
    return (
        f"postgresql://{_cfg('DB_USER', 'postgres')}:{_cfg('DB_PASSWORD', '')}"
        f"@{_cfg('DB_HOST', 'localhost')}:{_cfg('DB_PORT', '5432')}/{_cfg('DB_NAME', 'madrasa_erp')}"
    )


def bootstrap_schema() -> bool:
    """
    Alembic migration history head পর্যন্ত চালায় — schema-র একমাত্র
    source of truth (migrations/versions/)। বারবার চালানো safe।
    Production-এ DISABLE_BOOTSTRAP=true env set করলে skip করবে (migration
    তখন deploy pipeline থেকে আলাদাভাবে চালানো হয়)।
    """
    if os.environ.get("DISABLE_BOOTSTRAP", "").lower() == "true":
        return True

    try:
        from alembic.config import Config
        from alembic import command

        base = os.path.dirname(__file__)
        # alembic.ini-এর path pass করা হয় না ইচ্ছাকৃতভাবে — Alembic-এর
        # ConfigParser system locale encoding দিয়ে ini file পড়ে (Windows-এ
        # cp1252), যেটা এই ini-র বাংলা কমেন্টে UnicodeDecodeError দেয়।
        # খালি Config() ব্যবহার করলে in-memory parser তৈরি হয়, কোনো ফাইল
        # পড়ে না — logging/config file ছাড়াই migration চালানোর জন্য যথেষ্ট।
        cfg = Config()
        cfg.set_main_option("script_location", os.path.join(base, "migrations"))
        # Fix: cfg.set_main_option("sqlalchemy.url", ...) round-trips the DSN
        # through ConfigParser, which crashes on a literal "%" (e.g. "%40"
        # for "@" in a real managed-Postgres password like Supabase's) —
        # same bug already fixed in migrations/env.py. env.py reads the DSN
        # straight from this env var instead, so set that here rather than
        # ever handing the raw DSN to ConfigParser.
        os.environ["DATABASE_URL"] = _build_dsn() or _fallback_dsn()
        command.upgrade(cfg, "head")
        return True
    except Exception as e:
        st.error(f"Schema bootstrap failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Convenience helpers — সব pool-aware
# ---------------------------------------------------------------------------

def fetchall(sql: str, params: tuple = ()) -> list:
    conn = get_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception as e:
        st.error(f"Query error: {e}")
        return []
    finally:
        release_connection(conn)


def fetchone(sql: str, params: tuple = ()):
    conn = get_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    except Exception as e:
        st.error(f"Query error: {e}")
        return None
    finally:
        release_connection(conn)


def execute(sql: str, params: tuple = ()):
    """INSERT/UPDATE/DELETE চালায়। RETURNING row বা True/False return করে।"""
    conn = get_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            try:
                return cur.fetchone()
            except Exception:
                return True
    except Exception as e:
        conn.rollback()
        st.error(f"Execute error: {e}")
        return None
    finally:
        release_connection(conn)
