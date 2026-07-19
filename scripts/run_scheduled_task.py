"""
scripts/run_scheduled_task.py — Celery beat/worker ছাড়াই scheduled task চালানো

Free-tier deployment (Streamlit Community Cloud + Supabase) এ কোনো persistent
Celery worker/beat/Redis চালানো যায় না। এই স্ক্রিপ্ট সেই বিকল্প: GitHub Actions
cron থেকে ডাকা হয়, task_always_eager=True সেট করে (তাই workers/tasks.py-এর
কোনো ফাংশন যদি অন্য একটা Celery task-কে .delay() দিয়ে ডাকে — যেমন
monthly_fee_reminder() ভেতরে send_whatsapp_message.delay() — সেটাও broker
ছাড়াই একই প্রসেসে সরাসরি চলে)। DATABASE_URL ইত্যাদি env var হিসেবে GitHub
Actions secrets থেকে আসে।

Usage: python scripts/run_scheduled_task.py <task_name>
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

TASK_NAMES = {
    "daily_absence_alert",
    "monthly_fee_reminder",
    "donor_payment_reminder",
    "weekly_due_alert",
    "warmup_dashboard_cache",
    "generate_monthly_report",
    "system_health_check",
    "automated_daily_backup",
    "automated_weekly_backup",
    "subscription_expiry_reminder",
    "check_expired_subscriptions",
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in TASK_NAMES:
        print(f"Usage: python {sys.argv[0]} <task_name>")
        print(f"Valid task names: {', '.join(sorted(TASK_NAMES))}")
        sys.exit(1)

    task_name = sys.argv[1]

    from workers.celery_app import celery_app
    celery_app.conf.task_always_eager = True
    # Fix: propagates=True made nested .delay() calls (e.g.
    # send_whatsapp_message.delay() inside monthly_fee_reminder()) raise
    # straight into the caller the moment WA_TOKEN/SMS_API_KEY aren't
    # configured -- crashing the whole scheduled task on its first unpaid
    # voucher. A real Celery worker's .delay() never blocks/raises like
    # that; propagates=False restores that same fire-and-forget behavior
    # for eager mode (the failure is still logged, just not fatal here).
    celery_app.conf.task_eager_propagates = False

    import workers.tasks as tasks
    fn = getattr(tasks, task_name)

    print(f"Running {task_name}...")
    result = fn()
    print(f"Result: {result}")


if __name__ == "__main__":
    main()
