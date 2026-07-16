"""
tenant_registration.py — Public "Register your Madrasa" self-serve signup
লগইন ছাড়াই নতুন মাদ্রাসা (tenant) + প্রথম admin ইউজার তৈরি করা যায়।
branch_module.py-এর _create_branch_tenant()-এর মতো একই tenant+session+
classes+admin-user সিকোয়েন্স, কিন্তু (ক) সম্পূর্ণ পাবলিক/pre-login রুট
হিসেবে, এবং (খ) ব্যবহারকারীর নিজের দেওয়া username/password দিয়ে —
হার্ডকোড করা "admin123" নয়। সাথে সাথেই সক্রিয় — কোনো অনুমোদনের সারি নেই।
"""

import streamlit as st
import time
from datetime import date, timedelta
from db import get_connection, release_connection
from utils import inject_css, PALETTE, flatten_html
from auth import hash_password, validate_password_strength
from error_handler import safe_db_error
from ip_throttle import check_ip_rate_limit
import monitoring
from i18n import t

TRIAL_DAYS = 7  # self-registration মানেই বিনামূল্যে ট্রায়াল — এর পর সুপার
                 # এডমিনের অনুমোদন (Mark Paid) ছাড়া platform_admin.py-এর
                 # check_expired_subscriptions() টাস্ক স্বয়ংক্রিয়ভাবে suspend করে

MIN_SUBMIT_SECONDS = 3  # online_admission.py-এর মতোই — এত অল্প সময়ে মানুষ
                          # এই ফর্ম পূরণ করতে পারবে না


def _slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")[:30] or "madrasa"


def _register_tenant(madrasa_name, address, phone, username, password, full_name):
    conn = get_connection()
    if not conn:
        return False, t("register.err_db_generic")
    try:
        with conn.cursor() as cur:
            trial_expiry = date.today() + timedelta(days=TRIAL_DAYS)
            cur.execute(
                """INSERT INTO tenants
                   (madrasa_name, address, phone, slug, plan_type, monthly_fee, subscription_expiry)
                   VALUES (%s,%s,%s,%s,'trial',0,%s) RETURNING id""",
                (madrasa_name, address, phone, _slugify(madrasa_name), trial_expiry),
            )
            tid = cur.fetchone()["id"]

            cur.execute(
                """INSERT INTO academic_sessions (tenant_id, session_name, is_active)
                   VALUES (%s,%s,TRUE)""",
                (tid, str(__import__("datetime").date.today().year)),
            )
            for cls_name, cls_num in [
                ("Hifz-1", 1), ("Hifz-2", 2), ("Nazera-1", 3), ("Ibtedaee", 4),
            ]:
                cur.execute(
                    "INSERT INTO classes (tenant_id, class_name, class_numeric) VALUES (%s,%s,%s)",
                    (tid, cls_name, cls_num),
                )

            cur.execute(
                """INSERT INTO app_users (tenant_id, username, password_hash, role, full_name, is_active, language)
                   VALUES (%s,%s,%s,'admin',%s,TRUE,'bn')""",
                (tid, username, hash_password(password), full_name),
            )
        conn.commit()
        return True, tid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def render():
    inject_css()
    if "reg_form_started_at" not in st.session_state:
        st.session_state.reg_form_started_at = time.time()

    st.markdown(
        flatten_html(f"""<div style="max-width:460px;margin:3rem auto">
          <div style="text-align:center;margin-bottom:2rem">
            <div style="font-size:3.5rem">🕌</div>
            <h1 style="font-size:1.5rem;font-weight:700;color:{PALETTE['primary']};margin:0.5rem 0 0.25rem">
              {t("register.page_title")}
            </h1>
            <p style="color:{PALETTE['muted']};font-size:0.85rem">{t("register.page_subtitle")}</p>
          </div>
        </div>"""),
        unsafe_allow_html=True,
    )

    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("tenant_register_form"):
            st.markdown(f"#### {t('register.form_heading')}")
            madrasa_name = st.text_input(t("register.madrasa_name_label"), placeholder=t("register.madrasa_name_placeholder"))
            c1, c2 = st.columns(2)
            address = c1.text_input(t("register.address_label"))
            phone   = c2.text_input(t("register.phone_label"))

            st.markdown(f"##### {t('register.admin_account_heading')}")
            full_name = st.text_input(t("register.full_name_label"))
            c3, c4 = st.columns(2)
            username  = c3.text_input(t("register.username_label"))
            password  = c4.text_input(t("register.password_label"), type="password")

            st.info(t("register.trial_notice", days=TRIAL_DAYS))
            st.markdown(t("register.legal_links_html"), unsafe_allow_html=True)
            agree = st.checkbox(t("register.agree_terms_checkbox"))

            # honeypot — utils.py-এর CSS দিয়ে লুকানো, আসল ব্যবহারকারী দেখে না
            hp_website = st.text_input("Website", key="reg_hp_website")

            submitted = st.form_submit_button(t("register.submit_btn"), type="primary", use_container_width=True)

            if submitted:
                elapsed = time.time() - st.session_state.get("reg_form_started_at", 0)

                if hp_website.strip():
                    # honeypot ধরা পড়েছে — bot-কে বোঝানো হয় না, চুপচাপ fake success
                    monitoring.capture_message(
                        "Tenant registration honeypot triggered", level="warning",
                    )
                    st.success(t("register.success", tenant_id="—"))
                    st.info(t("register.trial_success_note", days=TRIAL_DAYS))
                    st.info(t("register.login_hint"))
                    st.stop()

                if elapsed < MIN_SUBMIT_SECONDS:
                    st.error(t("oadm.err_try_again"))
                    st.stop()

                if not check_ip_rate_limit("tenant_register", max_requests=3, window_seconds=3600):
                    st.error(t("register.err_too_many_registrations"))
                    st.stop()

                errors = []
                if not madrasa_name.strip():
                    errors.append(t("register.err_madrasa_name_required"))
                if not full_name.strip():
                    errors.append(t("register.err_full_name_required"))
                if not username.strip() or len(username.strip()) < 3:
                    errors.append(t("register.err_username_invalid"))
                pw_err = validate_password_strength(password)
                if pw_err:
                    errors.append(pw_err)
                if not agree:
                    errors.append(t("register.err_must_agree_terms"))

                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    ok, result = _register_tenant(
                        madrasa_name.strip(), address.strip(), phone.strip(),
                        username.strip(), password, full_name.strip(),
                    )
                    if ok:
                        st.session_state.pop("reg_form_started_at", None)
                        st.success(t("register.success", tenant_id=result))
                        st.info(t("register.trial_success_note", days=TRIAL_DAYS))
                        st.info(t("register.login_hint"))
                    else:
                        st.error(result)

        st.markdown(
            f'<div style="text-align:center;margin-top:1rem;font-size:0.8rem;color:{PALETTE["muted"]}">'
            f'<a href="/" target="_self">{t("register.back_to_login")}</a></div>',
            unsafe_allow_html=True,
        )
