"""
005_parent_portal_api.py — Parent Portal API-র OTP token টেবিল

Parents-দের কোনো user account নেই — তারা mobile+OTP দিয়ে verify হয়ে
স্বল্পমেয়াদি (30 মিনিট) scoped JWT পায়। password_reset_tokens-এর
একই নিরাপত্তা ডিজাইন: hashed OTP নয় কিন্তু expiry + attempts + used flag।

Revision ID: 005_parent_portal
Revises: 004_enterprise
"""
from alembic import op
import sqlalchemy as sa

revision      = "005_parent_portal"
down_revision = "004_enterprise"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS parent_otp_tokens (
            id          SERIAL PRIMARY KEY,
            tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            mobile_no   TEXT NOT NULL,
            otp_code    TEXT NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL,
            attempts    INTEGER DEFAULT 0,
            used        BOOLEAN DEFAULT FALSE,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_parent_otp_lookup
            ON parent_otp_tokens(tenant_id, mobile_no, used, expires_at);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS parent_otp_tokens;")
