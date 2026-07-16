"""001_initial — Core tables: tenants, sessions, classes, students"""
from alembic import op
revision = "001_initial"; down_revision = None

def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS tenants (id SERIAL PRIMARY KEY, madrasa_name TEXT NOT NULL,
      slug TEXT UNIQUE, address TEXT, phone TEXT, email TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS academic_sessions (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      session_name TEXT NOT NULL, is_active BOOLEAN DEFAULT FALSE,
      start_date DATE, end_date DATE, UNIQUE(tenant_id, session_name));
    CREATE TABLE IF NOT EXISTS classes (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      class_name TEXT NOT NULL, class_numeric INTEGER, section TEXT DEFAULT 'A',
      UNIQUE(tenant_id, class_name, section));
    CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      name TEXT NOT NULL, father_name TEXT, mobile_no TEXT,
      date_of_birth DATE, gender TEXT DEFAULT 'Male', photo TEXT,
      blood_group TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS student_enrollments (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      session_id INTEGER NOT NULL REFERENCES academic_sessions(id) ON DELETE CASCADE,
      class_id   INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
      roll_no INTEGER, monthly_fee NUMERIC(10,2) DEFAULT 0,
      enrollment_status TEXT DEFAULT 'pending', enrolled_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(tenant_id, student_id, session_id));
    CREATE INDEX IF NOT EXISTS idx_students_tenant ON students(tenant_id, status);
    CREATE INDEX IF NOT EXISTS idx_enroll_tenant   ON student_enrollments(tenant_id, session_id);
    """)

def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS student_enrollments CASCADE;
    DROP TABLE IF EXISTS students CASCADE;
    DROP TABLE IF EXISTS classes CASCADE;
    DROP TABLE IF EXISTS academic_sessions CASCADE;
    DROP TABLE IF EXISTS tenants CASCADE;
    """)
