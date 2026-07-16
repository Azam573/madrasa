"""004_enterprise — Teachers, audit, notifications, online payments"""
from alembic import op
revision = "004_enterprise"; down_revision = "003_academics"

def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS app_users (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      username TEXT NOT NULL, password_hash TEXT NOT NULL,
      role TEXT DEFAULT 'staff', full_name TEXT, email TEXT, mobile_no TEXT,
      is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(tenant_id, username));
    CREATE TABLE IF NOT EXISTS audit_logs (id BIGSERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      user_id INTEGER, username TEXT, action TEXT NOT NULL,
      module TEXT, record_type TEXT, record_id INTEGER,
      description TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
      message TEXT NOT NULL, type TEXT DEFAULT 'info',
      is_read BOOLEAN DEFAULT FALSE, created_at TIMESTAMPTZ DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS online_payments (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      voucher_id INTEGER REFERENCES fee_vouchers(id) ON DELETE SET NULL,
      student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
      payment_method TEXT NOT NULL, transaction_id TEXT UNIQUE,
      merchant_invoice TEXT, amount NUMERIC(10,2) NOT NULL,
      status TEXT DEFAULT 'pending', gateway_response JSONB,
      initiated_at TIMESTAMPTZ DEFAULT NOW(), completed_at TIMESTAMPTZ);
    CREATE TABLE IF NOT EXISTS user_2fa (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
      totp_secret TEXT, totp_enabled BOOLEAN DEFAULT FALSE,
      backup_codes JSONB, otp_code TEXT, otp_expires_at TIMESTAMPTZ,
      UNIQUE(user_id));
    CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_notif_tenant ON notifications(tenant_id, is_read);
    """)

def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS user_2fa CASCADE;
    DROP TABLE IF EXISTS online_payments CASCADE;
    DROP TABLE IF EXISTS notifications CASCADE;
    DROP TABLE IF EXISTS audit_logs CASCADE;
    DROP TABLE IF EXISTS app_users CASCADE;
    """)
