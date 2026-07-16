"""
013_login_lockout.py — Login brute-force lockout columns

app_users.failed_attempts / locked_until যোগ করা হচ্ছে যাতে বারবার ভুল
পাসওয়ার্ড দিলে অ্যাকাউন্ট সাময়িকভাবে লক হয়ে যায় (আগে কোনো brute-force
protection ছিল না — যত খুশি guess করা যেত)।

Revision ID: 013_login_lockout
Revises: 012_column_level_fixes
"""
from alembic import op

revision      = "013_login_lockout"
down_revision = "012_column_level_fixes"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    ALTER TABLE app_users ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE app_users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;
    """)


def downgrade():
    op.execute("""
    ALTER TABLE app_users DROP COLUMN IF EXISTS locked_until;
    ALTER TABLE app_users DROP COLUMN IF EXISTS failed_attempts;
    """)
