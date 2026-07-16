"""
009_exam_integrity_and_fixes.py — Brutal review-এর 🔴 ফিক্সসমূহ

১. exam_starts — server-side সময়সীমা enforce করার ভিত্তি: paper খোলার
   মুহূর্ত রেকর্ড হয়; submit-এ duration যাচাই হয়।
২. tc_number-এর global UNIQUE → per-tenant UNIQUE: আগে মাদ্রাসা A
   "TC-001" ইস্যু করলে মাদ্রাসা B আর পারত না (cross-tenant collision)।

Revision ID: 009_exam_integrity
Revises: 008_tenant_branding
"""
from alembic import op

revision      = "009_exam_integrity"
down_revision = "008_tenant_branding"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS exam_starts (
        id            SERIAL PRIMARY KEY,
        tenant_id     INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        exam_id       INTEGER NOT NULL REFERENCES online_exams(id) ON DELETE CASCADE,
        enrollment_id INTEGER NOT NULL REFERENCES student_enrollments(id) ON DELETE CASCADE,
        started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(exam_id, enrollment_id)
    );

    -- tc_number: global unique → per-tenant unique
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'tc_records_tc_number_key') THEN
            ALTER TABLE tc_records DROP CONSTRAINT tc_records_tc_number_key;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conname = 'tc_records_tenant_tc_number_key') THEN
            ALTER TABLE tc_records
                ADD CONSTRAINT tc_records_tenant_tc_number_key
                UNIQUE (tenant_id, tc_number);
        END IF;
    END $$;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS exam_starts CASCADE;
    ALTER TABLE tc_records
        DROP CONSTRAINT IF EXISTS tc_records_tenant_tc_number_key;
    """)
