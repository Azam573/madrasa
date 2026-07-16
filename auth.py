"""
auth.py — Multi-Tenant Login & Session Management  v9.0
Role-based access: admin | staff | teacher | accountant

পরিবর্তন (v9.0):
- SHA-256 সম্পূর্ণ বাদ দেওয়া হয়েছে
- argon2-cffi দিয়ে password hash করা হচ্ছে (industry standard, GPU-resistant)
- Legacy demo hash backward-compatible রাখা হয়েছে migration-এর জন্য
- _migrate_password_on_login(): প্রথম সফল login-এ পুরনো hash auto-upgrade করে
"""

import os
import time
from datetime import datetime, timedelta, timezone
import streamlit as st
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from db import get_connection, fetchone, fetchall, release_connection
from utils import inject_css, PALETTE
from password_reset import render_password_reset_page
from __version__ import VERSION_LABEL
from error_handler import safe_db_error
from i18n import t

# Brute-force lockout: N wrong passwords in a row locks the account for
# LOCKOUT_MINUTES. Reset to 0 on any successful login.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# Admins must enable 2FA within this many days of account creation —
# a warning banner shows until then, then login is blocked until they
# complete TOTP/SMS setup.
ADMIN_2FA_GRACE_DAYS = 7

# নিষ্ক্রিয় সেশন এই সময় পরে স্বয়ংক্রিয়ভাবে লগআউট হয়ে যায় — আর্থিক ও
# শিক্ষার্থীদের (নাবালক) ডেটা হ্যান্ডল করা অ্যাপে ব্রাউজার খোলা রেখে চলে
# গেলে যে কেউ অ্যাক্সেস পেয়ে যাওয়ার ঝুঁকি কমাতে।
IDLE_TIMEOUT_SECONDS = 30 * 60

# ---------------------------------------------------------------------------
# Argon2 Configuration
# time_cost=3, memory_cost=65536 (64MB), parallelism=2 — OWASP recommended
# ---------------------------------------------------------------------------
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

DEMO_LEGACY_HASH = "demo_hash_admin123"
DEMO_LEGACY_PASSWORD = "admin123"

# Enterprise fix: the legacy demo credential must never be a valid login
# path in production. Previously any user row still carrying the seed
# value DEMO_LEGACY_HASH could log in with "admin123" regardless of
# environment — effectively a hardcoded backdoor if demo data ever
# leaked into (or a seed script ran against) a production database.
_ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()
_DEMO_LOGIN_ALLOWED = _ENVIRONMENT != "production"


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """argon2id দিয়ে password hash করে। প্রতিবার unique salt তৈরি হয়।"""
    return _ph.hash(password)


def validate_password_strength(password: str) -> str | None:
    """
    নূন্যতম পাসওয়ার্ড শক্তি যাচাই করে। সব password-set পয়েন্টে (self-signup,
    platform admin-এর tenant creation, password reset) একই নিয়ম প্রয়োগ করতে
    এই একটি জায়গা থেকে ডাকা হয়। জটিল rule (special char বাধ্যতামূলক ইত্যাদি)
    এড়িয়ে length + letter + digit-এ সীমাবদ্ধ রাখা হয়েছে (NIST SP 800-63B
    অনুযায়ী length-ই মূল নিরাপত্তা ফ্যাক্টর, অতিরিক্ত জটিলতা ব্যবহারকারীকে
    দুর্বল প্যাটার্নে (Password1!) ঠেলে দেয়)।
    Returns None if valid, নাহলে ব্যবহারকারীকে দেখানোর মতো একটা এরর মেসেজ।
    """
    if len(password) < 8:
        return t("auth.err_password_too_short")
    if not any(c.isalpha() for c in password):
        return t("auth.err_password_needs_letter")
    if not any(c.isdigit() for c in password):
        return t("auth.err_password_needs_digit")
    return None


def verify_password(plain: str, stored_hash: str) -> bool:
    """
    Password verify করে। তিনটি case handle করে:
    1. Legacy demo hash (migration period)
    2. পুরনো SHA-256 hash (যদি থাকে) — reject করে False return
    3. argon2 hash — সঠিকভাবে verify করে
    """
    # Legacy demo hash — backward compatibility (non-production only)
    if stored_hash == DEMO_LEGACY_HASH:
        return _DEMO_LOGIN_ALLOWED and plain == DEMO_LEGACY_PASSWORD

    # argon2 verify
    try:
        return _ph.verify(stored_hash, plain)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError):
        # পুরনো SHA-256 hash হলে reject — user-কে reset করতে হবে
        return False


def needs_rehash(stored_hash: str) -> bool:
    """argon2 parameters পুরনো হলে True — auto-upgrade trigger করে।"""
    if stored_hash == DEMO_LEGACY_HASH:
        return False  # Demo hash migration আলাদাভাবে handle হয়
    try:
        return _ph.check_needs_rehash(stored_hash)
    except Exception:
        return False


def _migrate_password_on_login(user_id: int, tenant_id: int, plain_password: str, stored_hash: str):
    """
    Login সফল হলে পুরনো hash auto-upgrade করে argon2-তে।
    Legacy demo hash বা needs_rehash() True হলে চালে।
    """
    should_migrate = (stored_hash == DEMO_LEGACY_HASH) or needs_rehash(stored_hash)
    if not should_migrate:
        return

    new_hash = hash_password(plain_password)
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_users SET password_hash=%s WHERE id=%s AND tenant_id=%s",
                (new_hash, user_id, tenant_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


# ---------------------------------------------------------------------------
# Auth DB helpers
# ---------------------------------------------------------------------------

def _get_tenants():
    return fetchall("SELECT id, madrasa_name, slug FROM tenants ORDER BY madrasa_name")


def _authenticate(tenant_id: int, username: str, password: str):
    tenant = fetchone("SELECT status FROM tenants WHERE id=%s", (tenant_id,))
    if tenant and tenant.get("status") == "suspended":
        return None, t("auth.err_tenant_suspended")

    user = fetchone(
        """SELECT id, username, password_hash, role, full_name, is_active, language,
                  failed_attempts, locked_until, created_at, must_change_password
           FROM app_users
           WHERE tenant_id=%s AND username=%s""",
        (tenant_id, username.strip()),
    )
    if not user:
        return None, t("auth.err_user_not_found")
    if not user["is_active"]:
        return None, t("auth.err_inactive")

    locked_until = user.get("locked_until")
    if locked_until and locked_until > datetime.now(timezone.utc):
        minutes_left = max(1, int((locked_until - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
        return None, t("auth.err_locked_out", minutes=minutes_left)

    if not verify_password(password, user["password_hash"]):
        _record_failed_attempt(tenant_id, user["id"], user.get("failed_attempts", 0) or 0)
        return None, t("auth.err_wrong_password")

    # সফল login — lockout counter রিসেট
    _reset_login_lockout(tenant_id, user["id"])

    # Auto-migrate hash in background (silent)
    _migrate_password_on_login(user["id"], tenant_id, password, user["password_hash"])

    return user, None


def _record_failed_attempt(tenant_id: int, user_id: int, current_failed_attempts: int):
    """ভুল পাসওয়ার্ড হলে counter বাড়ায়; থ্রেশহোল্ডে পৌঁছালে অ্যাকাউন্ট লক করে।"""
    new_count = current_failed_attempts + 1
    lock_until = None
    if new_count >= MAX_FAILED_ATTEMPTS:
        lock_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
        new_count = 0  # লক হওয়ার পর counter রিসেট — আনলক হলে আবার ৫ বার সুযোগ পাবে

    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_users SET failed_attempts=%s, locked_until=%s WHERE id=%s AND tenant_id=%s",
                (new_count, lock_until, user_id, tenant_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _reset_login_lockout(tenant_id: int, user_id: int):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_users SET failed_attempts=0, locked_until=NULL WHERE id=%s AND tenant_id=%s",
                (user_id, tenant_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _create_user(tenant_id, username, password, role, full_name, email):
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO app_users
                   (tenant_id, username, password_hash, role, full_name, email)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tenant_id, username.strip(), hash_password(password),
                 role, full_name.strip(), email.strip()),
            )
            uid = cur.fetchone()["id"]
        conn.commit()
        return True, uid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _get_users(tenant_id):
    return fetchall(
        "SELECT id, username, role, full_name, email, is_active FROM app_users WHERE tenant_id=%s ORDER BY id",
        (tenant_id,),
    )


def _count_active_admins(tenant_id: int) -> int:
    row = fetchone(
        "SELECT COUNT(*) AS n FROM app_users WHERE tenant_id=%s AND role='admin' AND is_active=TRUE",
        (tenant_id,),
    )
    return int(row["n"]) if row else 0


def _toggle_user(tenant_id, user_id, is_active):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_users SET is_active=%s WHERE id=%s AND tenant_id=%s",
                (is_active, user_id, tenant_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def update_language(tenant_id, user_id, language):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_users SET language=%s WHERE id=%s AND tenant_id=%s",
                (language, user_id, tenant_id),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _change_password(tenant_id, user_id, new_password):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE app_users SET password_hash=%s, must_change_password=FALSE
                   WHERE id=%s AND tenant_id=%s""",
                (hash_password(new_password), user_id, tenant_id),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


# ---------------------------------------------------------------------------
# Role-based access control
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS = {
    "admin": [
        "Dashboard","Admissions","Finance","Academics","Attendance",
        "Digital Attendance","Timetable","AI Analytics","Online Exam",
        "Teachers","Reports","Govt Report","Notice Board","Student Portal",
        "Settings","User Management","Print Center","Audit Trail",
        "Documents","Expense","Hostel","Library","WhatsApp","Backup",
        "Cache","Live Dashboard","Security 2FA","Payment Gateway",
        "Online Admission","Zakat","Parent Portal","Donors","Financial Overview",
    ],
    "staff": [
        "Dashboard","Admissions","Finance","Attendance",
        "Digital Attendance","Notice Board","Student Portal",
        "Print Center","Documents","Online Admission",
    ],
    "teacher": [
        "Dashboard","Academics","Online Exam","Timetable",
        "Attendance","Digital Attendance","Notice Board",
        "Student Portal","Library",
    ],
    "accountant": [
        "Dashboard","Finance","Reports","Govt Report",
        "Student Portal","Print Center","Expense","WhatsApp","Zakat","Donors","Financial Overview",
    ],
}

def can_access(role: str, page: str) -> bool:
    return page in ROLE_PERMISSIONS.get(role, [])


# ---------------------------------------------------------------------------
# Login page UI
# ---------------------------------------------------------------------------

def _login_page():
    # পাসওয়ার্ড রিসেট মোড চালু থাকলে সেই পেজ দেখাও
    if st.session_state.get("show_password_reset"):
        inject_css()
        tenants = _get_tenants()
        if tenants:
            tid = st.session_state.get("reset_tenant_id", tenants[0]["id"])
            madrasa = next((t["madrasa_name"] for t in tenants if t["id"] == tid),
                            tenants[0]["madrasa_name"])
            render_password_reset_page(tid, madrasa)
        return

    inject_css()
    st.markdown(
        f"""<div style="max-width:420px;margin:3rem auto">
          <div style="text-align:center;margin-bottom:2rem">
            <div style="font-size:3.5rem">🕌</div>
            <h1 style="font-size:1.6rem;font-weight:700;color:{PALETTE['primary']};margin:0.5rem 0 0.25rem">
              Smart Madrasa ERP
            </h1>
            <p style="color:{PALETTE['muted']};font-size:0.85rem">
              {t('login.subtitle')}
            </p>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    if st.session_state.pop("_session_expired_notice", False):
        st.warning(t("auth.session_expired_notice"))

    _, col, _ = st.columns([1, 2, 1])
    with col:
        tenants = _get_tenants()
        if not tenants:
            st.error(t("login.no_tenants"))
            return

        tenant_opts = {t_row["madrasa_name"]: t_row["id"] for t_row in tenants}

        with st.form("login_form"):
            st.markdown(
                f'<div style="background:{PALETTE["card"]};padding:1.5rem;'
                f'border-radius:12px;border:1px solid {PALETTE["border"]};'
                f'box-shadow:0 4px 20px rgba(15,76,92,0.12)">',
                unsafe_allow_html=True,
            )
            st.markdown(f"#### {t('login.heading')}")

            selected_madrasa = st.selectbox(t("login.select_madrasa"), list(tenant_opts.keys()))
            username = st.text_input(t("login.username"), placeholder="admin")
            password = st.text_input(t("login.password"), type="password", placeholder="••••••••")

            submitted = st.form_submit_button(t("login.submit"), type="primary", use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)

            if submitted:
                if not username or not password:
                    st.error(t("login.error_required"))
                    return

                tenant_id = tenant_opts[selected_madrasa]
                user, err = _authenticate(tenant_id, username, password)

                if err:
                    st.error(f"❌ {err}")
                else:
                    # ── 2FA Check (v9.0) ─────────────────────────
                    # Login সফল — এখন 2FA enabled কিনা দেখি
                    from security_2fa import get_2fa_record, verify_totp, verify_sms_otp
                    rec = get_2fa_record(user["id"])
                    needs_2fa = rec and (rec.get("totp_enabled") or rec.get("sms_otp_enabled"))

                    if needs_2fa:
                        # 2FA pending state — session-এ রাখি, logged_in=False
                        st.session_state["_2fa_pending"]      = True
                        st.session_state["_2fa_user"]         = user
                        st.session_state["_2fa_tenant_id"]    = tenant_id
                        st.session_state["_2fa_madrasa"]      = selected_madrasa
                        st.session_state["_2fa_record"]       = dict(rec)
                        st.rerun()
                    else:
                        # 2FA নেই — admin হলে grace period যাচাই করি
                        _complete_login(user, tenant_id, selected_madrasa)
                        if user.get("role") == "admin":
                            created_at = user.get("created_at")
                            days_since = (datetime.now(timezone.utc) - created_at).days if created_at else 0
                            days_left = ADMIN_2FA_GRACE_DAYS - days_since
                            if days_left <= 0:
                                st.session_state["_2fa_setup_mandatory"] = True
                            else:
                                st.session_state["_2fa_grace_days_left"] = days_left
                        st.rerun()

        if _DEMO_LOGIN_ALLOWED:
            st.markdown(
                f'<div style="text-align:center;margin-top:1rem;'
                f'font-size:0.78rem;color:{PALETTE["muted"]};">'
                f'{t("login.demo_prefix")} username=<b>admin</b> · password=<b>admin123</b></div>',
                unsafe_allow_html=True,
            )

        if st.button(t("login.forgot_password"), use_container_width=True, key="forgot_pwd_btn"):
            st.session_state["show_password_reset"] = True
            st.session_state["reset_tenant_id"] = tenant_opts[selected_madrasa]
            st.rerun()

        st.markdown(
            f'<div style="text-align:center;margin-top:0.75rem;font-size:0.8rem;color:{PALETTE["muted"]}">'
            f'<a href="/?page=register" target="_self">{t("login.new_madrasa_register")}</a></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="text-align:center;margin-top:0.5rem;font-size:0.72rem;color:{PALETTE["muted"]}">'
            f'<a href="/?page=terms" target="_blank">{t("login.terms_link")}</a>'
            f' · '
            f'<a href="/?page=privacy" target="_blank">{t("login.privacy_link")}</a></div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# User Management UI (admin only)
# ---------------------------------------------------------------------------

def render_user_management():
    from utils import page_header, alert, divider
    tid  = st.session_state.get("tenant_id", 1)
    role = st.session_state.get("user_role", "staff")

    page_header("👥", "ব্যবহারকারী ব্যবস্থাপনা", "স্টাফ অ্যাকাউন্ট ও ভূমিকা পরিচালনা")

    if role != "admin":
        alert("⛔ শুধুমাত্র Admin এই পেজ দেখতে পারবেন।", "danger")
        return

    tab_list, tab_add, tab_pwd = st.tabs(["📋 ব্যবহারকারী তালিকা", "➕ নতুন ব্যবহারকারী", "🔑 পাসওয়ার্ড পরিবর্তন"])
    users = _get_users(tid)

    with tab_list:
        if not users:
            alert("কোনো ব্যবহারকারী নেই।", "info")
        else:
            ROLE_BADGE = {
                "admin":      "🔴 Admin",
                "staff":      "🟡 Staff",
                "teacher":    "🟢 Teacher",
                "accountant": "🔵 Accountant",
            }
            active_admin_count = _count_active_admins(tid)
            current_user_id    = st.session_state.get("user_id")
            for u in users:
                c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
                c1.markdown(f"**{u['full_name'] or u['username']}**  \n`{u['username']}`")
                c2.markdown(ROLE_BADGE.get(u["role"], u["role"]))
                c3.markdown(u["email"] or "—")

                deactivating_admin = u["role"] == "admin" and u["is_active"]
                if deactivating_admin and u["id"] == current_user_id:
                    c4.button("❌ নিষ্ক্রিয় করুন", key=f"usr_tog_{u['id']}", disabled=True,
                              help=t("auth.err_cannot_deactivate_self"))
                elif deactivating_admin and active_admin_count <= 1:
                    c4.button("❌ নিষ্ক্রিয় করুন", key=f"usr_tog_{u['id']}", disabled=True,
                              help=t("auth.err_cannot_deactivate_last_admin"))
                else:
                    status_btn = "❌ নিষ্ক্রিয় করুন" if u["is_active"] else "✅ সক্রিয় করুন"
                    if c4.button(status_btn, key=f"usr_tog_{u['id']}"):
                        _toggle_user(tid, u["id"], not u["is_active"])
                        st.rerun()
                st.markdown('<hr style="margin:0.4rem 0;border-color:#DDE3E7">', unsafe_allow_html=True)

    with tab_add:
        st.markdown("#### ➕ নতুন অ্যাকাউন্ট তৈরি করুন")
        with st.form("add_user_form"):
            c1, c2 = st.columns(2)
            new_username  = c1.text_input("ব্যবহারকারীর নাম *")
            new_full_name = c2.text_input("পূর্ণ নাম *")
            c3, c4 = st.columns(2)
            new_password = c3.text_input("পাসওয়ার্ড *", type="password")
            new_role     = c4.selectbox("ভূমিকা", ["staff", "teacher", "accountant", "admin"])
            new_email    = st.text_input("ইমেইল")

            if st.form_submit_button("✅ অ্যাকাউন্ট তৈরি করুন", type="primary"):
                pw_err = validate_password_strength(new_password)
                if not new_username or not new_password or not new_full_name:
                    st.error("নাম, ব্যবহারকারী নাম ও পাসওয়ার্ড আবশ্যক।")
                elif pw_err:
                    st.error(pw_err)
                else:
                    ok, result = _create_user(tid, new_username, new_password,
                                               new_role, new_full_name, new_email)
                    if ok:
                        st.success(f"✅ অ্যাকাউন্ট তৈরি হয়েছে! ID: {result}")
                        st.rerun()
                    else:
                        st.error(f"ব্যর্থ: {result}")

    with tab_pwd:
        st.markdown("#### 🔑 পাসওয়ার্ড পরিবর্তন করুন")
        user_opts = {f"{u['full_name'] or u['username']} ({u['role']})": u["id"] for u in users}
        sel_user = st.selectbox("ব্যবহারকারী", list(user_opts.keys()))
        with st.form("pwd_form"):
            new_pwd1 = st.text_input("নতুন পাসওয়ার্ড", type="password")
            new_pwd2 = st.text_input("পাসওয়ার্ড নিশ্চিত করুন", type="password")
            if st.form_submit_button("🔑 পরিবর্তন করুন", type="primary"):
                pw_err = validate_password_strength(new_pwd1)
                if not new_pwd1 or not new_pwd2:
                    st.error("উভয় ঘর পূরণ করুন।")
                elif new_pwd1 != new_pwd2:
                    st.error("পাসওয়ার্ড মিলছে না।")
                elif pw_err:
                    st.error(pw_err)
                else:
                    ok = _change_password(tid, user_opts[sel_user], new_pwd1)
                    if ok:
                        st.success("✅ পাসওয়ার্ড পরিবর্তন হয়েছে!")
                    else:
                        st.error("পরিবর্তন ব্যর্থ হয়েছে।")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# ── Login completion helper ──────────────────────────────────────

def _complete_login(user: dict, tenant_id: int, madrasa_name: str):
    """Login সম্পূর্ণ করে session set করে। 2FA pass হওয়ার পরে বা 2FA না থাকলে call হয়।"""
    st.session_state["logged_in"]    = True
    st.session_state["tenant_id"]    = tenant_id
    st.session_state["user_id"]      = user["id"]
    st.session_state["username"]     = user["username"]
    st.session_state["user_role"]    = user["role"]
    st.session_state["user_name"]    = user["full_name"] or user["username"]
    st.session_state["madrasa_name"] = madrasa_name
    st.session_state["language"]     = user.get("language") or "bn"
    st.session_state["nav_page"]     = "Dashboard"
    st.session_state["_force_pwd_change"] = bool(user.get("must_change_password"))
    # 2FA pending state clear
    for k in ["_2fa_pending", "_2fa_user", "_2fa_tenant_id",
              "_2fa_madrasa", "_2fa_record"]:
        st.session_state.pop(k, None)


def _update_2fa_last_used(user_id: int):
    """TOTP/SMS OTP/backup code দিয়ে সফল login-এর পর user_2fa.last_used_at
    আপডেট করে — 'Security Overview' ট্যাবে (security_2fa.py) প্রতিটা
    ব্যবহারকারী সর্বশেষ কবে 2FA দিয়ে লগইন করেছে তা দেখানোর জন্য।"""
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_2fa SET last_used_at=NOW() WHERE user_id=%s",
                (user_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


# ── 2FA Verification Page ────────────────────────────────────────

def _2fa_page():
    """
    2FA Verification Page (v9.0)
    Login সফল হওয়ার পর 2FA code চাওয়া হয়।
    TOTP (Google Authenticator) ও SMS OTP — দুটোই support।
    """
    from security_2fa import verify_totp, verify_sms_otp, verify_backup_code

    inject_css()
    user    = st.session_state.get("_2fa_user", {})
    tid     = st.session_state.get("_2fa_tenant_id", 1)
    madrasa = st.session_state.get("_2fa_madrasa", "")
    rec     = st.session_state.get("_2fa_record", {})

    st.markdown(
        f"""<div style="max-width:420px;margin:3rem auto;text-align:center">
          <div style="font-size:3rem">🔐</div>
          <h2 style="color:{PALETTE['primary']}">দুই-ধাপ যাচাইকরণ</h2>
          <p style="color:{PALETTE['muted']};font-size:0.88rem">
            {user.get('username','')} — {madrasa}
          </p>
        </div>""",
        unsafe_allow_html=True,
    )

    _, col, _ = st.columns([1, 2, 1])
    with col:
        method_label = "TOTP কোড (Google Authenticator)" if rec.get("totp_enabled") \
                       else "SMS OTP কোড"

        with st.form("2fa_form"):
            st.markdown(f"#### {method_label}")
            code = st.text_input(
                "৬ সংখ্যার কোড",
                placeholder="000000",
                max_chars=8,
            )
            col_verify, col_backup = st.columns(2)
            submitted       = col_verify.form_submit_button("✅ যাচাই করুন", type="primary", use_container_width=True)
            use_backup      = col_backup.form_submit_button("🔑 Backup কোড", use_container_width=True)

            if submitted:
                if not code.strip():
                    st.error("কোড দিন।")
                else:
                    verified = False
                    if rec.get("totp_enabled") and rec.get("totp_secret"):
                        verified = verify_totp(rec["totp_secret"], code.strip())
                    elif rec.get("sms_otp_enabled"):
                        verified = verify_sms_otp(user["id"], code.strip())

                    if verified:
                        st.success("✅ যাচাই সফল!")
                        _update_2fa_last_used(user["id"])
                        _complete_login(user, tid, madrasa)
                        st.rerun()
                    else:
                        st.error("❌ কোড ভুল বা মেয়াদ শেষ। আবার চেষ্টা করুন।")

            if use_backup:
                backup_code = st.text_input("Backup Recovery কোড", placeholder="XXXX-XXXX")
                if backup_code:
                    if verify_backup_code(user["id"], backup_code.strip()):
                        st.success("✅ Backup কোড গৃহীত হয়েছে।")
                        _update_2fa_last_used(user["id"])
                        _complete_login(user, tid, madrasa)
                        st.rerun()
                    else:
                        st.error("❌ Backup কোড ভুল।")

        # Cancel — আবার login page-এ যাওয়া
        if st.button("← পিছনে যান", use_container_width=True):
            for k in ["_2fa_pending", "_2fa_user", "_2fa_tenant_id",
                      "_2fa_madrasa", "_2fa_record"]:
                st.session_state.pop(k, None)
            st.rerun()


def _force_password_change_page():
    """
    temporary/system-generated পাসওয়ার্ড (যেমন branch_module.py-এর নতুন branch
    admin) দিয়ে লগইন করা ব্যবহারকারীকে normal অ্যাপে ঢোকার আগে বাধ্যতামূলকভাবে
    নতুন পাসওয়ার্ড সেট করতে হয় — check_auth()-এর গেট এই পেজ দেখায়।
    """
    inject_css()
    st.markdown(
        f"""<div style="max-width:420px;margin:3rem auto;text-align:center">
          <div style="font-size:3rem">🔑</div>
          <h2 style="color:{PALETTE['primary']}">{t('auth.force_pwd_change_heading')}</h2>
          <p style="color:{PALETTE['muted']};font-size:0.88rem">{t('auth.force_pwd_change_subtitle')}</p>
        </div>""",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("force_pwd_change_form"):
            new_pwd1 = st.text_input(t("auth.new_password_label"), type="password")
            new_pwd2 = st.text_input(t("auth.confirm_password_label"), type="password")
            if st.form_submit_button(t("auth.force_pwd_change_btn"), type="primary", use_container_width=True):
                pw_err = validate_password_strength(new_pwd1)
                if not new_pwd1 or not new_pwd2:
                    st.error(t("auth.err_both_fields_required"))
                elif new_pwd1 != new_pwd2:
                    st.error(t("auth.err_passwords_dont_match"))
                elif pw_err:
                    st.error(pw_err)
                else:
                    ok = _change_password(
                        st.session_state.get("tenant_id"), st.session_state.get("user_id"), new_pwd1,
                    )
                    if ok:
                        st.session_state["_force_pwd_change"] = False
                        st.success(t("auth.force_pwd_change_success"))
                        st.rerun()
                    else:
                        st.error(t("auth.err_password_change_failed"))


def check_auth() -> bool:
    """
    True হলে logged in, False হলে login/2FA/force-password-change page দেখায়।

    Flow (v9.0):
    1. logged_in=True কিন্তু IDLE_TIMEOUT_SECONDS-এর বেশি নিষ্ক্রিয় → auto-logout
    2. logged_in=True এবং _force_pwd_change=True → বাধ্যতামূলক পাসওয়ার্ড পরিবর্তন পেজ
    3. logged_in=True → OK
    4. _2fa_pending=True → 2FA verification page
    5. else → login page
    """
    if st.session_state.get("logged_in"):
        now = time.time()
        last_activity = st.session_state.get("_last_activity_at")
        if last_activity and (now - last_activity) > IDLE_TIMEOUT_SECONDS:
            st.session_state["_session_expired_notice"] = True
            logout()  # rerun() করে ভেতরেই — এর পরের কোড চলবে না
            return False
        st.session_state["_last_activity_at"] = now

        if st.session_state.get("_force_pwd_change"):
            _force_password_change_page()
            return False
        return True
    if st.session_state.get("_2fa_pending"):
        _2fa_page()
        return False
    _login_page()
    return False


def logout():
    for key in ["logged_in", "tenant_id", "user_id", "username",
                "user_role", "user_name", "madrasa_name", "nav_page",
                "active_session_id", "schema_ready", "post_login_init_done",
                "_2fa_setup_mandatory", "_2fa_grace_days_left", "_force_pwd_change",
                "_last_activity_at"]:
        st.session_state.pop(key, None)
    st.rerun()
