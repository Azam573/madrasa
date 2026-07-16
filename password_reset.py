"""
password_reset.py — Password Reset System
সম্পূর্ণ OTP-ভিত্তিক পাসওয়ার্ড রিসেট ফ্লো।
"""

import os
import random
import hashlib
import logging
from datetime import datetime, timedelta
from db import get_connection, release_connection, fetchone
from utils import PALETTE
from error_handler import safe_db_error
from ip_throttle import check_ip_rate_limit
from i18n import t
# নোট: hash_password auth.py থেকে lazy-import করা হয় (নিচে ব্যবহারের সময়),
# কারণ auth.py নিজেই module-level এ password_reset.py import করে —
# circular import এড়াতে এই pattern প্রয়োজন।

logger = logging.getLogger("madrasa.password_reset")

def _generate_otp() -> str:
    return str(random.randint(100000, 999999))


def _send_otp_sms(phone: str, otp: str, madrasa_name: str) -> bool:
    from sms_gateway import send_sms
    msg = (
        f"{madrasa_name} ERP Password Reset OTP: {otp}\n"
        f"৫ মিনিটের মধ্যে ব্যবহার করুন। কাউকে জানাবেন না।"
    )
    return send_sms(phone, msg)


def _rate_check(user_id: int) -> bool:
    row = fetchone(
        """SELECT COUNT(*) AS n FROM password_reset_tokens
           WHERE user_id=%s AND created_at > NOW() - INTERVAL '3 minutes'""",
        (user_id,),
    )
    return int(row["n"]) == 0 if row else True


def initiate_reset(tenant_id: int, username: str) -> dict:
    user = fetchone(
        """SELECT id, username, mobile_no, full_name
           FROM app_users
           WHERE tenant_id=%s AND username=%s AND is_active=TRUE""",
        (tenant_id, username.strip()),
    )
    if not user:
        return {"success": False, "error": t("pwreset.err_user_not_found")}
    if not user.get("mobile_no"):
        return {"success": False, "error": t("pwreset.err_no_mobile")}
    if not check_ip_rate_limit("password_reset_otp", max_requests=5, window_seconds=3600):
        return {"success": False, "error": t("pwreset.err_rate_limited_ip")}
    if not _rate_check(user["id"]):
        return {"success": False, "error": t("pwreset.err_rate_limited")}

    otp        = _generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=5)

    conn = get_connection()
    if not conn:
        return {"success": False, "error": t("pwreset.err_db_connection")}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE password_reset_tokens SET used=TRUE WHERE user_id=%s AND used=FALSE",
                (user["id"],),
            )
            cur.execute(
                """INSERT INTO password_reset_tokens
                   (tenant_id, user_id, otp_code, expires_at)
                   VALUES (%s,%s,%s,%s)""",
                (tenant_id, user["id"], otp, expires_at),
            )
        conn.commit()
    except Exception as ex:
        conn.rollback()
        return {"success": False, "error": safe_db_error(ex)}
    finally:
        release_connection(conn)

    tenant = fetchone("SELECT madrasa_name FROM tenants WHERE id=%s", (tenant_id,))
    madrasa = tenant["madrasa_name"] if tenant else "Smart Madrasa"
    sms_sent = _send_otp_sms(user["mobile_no"], otp, madrasa)

    phone = user["mobile_no"]
    masked = phone[:5] + "***" + phone[-3:] if len(phone) >= 8 else "***"

    result = {"success": True, "user_id": user["id"], "masked_phone": masked, "sms_sent": sms_sent}
    if os.environ.get("ENVIRONMENT", "development") == "development":
        result["otp_dev"] = otp
        logger.warning(f"DEV MODE OTP for {username}: {otp}")
    return result


def verify_otp(user_id: int, otp: str) -> dict:
    row = fetchone(
        """SELECT id, otp_code, expires_at, attempts, used
           FROM password_reset_tokens
           WHERE user_id=%s AND used=FALSE
           ORDER BY created_at DESC LIMIT 1""",
        (user_id,),
    )
    if not row:
        return {"success": False, "error": t("pwreset.err_no_active_request")}
    if row["used"]:
        return {"success": False, "error": t("pwreset.err_otp_already_used")}
    if datetime.utcnow() > row["expires_at"].replace(tzinfo=None):
        return {"success": False, "error": t("pwreset.err_otp_expired")}
    if int(row["attempts"]) >= 5:
        return {"success": False, "error": t("pwreset.err_too_many_attempts")}

    conn = get_connection()
    if not conn:
        return {"success": False, "error": t("pwreset.err_db_connection")}
    try:
        with conn.cursor() as cur:
            if row["otp_code"] != otp.strip():
                cur.execute(
                    "UPDATE password_reset_tokens SET attempts=attempts+1 WHERE id=%s",
                    (row["id"],),
                )
                conn.commit()
                remaining = 5 - int(row["attempts"]) - 1
                return {"success": False, "error": t("pwreset.err_wrong_otp", remaining=remaining)}

            reset_token = hashlib.sha256(
                f"{user_id}{otp}{datetime.utcnow()}".encode()
            ).hexdigest()
            cur.execute(
                "UPDATE password_reset_tokens SET used=TRUE, otp_code=%s WHERE id=%s",
                (reset_token, row["id"]),
            )
        conn.commit()
        return {"success": True, "reset_token": reset_token}
    except Exception as ex:
        conn.rollback()
        return {"success": False, "error": safe_db_error(ex)}
    finally:
        release_connection(conn)


def set_new_password(user_id: int, reset_token: str, new_password: str) -> dict:
    from auth import validate_password_strength  # deferred — auth.py imports this module at top level
    pw_err = validate_password_strength(new_password)
    if pw_err:
        return {"success": False, "error": pw_err}

    row = fetchone(
        """SELECT id FROM password_reset_tokens
           WHERE user_id=%s AND otp_code=%s AND used=TRUE
             AND expires_at > NOW() - INTERVAL '10 minutes'""",
        (user_id, reset_token),
    )
    if not row:
        return {"success": False, "error": t("pwreset.err_invalid_token")}

    conn = get_connection()
    if not conn:
        return {"success": False, "error": t("pwreset.err_db_connection")}
    try:
        from auth import hash_password as _hash_password  # lazy import — circular dep এড়াতে
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_users SET password_hash=%s WHERE id=%s",
                (_hash_password(new_password), user_id),
            )
            cur.execute("DELETE FROM password_reset_tokens WHERE user_id=%s", (user_id,))
            cur.execute(
                "SELECT tenant_id FROM app_users WHERE id=%s",
                (user_id,),
            )
            tenant_row = cur.fetchone()
        conn.commit()

        # #4 fix: পাসওয়ার্ড পরিবর্তনের পর পুরোনো সব token (যেকোনো ডিভাইসে
        # লগইন করা থাকলেও) অবিলম্বে invalid করে দাও — চুরি যাওয়া session
        # টিকে থাকতে পারবে না।
        if tenant_row:
            try:
                from api.core.jwt_revoke import revoke_all_user_tokens
                revoke_all_user_tokens(user_id, tenant_row["tenant_id"])
            except Exception as ex:
                logger.warning(f"Post-reset token revocation ব্যর্থ (non-fatal): {ex}")

        logger.info(f"Password reset successful for user_id={user_id}")
        return {"success": True}
    except Exception as ex:
        conn.rollback()
        return {"success": False, "error": safe_db_error(ex)}
    finally:
        release_connection(conn)


def render_password_reset_page(tenant_id: int, madrasa_name: str):
    import streamlit as st

    st.markdown(
        f"""<div style="text-align:center;margin-bottom:1.5rem">
          <div style="font-size:2rem">🔑</div>
          <div style="font-size:1.1rem;font-weight:700;color:{PALETTE['primary']}">{t('pwreset.heading')}</div>
          <div style="font-size:0.78rem;color:{PALETTE['muted']};margin-top:4px">{madrasa_name}</div>
        </div>""",
        unsafe_allow_html=True,
    )

    step = st.session_state.get("reset_step", 1)

    if step == 1:
        with st.form("reset_step1"):
            st.markdown(t("pwreset.step1_instruction"))
            username = st.text_input(t("pwreset.username_label"), placeholder=t("pwreset.username_placeholder"))
            if st.form_submit_button(t("pwreset.btn_send_otp"), type="primary", use_container_width=True):
                if not username.strip():
                    st.error(t("pwreset.err_username_required"))
                else:
                    result = initiate_reset(tenant_id, username.strip())
                    if result["success"]:
                        st.session_state.reset_step    = 2
                        st.session_state.reset_user_id = result["user_id"]
                        st.session_state.reset_phone   = result["masked_phone"]
                        if result.get("otp_dev"):
                            st.info(t("pwreset.dev_otp_note", otp=result['otp_dev']))
                        st.rerun()
                    else:
                        st.error(result["error"])

    elif step == 2:
        phone = st.session_state.get("reset_phone", "***")
        st.info(t("pwreset.otp_sent_to", phone=phone))
        with st.form("reset_step2"):
            st.markdown(t("pwreset.step2_instruction"))
            otp = st.text_input(t("pwreset.otp_label"), max_chars=6, placeholder=t("pwreset.otp_placeholder"))
            if st.form_submit_button(t("pwreset.btn_verify"), type="primary", use_container_width=True):
                result = verify_otp(st.session_state.reset_user_id, otp.strip())
                if result["success"]:
                    st.session_state.reset_step  = 3
                    st.session_state.reset_token = result["reset_token"]
                    st.rerun()
                else:
                    st.error(result["error"])
        if st.button(t("pwreset.btn_back"), key="back1"):
            st.session_state.reset_step = 1
            st.rerun()

    elif step == 3:
        with st.form("reset_step3"):
            st.markdown(t("pwreset.step3_instruction"))
            pwd1 = st.text_input(t("pwreset.new_password_label"), type="password", placeholder=t("pwreset.new_password_placeholder"))
            pwd2 = st.text_input(t("pwreset.confirm_password_label"), type="password")
            if st.form_submit_button(t("pwreset.btn_change_password"), type="primary", use_container_width=True):
                if pwd1 != pwd2:
                    st.error(t("pwreset.err_passwords_mismatch"))
                else:
                    result = set_new_password(
                        st.session_state.reset_user_id,
                        st.session_state.reset_token,
                        pwd1,
                    )
                    if result["success"]:
                        for k in ["reset_step", "reset_user_id", "reset_phone", "reset_token"]:
                            st.session_state.pop(k, None)
                        st.success(t("pwreset.success_changed"))
                        st.balloons()
                    else:
                        st.error(result["error"])

    if step in (1, 2, 3):
        if st.button(t("pwreset.btn_back_to_login"), use_container_width=True):
            for k in ["reset_step", "reset_user_id", "reset_phone", "reset_token"]:
                st.session_state.pop(k, None)
            st.session_state.pop("show_password_reset", None)
            st.rerun()
