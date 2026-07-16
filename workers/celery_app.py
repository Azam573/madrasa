"""
workers/celery_app.py — Celery Configuration
Background jobs: SMS, fee reminders, monthly reports,
attendance alerts, salary slip generation।
"""

import os
import logging
from celery import Celery
from celery.schedules import crontab

logger = logging.getLogger("madrasa.celery")

REDIS_URL    = os.environ.get("REDIS_URL",    "redis://localhost:6379/0")
BROKER_URL   = os.environ.get("BROKER_URL",   REDIS_URL)
BACKEND_URL  = os.environ.get("BACKEND_URL",  REDIS_URL)

# ── Celery App ───────────────────────────────────────────────────
celery_app = Celery(
    "madrasa_erp",
    broker=BROKER_URL,
    backend=BACKEND_URL,
    include=["workers.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer          = "json",
    accept_content           = ["json"],
    result_serializer        = "json",
    timezone                 = "Asia/Dhaka",
    enable_utc               = True,

    # Performance
    worker_concurrency       = 4,
    worker_prefetch_multiplier = 2,
    task_acks_late           = True,
    task_reject_on_worker_lost = True,

    # Retry policy
    task_max_retries         = 3,
    task_default_retry_delay = 60,

    # Result expiry
    result_expires           = 3600,  # 1 hour

    # Rate limits
    task_annotations = {
        "workers.tasks.send_whatsapp_message": {"rate_limit": "30/m"},
        "workers.tasks.send_sms_message":      {"rate_limit": "60/m"},
    },

    # ── Scheduled Tasks (Celery Beat) ───────────────────────────
    beat_schedule = {
        # প্রতিদিন সকাল ৮টায় অনুপস্থিতি alert
        "daily-absence-alert": {
            "task":     "workers.tasks.daily_absence_alert",
            "schedule": crontab(hour=8, minute=0),
        },
        # প্রতি মাসের ১ তারিখ সকাল ৯টায় ফি রিমাইন্ডার
        "monthly-fee-reminder": {
            "task":     "workers.tasks.monthly_fee_reminder",
            "schedule": crontab(day_of_month=1, hour=9, minute=0),
        },
        # প্রতি মাসের ৫ তারিখ সকাল ৯টায় বকেয়া দাতাদের চাঁদা রিমাইন্ডার
        # (fee reminder-এর কয়েকদিন পরে, যেন বার্তা একসাথে জমে না যায়)
        "donor-payment-reminder": {
            "task":     "workers.tasks.donor_payment_reminder",
            "schedule": crontab(day_of_month=5, hour=9, minute=0),
        },
        # প্রতি সপ্তাহে রবিবার বকেয়া alert
        "weekly-due-alert": {
            "task":     "workers.tasks.weekly_due_alert",
            "schedule": crontab(day_of_week=0, hour=10, minute=0),
        },
        # প্রতিদিন রাত ১১টায় cache warm-up
        "nightly-cache-warmup": {
            "task":     "workers.tasks.warmup_dashboard_cache",
            "schedule": crontab(hour=23, minute=0),
        },
        # প্রতি মাসের শেষ দিন স্বয়ংক্রিয় রিপোর্ট
        "monthly-auto-report": {
            "task":     "workers.tasks.generate_monthly_report",
            "schedule": crontab(day_of_month=28, hour=22, minute=0),
        },
        # প্রতিদিন রাত ২টায় DB health check
        "nightly-health-check": {
            "task":     "workers.tasks.system_health_check",
            "schedule": crontab(hour=2, minute=0),
        },

        # ── Automated Backup (v9.0) ──────────────────────────────
        # প্রতিদিন রাত ৩টায় full backup — রাতের কম traffic সময়ে
        "daily-auto-backup": {
            "task":     "workers.tasks.automated_daily_backup",
            "schedule": crontab(hour=3, minute=0),
        },
        # প্রতি সপ্তাহে শনিবার রাত ৪টায় weekly backup (বেশি retain)
        "weekly-auto-backup": {
            "task":     "workers.tasks.automated_weekly_backup",
            "schedule": crontab(day_of_week=6, hour=4, minute=0),
        },
        # প্রতিদিন সকাল ৬টায় — মেয়াদ শেষ হওয়ার ৩ ও ১ দিন আগে reminder
        # (auto-suspend চেক হওয়ার ১ ঘণ্টা আগে, যেন suspend হওয়ার আগেই
        # tenant admin জানতে পারেন)
        "subscription-expiry-reminder": {
            "task":     "workers.tasks.subscription_expiry_reminder",
            "schedule": crontab(hour=6, minute=0),
        },
        # প্রতিদিন সকাল ৭টায় মেয়াদোত্তীর্ণ সাবস্ক্রিপশন চেক ও auto-suspend (v9.0)
        "check-expired-subscriptions": {
            "task":     "workers.tasks.check_expired_subscriptions",
            "schedule": crontab(hour=7, minute=0),
        },
    },
)
