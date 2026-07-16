"""
012_column_level_fixes.py — Column-level schema consolidation follow-up

011_consolidate_module_tables.py fixed 12 tables that existed only in a
module's lazy _ensure_tables() and nowhere else. That check compared TABLE
NAMES only. Running the app end-to-end after removing the per-module
SCHEMA/_ensure_tables() blocks surfaced two further classes of drift that
only existed in db.py's old SCHEMA_SQL and nowhere else:
  1. Columns missing on tables that DO exist in migrations — found via
     seed_demo.py crashing with "column present_address does not exist"
     and a static scan of every INSERT/UPDATE column list in the repo
     against the live migrated schema.
  2. A whole table, marks_distribution, used by academic_module.py /
     seed_demo.py / whitelisted in sql_safe.py, that never had a module-level
     _ensure_tables() fallback at all — it only ever existed in SCHEMA_SQL.

Revision ID: 012_column_level_fixes
Revises: 011_consolidate_module_tables
"""
from alembic import op

revision      = "012_column_level_fixes"
down_revision = "011_consolidate_module_tables"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS marks_distribution (
        id              SERIAL PRIMARY KEY,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        exam_id         INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
        subject_id      INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        written_marks   INTEGER DEFAULT 0,
        mcq_marks       INTEGER DEFAULT 0,
        practical_marks INTEGER DEFAULT 0,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(exam_id, subject_id)
    );

    -- students: address/identity fields used by admission_module.py,
    -- online_admission.py, api/routers/students_router.py, seed_demo.py
    ALTER TABLE students ADD COLUMN IF NOT EXISTS mother_name TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS present_address TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS permanent_address TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS nid_no TEXT;

    -- audit_logs: before/after value snapshots used by audit_module.log()
    ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS old_value JSONB;
    ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS new_value JSONB;

    -- student_documents: fields used by document_module.py (TC/certificate
    -- generator) alongside the existing mime_type/file_data/uploaded_at
    -- columns already used by storage.py — both sets coexist.
    ALTER TABLE student_documents ADD COLUMN IF NOT EXISTS doc_title TEXT;
    ALTER TABLE student_documents ADD COLUMN IF NOT EXISTS file_mime TEXT;
    ALTER TABLE student_documents ADD COLUMN IF NOT EXISTS issued_date DATE DEFAULT CURRENT_DATE;
    ALTER TABLE student_documents ADD COLUMN IF NOT EXISTS issued_by TEXT;
    ALTER TABLE student_documents ADD COLUMN IF NOT EXISTS notes TEXT;
    ALTER TABLE student_documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

    -- online_payments: fields used by payment_gateway.py
    ALTER TABLE online_payments ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'BDT';
    ALTER TABLE online_payments ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ;
    ALTER TABLE online_payments ADD COLUMN IF NOT EXISTS failure_reason TEXT;
    """)


def downgrade():
    op.execute("""
    ALTER TABLE online_payments DROP COLUMN IF EXISTS failure_reason;
    ALTER TABLE online_payments DROP COLUMN IF EXISTS refunded_at;
    ALTER TABLE online_payments DROP COLUMN IF EXISTS currency;

    ALTER TABLE student_documents DROP COLUMN IF EXISTS created_at;
    ALTER TABLE student_documents DROP COLUMN IF EXISTS notes;
    ALTER TABLE student_documents DROP COLUMN IF EXISTS issued_by;
    ALTER TABLE student_documents DROP COLUMN IF EXISTS issued_date;
    ALTER TABLE student_documents DROP COLUMN IF EXISTS file_mime;
    ALTER TABLE student_documents DROP COLUMN IF EXISTS doc_title;

    ALTER TABLE audit_logs DROP COLUMN IF EXISTS new_value;
    ALTER TABLE audit_logs DROP COLUMN IF EXISTS old_value;

    ALTER TABLE students DROP COLUMN IF EXISTS nid_no;
    ALTER TABLE students DROP COLUMN IF EXISTS permanent_address;
    ALTER TABLE students DROP COLUMN IF EXISTS present_address;
    ALTER TABLE students DROP COLUMN IF EXISTS mother_name;

    DROP TABLE IF EXISTS marks_distribution;
    """)
