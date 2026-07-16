"""
platform_admin.py — SaaS প্ল্যাটফর্ম অপারেটর (Super Admin) প্যানেল

tenant/app_users থেকে সম্পূর্ণ আলাদা — লগইন কোনো tenant selector ছাড়াই,
স্বতন্ত্র platform_admins টেবিল ব্যবহার করে। সব tenant দেখা, স্থগিত/
পুনঃসক্রিয় করা, প্ল্যাটফর্ম-ব্যাপী পরিসংখ্যান, আর সাপোর্টের জন্য নিরাপদ
impersonation ("log in as tenant") — branch_module.py-এর leaky
switch-tenant বাটনের সঠিক প্রতিস্থাপন, যা যেকোনো tenant-এর নিজস্ব admin
ব্যবহারকারীকেও অন্য tenant-এ ঢুকতে দিত।
"""

import streamlit as st
import re, secrets
from datetime import datetime, timezone, timedelta, date
from db import get_connection, release_connection, fetchone, fetchall
from utils import inject_css, PALETTE, page_header, kpi_row, alert, divider
from auth import verify_password, hash_password, validate_password_strength
from error_handler import safe_db_error
import audit_module
from i18n import t


def _add_months(d: date, months: int) -> date:
    """dateutil ছাড়াই মাস যোগ করে (নতুন dependency এড়াতে)।"""
    month_index = d.month - 1 + months
    year  = d.year + month_index // 12
    month = month_index % 12 + 1
    day   = min(d.day, [31,29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                        31,30,31,30,31,31,30,31,30,31][month - 1])
    return date(year, month, day)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────

def _authenticate_platform_admin(username: str, password: str):
    user = fetchone(
        """SELECT id, username, password_hash, full_name, is_active,
                  failed_attempts, locked_until
           FROM platform_admins WHERE username=%s""",
        (username.strip(),),
    )
    if not user:
        return None, t("platform.err_user_not_found")
    if not user["is_active"]:
        return None, t("platform.err_inactive")

    locked_until = user.get("locked_until")
    if locked_until and locked_until > datetime.now(timezone.utc):
        minutes_left = max(1, int((locked_until - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
        return None, t("platform.err_locked_out", minutes=minutes_left)

    if not verify_password(password, user["password_hash"]):
        _record_failed_attempt(user["id"], user.get("failed_attempts", 0) or 0)
        return None, t("platform.err_wrong_password")

    _reset_lockout(user["id"])
    return user, None


def _record_failed_attempt(admin_id: int, current_failed_attempts: int):
    new_count = current_failed_attempts + 1
    lock_until = None
    if new_count >= MAX_FAILED_ATTEMPTS:
        lock_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
        new_count = 0
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE platform_admins SET failed_attempts=%s, locked_until=%s WHERE id=%s",
                (new_count, lock_until, admin_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _reset_lockout(admin_id: int):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE platform_admins SET failed_attempts=0, locked_until=NULL WHERE id=%s",
                (admin_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _list_tenants():
    """প্ল্যাটফর্মের প্রতিটা tenant + student সংখ্যা + admin contact, একটাই query-তে।"""
    return fetchall("""
        SELECT t.id, t.madrasa_name, t.email, t.phone, t.created_at, t.status,
               t.plan_type, t.monthly_fee, t.subscription_expiry,
               COALESCE(sc.n, 0) AS student_count,
               au.full_name AS admin_name, au.username AS admin_username
        FROM tenants t
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS n FROM students s
            WHERE s.tenant_id = t.id AND s.status = 'active'
        ) sc ON TRUE
        LEFT JOIN LATERAL (
            SELECT full_name, username FROM app_users a
            WHERE a.tenant_id = t.id AND a.role = 'admin'
            ORDER BY a.id LIMIT 1
        ) au ON TRUE
        ORDER BY t.created_at DESC
    """)


def _generate_unique_slug(madrasa_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", madrasa_name.lower()).strip("-") or "madrasa"
    return f"{base}-{secrets.token_hex(3)}"


def _create_tenant(madrasa_name, address, phone, email, admin_username, admin_password,
                    admin_full_name, plan_type, monthly_fee, subscription_expiry):
    """নতুন মাদরাসা (tenant) + প্রথম admin ইউজার + default academic session/classes —
    সবকিছু একটাই transaction-এ (all-or-nothing)। শুধু super admin এই ফাংশন কল করতে
    পারবে (render()-এর ভেতর থেকে, platform_admin_logged_in চেক করার পরে)।"""
    conn = get_connection()
    if not conn:
        return False, t("platform.err_db_connection")
    try:
        with conn.cursor() as cur:
            slug = _generate_unique_slug(madrasa_name)
            cur.execute(
                """INSERT INTO tenants
                   (madrasa_name, slug, address, phone, email, status,
                    plan_type, monthly_fee, subscription_expiry)
                   VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s)
                   RETURNING id""",
                (madrasa_name, slug, address or None, phone or None, email or None,
                 plan_type, monthly_fee, subscription_expiry),
            )
            tid = cur.fetchone()["id"]

            yr = date.today().year
            cur.execute(
                """INSERT INTO academic_sessions (tenant_id, session_name, is_active, start_date, end_date)
                   VALUES (%s,%s,TRUE,%s,%s)""",
                (tid, str(yr), date(yr, 1, 1), date(yr, 12, 31)),
            )

            for cname, cnum in [("হিফজ বিভাগ", 1), ("নাজেরা বিভাগ", 2), ("ইবতেদায়ী", 3)]:
                cur.execute(
                    """INSERT INTO classes (tenant_id, class_name, class_numeric, section)
                       VALUES (%s,%s,%s,'A')""",
                    (tid, cname, cnum),
                )

            cur.execute(
                """INSERT INTO app_users
                   (tenant_id, username, password_hash, role, full_name, is_active)
                   VALUES (%s,%s,%s,'admin',%s,TRUE)""",
                (tid, admin_username, hash_password(admin_password),
                 admin_full_name or admin_username),
            )
        conn.commit()
        return True, tid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex, "create_tenant")
    finally:
        release_connection(conn)


def _record_tenant_payment(tenant_id: int, amount: float, method: str,
                            months_covered: int, notes: str):
    """ম্যানুয়াল পেমেন্ট রেকর্ড করে + subscription_expiry বাড়ায়। মেয়াদ ইতিমধ্যে
    পার হয়ে থাকলে আজ থেকে গণনা শুরু হয় (stacking নয়), না হলে বিদ্যমান
    মেয়াদ থেকে যোগ হয়। পেমেন্ট পাওয়ার পর tenant suspended থাকলে reactivate হয়
    (ধরে নেওয়া হচ্ছে suspension অ-পেমেন্টের কারণে হয়েছিল)।"""
    conn = get_connection()
    if not conn:
        return False, t("platform.err_db_connection")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT subscription_expiry FROM tenants WHERE id=%s FOR UPDATE", (tenant_id,))
            row = cur.fetchone()
            if not row:
                return False, t("platform.err_tenant_not_found")
            current_expiry = row["subscription_expiry"] or date.today()
            base = max(current_expiry, date.today())
            new_expiry = _add_months(base, months_covered)

            platform_username = st.session_state.get("platform_admin_username", "?")
            cur.execute(
                """INSERT INTO tenant_payments
                   (tenant_id, amount, payment_method, months_covered, recorded_by, notes)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (tenant_id, amount, method, months_covered, platform_username, notes),
            )
            cur.execute(
                "UPDATE tenants SET subscription_expiry=%s, status='active' WHERE id=%s",
                (new_expiry, tenant_id),
            )
        conn.commit()
        return True, new_expiry
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex, "record_tenant_payment")
    finally:
        release_connection(conn)


def _set_tenant_status(tenant_id: int, status: str) -> bool:
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE tenants SET status=%s WHERE id=%s", (status, tenant_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _platform_stats():
    totals = fetchone("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status='suspended') AS suspended,
               COUNT(*) FILTER (WHERE status='active' AND subscription_expiry < CURRENT_DATE) AS overdue,
               COUNT(*) FILTER (WHERE status='active' AND subscription_expiry >= CURRENT_DATE
                                 AND subscription_expiry <= CURRENT_DATE + INTERVAL '7 days') AS expiring_soon,
               COALESCE(SUM(monthly_fee) FILTER (WHERE status='active'), 0) AS mrr
        FROM tenants
    """)
    students = fetchone("SELECT COUNT(*) AS n FROM students WHERE status='active'")
    recent = fetchall("SELECT madrasa_name, created_at FROM tenants ORDER BY created_at DESC LIMIT 5")
    total = totals["total"] if totals else 0
    suspended = totals["suspended"] if totals else 0
    return {
        "total": total,
        "active": total - suspended,
        "suspended": suspended,
        "overdue": totals["overdue"] if totals else 0,
        "expiring_soon": totals["expiring_soon"] if totals else 0,
        "mrr": float(totals["mrr"]) if totals else 0.0,
        "students": students["n"] if students else 0,
        "recent": recent,
    }


def _get_tenant_admin_for_impersonation(tenant_id: int):
    return fetchone(
        """SELECT id, username, full_name, role, language FROM app_users
           WHERE tenant_id=%s AND role='admin' ORDER BY id LIMIT 1""",
        (tenant_id,),
    )


# ─────────────────────────────────────────────
# UI — Login
# ─────────────────────────────────────────────

def _login_form():
    inject_css()
    st.markdown(
        f"""<div style="max-width:420px;margin:3rem auto">
          <div style="text-align:center;margin-bottom:2rem">
            <div style="font-size:3.5rem">🔐</div>
            <h1 style="font-size:1.6rem;font-weight:700;color:{PALETTE['primary']};margin:0.5rem 0 0.25rem">
              {t('platform.login_heading')}
            </h1>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("platform_login_form"):
            st.markdown(
                f'<div style="background:{PALETTE["card"]};padding:1.5rem;'
                f'border-radius:12px;border:1px solid {PALETTE["border"]};'
                f'box-shadow:0 4px 20px rgba(15,76,92,0.12)">',
                unsafe_allow_html=True,
            )
            username = st.text_input(t("platform.username"))
            password = st.text_input(t("platform.password"), type="password")
            submitted = st.form_submit_button(t("platform.submit"), type="primary", use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)

            if submitted:
                if not username or not password:
                    st.error(t("platform.err_user_not_found"))
                else:
                    admin, err = _authenticate_platform_admin(username, password)
                    if err:
                        st.error(f"❌ {err}")
                    else:
                        st.session_state["platform_admin_logged_in"] = True
                        st.session_state["platform_admin_id"] = admin["id"]
                        st.session_state["platform_admin_username"] = admin["username"]
                        st.rerun()


# ─────────────────────────────────────────────
# UI — Dashboard
# ─────────────────────────────────────────────

def _render_dashboard():
    inject_css()
    page_header("🛡️", t("platform.dashboard_title"), t("platform.dashboard_subtitle"))

    col_logout = st.columns([5, 1])[1]
    if col_logout.button(t("platform.logout"), use_container_width=True, key="platform_logout_btn"):
        logout_platform_admin()
        st.rerun()

    tab_overview, tab_tenants, tab_new = st.tabs([
        t("platform.tab_overview"), t("platform.tab_tenants"), t("platform.tab_new_tenant")
    ])

    with tab_overview:
        stats = _platform_stats()
        kpi_row([
            {"label": t("platform.kpi_total_tenants"), "value": stats["total"], "cls": ""},
            {"label": t("platform.kpi_active"), "value": stats["active"], "cls": "success"},
            {"label": t("platform.kpi_suspended"), "value": stats["suspended"],
             "cls": "danger" if stats["suspended"] else ""},
            {"label": t("platform.kpi_total_students"), "value": stats["students"], "cls": "accent"},
        ])
        kpi_row([
            {"label": t("platform.kpi_mrr"), "value": f"৳{stats['mrr']:,.0f}", "cls": "success"},
            {"label": t("platform.kpi_expiring_soon"), "value": stats["expiring_soon"],
             "cls": "warning" if stats["expiring_soon"] else ""},
            {"label": t("platform.kpi_overdue"), "value": stats["overdue"],
             "cls": "danger" if stats["overdue"] else ""},
        ])
        st.markdown(f"#### {t('platform.recent_signups_heading')}")
        if not stats["recent"]:
            alert(t("platform.no_recent_signups"), "info")
        for r in stats["recent"]:
            st.markdown(f"- **{r['madrasa_name']}** — {str(r['created_at'])[:10]}")

    with tab_tenants:
        tenants = _list_tenants()
        if not tenants:
            alert(t("platform.no_tenants"), "info")
        today = date.today()
        for tn in tenants:
            status = tn["status"] or "active"
            expiry = tn["subscription_expiry"]
            if status == "suspended":
                status_icon, status_label = "🔴", t("platform.status_suspended")
            elif expiry and expiry < today:
                status_icon, status_label = "🟠", t("platform.status_overdue")
            elif expiry and expiry <= today + timedelta(days=7):
                status_icon, status_label = "🟡", t("platform.status_expiring_soon")
            else:
                status_icon, status_label = "🟢", t("platform.status_active")

            with st.expander(f"{status_icon} **{tn['madrasa_name']}** — {status_label}"):
                st.markdown(
                    f"**{t('platform.col_admin')}:** {tn['admin_name'] or '—'} "
                    f"({tn['admin_username'] or '—'})  \n"
                    f"**{t('platform.col_students')}:** {tn['student_count']}  \n"
                    f"**{t('platform.col_created')}:** {str(tn['created_at'])[:10]}  \n"
                    f"**{t('platform.col_plan')}:** {tn['plan_type']}  —  "
                    f"**{t('platform.col_monthly_fee')}:** ৳{float(tn['monthly_fee']):,.0f}  \n"
                    f"**{t('platform.col_expiry')}:** {str(expiry) if expiry else '—'}"
                )
                c1, c2 = st.columns(2)
                if status == "suspended":
                    if c1.button(t("platform.reactivate_btn"), key=f"react_{tn['id']}", use_container_width=True):
                        _set_tenant_status(tn["id"], "active")
                        _log_platform_action(tn["id"], "UPDATE", "platform.audit_reactivated")
                        st.success(t("platform.status_changed_success"))
                        st.rerun()
                else:
                    if c1.button(t("platform.suspend_btn"), key=f"susp_{tn['id']}", use_container_width=True):
                        _set_tenant_status(tn["id"], "suspended")
                        _log_platform_action(tn["id"], "UPDATE", "platform.audit_suspended")
                        st.success(t("platform.status_changed_success"))
                        st.rerun()

                if c2.button(t("platform.impersonate_btn"), key=f"imp_{tn['id']}", use_container_width=True):
                    _impersonate_tenant(tn)

                divider()
                st.markdown(f"**{t('platform.record_payment_heading')}**")
                with st.form(f"pay_form_{tn['id']}"):
                    pc1, pc2, pc3 = st.columns(3)
                    pay_amount = pc1.number_input(
                        t("platform.amount_label"), min_value=0.0,
                        value=float(tn["monthly_fee"]), step=100.0, key=f"pay_amt_{tn['id']}",
                    )
                    pay_method = pc2.selectbox(
                        t("platform.payment_method_label"), ["bkash", "bank", "cash", "nagad"],
                        key=f"pay_method_{tn['id']}",
                    )
                    pay_months = pc3.number_input(
                        t("platform.months_covered_label"), min_value=1, max_value=24,
                        value=1, step=1, key=f"pay_months_{tn['id']}",
                    )
                    pay_notes = st.text_input(t("platform.notes_optional"), key=f"pay_notes_{tn['id']}")
                    if st.form_submit_button(t("platform.mark_paid_btn"), type="primary"):
                        ok, result = _record_tenant_payment(
                            tn["id"], pay_amount, pay_method, int(pay_months), pay_notes
                        )
                        if ok:
                            _log_platform_action(tn["id"], "CREATE", "platform.audit_payment_recorded")
                            st.success(t("platform.payment_recorded_success", expiry=str(result)))
                            st.rerun()
                        else:
                            st.error(result)

                payments = fetchall(
                    """SELECT amount, payment_method, months_covered, created_at
                       FROM tenant_payments WHERE tenant_id=%s
                       ORDER BY created_at DESC LIMIT 5""",
                    (tn["id"],),
                )
                if payments:
                    st.caption(t("platform.recent_payments_heading"))
                    for p in payments:
                        st.caption(
                            f"৳{float(p['amount']):,.0f} — {p['payment_method']} — "
                            f"{p['months_covered']} {t('platform.months_unit')} — "
                            f"{str(p['created_at'])[:10]}"
                        )

    with tab_new:
        st.markdown(f"#### {t('platform.new_tenant_heading')}")
        with st.form("new_tenant_form"):
            madrasa_name = st.text_input(t("platform.madrasa_name_label") + " *")
            c1, c2, c3 = st.columns(3)
            address = c1.text_input(t("platform.address_label"))
            phone   = c2.text_input(t("platform.phone_label"))
            email   = c3.text_input(t("platform.email_label"))

            divider()
            st.markdown(f"**{t('platform.admin_account_heading')}**")
            ac1, ac2, ac3 = st.columns(3)
            admin_full_name = ac1.text_input(t("platform.admin_full_name_label"))
            admin_username  = ac2.text_input(t("platform.admin_username_label") + " *")
            admin_password  = ac3.text_input(t("platform.admin_password_label") + " *")

            divider()
            st.markdown(f"**{t('platform.billing_heading')}**")
            bc1, bc2, bc3 = st.columns(3)
            plan_type = bc1.selectbox(t("platform.plan_type_label"), ["trial", "basic", "premium"])
            monthly_fee = bc2.number_input(t("platform.monthly_fee_label"), min_value=0.0, step=100.0)
            trial_days = bc3.number_input(t("platform.initial_days_label"), min_value=1, max_value=365, value=30)

            submitted = st.form_submit_button(t("platform.create_tenant_btn"), type="primary")
            if submitted:
                pw_err = validate_password_strength(admin_password)
                if not madrasa_name.strip() or not admin_username.strip():
                    st.error(t("platform.err_new_tenant_validation"))
                elif pw_err:
                    st.error(pw_err)
                else:
                    expiry = date.today() + timedelta(days=int(trial_days))
                    ok, result = _create_tenant(
                        madrasa_name.strip(), address.strip(), phone.strip(), email.strip(),
                        admin_username.strip(), admin_password, admin_full_name.strip(),
                        plan_type, monthly_fee, expiry,
                    )
                    if ok:
                        _log_platform_action(result, "CREATE", "platform.audit_tenant_created")
                        st.success(t("platform.tenant_created_success", name=madrasa_name))
                        st.info(t("platform.tenant_created_credentials", username=admin_username))
                        st.rerun()
                    else:
                        st.error(result)


def _log_platform_action(tenant_id: int, action: str, description_key: str):
    platform_username = st.session_state.get("platform_admin_username", "?")
    audit_module.log(
        action, "Platform", t(description_key, platform_user=platform_username),
        tenant_id_override=tenant_id,
        actor_username=f"platform:{platform_username}",
    )


def _impersonate_tenant(tenant_row: dict):
    target = _get_tenant_admin_for_impersonation(tenant_row["id"])
    if not target:
        st.error(t("platform.err_no_admin_user"))
        return

    _log_platform_action(tenant_row["id"], "LOGIN", "platform.audit_impersonate")
    st.session_state.update({
        "logged_in": True,
        "tenant_id": tenant_row["id"],
        "user_id": target["id"],
        "username": target["username"],
        "user_role": target["role"],
        "user_name": target["full_name"] or target["username"],
        "madrasa_name": tenant_row["madrasa_name"],
        "language": target.get("language") or "bn",
        "nav_page": "Dashboard",
        "impersonating": True,
        "impersonated_tenant_name": tenant_row["madrasa_name"],
    })
    st.query_params.clear()
    st.rerun()


def render_impersonation_banner():
    """app.py থেকে normal tenant-authenticated flow-এর ভেতর থেকে কল হয়,
    যখন কোনো platform admin support-এর জন্য impersonate করছে।"""
    tenant_name = st.session_state.get("impersonated_tenant_name", "")
    st.warning(t("platform.impersonation_banner", tenant=tenant_name))
    if st.button(t("platform.end_impersonation_btn"), key="end_impersonation_btn"):
        _end_impersonation()


def _end_impersonation():
    for key in ["logged_in", "tenant_id", "user_id", "username",
                "user_role", "user_name", "madrasa_name", "nav_page",
                "active_session_id", "_2fa_setup_mandatory", "_2fa_grace_days_left",
                "impersonating", "impersonated_tenant_name"]:
        st.session_state.pop(key, None)
    st.query_params["page"] = "platform"
    st.rerun()


def logout_platform_admin():
    for key in ["platform_admin_logged_in", "platform_admin_id", "platform_admin_username"]:
        st.session_state.pop(key, None)


def render():
    """app.py-এর ?page=platform পাবলিক রুট থেকে কল হয় — কোনো tenant
    selector নেই, সম্পূর্ণ আলাদা platform_admins পরিচয়ে লগইন।"""
    if st.session_state.get("platform_admin_logged_in"):
        _render_dashboard()
    else:
        _login_form()
