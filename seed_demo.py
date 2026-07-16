"""
seed_demo.py — Demo data seeder
Run once after schema bootstrap to populate the system with realistic sample data.
Usage: python seed_demo.py
"""

import os, sys, random
from datetime import date, timedelta

# Allow running from project root
sys.path.insert(0, os.path.dirname(__file__))

# Mock st.session_state before importing db
import types
import streamlit as st

def seed():
    from db import get_connection, release_connection, bootstrap_schema

    print("⏳ Bootstrapping schema...")
    bootstrap_schema()

    conn = get_connection()
    if not conn:
        print("❌ DB connection failed. Set DATABASE_URL env var.")
        return

    MONTHS = ["January","February","March","April","May","June",
              "July","August","September","October","November","December"]
    STUDENT_NAMES = [
        "Muhammad Abdullah","Ibrahim Khalil","Usman Ghani","Ali Hassan",
        "Yusuf Islam","Sulaiman Ahmad","Dawud Rahman","Isa Karim",
        "Musa Akbar","Harun Rashid","Nuh Siddiq","Idris Faruk",
        "Zakaria Hussain","Yahya Amin","Ishaq Jubayer","Ismail Hossain",
        "Saleh Chowdhury","Talha Noman","Zubair Masum","Bilal Sakib",
    ]
    FATHER_NAMES = [
        "Abdul Karim","Abdur Rahman","Md Siddique","Md Ibrahim",
        "Shahidul Islam","Rafiqul Hasan","Belal Hossain","Nurul Amin",
        "Mizanur Rahman","Habibur Rahman","Shafiqul Islam","Golam Mostafa",
        "Khairul Bashar","Anisur Rahman","Mozammel Haque","Matiur Rahman",
        "Nazrul Islam","Fazlur Rahman","Zahirul Islam","Saiful Islam",
    ]

    try:
        with conn.cursor() as cur:
            tid = 1  # Demo tenant

            # ── Subjects ──
            print("📖 Seeding subjects...")
            subjects_data = [
                (tid, None, "আল-কুরআন", "QUR101", 100, 33),
                (tid, None, "হাদীস শাস্ত্র", "HAD201", 100, 33),
                (tid, None, "ফিকহ", "FIK301", 100, 33),
                (tid, None, "আরবী সাহিত্য", "ARB401", 100, 33),
                (tid, None, "বাংলা", "BAN501", 100, 33),
                (tid, None, "গণিত", "MAT601", 100, 33),
            ]
            for s in subjects_data:
                cur.execute(
                    """INSERT INTO subjects (tenant_id, class_id, subject_name, subject_code, full_marks, pass_marks)
                       VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", s
                )

            # ── Exam ──
            print("📝 Seeding exam...")
            cur.execute(
                """INSERT INTO exams (tenant_id, session_id, exam_name, exam_date)
                   VALUES (%s, (SELECT id FROM academic_sessions WHERE tenant_id=%s AND is_active=TRUE LIMIT 1),
                   'বার্ষিক পরীক্ষা ২০২৪', '2024-12-01')
                   ON CONFLICT DO NOTHING RETURNING id""",
                (tid, tid),
            )
            exam_row = cur.fetchone()

            # Get active session & classes
            cur.execute("SELECT id FROM academic_sessions WHERE tenant_id=%s AND is_active=TRUE LIMIT 1", (tid,))
            sess = cur.fetchone()
            cur.execute("SELECT id FROM classes WHERE tenant_id=%s ORDER BY class_numeric LIMIT 5", (tid,))
            classes = cur.fetchall()
            cur.execute("SELECT id FROM subjects WHERE tenant_id=%s", (tid,))
            subjects = cur.fetchall()

            if not sess or not classes:
                print("⚠️  No active session/classes. Run app first to bootstrap.")
                conn.rollback()
                return

            sess_id = sess["id"]

            # ── Students + Enrollments ──
            print("🎓 Seeding students...")
            student_ids = []
            enrollment_ids = []
            roll = 1

            for i, (sname, fname) in enumerate(zip(STUDENT_NAMES, FATHER_NAMES)):
                cls = classes[i % len(classes)]
                mobile = f"017{random.randint(10000000,99999999)}"
                dob = date(2008, random.randint(1,12), random.randint(1,28))

                cur.execute(
                    """INSERT INTO students
                       (tenant_id, name, father_name, mobile_no, date_of_birth,
                        gender, present_address, status)
                       VALUES (%s,%s,%s,%s,%s,'Male','ঢাকা, বাংলাদেশ','active')
                       ON CONFLICT DO NOTHING RETURNING id""",
                    (tid, sname, fname, mobile, dob),
                )
                stu_row = cur.fetchone()
                if not stu_row:
                    continue
                sid = stu_row["id"]
                student_ids.append(sid)

                cur.execute(
                    """INSERT INTO student_enrollments
                       (tenant_id, student_id, session_id, class_id, roll_no, monthly_fee, enrollment_status)
                       VALUES (%s,%s,%s,%s,%s,%s,'active')
                       ON CONFLICT DO NOTHING RETURNING id""",
                    (tid, sid, sess_id, cls["id"], roll, random.choice([500, 600, 700, 800])),
                )
                enr_row = cur.fetchone()
                if enr_row:
                    enrollment_ids.append((sid, enr_row["id"], cls["id"]))
                roll += 1

            # ── Fee Vouchers & Payments ──
            print("💰 Seeding fee vouchers & payments...")
            for sid, enr_id, cls_id in enrollment_ids:
                for m_idx, month in enumerate(MONTHS[:10]):  # Jan–Oct
                    amount = random.choice([500, 600, 700, 800])
                    fund   = random.choice(["general", "general", "general", "zakat", "lillah_boarding"])
                    status = random.choice(["paid", "paid", "paid", "unpaid"])
                    issue  = date(2024, m_idx + 1, 1)
                    due    = date(2024, m_idx + 1, 10)

                    cur.execute(
                        """INSERT INTO fee_vouchers
                           (tenant_id, enrollment_id, student_id, voucher_no, issue_date,
                            due_date, month_name, year, amount, fund_type, status, paid_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,2024,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        (tid, enr_id, sid,
                         f"VCH-{tid:03d}-{sid:03d}-{m_idx:02d}",
                         issue, due, month, amount, fund, status,
                         date(2024, m_idx+1, random.randint(5,9)) if status == "paid" else None),
                    )

                    if status == "paid":
                        cur.execute(
                            """INSERT INTO fee_payments
                               (tenant_id, voucher_id, amount_paid, payment_date, payment_method, receipt_no)
                               SELECT %s, v.id, v.amount, %s, 'cash', %s
                               FROM fee_vouchers v
                               WHERE v.voucher_no=%s AND v.tenant_id=%s
                               ON CONFLICT DO NOTHING""",
                            (tid,
                             date(2024, m_idx+1, random.randint(5,9)),
                             f"RCP-{tid:03d}-{sid:03d}-{m_idx:02d}",
                             f"VCH-{tid:03d}-{sid:03d}-{m_idx:02d}", tid),
                        )

            # ── Marks Distribution ──
            if exam_row and subjects:
                print("📐 Seeding marks distribution...")
                exam_id = exam_row["id"]
                for subj in subjects:
                    fm = 100
                    cur.execute(
                        """INSERT INTO marks_distribution
                           (tenant_id, exam_id, subject_id, written_marks, mcq_marks, practical_marks)
                           VALUES (%s,%s,%s,80,10,10)
                           ON CONFLICT (exam_id, subject_id) DO NOTHING""",
                        (tid, exam_id, subj["id"]),
                    )

                # ── Student Marks ──
                print("✏️  Seeding student marks...")
                for sid, enr_id, cls_id in enrollment_ids:
                    for subj in subjects:
                        w  = random.randint(35, 78)
                        mq = random.randint(4, 10)
                        pr = random.randint(5, 10)
                        cur.execute(
                            """INSERT INTO student_marks
                               (tenant_id, enrollment_id, exam_id, subject_id,
                                written_obtained, mcq_obtained, practical_obtained, is_absent)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE)
                               ON CONFLICT (enrollment_id, exam_id, subject_id) DO NOTHING""",
                            (tid, enr_id, exam_id, subj["id"], w, mq, pr),
                        )

            # ── Attendance (last 30 days) ──
            print("📅 Seeding attendance...")
            today = date.today()
            for sid, enr_id, cls_id in enrollment_ids[:10]:  # Limit for speed
                for d in range(30):
                    att_date = today - timedelta(days=d)
                    if att_date.weekday() == 4:  # Friday = holiday
                        status = "holiday"
                    else:
                        status = random.choice(["present","present","present","absent","late"])
                    cur.execute(
                        """INSERT INTO attendance (tenant_id, enrollment_id, date, status)
                           VALUES (%s,%s,%s,%s)
                           ON CONFLICT (enrollment_id, date) DO NOTHING""",
                        (tid, enr_id, att_date, status),
                    )

            # ── Notice ──
            print("📢 Seeding notices...")
            notices = [
                (tid, "বার্ষিক পরীক্ষার সময়সূচী", "আগামী ১ ডিসেম্বর থেকে বার্ষিক পরীক্ষা শুরু হবে। সকল ছাত্রদের যথাসময়ে উপস্থিত থাকতে বলা হচ্ছে।", "exam", True),
                (tid, "ফি পরিশোধের নোটিশ", "অক্টোবর মাসের ফি ১০ তারিখের মধ্যে পরিশোধ করতে হবে। বিলম্বে জরিমানা আরোপ করা হবে।", "fee", False),
                (tid, "ঈদুল আযহার ছুটি", "আগামী ১৬-২২ জুন ঈদুল আযহার ছুটি থাকবে।", "holiday", False),
            ]
            for n in notices:
                cur.execute(
                    """INSERT INTO notices (tenant_id, title, body, category, is_pinned)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", n
                )

        conn.commit()
        print("\n✅ Demo data seeded successfully!")
        print(f"   📊 {len(student_ids)} students")
        print(f"   💰 ~{len(student_ids)*10} fee vouchers")
        print(f"   📝 ~{len(student_ids)*len(subjects)} mark entries")
        print("\n🚀 Run: streamlit run app.py")
        print("   Login: admin / admin123")

    except Exception as ex:
        conn.rollback()
        print(f"❌ Seed failed: {ex}")
        import traceback; traceback.print_exc()
    finally:
        release_connection(conn)


if __name__ == "__main__":
    seed()
