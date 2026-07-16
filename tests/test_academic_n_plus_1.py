"""
tests/test_academic_n_plus_1.py — N+1 Query Regression Test (brutal-list #4)

সমস্যা: academic_module.py-র _class_result_summary()-এ একটা for-loop-এর
ভেতরে প্রতি ছাত্রের জন্য _get_student_marks() কল হতো, যা নিজে একটা DB query
চালায়। ৪০ ছাত্রের ক্লাসে ৪০+ query per report — প্রতিটা fetchall() call
আবার একটা full connection-pool checkout/checkin-ও করে।

সমাধান: _get_class_marks_bulk() — পুরো ক্লাসের জন্য একটাই JOIN query,
ছাত্র সংখ্যা নির্বিশেষে constant।

এই test বাস্তব PostgreSQL-এর বিপরীতে চালিয়ে প্রমাণ করে (tests/test_n_plus_1.py-র
কাঠামো অনুসরণ করে):
  ১. ডেটা সঠিক থাকে (bulk ও per-student ফলাফল হুবহু মেলে)
  ২. Query সংখ্যা ছাত্র বাড়লেও বাড়ে না (constant ৩টা)

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
class TestAcademicResultNPlus1Fix:
    """academic_module.py-র _get_class_marks_bulk() — N+1 fix যাচাই।"""

    @pytest.fixture(scope="class")
    def seeded_db(self):
        """Test schema তৈরি করে ২০ জন ছাত্র + ৫টা বিষয় + marks seed করে।"""
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(
            "DROP TABLE IF EXISTS student_marks, exams, subjects, "
            "student_enrollments, students, classes, academic_sessions, tenants CASCADE"
        )
        cur.execute("""
            CREATE TABLE tenants (id SERIAL PRIMARY KEY, madrasa_name TEXT);
            CREATE TABLE academic_sessions (id SERIAL PRIMARY KEY, tenant_id INT,
                session_name TEXT, is_active BOOLEAN DEFAULT TRUE);
            CREATE TABLE classes (id SERIAL PRIMARY KEY, tenant_id INT, class_name TEXT);
            CREATE TABLE students (id SERIAL PRIMARY KEY, tenant_id INT, name TEXT,
                status TEXT DEFAULT 'active');
            CREATE TABLE student_enrollments (id SERIAL PRIMARY KEY, tenant_id INT,
                student_id INT, session_id INT, class_id INT, roll_no INT,
                enrollment_status TEXT DEFAULT 'active');
            CREATE TABLE subjects (id SERIAL PRIMARY KEY, tenant_id INT, class_id INT,
                subject_name TEXT, subject_code TEXT,
                full_marks INTEGER DEFAULT 100, pass_marks INTEGER DEFAULT 33);
            CREATE TABLE exams (id SERIAL PRIMARY KEY, tenant_id INT, session_id INT,
                exam_name TEXT);
            CREATE TABLE student_marks (
                id SERIAL PRIMARY KEY, tenant_id INT, enrollment_id INT,
                exam_id INT, subject_id INT,
                written_obtained INTEGER DEFAULT 0, mcq_obtained INTEGER DEFAULT 0,
                practical_obtained INTEGER DEFAULT 0,
                total_obtained INTEGER GENERATED ALWAYS AS
                    (written_obtained + mcq_obtained + practical_obtained) STORED,
                is_absent BOOLEAN DEFAULT FALSE,
                UNIQUE(enrollment_id, exam_id, subject_id));
        """)
        cur.execute("INSERT INTO tenants (madrasa_name) VALUES ('Test Madrasa') RETURNING id")
        tid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO academic_sessions (tenant_id, session_name) VALUES (%s,'2026') RETURNING id",
            (tid,),
        )
        session_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO classes (tenant_id, class_name) VALUES (%s,'Hifz-1') RETURNING id",
            (tid,),
        )
        class_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO exams (tenant_id, session_id, exam_name) VALUES (%s,%s,'Half Yearly') RETURNING id",
            (tid, session_id),
        )
        exam_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO subjects (tenant_id, class_id, subject_name, full_marks, pass_marks) "
            "SELECT %s, %s, 'Subject ' || g, 100, 33 FROM generate_series(1,5) g",
            (tid, class_id),
        )

        cur.execute(
            "INSERT INTO students (tenant_id, name) "
            "SELECT %s, 'Student ' || g FROM generate_series(1,20) g RETURNING id",
            (tid,),
        )
        student_ids = [r["id"] for r in cur.fetchall()]
        for i, sid in enumerate(student_ids, 1):
            cur.execute(
                "INSERT INTO student_enrollments (tenant_id, student_id, session_id, class_id, roll_no) "
                "VALUES (%s,%s,%s,%s,%s)",
                (tid, sid, session_id, class_id, i),
            )

        cur.execute(
            "INSERT INTO student_marks (tenant_id, enrollment_id, exam_id, subject_id, "
            "written_obtained, mcq_obtained, practical_obtained) "
            "SELECT %s, e.id, %s, s.id, 30, 20, 10 "
            "FROM student_enrollments e, subjects s WHERE e.tenant_id=%s AND s.tenant_id=%s",
            (tid, exam_id, tid, tid),
        )
        conn.close()
        yield {"tid": tid, "session_id": session_id, "class_id": class_id, "exam_id": exam_id}

    def test_data_correctness_bulk_matches_individual(self, seeded_db, monkeypatch):
        """Bulk fetch-এর ফলাফল per-student fetch-এর সাথে হুবহু মেলে কিনা।"""
        monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
        import importlib
        import db as db_module
        importlib.reload(db_module)
        import academic_module
        importlib.reload(academic_module)

        tid, exam_id, class_id, session_id = (
            seeded_db["tid"], seeded_db["exam_id"], seeded_db["class_id"], seeded_db["session_id"],
        )
        students = academic_module._get_enrolled_students(tid, session_id, class_id)
        assert len(students) == 20

        marks_by_enrollment = academic_module._get_class_marks_bulk(tid, exam_id, class_id, session_id)

        for stu in students:
            old = academic_module._get_student_marks(tid, stu["enrollment_id"], exam_id)
            new = marks_by_enrollment.get(stu["enrollment_id"], [])
            assert old == new, (
                f"enrollment={stu['enrollment_id']}-এ mismatch: old={old} vs new={new}"
            )

        # _class_result_summary() end-to-end আউটপুটও সামঞ্জস্যপূর্ণ কিনা
        results = academic_module._class_result_summary(tid, exam_id, class_id, session_id)
        assert len(results) == 20
        ranks = sorted(r["rank"] for r in results)
        assert ranks == list(range(1, 21))
        for r in results:
            assert r["total_obtained"] == 60 * 5  # প্রতি বিষয়ে 30+20+10=60, ৫টা বিষয়

    def test_query_count_is_constant_not_linear(self, seeded_db, monkeypatch):
        """
        মূল N+1 regression check: _class_result_summary() ঠিক ৩টা query চালানো
        উচিত, ছাত্র সংখ্যা (২০, তারপর ৪০) নির্বিশেষে।
        """
        monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
        import importlib
        import db as db_module
        importlib.reload(db_module)
        import academic_module
        importlib.reload(academic_module)

        tid, exam_id, class_id, session_id = (
            seeded_db["tid"], seeded_db["exam_id"], seeded_db["class_id"], seeded_db["session_id"],
        )

        query_log = []
        orig_fetchall = db_module.fetchall

        def _counted_fetchall(sql, params=()):
            query_log.append(sql)
            return orig_fetchall(sql, params)

        monkeypatch.setattr(academic_module, "fetchall", _counted_fetchall)

        query_log.clear()
        academic_module._class_result_summary(tid, exam_id, class_id, session_id)
        assert len(query_log) == 3, (
            f"{len(query_log)}টা query চলেছে, প্রত্যাশিত ছিল ঠিক ৩টা (constant, "
            f"ছাত্র সংখ্যা নির্বিশেষে)"
        )

        # আরও ২০ জন ছাত্র যোগ করে প্রমাণ করি সত্যিই N-independent
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO students (tenant_id, name) "
            "SELECT %s, 'Extra ' || g FROM generate_series(1,20) g RETURNING id",
            (tid,),
        )
        extra_ids = [r[0] for r in cur.fetchall()]
        for i, sid in enumerate(extra_ids, 21):
            cur.execute(
                "INSERT INTO student_enrollments (tenant_id, student_id, session_id, class_id, roll_no) "
                "VALUES (%s,%s,%s,%s,%s)",
                (tid, sid, session_id, class_id, i),
            )
        cur.execute(
            "INSERT INTO student_marks (tenant_id, enrollment_id, exam_id, subject_id, "
            "written_obtained, mcq_obtained, practical_obtained) "
            "SELECT %s, e.id, %s, s.id, 30, 20, 10 "
            "FROM student_enrollments e, subjects s "
            "WHERE e.tenant_id=%s AND s.tenant_id=%s AND e.roll_no > 20",
            (tid, exam_id, tid, tid),
        )
        conn.close()

        students_after = academic_module._get_enrolled_students(tid, session_id, class_id)
        assert len(students_after) == 40

        query_log.clear()
        academic_module._class_result_summary(tid, exam_id, class_id, session_id)
        assert len(query_log) == 3, (
            f"ছাত্র সংখ্যা ২০ থেকে ৪০ হওয়ার পরও query সংখ্যা ৩ থাকা উচিত ছিল, "
            f"পাওয়া গেছে {len(query_log)} — এর মানে এখনও N-নির্ভর!"
        )
