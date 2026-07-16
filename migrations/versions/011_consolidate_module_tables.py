"""
011_consolidate_module_tables.py — Schema consolidation

এই migration-এর আগে ৪টা মডিউলের ১২টা টেবিল কোথাও Alembic migration-এ
ছিল না — শুধু সেই মডিউলের নিজস্ব lazy _ensure_tables()-এ সংজ্ঞায়িত ছিল:
    branch_module.py       -> branch_groups, tenant_branches
    donor_module.py        -> monthly_donors, donor_payments
    enterprise_modules.py  -> expense_categories, expenses, budgets,
                               hostel_rooms, hostel_allocations,
                               library_books, book_issues
    password_reset.py      -> password_reset_tokens

মানে DISABLE_BOOTSTRAP=true (production convention) সেট থাকলে এই
টেবিলগুলো কখনো তৈরি হতো না। এই migration সব কটা টেবিল একত্র করে
Alembic-কে schema-র একমাত্র source of truth বানায়।

Revision ID: 011_consolidate_module_tables
Revises: 010_user_language
"""
from alembic import op

revision      = "011_consolidate_module_tables"
down_revision = "010_user_language"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    -- ── branch_module.py ────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS branch_groups (
        id          SERIAL PRIMARY KEY,
        group_name  TEXT NOT NULL,
        owner_email TEXT,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS tenant_branches (
        id              SERIAL PRIMARY KEY,
        group_id        INTEGER REFERENCES branch_groups(id) ON DELETE CASCADE,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        branch_label    TEXT,
        is_head_office  BOOLEAN DEFAULT FALSE,
        UNIQUE(group_id, tenant_id)
    );

    -- ── donor_module.py ─────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS monthly_donors (
        id             SERIAL PRIMARY KEY,
        tenant_id      INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        name           TEXT NOT NULL,
        mobile_no      TEXT,
        address        TEXT,
        monthly_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
        start_date     DATE DEFAULT CURRENT_DATE,
        status         TEXT DEFAULT 'active',
        notes          TEXT,
        created_at     TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS donor_payments (
        id             SERIAL PRIMARY KEY,
        tenant_id      INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        donor_id       INTEGER NOT NULL REFERENCES monthly_donors(id) ON DELETE CASCADE,
        month_name     TEXT NOT NULL,
        year           INTEGER NOT NULL,
        amount_paid    NUMERIC(10,2) NOT NULL,
        payment_date   DATE DEFAULT CURRENT_DATE,
        payment_method TEXT DEFAULT 'cash',
        receipt_no     TEXT,
        collected_by   TEXT,
        notes          TEXT,
        created_at     TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(donor_id, month_name, year)
    );
    CREATE INDEX IF NOT EXISTS idx_donor_payments_donor ON donor_payments(donor_id);
    CREATE INDEX IF NOT EXISTS idx_donor_payments_tenant_period ON donor_payments(tenant_id, year, month_name);

    -- ── enterprise_modules.py: Expense ──────────────────────────────
    CREATE TABLE IF NOT EXISTS expense_categories (
        id          SERIAL PRIMARY KEY,
        tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        parent_id   INTEGER REFERENCES expense_categories(id),
        UNIQUE(tenant_id, name)
    );

    CREATE TABLE IF NOT EXISTS expenses (
        id            SERIAL PRIMARY KEY,
        tenant_id     INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        category_id   INTEGER REFERENCES expense_categories(id),
        amount        NUMERIC(12,2) NOT NULL,
        description   TEXT,
        expense_date  DATE DEFAULT CURRENT_DATE,
        payment_method TEXT DEFAULT 'cash',
        voucher_ref   TEXT,
        approved_by   TEXT,
        created_by    TEXT,
        month_name    TEXT,
        year          INTEGER,
        created_at    TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS budgets (
        id           SERIAL PRIMARY KEY,
        tenant_id    INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        category_id  INTEGER REFERENCES expense_categories(id),
        month_name   TEXT NOT NULL,
        year         INTEGER NOT NULL,
        amount       NUMERIC(12,2) NOT NULL,
        UNIQUE(tenant_id, category_id, month_name, year)
    );

    -- ── enterprise_modules.py: Hostel ────────────────────────────────
    CREATE TABLE IF NOT EXISTS hostel_rooms (
        id         SERIAL PRIMARY KEY,
        tenant_id  INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        room_no    TEXT NOT NULL,
        capacity   INTEGER DEFAULT 4,
        floor      TEXT,
        type       TEXT DEFAULT 'general',
        status     TEXT DEFAULT 'active',
        UNIQUE(tenant_id, room_no)
    );

    CREATE TABLE IF NOT EXISTS hostel_allocations (
        id             SERIAL PRIMARY KEY,
        tenant_id      INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        student_id     INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        room_id        INTEGER NOT NULL REFERENCES hostel_rooms(id) ON DELETE CASCADE,
        alloc_date     DATE DEFAULT CURRENT_DATE,
        vacate_date    DATE,
        monthly_charge NUMERIC(10,2) DEFAULT 0,
        status         TEXT DEFAULT 'active',
        UNIQUE(tenant_id, student_id, room_id)
    );

    -- ── enterprise_modules.py: Library ───────────────────────────────
    CREATE TABLE IF NOT EXISTS library_books (
        id            SERIAL PRIMARY KEY,
        tenant_id     INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        title         TEXT NOT NULL,
        author        TEXT,
        isbn          TEXT,
        category      TEXT DEFAULT 'Islamic',
        total_copies  INTEGER DEFAULT 1,
        available     INTEGER DEFAULT 1,
        shelf_no      TEXT,
        added_date    DATE DEFAULT CURRENT_DATE
    );

    CREATE TABLE IF NOT EXISTS book_issues (
        id           SERIAL PRIMARY KEY,
        tenant_id    INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        book_id      INTEGER NOT NULL REFERENCES library_books(id) ON DELETE CASCADE,
        student_id   INTEGER REFERENCES students(id) ON DELETE SET NULL,
        teacher_id   INTEGER,
        issue_date   DATE DEFAULT CURRENT_DATE,
        due_date     DATE,
        return_date  DATE,
        fine_amount  NUMERIC(8,2) DEFAULT 0,
        status       TEXT DEFAULT 'issued',
        issued_by    TEXT
    );

    -- ── password_reset.py ────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id           SERIAL PRIMARY KEY,
        tenant_id    INTEGER NOT NULL,
        user_id      INTEGER NOT NULL,
        otp_code     TEXT NOT NULL,
        expires_at   TIMESTAMPTZ NOT NULL,
        attempts     INTEGER DEFAULT 0,
        used         BOOLEAN DEFAULT FALSE,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_reset_user
        ON password_reset_tokens(user_id, used, expires_at);
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS password_reset_tokens;
    DROP TABLE IF EXISTS book_issues;
    DROP TABLE IF EXISTS library_books;
    DROP TABLE IF EXISTS hostel_allocations;
    DROP TABLE IF EXISTS hostel_rooms;
    DROP TABLE IF EXISTS budgets;
    DROP TABLE IF EXISTS expenses;
    DROP TABLE IF EXISTS expense_categories;
    DROP TABLE IF EXISTS donor_payments;
    DROP TABLE IF EXISTS monthly_donors;
    DROP TABLE IF EXISTS tenant_branches;
    DROP TABLE IF EXISTS branch_groups;
    """)
