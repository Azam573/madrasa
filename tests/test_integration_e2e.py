"""
tests/test_integration_e2e.py — Real PostgreSQL end-to-end tests

Mock নয় — সত্যিকারের Postgres-এ সম্পূর্ণ flow:
    migration → tenant/user seed → login → JWT → API call → DB verify

চালানোর শর্ত: INTEGRATION_DATABASE_URL env var সেট থাকতে হবে।
না থাকলে সব test skip হয় (unit test-এর মতো সবসময় চলে না)।

লোকালে চালাতে:
    docker run -d --name itest-pg -e POSTGRES_PASSWORD=itest \\
        -e POSTGRES_DB=itest -p 55433:5432 postgres:16-alpine
    export INTEGRATION_DATABASE_URL=postgresql://postgres:itest@localhost:55433/itest
    alembic upgrade head        # (env var DATABASE_URL=$INTEGRATION_DATABASE_URL সহ)
    pytest tests/test_integration_e2e.py -v

CI-তে এটা আলাদা job-এ চলে (postgres service container সহ)।
"""
import os
import pytest

INTEGRATION_URL = os.environ.get("INTEGRATION_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not INTEGRATION_URL,
    reason="INTEGRATION_DATABASE_URL সেট নেই — integration tests skip",
)


@pytest.fixture(scope="module")
def db_conn():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(
        INTEGRATION_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def seeded(db_conn):
    """একটি tenant, admin user, session, class, student, enrollment seed করে।"""
    from api.core.security import hash_password

    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (madrasa_name, slug) VALUES (%s,%s) RETURNING id",
            ("Integration Test Madrasa", f"itest-{os.getpid()}"),
        )
        tid = cur.fetchone()["id"]

        cur.execute(
            """INSERT INTO app_users (tenant_id, username, password_hash,
                                      role, full_name, is_active)
               VALUES (%s,%s,%s,'admin','Integration Admin',TRUE) RETURNING id""",
            (tid, "itest_admin", hash_password("Str0ng!Pass")),
        )
        cur.execute(
            """INSERT INTO academic_sessions (tenant_id, session_name, is_active)
               VALUES (%s,'2026',TRUE) RETURNING id""",
            (tid,),
        )
        session_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO classes (tenant_id, class_name, class_numeric)
               VALUES (%s,'Hifz-1',1) RETURNING id""",
            (tid,),
        )
        class_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO students (tenant_id, name, mobile_no, status)
               VALUES (%s,'Integration Student','01712340001','active') RETURNING id""",
            (tid,),
        )
        student_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO student_enrollments
               (tenant_id, student_id, session_id, class_id, roll_no,
                monthly_fee, enrollment_status)
               VALUES (%s,%s,%s,%s,1,500,'active') RETURNING id""",
            (tid, student_id, session_id, class_id),
        )
        enrollment_id = cur.fetchone()["id"]

    return {
        "tenant_id": tid, "session_id": session_id, "class_id": class_id,
        "student_id": student_id, "enrollment_id": enrollment_id,
        "username": "itest_admin", "password": "Str0ng!Pass",
    }


@pytest.fixture(scope="module")
def client(seeded):
    """API-র DB pool integration DB-তে point করে TestClient।"""
    os.environ["DATABASE_URL"] = INTEGRATION_URL
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def auth_headers(client, seeded):
    r = client.post("/api/v1/auth/login", json={
        "tenant_id": seeded["tenant_id"],
        "username":  seeded["username"],
        "password":  seeded["password"],
    })
    assert r.status_code == 200, f"Login failed: {r.text}"
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestE2EAuth:

    def test_login_wrong_password_401(self, client, seeded):
        r = client.post("/api/v1/auth/login", json={
            "tenant_id": seeded["tenant_id"],
            "username":  seeded["username"],
            "password":  "wrong-password",
        })
        assert r.status_code == 401

    def test_login_success_returns_jwt(self, auth_headers):
        assert auth_headers["Authorization"].startswith("Bearer ")


class TestE2ENotices:

    def test_create_and_list_notice(self, client, auth_headers, db_conn, seeded):
        r = client.post("/api/v1/notices", headers=auth_headers, json={
            "title": "পরীক্ষার সময়সূচি",
            "body":  "আগামী রবিবার থেকে বার্ষিক পরীক্ষা শুরু হবে।",
            "category": "exam",
            "is_pinned": True,
        })
        assert r.status_code == 201, r.text
        notice_id = r.json()["data"]["id"]

        # API list-এ আসে
        r = client.get("/api/v1/notices", headers=auth_headers)
        assert r.status_code == 200
        titles = [n["title"] for n in r.json()["data"]]
        assert "পরীক্ষার সময়সূচি" in titles

        # DB-তে সত্যিই tenant-scoped ভাবে আছে
        with db_conn.cursor() as cur:
            cur.execute("SELECT tenant_id FROM notices WHERE id=%s", (notice_id,))
            assert cur.fetchone()["tenant_id"] == seeded["tenant_id"]


class TestE2EFinanceFlow:

    def test_voucher_create_and_pay(self, client, auth_headers, seeded):
        # ভাউচার তৈরি
        r = client.post("/api/v1/finance/vouchers", headers=auth_headers, json={
            "student_id":    seeded["student_id"],
            "enrollment_id": seeded["enrollment_id"],
            "month_name":    "January",
            "year":          2026,
            "amount":        500,
            "fund_type":     "general",
            "due_date":      "2026-01-31",
        })
        assert r.status_code == 201, r.text
        voucher_id = r.json()["data"]["id"]

        # পেমেন্ট — status paid হওয়া উচিত
        r = client.post("/api/v1/finance/payments", headers=auth_headers, json={
            "voucher_id":     voucher_id,
            "amount_paid":    500,
            "payment_method": "cash",
        })
        assert r.status_code == 201, r.text
        assert r.json()["data"]["status"] == "paid"

        # দ্বিতীয়বার পে করা যাবে না
        r = client.post("/api/v1/finance/payments", headers=auth_headers, json={
            "voucher_id":     voucher_id,
            "amount_paid":    500,
            "payment_method": "cash",
        })
        assert r.status_code == 400


class TestE2EExamFlow:

    def test_full_exam_lifecycle(self, client, auth_headers, seeded):
        # ১. Exam তৈরি
        r = client.post("/api/v1/exams", headers=auth_headers, json={
            "title": "Integration কুরআন কুইজ", "duration_mins": 15,
        })
        assert r.status_code == 201, r.text
        exam_id = r.json()["data"]["id"]

        # ২. প্রশ্ন-শূন্য অবস্থায় activate নিষেধ
        r = client.patch(f"/api/v1/exams/{exam_id}/status?active=true",
                         headers=auth_headers)
        assert r.status_code == 400

        # ৩. প্রশ্ন যোগ
        r = client.post(f"/api/v1/exams/{exam_id}/questions",
                        headers=auth_headers, json={
            "question_text": "কুরআনে কয়টি সূরা?",
            "option_a": "১১০", "option_b": "১১৪",
            "correct_option": "B", "marks": 5,
        })
        assert r.status_code == 201, r.text
        question_id = r.json()["data"]["id"]

        # ৪. Activate
        r = client.patch(f"/api/v1/exams/{exam_id}/status?active=true",
                         headers=auth_headers)
        assert r.status_code == 200

        # ৫. Paper না খুলে সরাসরি submit নিষেধ (টাইমার guard)
        r = client.post(f"/api/v1/exams/{exam_id}/submit",
                        headers=auth_headers, json={
            "enrollment_id": seeded["enrollment_id"],
            "answers": {str(question_id): "B"},
        })
        assert r.status_code == 403

        # ৬. Paper — enrollment লাগে, উত্তর নেই, টাইমার তথ্য আছে
        r = client.get(f"/api/v1/exams/{exam_id}/paper",
                       headers=auth_headers,
                       params={"enrollment_id": seeded["enrollment_id"]})
        assert r.status_code == 200
        paper_text = r.text
        assert "correct_option" not in paper_text
        assert "explanation" not in paper_text
        assert "deadline" in paper_text
        first_started = r.json()["exam"]["started_at"]

        # Refresh করলে টাইমার reset হয় না
        r = client.get(f"/api/v1/exams/{exam_id}/paper",
                       headers=auth_headers,
                       params={"enrollment_id": seeded["enrollment_id"]})
        assert r.json()["exam"]["started_at"] == first_started

        # ৭. Submit — server-side scoring
        r = client.post(f"/api/v1/exams/{exam_id}/submit",
                        headers=auth_headers, json={
            "enrollment_id": seeded["enrollment_id"],
            "answers": {str(question_id): "B"},
        })
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["score"] == 5 and data["percentage"] == 100.0

        # ৮. Resubmit নিষেধ — প্রথম submission-ই চূড়ান্ত (cheating guard)
        r = client.post(f"/api/v1/exams/{exam_id}/submit",
                        headers=auth_headers, json={
            "enrollment_id": seeded["enrollment_id"],
            "answers": {str(question_id): "A"},
        })
        assert r.status_code == 409

        # ৯. জমার পর paper-ও আর খোলা যায় না
        r = client.get(f"/api/v1/exams/{exam_id}/paper",
                       headers=auth_headers,
                       params={"enrollment_id": seeded["enrollment_id"]})
        assert r.status_code == 409


class TestE2ETenantIsolation:

    def test_second_tenant_sees_nothing(self, client, db_conn, seeded):
        """দ্বিতীয় tenant-এর admin প্রথম tenant-এর কোনো ডেটা দেখতে পায় না।"""
        from api.core.security import hash_password
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (madrasa_name, slug) VALUES (%s,%s) RETURNING id",
                ("Other Madrasa", f"other-{os.getpid()}"),
            )
            tid2 = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO app_users (tenant_id, username, password_hash,
                                          role, full_name, is_active)
                   VALUES (%s,'other_admin',%s,'admin','Other',TRUE)""",
                (tid2, hash_password("Other!Pass1")),
            )

        r = client.post("/api/v1/auth/login", json={
            "tenant_id": tid2, "username": "other_admin", "password": "Other!Pass1",
        })
        assert r.status_code == 200
        headers2 = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # Tenant-1-এর notice tenant-2-এর list-এ নেই
        r = client.get("/api/v1/notices", headers=headers2)
        assert r.status_code == 200
        titles = [n["title"] for n in r.json()["data"]]
        assert "পরীক্ষার সময়সূচি" not in titles


class TestE2EBranding:

    def test_branding_customize_and_print(self, client, auth_headers, seeded):
        """Branding সেট → লোগো আপলোড → receipt print-এ সব প্রতিফলিত।"""
        import base64
        png_1px = base64.b64encode(bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d4944415478da63fcffff3f030005fe02fea72d0e660000000049454e44ae426082"
        )).decode()

        # ১. Branding আপডেট
        r = client.put("/api/v1/settings/branding", headers=auth_headers, json={
            "name_arabic":    "مدرسة الاختبار",
            "name_english":   "Integration Test Madrasa",
            "principal_name": "মাওলানা টেস্ট সাহেব",
            "primary_color":  "#1B5E20",
            "receipt_footer": "ইন্টিগ্রেশন ফুটার লাইন",
        })
        assert r.status_code == 200, r.text

        # ভুল রঙ reject হয়
        r = client.put("/api/v1/settings/branding", headers=auth_headers,
                       json={"primary_color": "green"})
        assert r.status_code == 422   # pydantic schema validation

        # ২. লোগো আপলোড (বৈধ PNG)
        r = client.post("/api/v1/settings/branding/logo", headers=auth_headers,
                        json={"image_base64": png_1px})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["mime"] == "image/png"

        # Fake image reject হয়
        fake = base64.b64encode(b"<script>alert(1)</script>xxxx").decode()
        r = client.post("/api/v1/settings/branding/logo", headers=auth_headers,
                        json={"image_base64": fake})
        assert r.status_code == 400

        # ৩. GET-এ blob নেই, flag আছে
        r = client.get("/api/v1/settings/branding", headers=auth_headers)
        data = r.json()["data"]
        assert data["has_logo"] is True
        assert "logo_base64" not in data
        assert data["primary_color"] == "#1B5E20"

        # ৪. Receipt print — branding প্রতিফলিত (আগের ফি payment থেকে)
        # আগের finance flow-এ payment হয়েছে; সেটির id বের করি
        r = client.get("/api/v1/finance/vouchers", headers=auth_headers,
                       params={"year": 2026})
        # payment id সরাসরি জানা নেই — DB নয়, receipt endpoint-টা
        # tenant-scoped 404 আচরণও যাচাই করি ভুল id দিয়ে
        r = client.get("/api/v1/print/receipt/999999", headers=auth_headers)
        assert r.status_code == 404

    def test_receipt_print_renders_branding(self, client, auth_headers,
                                            db_conn, seeded):
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM fee_payments WHERE tenant_id=%s ORDER BY id LIMIT 1",
                (seeded["tenant_id"],),
            )
            row = cur.fetchone()
        assert row, "আগের finance flow-এর payment থাকা উচিত"

        r = client.get(f"/api/v1/print/receipt/{row['id']}", headers=auth_headers)
        assert r.status_code == 200
        html = r.text
        assert "টাকা প্রাপ্তির রসিদ" in html
        assert "#1B5E20" in html                        # কাস্টম রঙ
        assert "مدرسة الاختبار" in html                  # আরবি নাম
        assert "ইন্টিগ্রেশন ফুটার লাইন" in html          # কাস্টম ফুটার
        assert "data:image/png;base64," in html          # লোগো embed
        assert "মাওলানা টেস্ট সাহেব" in html             # অধ্যক্ষ স্বাক্ষর ব্লক
        assert "window.print()" in html                  # প্রিন্ট বাটন

    def test_tc_print_renders_branding(self, client, auth_headers,
                                       db_conn, seeded):
        # TC রেকর্ড seed করে print যাচাই
        with db_conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tc_records
                   (tenant_id, student_id, tc_number, reason, last_class,
                    last_session, conduct)
                   VALUES (%s,%s,%s,'অভিভাবকের বদলি','Hifz-1','2026','ভালো')
                   RETURNING id""",
                (seeded["tenant_id"], seeded["student_id"],
                 f"TC-IT-{seeded['tenant_id']}"),
            )
            tc_id = cur.fetchone()["id"]

        r = client.get(f"/api/v1/print/tc/{tc_id}", headers=auth_headers)
        assert r.status_code == 200
        html = r.text
        assert "ছাড়পত্র" in html
        assert "Integration Student" in html
        assert "#1B5E20" in html
        assert "অভিভাবকের বদলি" in html
