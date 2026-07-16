"""
015_platform_admin.py — SaaS platform operator support

SaaS মডেলে বিক্রয়ের জন্য tenants টেবিলে status কলাম যোগ করা হচ্ছে, যাতে
non-paying/violating কাস্টমারকে ডেটা না মুছে suspend করা যায়। আর
platform_admins — tenant_id-হীন সম্পূর্ণ আলাদা identity টেবিল, প্ল্যাটফর্ম
অপারেটরের নিজস্ব লগইনের জন্য (app_users/ROLE_PERMISSIONS-এর অংশ না)।

Revision ID: 015_platform_admin
Revises: 014_admission_step3_fields
"""
from alembic import op

revision      = "015_platform_admin"
down_revision = "014_admission_step3_fields"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    ALTER TABLE tenants ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

    CREATE TABLE IF NOT EXISTS platform_admins (
        id              SERIAL PRIMARY KEY,
        username        TEXT NOT NULL UNIQUE,
        password_hash   TEXT NOT NULL,
        full_name       TEXT,
        is_active       BOOLEAN NOT NULL DEFAULT TRUE,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until    TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS platform_admins;
    ALTER TABLE tenants DROP COLUMN IF EXISTS status;
    """)
