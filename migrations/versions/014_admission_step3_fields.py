"""
014_admission_step3_fields.py — Online admission Step-3 fields

online_admission.py-এর পাবলিক আবেদন ফর্মের Step 3 ("ভর্তির তথ্য")-তে
prev_school/prev_class/hafiz_status/has_disability/remarks সংগ্রহ করা
হতো, কিন্তু _save_online_application()-এর INSERT-এ কোনো কলাম না
থাকায় s3 প্যারামিটারটি সম্পূর্ণ অব্যবহৃত থেকে যেত এবং এই তথ্য সাইলেন্টলি
হারিয়ে যেত। এই migration students টেবিলে প্রয়োজনীয় কলাম যোগ করছে।

নোট: revision id ৩২ ক্যারেক্টারের মধ্যে রাখতে হবে — alembic_version টেবিলের
version_num কলাম VARCHAR(32), বেশি লম্বা id দিলে migration নিজেই ব্যর্থ হয়
(StringDataRightTruncation), যেমনটা প্রথমবার "014_online_admission_step3_fields"
(৩৩ char) দিয়ে হয়েছিল।

Revision ID: 014_admission_step3_fields
Revises: 013_login_lockout
"""
from alembic import op

revision      = "014_admission_step3_fields"
down_revision = "013_login_lockout"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    ALTER TABLE students ADD COLUMN IF NOT EXISTS prev_school TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS prev_class TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS hafiz_status TEXT;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS has_disability BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE students ADD COLUMN IF NOT EXISTS admission_remarks TEXT;
    """)


def downgrade():
    op.execute("""
    ALTER TABLE students DROP COLUMN IF EXISTS admission_remarks;
    ALTER TABLE students DROP COLUMN IF EXISTS has_disability;
    ALTER TABLE students DROP COLUMN IF EXISTS hafiz_status;
    ALTER TABLE students DROP COLUMN IF EXISTS prev_class;
    ALTER TABLE students DROP COLUMN IF EXISTS prev_school;
    """)
