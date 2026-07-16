"""
018_question_bank.py — অটোমেটিক প্রশ্ন জেনারেটরের জন্য প্রশ্ন ব্যাংক

online_exam.py-এর exam_questions টেবিল আগে প্রতিটা প্রশ্নকে একটা নির্দিষ্ট
exam_id-এর সাথে সরাসরি বেঁধে রাখত — কোনো reusable প্রশ্ন ব্যাংক ছিল না,
প্রতিটা পরীক্ষার জন্য শিক্ষককে একটা একটা করে নতুন প্রশ্ন লিখতে হতো।

question_bank টেবিল — subject/class/topic/difficulty অনুযায়ী ট্যাগ করা,
পুনঃব্যবহারযোগ্য প্রশ্ন সংরক্ষণ করে। নতুন পরীক্ষা তৈরির সময় এখান থেকে
এলোমেলোভাবে প্রশ্ন বেছে exam_questions-এ কপি করা হয় (COPY, লিংক নয়) —
তাই পরে ব্যাংকের কোনো প্রশ্ন এডিট/রিটায়ার করলেও আগের পরীক্ষাগুলো অপরিবর্তিত
থাকে। subject_id/class_id ইচ্ছাকৃতভাবে online_exams-এর নিজস্ব কলাম দুটোর
সাথে হুবহু মেলানো, যাতে অটো-জেনারেট কোয়েরি সরাসরি মিলাতে পারে, subjects.class_id
হয়ে ঘুরপথে না গিয়ে।

source_bank_id — exam_questions-এ নতুন কলাম, কোন bank প্রশ্ন থেকে কপি হয়েছে
তার ট্র্যাক রাখে (ম্যানুয়ালি টাইপ করা প্রশ্নে NULL থাকবে) — regenerate করার
সময় শুধু bank-sourced প্রশ্নগুলো মুছে নতুন করে বসানো হয়, হাতে লেখা প্রশ্ন
অক্ষত থাকে।

Revision ID: 018_question_bank
Revises: 017_tenant_billing
"""
from alembic import op

revision      = "018_question_bank"
down_revision = "017_tenant_billing"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS question_bank (
        id              SERIAL PRIMARY KEY,
        tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        subject_id      INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
        class_id        INTEGER REFERENCES classes(id) ON DELETE SET NULL,
        topic           TEXT,
        difficulty      TEXT NOT NULL DEFAULT 'medium',
        question_text   TEXT NOT NULL,
        option_a        TEXT NOT NULL,
        option_b        TEXT NOT NULL,
        option_c        TEXT,
        option_d        TEXT,
        correct_option  TEXT NOT NULL,
        marks           INTEGER NOT NULL DEFAULT 1,
        explanation     TEXT,
        is_active       BOOLEAN NOT NULL DEFAULT TRUE,
        created_by      TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_question_bank_filter
      ON question_bank(tenant_id, subject_id, class_id, difficulty);

    ALTER TABLE exam_questions ADD COLUMN IF NOT EXISTS source_bank_id
      INTEGER REFERENCES question_bank(id) ON DELETE SET NULL;
    """)


def downgrade():
    op.execute("""
    ALTER TABLE exam_questions DROP COLUMN IF EXISTS source_bank_id;
    DROP TABLE IF EXISTS question_bank;
    """)
