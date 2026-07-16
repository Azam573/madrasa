"""
create_platform_admin.py — প্ল্যাটফর্ম অ্যাডমিন (Super Admin) প্রোভিশনিং

platform_admins-এর জন্য কোনো self-service সাইনআপ নেই (থাকাও উচিত না) —
এগুলো SaaS অপারেটরের নিজস্ব অ্যাকাউন্ট, শুধু যাদের সার্ভার/DB অ্যাক্সেস
আছে তারাই তৈরি করবে। নতুন কোনো platform admin দরকার হলে এই স্ক্রিপ্ট
আবার চালান — এটা শুধু প্রথমবারের জন্য না, চলমান ব্যবস্থাপনার পদ্ধতি।

Usage: python create_platform_admin.py
"""
import os, sys, getpass
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st  # noqa: F401  — db.py session_state নির্ভরতার জন্য মক প্রয়োজন


def main():
    from db import get_connection, release_connection, bootstrap_schema
    from auth import hash_password, validate_password_strength

    print("⏳ Bootstrapping schema...")
    bootstrap_schema()

    username = input("Username: ").strip()
    full_name = input("Full name: ").strip()
    password = getpass.getpass("Password (min 8 chars, needs a letter and a number): ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("❌ Passwords do not match. Aborted.")
        return
    if not username:
        print("❌ Username required. Aborted.")
        return
    pw_err = validate_password_strength(password)
    if pw_err:
        print(f"❌ {pw_err}")
        return

    conn = get_connection()
    if not conn:
        print("❌ DB connection failed. Set DATABASE_URL env var.")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM platform_admins WHERE username=%s", (username,))
            if cur.fetchone():
                print(f"❌ '{username}' already exists.")
                return
            cur.execute(
                """INSERT INTO platform_admins (username, password_hash, full_name, is_active)
                   VALUES (%s,%s,%s,TRUE) RETURNING id""",
                (username, hash_password(password), full_name),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
        print(f"✅ Platform admin created (id={new_id}).")
        print("🚀 Log in at: http://localhost:8501/?page=platform")
    except Exception as ex:
        conn.rollback()
        print(f"❌ Failed: {ex}")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()
