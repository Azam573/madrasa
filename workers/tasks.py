"""
workers/tasks.py — সব Background Tasks
SMS, WhatsApp, fee reminder, monthly report, cache warmup।
"""

import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import date, datetime
from workers.celery_app import celery_app

logger = logging.getLogger("madrasa.tasks")

# PII Masking import (v9.0)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from mask_pii import mask_phone, mask_name, safe_log
from error_handler import safe_db_error
import monitoring


# ─────────────────────────────────────────────────────────────────
# Celery DB Connection Pool (v9.0 fix)
#
# আগে: প্রতিটি _fetchall/_fetchone-এ psycopg2.connect() → নতুন connection
# এখন: ThreadedConnectionPool (min=1, max=5) — connection reuse হয়
#
# Celery-র জন্য আলাদা pool রাখা হয়েছে (db.py-র pool আলাদা process-এ)।
# max=5 — Celery worker concurrency=4 + ১টি buffer
# ─────────────────────────────────────────────────────────────────

import threading
import psycopg2
import psycopg2.pool
import psycopg2.extras

_celery_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_celery_pool_lock = threading.Lock()


def _get_celery_pool() -> psycopg2.pool.ThreadedConnectionPool | None:
    """Lazy-init singleton pool — thread-safe double-check locking।"""
    global _celery_pool
    if _celery_pool is not None:
        return _celery_pool
    with _celery_pool_lock:
        if _celery_pool is not None:
            return _celery_pool
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            logger.error("DATABASE_URL নেই — Celery DB pool তৈরি ব্যর্থ।")
            return None
        try:
            _celery_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=5,
                dsn=dsn,
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
            logger.info("✅ Celery DB pool তৈরি হয়েছে (min=1, max=5)")
        except Exception as ex:
            logger.error(f"Celery DB pool error: {ex}")
            return None
    return _celery_pool


def _get_db_conn():
    """Pool থেকে connection নেয়। ব্যবহার শেষে _release_conn() করতে হবে।"""
    pool = _get_celery_pool()
    if not pool:
        # Fallback: direct connect (pool না থাকলে)
        return psycopg2.connect(
            os.environ.get("DATABASE_URL", ""),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    try:
        conn = pool.getconn()
        conn.autocommit = False
        return conn
    except psycopg2.pool.PoolError:
        logger.warning("Celery pool exhausted — direct connect fallback")
        return psycopg2.connect(
            os.environ.get("DATABASE_URL", ""),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )


def _release_conn(conn):
    """Connection pool-এ ফেরত দেয়।"""
    if conn is None:
        return
    pool = _get_celery_pool()
    if pool:
        try:
            pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    else:
        try:
            conn.close()
        except Exception:
            pass


def _fetchall(sql, params=()):
    """Pool থেকে connection নিয়ে SELECT চালায়, তারপর ফেরত দেয়।"""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception as ex:
        logger.error(f"Celery fetchall error: {ex}")
        return []
    finally:
        _release_conn(conn)


def _fetchall_strict(sql, params=()):
    """_fetchall()-এর মতো, কিন্তু ব্যর্থতা swallow করে না — raise করে।
    শুধু automated backup tasks-এর per-table loop-এ ব্যবহৃত, যেখানে
    'টেবিল সত্যিই খালি' বনাম 'fetch ব্যর্থ' আলাদা করা জরুরি। _fetchall()
    ইচ্ছাকৃতভাবে অপরিবর্তিত — এর ১৫টি অন্য call site-এর swallow-and-log
    আচরণে নির্ভরতা এই fix-এর scope-এর বাইরে।"""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        _release_conn(conn)


def _fetchone(sql, params=()):
    """Pool থেকে connection নিয়ে SELECT ONE চালায়, তারপর ফেরত দেয়।"""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    except Exception as ex:
        logger.error(f"Celery fetchone error: {ex}")
        return None
    finally:
        _release_conn(conn)


# ─────────────────────────────────────────────
# SMS / WhatsApp helpers
# ─────────────────────────────────────────────

def _send_whatsapp(phone: str, message: str) -> bool:
    instance_id = os.environ.get("WA_INSTANCE_ID", "")
    token       = os.environ.get("WA_TOKEN", "")
    if not instance_id or not token:
        logger.warning("WhatsApp credentials নেই।")
        return False
    try:
        url     = f"https://api.ultramsg.com/{instance_id}/messages/chat"
        payload = urllib.parse.urlencode({
            "token": token, "to": phone, "body": message, "priority": "1"
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            return "sent" in str(result).lower()
    except Exception as ex:
        logger.error(f"WhatsApp send failed: {ex}")
        return False


def _send_sms(phone: str, message: str) -> bool:
    from sms_gateway import send_sms
    return send_sms(phone, message)


# ─────────────────────────────────────────────
# Individual message tasks
# ─────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_whatsapp_message(self, phone: str, message: str, student_name: str = ""):
    """একটি WhatsApp বার্তা পাঠান।"""
    try:
        success = _send_whatsapp(phone, message)
        if success:
            logger.info(safe_log("✅ WhatsApp sent", phone=phone, name=student_name))
            return {"status": "sent", "phone": phone}
        else:
            raise Exception("WhatsApp send returned False")
    except Exception as exc:
        logger.warning(safe_log("Retry WhatsApp", phone=phone) + f" | error={type(exc).__name__}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_sms_message(self, phone: str, message: str, student_name: str = ""):
    """একটি SMS পাঠান।"""
    try:
        success = _send_sms(phone, message)
        if success:
            logger.info(safe_log("✅ SMS sent", phone=phone, name=student_name))
            return {"status": "sent", "phone": phone}
        else:
            raise Exception("SMS send returned False")
    except Exception as exc:
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────
# Bulk alert tasks
# ─────────────────────────────────────────────

@celery_app.task(name="workers.tasks.daily_absence_alert")
def daily_absence_alert():
    """
    প্রতিদিন সকাল ৮টায় চলে।
    আজকের অনুপস্থিত ছাত্রদের অভিভাবককে WhatsApp/SMS।
    """
    today = str(date.today())
    logger.info(f"🔔 Daily absence alert — {today}")

    tenants = _fetchall("SELECT id, madrasa_name FROM tenants")
    total_sent = 0

    for tenant in tenants:
        tid = tenant["id"]
        madrasa = tenant["madrasa_name"]

        absent_students = _fetchall(
            """SELECT s.name, s.mobile_no
               FROM attendance a
               JOIN student_enrollments e ON e.id=a.enrollment_id
               JOIN students s ON s.id=e.student_id
               WHERE a.tenant_id=%s AND a.date=%s
                 AND a.status='absent' AND s.mobile_no IS NOT NULL""",
            (tid, today),
        )

        for stu in absent_students:
            msg = (
                f"🕌 *{madrasa}*\n\n"
                f"প্রিয় অভিভাবক,\n"
                f"আপনার সন্তান *{stu['name']}* আজ *{today}* তারিখে"
                f" মাদ্রাসায় অনুপস্থিত ছিলেন।\n"
                f"অনুগ্রহ করে অফিসে জানান।"
            )
            send_whatsapp_message.delay(stu["mobile_no"], msg, stu["name"])
            total_sent += 1

    logger.info(f"✅ Daily absence alert: {total_sent} messages queued")
    return {"date": today, "messages_queued": total_sent}


@celery_app.task(name="workers.tasks.monthly_fee_reminder")
def monthly_fee_reminder():
    """
    প্রতি মাসের ১ তারিখ সকাল ৯টায়।
    বকেয়া ফি সহ সব অভিভাবককে reminder।
    """
    logger.info("💰 Monthly fee reminder started")
    tenants = _fetchall("SELECT id, madrasa_name FROM tenants")
    total_sent = 0

    for tenant in tenants:
        tid     = tenant["id"]
        madrasa = tenant["madrasa_name"]

        due_students = _fetchall(
            """SELECT s.name, s.mobile_no,
                      COUNT(v.id) AS due_count,
                      SUM(v.amount) AS total_due
               FROM fee_vouchers v
               JOIN students s ON s.id=v.student_id
               WHERE v.tenant_id=%s AND v.status='unpaid'
                 AND s.mobile_no IS NOT NULL AND s.status='active'
               GROUP BY s.id, s.name, s.mobile_no
               ORDER BY total_due DESC""",
            (tid,),
        )

        for stu in due_students:
            msg = (
                f"🕌 *{madrasa}*\n\n"
                f"প্রিয় অভিভাবক,\n"
                f"আপনার সন্তান *{stu['name']}*-এর\n"
                f"*{int(stu['due_count'])}টি* বকেয়া ফি রয়েছে।\n"
                f"মোট: *৳{float(stu['total_due']):,.0f}*\n\n"
                f"অনুগ্রহ করে দ্রুত পরিশোধ করুন।\nধন্যবাদ।"
            )
            send_whatsapp_message.delay(stu["mobile_no"], msg, stu["name"])
            total_sent += 1

    logger.info(f"✅ Monthly fee reminder: {total_sent} messages queued")
    return {"messages_queued": total_sent}


@celery_app.task(name="workers.tasks.donor_payment_reminder")
def donor_payment_reminder():
    """
    প্রতি মাসের ৫ তারিখ সকাল ৯টায় (fee reminder-এর কয়েকদিন পরে, যেন
    বার্তা একসাথে জমে না যায়)।
    যেসব active donor এই মাসে এখনো চাঁদা দেননি তাদের WhatsApp reminder।
    """
    logger.info("❤️ Donor payment reminder started")
    month_name = date.today().strftime("%B")
    year       = date.today().year
    tenants    = _fetchall("SELECT id, madrasa_name FROM tenants")
    total_sent = 0

    for tenant in tenants:
        tid     = tenant["id"]
        madrasa = tenant["madrasa_name"]

        unpaid_donors = _fetchall(
            """SELECT d.name, d.mobile_no, d.monthly_amount
               FROM monthly_donors d
               WHERE d.tenant_id=%s AND d.status='active' AND d.mobile_no IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM donor_payments p
                     WHERE p.donor_id=d.id AND p.month_name=%s AND p.year=%s
                 )""",
            (tid, month_name, year),
        )

        for donor in unpaid_donors:
            msg = (
                f"🕌 *{madrasa}*\n\n"
                f"প্রিয় {donor['name']},\n"
                f"*{month_name}* মাসের মাসিক চাঁদা (*৳{float(donor['monthly_amount']):,.0f}*)"
                f" এখনো পরিশোধ হয়নি।\n\n"
                f"অনুগ্রহ করে দ্রুত পরিশোধ করুন। ধন্যবাদ।"
            )
            send_whatsapp_message.delay(donor["mobile_no"], msg, donor["name"])
            total_sent += 1

    logger.info(f"✅ Donor payment reminder: {total_sent} messages queued")
    return {"messages_queued": total_sent, "month": f"{month_name} {year}"}


@celery_app.task(name="workers.tasks.weekly_due_alert")
def weekly_due_alert():
    """
    প্রতি রবিবার সকাল ১০টায়।
    গত সপ্তাহে পেমেন্ট হয়নি এমন ছাত্রদের alert।
    """
    logger.info("📅 Weekly due alert started")
    tenants    = _fetchall("SELECT id, madrasa_name FROM tenants")
    total_sent = 0

    for tenant in tenants:
        tid     = tenant["id"]
        madrasa = tenant["madrasa_name"]

        overdue = _fetchall(
            """SELECT s.name, s.mobile_no, v.due_date, v.amount, v.month_name
               FROM fee_vouchers v
               JOIN students s ON s.id=v.student_id
               WHERE v.tenant_id=%s AND v.status='unpaid'
                 AND v.due_date < CURRENT_DATE
                 AND s.mobile_no IS NOT NULL AND s.status='active'
               ORDER BY v.due_date ASC LIMIT 200""",
            (tid,),
        )

        for r in overdue:
            overdue_days = (date.today() - r["due_date"]).days if r["due_date"] else 0
            msg = (
                f"⚠️ *{madrasa} — ফি বকেয়া নোটিশ*\n\n"
                f"ছাত্র: *{r['name']}*\n"
                f"মাস: {r['month_name']}\n"
                f"পরিমাণ: ৳{float(r['amount']):,.0f}\n"
                f"মেয়াদ পেরিয়েছে: *{overdue_days} দিন*\n\n"
                f"অনুগ্রহ করে আজই পরিশোধ করুন।"
            )
            send_whatsapp_message.delay(r["mobile_no"], msg, r["name"])
            total_sent += 1

    return {"messages_queued": total_sent}


# ─────────────────────────────────────────────
# Cache warmup task
# ─────────────────────────────────────────────

@celery_app.task(name="workers.tasks.warmup_dashboard_cache")
def warmup_dashboard_cache():
    """
    প্রতিদিন রাত ১১টায়।
    সব tenant-এর dashboard cache pre-warm করে।
    পরদিন সকালে ব্যবহারকারীরা দ্রুত data পাবেন।
    """
    try:
        from cache import cache_set, cache_key, TTL
    except ImportError:
        logger.warning("cache module পাওয়া যায়নি।")
        return {"status": "skipped"}

    logger.info("🔥 Dashboard cache warmup started")
    tenants   = _fetchall("SELECT id FROM tenants")
    warmed    = 0
    yr        = date.today().year

    for tenant in tenants:
        tid = tenant["id"]

        # KPI data
        kpi = {
            "active_students": _fetchone(
                "SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='active'", (tid,)
            ),
            "pending": _fetchone(
                "SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='pending'", (tid,)
            ),
            "collected": _fetchone(
                "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='paid' AND year=%s",
                (tid, yr),
            ),
            "outstanding": _fetchone(
                "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='unpaid' AND year=%s",
                (tid, yr),
            ),
        }
        key = cache_key("dashboard_kpi", tid)
        cache_set(key, {k: dict(v) if v else {} for k, v in kpi.items()},
                  TTL["dashboard_kpi"])
        warmed += 1

        # Class list (rarely changes)
        classes = _fetchall(
            "SELECT id, class_name, class_numeric FROM classes WHERE tenant_id=%s ORDER BY class_numeric",
            (tid,),
        )
        key2 = cache_key("class_list", tid)
        cache_set(key2, [dict(c) for c in classes], TTL["class_list"])

    logger.info(f"✅ Cache warmed for {warmed} tenants")
    return {"tenants_warmed": warmed, "timestamp": str(datetime.now())}


# ─────────────────────────────────────────────
# Monthly report task
# ─────────────────────────────────────────────

@celery_app.task(name="workers.tasks.generate_monthly_report")
def generate_monthly_report():
    """
    প্রতি মাসের ২৮ তারিখ রাত ১০টায়।
    মাসিক summary report তৈরি করে Admin-কে notify করে।
    """
    logger.info("📊 Monthly report generation started")
    tenants = _fetchall("SELECT id, madrasa_name, email FROM tenants")
    reports = []

    for tenant in tenants:
        tid     = tenant["id"]
        madrasa = tenant["madrasa_name"]

        # This month stats
        month_name = date.today().strftime("%B")
        yr         = date.today().year

        stats = {
            "madrasa":        madrasa,
            "month":          month_name,
            "year":           yr,
            "active_students": dict(_fetchone(
                "SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='active'", (tid,)
            ) or {}),
            "fee_collected": dict(_fetchone(
                """SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers
                   WHERE tenant_id=%s AND status='paid' AND month_name=%s AND year=%s""",
                (tid, month_name, yr),
            ) or {}),
            "attendance_avg": dict(_fetchone(
                """SELECT ROUND(
                     COUNT(CASE WHEN status='present' THEN 1 END)::numeric /
                     NULLIF(COUNT(*), 0) * 100, 1
                   ) AS n FROM attendance WHERE tenant_id=%s
                   AND EXTRACT(MONTH FROM date)=EXTRACT(MONTH FROM CURRENT_DATE)
                   AND EXTRACT(YEAR FROM date)=EXTRACT(YEAR FROM CURRENT_DATE)""",
                (tid,),
            ) or {}),
        }
        reports.append(stats)

        # Admin-কে notification
        admin = _fetchone(
            "SELECT u.email FROM app_users u WHERE u.tenant_id=%s AND u.role='admin' LIMIT 1",
            (tid,),
        )
        if admin and admin.get("email"):
            logger.info(f"📧 Monthly report ready for {madrasa} → {admin['email']}")

    logger.info(f"✅ Monthly reports generated for {len(reports)} tenants")
    return {"reports": len(reports), "month": date.today().strftime("%B %Y")}


# ─────────────────────────────────────────────
# System health check task
# ─────────────────────────────────────────────

@celery_app.task(name="workers.tasks.system_health_check")
def system_health_check():
    """
    প্রতিদিন রাত ২টায়।
    DB connection, cache, disk space চেক করে।
    সমস্যা হলে admin-কে alert পাঠায়।
    """
    logger.info("🏥 System health check started")
    issues = []

    # DB check
    try:
        result = _fetchone("SELECT COUNT(*) AS n FROM tenants")
        db_status = "healthy"
    except Exception as ex:
        db_status = f"error: {ex}"
        issues.append(f"Database: {ex}")

    # Cache check
    try:
        from cache import cache_health
        cache_status = cache_health()
    except Exception as ex:
        cache_status = {"status": "error", "error": str(ex)}
        issues.append(f"Cache: {ex}")

    result = {
        "timestamp":    str(datetime.now()),
        "db_status":    db_status,
        "cache_status": cache_status,
        "issues":       issues,
        "healthy":      len(issues) == 0,
    }

    if issues:
        logger.error(f"⚠️ Health check issues: {issues}")
    else:
        logger.info("✅ System health check passed")

    return result


# ─────────────────────────────────────────────
# On-demand tasks (manually triggered)
# ─────────────────────────────────────────────

@celery_app.task
def send_exam_results_notification(tenant_id: int, exam_id: int):
    """পরীক্ষার ফলাফল প্রকাশ হলে সব অভিভাবককে notify।"""
    tenant = _fetchone("SELECT madrasa_name FROM tenants WHERE id=%s", (tenant_id,))
    madrasa = tenant["madrasa_name"] if tenant else "মাদ্রাসা"

    exam = _fetchone("SELECT exam_name FROM exams WHERE id=%s", (exam_id,))
    exam_name = exam["exam_name"] if exam else "পরীক্ষা"

    results = _fetchall(
        """SELECT s.name, s.mobile_no,
                  SUM(sm.total_obtained) AS obtained,
                  SUM(subj.full_marks) AS full_marks
           FROM student_marks sm
           JOIN student_enrollments e ON e.id=sm.enrollment_id
           JOIN students s ON s.id=e.student_id
           JOIN subjects subj ON subj.id=sm.subject_id
           WHERE sm.tenant_id=%s AND sm.exam_id=%s AND s.mobile_no IS NOT NULL
           GROUP BY s.id, s.name, s.mobile_no""",
        (tenant_id, exam_id),
    )

    sent = 0
    for r in results:
        fm  = float(r["full_marks"] or 1)
        obt = float(r["obtained"] or 0)
        pct = round(obt / fm * 100, 1)
        msg = (
            f"🕌 *{madrasa}*\n\n"
            f"📝 *{exam_name}* ফলাফল প্রকাশিত!\n\n"
            f"ছাত্র: *{r['name']}*\n"
            f"প্রাপ্ত নম্বর: *{obt:.0f}/{fm:.0f}*\n"
            f"শতকরা: *{pct}%*\n\n"
            f"বিস্তারিত মাদ্রাসায় যোগাযোগ করুন।"
        )
        send_whatsapp_message.delay(r["mobile_no"], msg, r["name"])
        sent += 1

    return {"exam_id": exam_id, "notifications_sent": sent}


@celery_app.task
def clear_tenant_cache(tenant_id: int):
    """নির্দিষ্ট tenant-এর সব cache clear করুন।"""
    try:
        from cache import invalidate_tenant
        deleted = invalidate_tenant(tenant_id)
        logger.info(f"Cache cleared for tenant {tenant_id}: {deleted} keys")
        return {"tenant_id": tenant_id, "keys_deleted": deleted}
    except Exception as ex:
        return {"error": str(ex)}


@celery_app.task
def generate_fee_vouchers_bulk(tenant_id: int, month_name: str, year: int, fund_type: str = "general"):
    """
    সব active ছাত্রের জন্য একসাথে fee voucher তৈরি করে।
    Finance মডিউল থেকে trigger করুন।
    """
    import psycopg2
    import psycopg2.extras

    students = _fetchall(
        """SELECT s.id AS student_id, e.id AS enrollment_id, e.monthly_fee
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           WHERE s.tenant_id=%s AND s.status='active' AND e.enrollment_status='active'""",
        (tenant_id,),
    )

    conn = _get_db_conn()
    conn.autocommit = False
    created = 0
    skipped = 0

    try:
        with conn.cursor() as cur:
            for stu in students:
                # Check duplicate
                cur.execute(
                    """SELECT id FROM fee_vouchers
                       WHERE tenant_id=%s AND student_id=%s
                         AND month_name=%s AND year=%s AND fund_type=%s""",
                    (tenant_id, stu["student_id"], month_name, year, fund_type),
                )
                if cur.fetchone():
                    skipped += 1
                    continue

                cur.execute(
                    "SELECT COUNT(*)+1 AS n FROM fee_vouchers WHERE tenant_id=%s",
                    (tenant_id,)
                )
                n = cur.fetchone()["n"]
                voucher_no = f"VCH-{tenant_id:03d}-{n:05d}"

                cur.execute(
                    """INSERT INTO fee_vouchers
                       (tenant_id, enrollment_id, student_id, voucher_no,
                        month_name, year, amount, fund_type, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'unpaid')""",
                    (tenant_id, stu["enrollment_id"], stu["student_id"],
                     voucher_no, month_name, year, stu["monthly_fee"], fund_type),
                )
                created += 1

        conn.commit()
        logger.info(f"✅ Bulk vouchers: {created} created, {skipped} skipped for tenant {tenant_id}")
        return {"created": created, "skipped": skipped, "month": f"{month_name} {year}"}

    except Exception as ex:
        conn.rollback()
        logger.error(f"Bulk voucher creation failed: {type(ex).__name__}")
        return {"error": type(ex).__name__}
    finally:
        # Pool-এ ফেরত দেওয়া হচ্ছে — conn.close() নয় (v9.0 fix)
        _release_conn(conn)


# ─────────────────────────────────────────────────────────────────
# Automated Backup Tasks (v9.0)
# Celery Beat দ্বারা নিয়মিত চালানো হয়।
# প্রতিদিন রাত ৩টায় ও প্রতি শনিবার রাত ৪টায়।
# ─────────────────────────────────────────────────────────────────

@celery_app.task(name="workers.tasks.automated_daily_backup")
def automated_daily_backup():
    """
    প্রতিদিন রাত ৩টায় সব tenant-এর backup নেয়।
    JSON format-এ — Supabase storage বা local-এ সেভ করে।
    """
    import json
    from datetime import date

    tenants = _fetchall("SELECT id, madrasa_name FROM tenants WHERE id > 0")
    results = []

    for tenant in tenants:
        tid  = tenant["id"]
        name = tenant["madrasa_name"]

        # Hardcoded backup queries (SQL injection safe)
        BACKUP_TABLES = {
            "students":            "SELECT * FROM students WHERE tenant_id=%s LIMIT 50000",
            "student_enrollments": "SELECT * FROM student_enrollments WHERE tenant_id=%s LIMIT 50000",
            "fee_vouchers":        "SELECT * FROM fee_vouchers WHERE tenant_id=%s LIMIT 50000",
            "fee_payments":        "SELECT * FROM fee_payments WHERE tenant_id=%s LIMIT 50000",
            "attendance":          "SELECT * FROM attendance WHERE tenant_id=%s LIMIT 50000",
            "student_marks":       "SELECT * FROM student_marks WHERE tenant_id=%s LIMIT 50000",
        }

        backup = {
            "tenant_id":   tid,
            "madrasa":     name,
            "backup_type": "daily",
            "backup_date": str(date.today()),
            "tables":      {},
        }

        total_rows = 0
        failed_tables = []
        for table, sql in BACKUP_TABLES.items():
            try:
                rows = _fetchall_strict(sql, (tid,))
                backup["tables"][table] = [dict(r) for r in rows]
                total_rows += len(rows)
            except Exception as ex:
                backup["tables"][table] = []
                failed_tables.append(table)
                safe_db_error(ex, f"automated_daily_backup tenant={tid} table={table}")

        # Storage-এ সেভ করা
        backup_saved = _save_backup_to_storage(tid, backup, backup_type="daily")
        status = "partial" if failed_tables else "success"
        results.append({
            "tenant_id":     tid,
            "rows":          total_rows,
            "saved":         backup_saved,
            "status":        status,
            "failed_tables": failed_tables,
        })
        if failed_tables:
            logger.error(safe_log(
                f"⚠️ Daily backup PARTIAL: tenant#{tid}",
                tenant_id=tid,
            ) + f" | failed_tables={failed_tables} | rows={total_rows} | saved={backup_saved}")
            monitoring.capture_message(
                f"Automated daily backup partial failure for tenant {tid}: "
                f"failed to fetch tables {failed_tables}",
                level="error",
                context={"tenant_id": tid, "backup_type": "daily", "failed_tables": failed_tables},
            )
        else:
            logger.info(safe_log(
                f"Daily backup completed: tenant#{tid}",
                tenant_id=tid,
            ) + f" | rows={total_rows} | saved={backup_saved}")

    return {"backups": results, "date": str(date.today())}


@celery_app.task(name="workers.tasks.automated_weekly_backup")
def automated_weekly_backup():
    """
    প্রতি শনিবার রাত ৪টায় weekly backup।
    Daily backup-এর চেয়ে বেশি দিন retain করা হয়।
    """
    import json
    from datetime import date

    tenants = _fetchall("SELECT id, madrasa_name FROM tenants WHERE id > 0")
    results = []

    for tenant in tenants:
        tid  = tenant["id"]
        name = tenant["madrasa_name"]

        # Weekly backup-এ সব table
        WEEKLY_TABLES = {
            "students":            "SELECT * FROM students WHERE tenant_id=%s",
            "student_enrollments": "SELECT * FROM student_enrollments WHERE tenant_id=%s",
            "fee_vouchers":        "SELECT * FROM fee_vouchers WHERE tenant_id=%s",
            "fee_payments":        "SELECT * FROM fee_payments WHERE tenant_id=%s",
            "attendance":          "SELECT * FROM attendance WHERE tenant_id=%s LIMIT 100000",
            "student_marks":       "SELECT * FROM student_marks WHERE tenant_id=%s",
            "classes":             "SELECT * FROM classes WHERE tenant_id=%s",
            "academic_sessions":   "SELECT * FROM academic_sessions WHERE tenant_id=%s",
            "subjects":            "SELECT * FROM subjects WHERE tenant_id=%s",
            "teachers":            "SELECT * FROM teachers WHERE tenant_id=%s",
            "exams":               "SELECT * FROM exams WHERE tenant_id=%s",
            "notices":             "SELECT * FROM notices WHERE tenant_id=%s",
            "audit_logs":          "SELECT * FROM audit_logs WHERE tenant_id=%s AND created_at > NOW() - INTERVAL '30 days'",
        }

        backup = {
            "tenant_id":   tid,
            "madrasa":     name,
            "backup_type": "weekly",
            "backup_date": str(date.today()),
            "tables":      {},
        }

        failed_tables = []
        for table, sql in WEEKLY_TABLES.items():
            try:
                rows = _fetchall_strict(sql, (tid,))
                backup["tables"][table] = [dict(r) for r in rows]
            except Exception as ex:
                backup["tables"][table] = []
                failed_tables.append(table)
                safe_db_error(ex, f"automated_weekly_backup tenant={tid} table={table}")

        backup_saved = _save_backup_to_storage(tid, backup, backup_type="weekly")
        status = "partial" if failed_tables else "success"
        results.append({
            "tenant_id":     tid,
            "saved":         backup_saved,
            "status":        status,
            "failed_tables": failed_tables,
        })
        if failed_tables:
            logger.error(f"⚠️ Weekly backup PARTIAL: tenant#{tid} | failed_tables={failed_tables} | saved={backup_saved}")
            monitoring.capture_message(
                f"Automated weekly backup partial failure for tenant {tid}: "
                f"failed to fetch tables {failed_tables}",
                level="error",
                context={"tenant_id": tid, "backup_type": "weekly", "failed_tables": failed_tables},
            )
        else:
            logger.info(f"Weekly backup completed: tenant#{tid} | saved={backup_saved}")

    return {"backups": results, "date": str(date.today())}


@celery_app.task(name="workers.tasks.subscription_expiry_reminder")
def subscription_expiry_reminder():
    """
    প্রতিদিন সকাল ৬টায় — check_expired_subscriptions()-এর (সকাল ৭টা,
    auto-suspend) আগে চলে। এতদিন শুধু auto-suspend ছিল, আগে থেকে কোনো
    সতর্কতা ছিল না — tenant admin হঠাৎ suspend হয়ে টের পেতেন। এখন
    মেয়াদ শেষ হওয়ার ৩ দিন ও ১ দিন আগে WhatsApp reminder পাঠানো হয়।
    """
    logger.info("⏰ Subscription expiry reminder started")
    total_sent = 0

    tenants = _fetchall(
        """SELECT id, madrasa_name, phone, subscription_expiry FROM tenants
           WHERE status='active' AND phone IS NOT NULL
             AND subscription_expiry IS NOT NULL
             AND (subscription_expiry - CURRENT_DATE) IN (3, 1)"""
    )

    for tenant in tenants:
        days_left = (tenant["subscription_expiry"] - date.today()).days
        msg = (
            f"🕌 *{tenant['madrasa_name']}*\n\n"
            f"আপনার Smart Madrasa ERP সাবস্ক্রিপশনের মেয়াদ *{days_left} দিন* পরে শেষ হবে।\n"
            f"মেয়াদ শেষ হওয়ার পরও সার্ভিস চালু রাখতে অনুগ্রহ করে দ্রুত সাবস্ক্রিপশন "
            f"রিনিউ করুন — নাহলে অ্যাকাউন্ট স্বয়ংক্রিয়ভাবে স্থগিত হয়ে যাবে।\n\n"
            f"সহায়তার জন্য যোগাযোগ করুন।"
        )
        send_whatsapp_message.delay(tenant["phone"], msg, tenant["madrasa_name"])
        total_sent += 1

    logger.info(f"✅ Subscription expiry reminder: {total_sent} messages queued")
    return {"messages_queued": total_sent, "date": str(date.today())}


@celery_app.task(name="workers.tasks.check_expired_subscriptions")
def check_expired_subscriptions():
    """
    প্রতিদিন সকালে চেক করে — কোন tenant-এর subscription_expiry ৩ দিনের বেশি
    আগে পার হয়ে গেছে (grace period) অথচ এখনো 'active'। সেগুলোকে auto-suspend
    করে দেয়, যাতে অ-পেমেন্টকারী কাস্টমার নিজে থেকে বন্ধ হয়ে যায় — সুপার
    এডমিনকে ম্যানুয়ালি প্রতিদিন চেক করতে হয় না।

    গ্রেস পিরিয়ড ৩ দিন — যাতে মেয়াদ শেষ হওয়ার দিনই হঠাৎ বন্ধ না হয়ে যায়,
    কাস্টমারকে টাকা পাঠানোর একটু সময় দেওয়া হয়। tenant নিজের audit_logs-এও
    এই সাসপেনশনের কারণ দেখতে পাবে (স্বচ্ছতার জন্য)।
    """
    GRACE_DAYS = 3
    conn = _get_db_conn()
    suspended = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, madrasa_name FROM tenants
                   WHERE status='active'
                     AND subscription_expiry IS NOT NULL
                     AND subscription_expiry < CURRENT_DATE - INTERVAL '%s days'""",
                (GRACE_DAYS,),
            )
            overdue = cur.fetchall()
            for tenant in overdue:
                tid = tenant["id"]
                cur.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (tid,))
                cur.execute(
                    """INSERT INTO audit_logs (tenant_id, username, action, module, description)
                       VALUES (%s,'system:auto-suspend','UPDATE','Platform',%s)""",
                    (tid, f"সাবস্ক্রিপশনের মেয়াদ {GRACE_DAYS} দিনের বেশি আগে শেষ হওয়ায় "
                          f"অ্যাকাউন্ট স্বয়ংক্রিয়ভাবে স্থগিত করা হয়েছে।"),
                )
                suspended.append(tid)
        conn.commit()
        if suspended:
            logger.info(f"Auto-suspended {len(suspended)} overdue tenant(s): {suspended}")
        return {"suspended_tenant_ids": suspended, "date": str(date.today())}
    except Exception as ex:
        conn.rollback()
        logger.error(f"check_expired_subscriptions failed: {ex}")
        monitoring.capture_message(
            f"check_expired_subscriptions task failed: {ex}", level="error",
        )
        return {"error": str(ex)}
    finally:
        _release_conn(conn)


def _save_backup_to_storage(tenant_id: int, backup: dict, backup_type: str = "daily") -> bool:
    """
    Backup JSON storage-এ সেভ করে।
    Provider: Supabase (production) বা local file (development)।
    """
    import json
    from datetime import date

    def _serialize(obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return str(obj)

    json_str = json.dumps(backup, default=_serialize, ensure_ascii=False)

    # ── Supabase storage ─────────────────────────────────────────
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    if supabase_url and supabase_key:
        try:
            import urllib.request
            filename   = f"backups/{backup_type}/tenant_{tenant_id}_{date.today()}.json"
            upload_url = f"{supabase_url}/storage/v1/object/madrasa-backups/{filename}"
            data       = json_str.encode("utf-8")
            req        = urllib.request.Request(
                upload_url,
                data    = data,
                method  = "POST",
                headers = {
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type":  "application/json",
                    "x-upsert":      "true",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status in (200, 201)
        except Exception as ex:
            logger.warning(f"Supabase backup failed: {type(ex).__name__} — falling back to local")

    # ── Local file fallback ──────────────────────────────────────
    try:
        backup_dir = os.path.join(os.getcwd(), "backups", backup_type)
        os.makedirs(backup_dir, exist_ok=True)
        filepath = os.path.join(backup_dir, f"tenant_{tenant_id}_{date.today()}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json_str)
        logger.info(f"Backup saved locally: {filepath}")
        return True
    except Exception as ex:
        logger.error(f"Local backup failed: {type(ex).__name__}")
        return False
