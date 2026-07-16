"""003_academics — Subjects, exams, marks, attendance"""
from alembic import op
revision = "003_academics"; down_revision = "002_finance"

def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS subjects (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL,
      subject_name TEXT NOT NULL, subject_code TEXT,
      full_marks INTEGER DEFAULT 100, pass_marks INTEGER DEFAULT 33);
    CREATE TABLE IF NOT EXISTS exams (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      session_id INTEGER REFERENCES academic_sessions(id) ON DELETE CASCADE,
      exam_name TEXT NOT NULL, exam_date DATE, created_at TIMESTAMPTZ DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS student_marks (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      enrollment_id INTEGER NOT NULL REFERENCES student_enrollments(id) ON DELETE CASCADE,
      exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
      subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
      written_obtained INTEGER DEFAULT 0, mcq_obtained INTEGER DEFAULT 0,
      practical_obtained INTEGER DEFAULT 0,
      total_obtained INTEGER GENERATED ALWAYS AS
          (written_obtained + mcq_obtained + practical_obtained) STORED,
      is_absent BOOLEAN DEFAULT FALSE,
      UNIQUE(enrollment_id, exam_id, subject_id));
    CREATE TABLE IF NOT EXISTS attendance (id SERIAL PRIMARY KEY,
      tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      enrollment_id INTEGER NOT NULL REFERENCES student_enrollments(id) ON DELETE CASCADE,
      date DATE NOT NULL, status TEXT DEFAULT 'present',
      UNIQUE(enrollment_id, date));
    CREATE INDEX IF NOT EXISTS idx_marks_exam      ON student_marks(tenant_id, exam_id);
    CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(tenant_id, date);
    """)

def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS attendance CASCADE; DROP TABLE IF EXISTS student_marks CASCADE;
    DROP TABLE IF EXISTS exams CASCADE; DROP TABLE IF EXISTS subjects CASCADE;
    """)
