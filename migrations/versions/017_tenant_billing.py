"""
017_tenant_billing.py — SaaS সাবস্ক্রিপশন/বিলিং সাপোর্ট

tenants টেবিলে plan_type/monthly_fee/subscription_expiry যোগ করা হচ্ছে,
আর tenant_payments — প্রতিটা ম্যানুয়াল পেমেন্ট (bKash/ব্যাংক ট্রান্সফার)
রেকর্ড করার জন্য আলাদা টেবিল। বিলিং এখনো সম্পূর্ণ ম্যানুয়াল — কোনো পেমেন্ট
গেটওয়ে ইন্টিগ্রেশন নেই, সুপার এডমিন নিজে টাকা পাওয়ার পর "Mark Paid" চাপে।

বিদ্যমান tenant-দের (যেমন Demo Madrasa) subscription_expiry NULL থাকলে
migration চালানোর সময় থেকে ৩০ দিন গ্রেস পিরিয়ড দেওয়া হচ্ছে, যাতে migration-এর
সাথে সাথেই তারা "overdue" না দেখায়।

Revision ID: 017_tenant_billing
Revises: 016_user_2fa_last_used
"""
from alembic import op

revision      = "017_tenant_billing"
down_revision = "016_user_2fa_last_used"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_type TEXT NOT NULL DEFAULT 'trial';
    ALTER TABLE tenants ADD COLUMN IF NOT EXISTS monthly_fee NUMERIC(10,2) NOT NULL DEFAULT 0;
    ALTER TABLE tenants ADD COLUMN IF NOT EXISTS subscription_expiry DATE;

    UPDATE tenants SET subscription_expiry = (NOW() + INTERVAL '30 days')::date
      WHERE subscription_expiry IS NULL;

    CREATE TABLE IF NOT EXISTS tenant_payments (
        id              SERIAL PRIMARY KEY,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        amount          NUMERIC(10,2) NOT NULL,
        payment_method  TEXT,
        months_covered  INTEGER NOT NULL DEFAULT 1,
        recorded_by     TEXT,
        notes           TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_tenant_payments_tenant ON tenant_payments(tenant_id, created_at);
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS tenant_payments;
    ALTER TABLE tenants DROP COLUMN IF EXISTS plan_type;
    ALTER TABLE tenants DROP COLUMN IF EXISTS monthly_fee;
    ALTER TABLE tenants DROP COLUMN IF EXISTS subscription_expiry;
    """)
