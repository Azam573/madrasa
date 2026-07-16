"""
010_user_language.py — প্রতি ইউজারের ভাষা পছন্দ (বাংলা/English) সংরক্ষণ

app_users.language কলাম যোগ করা হচ্ছে যাতে প্রতিটি ইউজার নিজের ভাষা
পছন্দ সেভ করে রাখতে পারে (default 'bn' — বিদ্যমান ইউজারদের আচরণ অপরিবর্তিত থাকে)।

Revision ID: 010_user_language
Revises: 009_exam_integrity
"""
from alembic import op

revision      = "010_user_language"
down_revision = "009_exam_integrity"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    ALTER TABLE app_users ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'bn';
    """)


def downgrade():
    op.execute("""
    ALTER TABLE app_users DROP COLUMN IF EXISTS language;
    """)
