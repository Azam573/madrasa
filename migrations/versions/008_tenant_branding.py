"""
008_tenant_branding.py — প্রতিষ্ঠানভিত্তিক branding/customization

প্রতিটি মাদ্রাসা নিজের লোগো, আরবি/ইংরেজি নাম, রঙ, অধ্যক্ষের নাম-স্বাক্ষর,
রিসিট/TC-র ফুটার টেক্সট ইত্যাদি কাস্টমাইজ করতে পারবে — সব printable
document (ফি রিসিট, ভাউচার, TC, প্রশংসাপত্র, মার্কশিট, বেতন স্লিপ)
এই টেবিল থেকেই সাজবে।

লোগো/স্বাক্ষর base64 TEXT হিসেবে DB-তে — filesystem dependency নেই,
multi-server deploy-এ সমস্যা নেই। Application layer 200KB সীমা enforce করে।

Revision ID: 008_tenant_branding
Revises: 007_api_module_tables
"""
from alembic import op

revision      = "008_tenant_branding"
down_revision = "007_api_module_tables"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS tenant_branding (
        tenant_id        INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
        name_arabic      TEXT,
        name_english     TEXT,
        tagline          TEXT,
        established_year INTEGER,
        eiin_no          TEXT,
        reg_no           TEXT,
        principal_name   TEXT,
        principal_title  TEXT DEFAULT 'মুহতামিম',
        logo_base64      TEXT,
        logo_mime        TEXT,
        signature_base64 TEXT,
        signature_mime   TEXT,
        primary_color    TEXT DEFAULT '#0F4C5C',
        secondary_color  TEXT DEFAULT '#C9A227',
        receipt_footer   TEXT DEFAULT 'এই রিসিটটি সংগ্রহে রাখুন।',
        tc_footer        TEXT,
        show_logo        BOOLEAN DEFAULT TRUE,
        updated_at       TIMESTAMPTZ DEFAULT NOW()
    );

    -- ── Printable document-সংশ্লিষ্ট টেবিল (আগে শুধু runtime-এ তৈরি হতো) ──
    CREATE TABLE IF NOT EXISTS tc_records (
        id             SERIAL PRIMARY KEY,
        tenant_id      INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        student_id     INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        tc_number      TEXT UNIQUE,
        issue_date     DATE DEFAULT CURRENT_DATE,
        reason         TEXT,
        last_class     TEXT,
        last_session   TEXT,
        conduct        TEXT DEFAULT 'ভালো',
        attendance_pct NUMERIC(5,1),
        remarks        TEXT,
        issued_by      TEXT,
        created_at     TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS student_documents (
        id          SERIAL PRIMARY KEY,
        tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        doc_type    TEXT,
        file_name   TEXT,
        mime_type   TEXT,
        file_data   TEXT,
        uploaded_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS holidays (
        id          SERIAL PRIMARY KEY,
        tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        holiday_date DATE NOT NULL,
        description TEXT,
        UNIQUE(tenant_id, holiday_date)
    );
    CREATE TABLE IF NOT EXISTS teacher_attendance (
        id          SERIAL PRIMARY KEY,
        tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
        date        DATE NOT NULL,
        status      TEXT DEFAULT 'present',
        punch_time  TEXT,
        UNIQUE(tenant_id, teacher_id, date)
    );
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS teacher_attendance CASCADE;
    DROP TABLE IF EXISTS holidays CASCADE;
    DROP TABLE IF EXISTS student_documents CASCADE;
    DROP TABLE IF EXISTS tc_records CASCADE;
    DROP TABLE IF EXISTS tenant_branding;
    """)
