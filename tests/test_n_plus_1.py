"""
tests/test_n_plus_1.py — N+1 Query Regression Test (v9.0 fix #5)

সমস্যা: branch_module.py-র render() ফাংশনে একটা for-loop-এর ভেতরে
_branch_stats(tenant_id) কল হতো, যা নিজে ৩টা DB query চালায়। N tenant
থাকলে 3×N query চলতো (overview ট্যাব) + আরও 3×N (compare ট্যাব)।

সমাধান: _branch_stats_bulk(tenant_ids) — সব tenant-এর জন্য ঠিক ৩টা
GROUP BY query, tenant সংখ্যা নির্বিশেষে constant।

এই test বাস্তব PostgreSQL-এর বিপরীতে চালিয়ে প্রমাণ করে:
  ১. ডেটা সঠিক থাকে (bulk ও per-tenant ফলাফল হুবহু মেলে)
  ২. Query সংখ্যা tenant বাড়লেও বাড়ে না (constant ৩টা)

এই test চালাতে সত্যিকারের PostgreSQL দরকার (DATABASE_URL env var দিয়ে);
না থাকলে স্বয়ংক্রিয়ভাবে skip হয়ে যাবে (CI-তে DB নাও থাকতে পারে)।
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _pg_available() -> bool:
    if not DATABASE_URL:
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


PG_UP = _pg_available()


@pytest.mark.skipif(
    not PG_UP,
    reason="বাস্তব PostgreSQL (DATABASE_URL) পাওয়া যায়নি — N+1 integration test skip করা হলো",
)
class TestBranchStatsNPlus1Fix:
    """branch_module.py-র _branch_stats_bulk() — N+1 fix যাচাই।"""

    @pytest.fixture(scope="class")
    def seeded_db(self):
        """Test schema তৈরি করে ১০টা tenant + related data seed করে।"""
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # পরিষ্কার স্লেট নিশ্চিত করি
        cur.execute("DROP TABLE IF EXISTS fee_vouchers, teachers, students, tenants CASCADE")
        cur.execute("""
            CREATE TABLE tenants (id SERIAL PRIMARY KEY, madrasa_name TEXT, address TEXT, phone TEXT);
            CREATE TABLE students (id SERIAL PRIMARY KEY, tenant_id INT, status TEXT DEFAULT 'active');
            CREATE TABLE teachers (id SERIAL PRIMARY KEY, tenant_id INT, status TEXT DEFAULT 'active');
            CREATE TABLE fee_vouchers (
                id SERIAL PRIMARY KEY, tenant_id INT, status TEXT,
                amount NUMERIC, issue_date DATE DEFAULT CURRENT_DATE
            );
        """)
        cur.execute("INSERT INTO tenants (madrasa_name) SELECT 'Madrasa ' || g FROM generate_series(1,10) g")
        cur.execute("INSERT INTO students (tenant_id) SELECT t.id FROM tenants t, generate_series(1,5)")
        cur.execute("INSERT INTO teachers (tenant_id) SELECT t.id FROM tenants t, generate_series(1,2)")
        cur.execute(
            "INSERT INTO fee_vouchers (tenant_id, status, amount, issue_date) "
            "SELECT t.id, 'paid', 500, CURRENT_DATE FROM tenants t, generate_series(1,3)"
        )
        conn.close()
        yield

    def test_data_correctness_bulk_matches_individual(self, seeded_db, monkeypatch):
        """Bulk fetch-এর ফলাফল individual per-tenant fetch-এর সাথে হুবহু মেলে কিনা।"""
        monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
        import importlib
        import db as db_module
        importlib.reload(db_module)
        import branch_module
        importlib.reload(branch_module)

        tenants = db_module.fetchall("SELECT id FROM tenants ORDER BY id")
        tenant_ids = [t["id"] for t in tenants]
        assert len(tenant_ids) == 10

        # পুরোনো per-tenant পদ্ধতি (এখনও কোডে আছে, single-tenant ব্যবহারের জন্য)
        old_results = {tid: branch_module._branch_stats(tid) for tid in tenant_ids}

        # নতুন bulk পদ্ধতি
        new_results = branch_module._branch_stats_bulk(tenant_ids)

        for tid in tenant_ids:
            assert old_results[tid] == new_results[tid], (
                f"tenant={tid}-এ mismatch: old={old_results[tid]} vs new={new_results[tid]}"
            )

    def test_query_count_is_constant_not_linear(self, seeded_db, monkeypatch):
        """
        মূল N+1 regression check: bulk fetch করলে ঠিক ৩টা query চলা উচিত,
        tenant সংখ্যা (১০) নির্বিশেষে — ৩০টা নয়।
        """
        monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
        import importlib
        import db as db_module
        importlib.reload(db_module)
        import branch_module
        importlib.reload(branch_module)

        query_log = []
        orig_fetchall = db_module.fetchall
        orig_fetchone = db_module.fetchone

        def _counted_fetchall(sql, params=()):
            query_log.append(sql)
            return orig_fetchall(sql, params)

        def _counted_fetchone(sql, params=()):
            query_log.append(sql)
            return orig_fetchone(sql, params)

        monkeypatch.setattr(branch_module, "fetchall", _counted_fetchall)
        monkeypatch.setattr(branch_module, "fetchone", _counted_fetchone)

        tenants = orig_fetchall("SELECT id FROM tenants ORDER BY id")
        tenant_ids = [t["id"] for t in tenants]

        query_log.clear()
        branch_module._branch_stats_bulk(tenant_ids)
        bulk_query_count = len(query_log)

        assert bulk_query_count == 3, (
            f"Bulk fetch-এ {bulk_query_count}টা query চলেছে, প্রত্যাশিত ছিল ঠিক ৩টা "
            f"(constant, tenant সংখ্যা নির্বিশেষে)"
        )

        # প্রমাণ করি এটা আসলেই N-independent: আরও tenant যোগ করেও query একই থাকে
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("INSERT INTO tenants (madrasa_name) SELECT 'Extra ' || g FROM generate_series(1,20) g")
        conn.close()

        tenants_more = orig_fetchall("SELECT id FROM tenants ORDER BY id")
        assert len(tenants_more) == 30  # ১০ + ২০ নতুন

        query_log.clear()
        branch_module._branch_stats_bulk([t["id"] for t in tenants_more])
        bulk_query_count_after_growth = len(query_log)

        assert bulk_query_count_after_growth == 3, (
            f"Tenant সংখ্যা ১০ থেকে ৩০ হওয়ার পরও query সংখ্যা ৩ থাকা উচিত ছিল, "
            f"পাওয়া গেছে {bulk_query_count_after_growth} — এর মানে এখনও N-নির্ভর!"
        )

    def test_old_per_tenant_method_scales_linearly_documenting_the_bug(self, seeded_db, monkeypatch):
        """
        Documentation test: প্রমাণ করে যে পুরোনো _branch_stats() (এখনও single-tenant
        ব্যবহারের জন্য কোডে আছে) per-call-এ ৩টা query চালায় — তাই কেউ যদি এটা আবার
        loop-এ বসায়, N+1 ফিরে আসবে। এই test ভবিষ্যতে regression ধরার জন্য।
        """
        monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
        import importlib
        import db as db_module
        importlib.reload(db_module)
        import branch_module
        importlib.reload(branch_module)

        query_log = []
        orig_fetchone = db_module.fetchone

        def _counted_fetchone(sql, params=()):
            query_log.append(sql)
            return orig_fetchone(sql, params)

        monkeypatch.setattr(branch_module, "fetchone", _counted_fetchone)

        query_log.clear()
        branch_module._branch_stats(1)  # একটা tenant-এর জন্য single call
        single_call_count = len(query_log)

        assert single_call_count == 3, (
            "single-tenant _branch_stats() ৩টা query চালায় — এটাই কারণ কেন এটাকে "
            "loop-এ (N tenant-এর জন্য) কল করা হলে N+1 সমস্যা হয়। render()-এ এই "
            "ফাংশনটা যেন আর loop-এর ভেতরে সরাসরি কল না হয়, বরং _branch_stats_bulk() "
            "ব্যবহার করা হয়।"
        )
