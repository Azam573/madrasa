"""006_performance_indexes — Performance indexes for v9.0

Fix: এই migration আগে revision='002', down_revision='001' নিয়ে orphan ছিল —
'001' নামে কোনো revision নেই (আসলটা "001_initial"), ফলে `alembic upgrade`
পুরো chain load করতেই ব্যর্থ হতো। কখনো apply হয়নি বলে re-parent করা নিরাপদ।

Revision ID: 006_performance_indexes
Revises: 005_parent_portal
Create Date: 2024-01-15
"""
from alembic import op

revision = "006_performance_indexes"
down_revision = "005_parent_portal"
branch_labels = None
depends_on = None


def upgrade():
    """Performance indexes তৈরি করে।"""

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fee_vouchers_tenant_year_status
            ON fee_vouchers (tenant_id, year, status);

        CREATE INDEX IF NOT EXISTS idx_fee_vouchers_student_month
            ON fee_vouchers (tenant_id, student_id, month_name, year);

        CREATE INDEX IF NOT EXISTS idx_attendance_tenant_date
            ON attendance (tenant_id, date);

        CREATE INDEX IF NOT EXISTS idx_attendance_enrollment_date
            ON attendance (enrollment_id, date);

        CREATE INDEX IF NOT EXISTS idx_student_marks_exam
            ON student_marks (tenant_id, exam_id);

        CREATE INDEX IF NOT EXISTS idx_student_marks_enrollment
            ON student_marks (enrollment_id, exam_id);

        CREATE INDEX IF NOT EXISTS idx_enrollments_session_class
            ON student_enrollments (tenant_id, session_id, class_id);

        CREATE INDEX IF NOT EXISTS idx_enrollments_student
            ON student_enrollments (tenant_id, student_id);

        CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_date
            ON audit_logs (tenant_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_notifications_tenant_unread
            ON notifications (tenant_id, is_read)
            WHERE is_read = FALSE;

        CREATE INDEX IF NOT EXISTS idx_students_tenant_status
            ON students (tenant_id, status);

        CREATE INDEX IF NOT EXISTS idx_fee_payments_voucher
            ON fee_payments (voucher_id, tenant_id);

        CREATE INDEX IF NOT EXISTS idx_fee_payments_date
            ON fee_payments (tenant_id, payment_date DESC);
    """)


def downgrade():
    """Indexes মুছে ফেলে।"""
    indexes = [
        "idx_fee_vouchers_tenant_year_status",
        "idx_fee_vouchers_student_month",
        "idx_attendance_tenant_date",
        "idx_attendance_enrollment_date",
        "idx_student_marks_exam",
        "idx_student_marks_enrollment",
        "idx_enrollments_session_class",
        "idx_enrollments_student",
        "idx_audit_logs_tenant_date",
        "idx_notifications_tenant_unread",
        "idx_students_tenant_status",
        "idx_fee_payments_voucher",
        "idx_fee_payments_date",
    ]
    for idx in indexes:
        op.execute(f"DROP INDEX IF EXISTS {idx};")
