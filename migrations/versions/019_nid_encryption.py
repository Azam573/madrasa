"""
019_nid_encryption.py — students.nid_no এনক্রিপশন

শুধু NID নম্বর এনক্রিপ্ট করা হচ্ছে (mobile_no নয়) — কারণ mobile_no
parent-portal লগইন ও bulk_import duplicate-check-এ WHERE/ILIKE lookup-এ
ব্যবহৃত হয়, এনক্রিপ্ট করলে সেগুলো ভেঙে যেত। NID কোথাও lookup-এ ব্যবহার
হয় না — শুধু সংরক্ষণ/প্রদর্শনের জন্য, তাই নিরাপদে এনক্রিপ্ট করা যায়।

নতুন `nid_no_encrypted` কলাম যোগ করে বিদ্যমান প্লেইনটেক্সট `nid_no`
মানগুলো Fernet দিয়ে এনক্রিপ্ট করে backfill করা হচ্ছে। পুরনো `nid_no`
কলাম এখনই ড্রপ করা হচ্ছে না — এই migration যাচাই করার পর আলাদাভাবে,
সম্পূর্ণ backfill নিশ্চিত হওয়ার পর ম্যানুয়ালি ড্রপ করা উচিত (এখানে
স্বয়ংক্রিয়ভাবে ড্রপ করলে backfill-এ কোনো সমস্যা হলে ডেটা হারানোর
ঝুঁকি থাকে)।

Revision ID: 019_nid_encryption
Revises: 018_question_bank
"""
from alembic import op
import sqlalchemy as sa

revision      = "019_nid_encryption"
down_revision = "018_question_bank"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS nid_no_encrypted TEXT;")

    from pii_crypto import encrypt_nid

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, nid_no FROM students WHERE nid_no IS NOT NULL AND nid_no <> '' "
        "AND nid_no_encrypted IS NULL"
    )).fetchall()

    for row in rows:
        enc = encrypt_nid(row.nid_no)
        if enc:
            bind.execute(
                sa.text("UPDATE students SET nid_no_encrypted=:enc WHERE id=:id"),
                {"enc": enc, "id": row.id},
            )


def downgrade():
    op.execute("ALTER TABLE students DROP COLUMN IF EXISTS nid_no_encrypted;")
