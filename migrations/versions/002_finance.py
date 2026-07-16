"""002_finance — Fee vouchers, payments"""
from alembic import op
revision = "002_finance"; down_revision = "001_initial"

def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS fee_vouchers (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      enrollment_id INTEGER REFERENCES student_enrollments(id) ON DELETE SET NULL,
      student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      voucher_no TEXT UNIQUE, issue_date DATE DEFAULT CURRENT_DATE, due_date DATE,
      month_name TEXT, year INTEGER, amount NUMERIC(10,2) NOT NULL,
      fund_type TEXT DEFAULT 'general', status TEXT DEFAULT 'unpaid',
      paid_at TIMESTAMPTZ, remarks TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS fee_payments (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      voucher_id INTEGER NOT NULL REFERENCES fee_vouchers(id) ON DELETE CASCADE,
      amount_paid NUMERIC(10,2) NOT NULL, payment_date DATE DEFAULT CURRENT_DATE,
      payment_method TEXT DEFAULT 'cash', receipt_no TEXT, notes TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW());
    CREATE INDEX IF NOT EXISTS idx_vouchers_tenant ON fee_vouchers(tenant_id, year, status);
    CREATE INDEX IF NOT EXISTS idx_payments_date   ON fee_payments(tenant_id, payment_date);
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS fee_payments CASCADE; DROP TABLE IF EXISTS fee_vouchers CASCADE;")
