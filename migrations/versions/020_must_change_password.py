"""
020_must_change_password.py — জোরপূর্বক পাসওয়ার্ড পরিবর্তন ফ্ল্যাগ

branch_module.py-এ নতুন branch তৈরি হলে এখন থেকে হার্ডকোড "admin123"-এর
বদলে random temporary password তৈরি হয় (এবং একবারই দেখানো হয়)। এই কলাম
সেই temporary password দিয়ে লগইন করা admin-কে প্রথম লগইনেই নতুন পাসওয়ার্ড
সেট করতে বাধ্য করে — auth.py::check_auth()-এর নতুন গেট এই ফ্ল্যাগ চেক করে।

Revision ID: 020_must_change_password
Revises: 019_nid_encryption
"""
from alembic import op
import sqlalchemy as sa

revision      = "020_must_change_password"
down_revision = "019_nid_encryption"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute(
        "ALTER TABLE app_users ADD COLUMN IF NOT EXISTS "
        "must_change_password BOOLEAN NOT NULL DEFAULT FALSE;"
    )


def downgrade():
    op.execute("ALTER TABLE app_users DROP COLUMN IF EXISTS must_change_password;")
