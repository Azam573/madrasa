"""
007_api_module_tables.py — Module টেবিলগুলো migration-এর নিয়ন্ত্রণে

আগে notices, timetable, zakat, exams, teachers টেবিলগুলো তৈরি হতো
Streamlit module-এর runtime `_ensure_tables()` থেকে — অর্থাৎ কেউ UI-র
সেই পেজ না খুললে টেবিলই থাকত না, আর API worker কখনোই তৈরি করত না।
এখন alembic-ই একমাত্র schema source of truth; নতুন deploy-এ
`alembic upgrade head` চালালেই সম্পূর্ণ schema পাওয়া যায়।

সব CREATE TABLE IF NOT EXISTS — বিদ্যমান ডেটার উপর নিরাপদ।

Revision ID: 007_api_module_tables
Revises: 006_performance_indexes
"""
from alembic import op

revision      = "007_api_module_tables"
down_revision = "006_performance_indexes"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    -- ── Notices ─────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS notices (
        id           SERIAL PRIMARY KEY,
        tenant_id    INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        title        TEXT NOT NULL,
        body         TEXT NOT NULL,
        category     TEXT DEFAULT 'general',
        target_class INTEGER REFERENCES classes(id) ON DELETE SET NULL,
        is_pinned    BOOLEAN DEFAULT FALSE,
        publish_date DATE DEFAULT CURRENT_DATE,
        expiry_date  DATE,
        created_by   TEXT DEFAULT 'Admin',
        created_at   TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_notices_tenant
        ON notices(tenant_id, publish_date DESC);

    -- ── Timetable ───────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS timetable (
        id           SERIAL PRIMARY KEY,
        tenant_id    INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        class_id     INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        session_id   INTEGER NOT NULL REFERENCES academic_sessions(id) ON DELETE CASCADE,
        day_of_week  TEXT NOT NULL,
        period_no    INTEGER NOT NULL,
        start_time   TIME,
        end_time     TIME,
        subject_id   INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
        teacher_id   INTEGER,
        room_no      TEXT,
        UNIQUE(tenant_id, class_id, session_id, day_of_week, period_no)
    );

    -- ── Zakat ───────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS zakat_collections (
        id              SERIAL PRIMARY KEY,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        donor_name      TEXT,
        donor_mobile    TEXT,
        amount          NUMERIC(12,2) NOT NULL,
        collection_date DATE DEFAULT CURRENT_DATE,
        collection_year INTEGER,
        zakat_type      TEXT DEFAULT 'zakat',
        receipt_no      TEXT,
        notes           TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS zakat_distributions (
        id                SERIAL PRIMARY KEY,
        tenant_id         INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        student_id        INTEGER REFERENCES students(id) ON DELETE SET NULL,
        recipient_name    TEXT,
        amount            NUMERIC(12,2) NOT NULL,
        distribution_date DATE DEFAULT CURRENT_DATE,
        distribution_year INTEGER,
        purpose           TEXT,
        approved_by       TEXT,
        created_at        TIMESTAMPTZ DEFAULT NOW()
    );

    -- ── Online Exams ────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS online_exams (
        id              SERIAL PRIMARY KEY,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        title           TEXT NOT NULL,
        class_id        INTEGER REFERENCES classes(id) ON DELETE SET NULL,
        session_id      INTEGER REFERENCES academic_sessions(id) ON DELETE SET NULL,
        subject_id      INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
        duration_mins   INTEGER DEFAULT 30,
        total_marks     INTEGER DEFAULT 0,
        pass_marks      INTEGER DEFAULT 0,
        instructions    TEXT,
        start_time      TIMESTAMPTZ,
        end_time        TIMESTAMPTZ,
        is_active       BOOLEAN DEFAULT FALSE,
        shuffle_questions BOOLEAN DEFAULT TRUE,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS exam_questions (
        id              SERIAL PRIMARY KEY,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        exam_id         INTEGER NOT NULL REFERENCES online_exams(id) ON DELETE CASCADE,
        question_text   TEXT NOT NULL,
        option_a        TEXT NOT NULL,
        option_b        TEXT NOT NULL,
        option_c        TEXT,
        option_d        TEXT,
        correct_option  TEXT NOT NULL,
        marks           INTEGER DEFAULT 1,
        explanation     TEXT,
        order_no        INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS exam_submissions (
        id              SERIAL PRIMARY KEY,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        exam_id         INTEGER NOT NULL REFERENCES online_exams(id) ON DELETE CASCADE,
        enrollment_id   INTEGER REFERENCES student_enrollments(id) ON DELETE CASCADE,
        student_name    TEXT,
        answers         JSONB,
        score           INTEGER DEFAULT 0,
        total_marks     INTEGER DEFAULT 0,
        percentage      NUMERIC(5,2) DEFAULT 0,
        time_taken_mins INTEGER,
        submitted_at    TIMESTAMPTZ DEFAULT NOW(),
        ip_address      TEXT,
        UNIQUE(exam_id, enrollment_id)
    );

    -- ── Teachers ────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS teachers (
        id               SERIAL PRIMARY KEY,
        tenant_id        INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        name             TEXT NOT NULL,
        father_name      TEXT,
        mobile_no        TEXT,
        email            TEXT,
        nid_no           TEXT,
        designation      TEXT DEFAULT 'Teacher',
        joining_date     DATE,
        monthly_salary   NUMERIC(10,2) DEFAULT 0,
        qualification    TEXT,
        present_address  TEXT,
        status           TEXT DEFAULT 'active',
        photo_url        TEXT,
        created_at       TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS teacher_assignments (
        id          SERIAL PRIMARY KEY,
        tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        teacher_id  INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
        class_id    INTEGER REFERENCES classes(id) ON DELETE SET NULL,
        subject_id  INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
        session_id  INTEGER REFERENCES academic_sessions(id) ON DELETE SET NULL,
        is_class_teacher BOOLEAN DEFAULT FALSE,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS teacher_salary (
        id              SERIAL PRIMARY KEY,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        teacher_id      INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
        month_name      TEXT NOT NULL,
        year            INTEGER NOT NULL,
        basic_salary    NUMERIC(10,2) DEFAULT 0,
        bonus           NUMERIC(10,2) DEFAULT 0,
        deduction       NUMERIC(10,2) DEFAULT 0,
        net_salary      NUMERIC(10,2) GENERATED ALWAYS AS
                            (basic_salary + bonus - deduction) STORED,
        payment_date    DATE,
        payment_method  TEXT DEFAULT 'cash',
        status          TEXT DEFAULT 'unpaid',
        remarks         TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(tenant_id, teacher_id, month_name, year)
    );

    -- attendance-এ QR punch upsert-এর জন্য প্রয়োজনীয় unique constraint
    -- (003-এ তৈরি attendance টেবিলে না থাকলে যোগ হবে)
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'attendance_enrollment_date_key'
        ) THEN
            ALTER TABLE attendance
                ADD CONSTRAINT attendance_enrollment_date_key
                UNIQUE (enrollment_id, date);
        END IF;
    EXCEPTION WHEN others THEN
        -- Duplicate data থাকলে constraint বসবে না — deploy আটকাবে না,
        -- log-এ warning হিসেবে থেকে যাবে
        RAISE NOTICE 'attendance unique constraint যোগ করা যায়নি: %', SQLERRM;
    END $$;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS teacher_salary CASCADE;
    DROP TABLE IF EXISTS teacher_assignments CASCADE;
    DROP TABLE IF EXISTS teachers CASCADE;
    DROP TABLE IF EXISTS exam_submissions CASCADE;
    DROP TABLE IF EXISTS exam_questions CASCADE;
    DROP TABLE IF EXISTS online_exams CASCADE;
    DROP TABLE IF EXISTS zakat_distributions CASCADE;
    DROP TABLE IF EXISTS zakat_collections CASCADE;
    DROP TABLE IF EXISTS timetable CASCADE;
    DROP TABLE IF EXISTS notices CASCADE;
    """)
