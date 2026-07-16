"""
016_user_2fa_last_used.py — user_2fa.last_used_at

security_2fa.py-এর "Security Overview" ট্যাব (auth.py:384) f.last_used_at
কলাম select করত, কিন্তু এটা user_2fa টেবিলে কখনোই তৈরি হয়নি (শুধু
004_enterprise.py-তে id/tenant_id/user_id/totp_secret/totp_enabled/
backup_codes/otp_code/otp_expires_at ছিল) — ফলে "column f.last_used_at
does not exist" এরর হতো। এই migration কলামটা যোগ করছে, আর auth.py-তে
প্রতিটা সফল 2FA verification (TOTP/SMS OTP/backup code)-এ এটা আপডেট করা
হচ্ছে, যাতে "Last Used" কলাম শুধু schema-তে থাকা না, বরং সত্যিই কাজ করে।

Revision ID: 016_user_2fa_last_used
Revises: 015_platform_admin
"""
from alembic import op

revision      = "016_user_2fa_last_used"
down_revision = "015_platform_admin"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    ALTER TABLE user_2fa ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ;
    """)


def downgrade():
    op.execute("""
    ALTER TABLE user_2fa DROP COLUMN IF EXISTS last_used_at;
    """)
