"""
security_2fa.py — Two-Factor Authentication (2FA)
TOTP (Google Authenticator) + OTP via SMS।
Admin ও Accountant-এর জন্য 2FA বাধ্যতামূলক।
"""

import os
import io
import json
import time
import base64
import hashlib
import hmac
import struct
import random
import logging
from datetime import datetime, timedelta
from db import get_connection, release_connection, fetchone, fetchall
from utils import page_header, alert, divider, get_tenant_id
from i18n import t

logger = logging.getLogger("madrasa.2fa")

# ─────────────────────────────────────────────
# TOTP (Time-based One-Time Password)
# RFC 6238 implementation
# ─────────────────────────────────────────────

def _generate_totp_secret() -> str:
    """32-character base32 secret।"""
    chars  = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    secret = "".join(random.choices(chars, k=32))
    return secret


def _hotp(secret: str, counter: int) -> str:
    """HMAC-based OTP।"""
    key = base64.b32decode(secret.upper())
    msg = struct.pack(">Q", counter)
    h   = hmac.new(key, msg, hashlib.sha1).digest()
    o   = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o+4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


def _totp(secret: str, window: int = 1) -> list[str]:
    """Current + adjacent time windows-এর TOTP codes।"""
    counter = int(time.time() // 30)
    return [_hotp(secret, counter + i) for i in range(-window, window + 1)]


def verify_totp(secret: str, code: str) -> bool:
    """User-এর TOTP code যাচাই করুন।"""
    return code.strip() in _totp(secret, window=1)


def generate_qr_uri(secret: str, username: str, issuer: str = "Smart Madrasa ERP") -> str:
    """Google Authenticator QR URI।"""
    return (
        f"otpauth://totp/{issuer}:{username}"
        f"?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    )


def generate_totp_qr_base64(secret: str, username: str) -> str:
    """QR code PNG → base64 string।"""
    try:
        import qrcode
        uri = generate_qr_uri(secret, username)
        qr  = qrcode.QRCode(version=1, box_size=6, border=2)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0F4C5C", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        return ""


def generate_backup_codes() -> list[str]:
    """৮টি একবার-ব্যবহারযোগ্য backup code।"""
    return [
        f"{random.randint(10000000, 99999999)}"
        for _ in range(8)
    ]


# ─────────────────────────────────────────────
# SMS OTP
# ─────────────────────────────────────────────

def _generate_otp(length: int = 6) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(length))


def _send_otp_sms(phone: str, otp: str, madrasa_name: str) -> bool:
    from sms_gateway import send_sms
    msg = t("2fa.sms_otp_message", madrasa=madrasa_name, otp=otp)
    return send_sms(phone, msg)


# ─────────────────────────────────────────────
# DB operations
# ─────────────────────────────────────────────

def get_2fa_record(user_id: int):
    return fetchone(
        "SELECT * FROM user_2fa WHERE user_id=%s", (user_id,)
    )


def enable_totp(user_id: int, tenant_id: int, secret: str) -> bool:
    backup = generate_backup_codes()
    conn   = get_connection()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            # Fix: backup_codes is a JSON column -- str(backup) produces
            # Python's single-quoted repr (['12345678', ...]), which
            # Postgres's JSON type rejects outright ("invalid input syntax
            # for type json"). This made enable_totp() fail on every call,
            # silently (the caller has no error branch for it), so 2FA
            # activation could never succeed no matter what code was
            # entered. json.dumps() matches what verify_backup_code()
            # already reads/writes elsewhere in this same file.
            cur.execute(
                """INSERT INTO user_2fa (tenant_id, user_id, totp_secret, totp_enabled, backup_codes)
                   VALUES (%s,%s,%s,TRUE,%s)
                   ON CONFLICT (user_id) DO UPDATE
                     SET totp_secret=%s, totp_enabled=TRUE, backup_codes=%s""",
                (tenant_id, user_id, secret, json.dumps(backup),
                 secret, json.dumps(backup)),
            )
        conn.commit()
        return True
    except Exception as ex:
        conn.rollback()
        logger.error(f"Enable TOTP failed: {ex}")
        return False
    finally:
        release_connection(conn)


def disable_2fa(user_id: int) -> bool:
    conn = get_connection()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_2fa SET totp_enabled=FALSE WHERE user_id=%s",
                (user_id,),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def send_sms_otp(user_id: int, tenant_id: int, phone: str) -> str | None:
    """SMS OTP পাঠান ও DB-তে store করুন।"""
    otp     = _generate_otp()
    expires = datetime.utcnow() + timedelta(minutes=5)
    tenant  = fetchone("SELECT madrasa_name FROM tenants WHERE id=%s", (tenant_id,))
    madrasa = tenant["madrasa_name"] if tenant else "Smart Madrasa"

    conn = get_connection()
    if not conn: return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO user_2fa (tenant_id, user_id, otp_code, otp_expires_at)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (user_id) DO UPDATE
                     SET otp_code=%s, otp_expires_at=%s""",
                (tenant_id, user_id, otp, expires, otp, expires),
            )
        conn.commit()
        sent = _send_otp_sms(phone, otp, madrasa)
        logger.info(f"OTP {'sent' if sent else 'failed'} for user {user_id}")
        return otp  # Return for demo/fallback
    except Exception as ex:
        conn.rollback()
        logger.error(f"Send OTP failed: {ex}")
        return None
    finally:
        release_connection(conn)


def verify_sms_otp(user_id: int, code: str) -> bool:
    record = get_2fa_record(user_id)
    if not record: return False
    if record["otp_code"] != code.strip(): return False
    if record["otp_expires_at"] < datetime.utcnow(): return False
    # Clear OTP after use
    conn = get_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE user_2fa SET otp_code=NULL, otp_expires_at=NULL WHERE user_id=%s",
                    (user_id,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    return True


def verify_backup_code(user_id: int, code: str) -> bool:
    """Backup code দিয়ে login করুন (একবারই ব্যবহার করা যাবে)।"""
    import json
    record = get_2fa_record(user_id)
    if not record or not record.get("backup_codes"): return False
    try:
        codes = json.loads(record["backup_codes"]) if isinstance(
            record["backup_codes"], str) else record["backup_codes"]
        if code.strip() in codes:
            codes.remove(code.strip())
            conn = get_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE user_2fa SET backup_codes=%s WHERE user_id=%s",
                            (json.dumps(codes), user_id),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                finally:
                    release_connection(conn)
            return True
    except Exception as ex:
        logger.error(f"Backup code verify failed: {ex}")
    return False


# ─────────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────────

def render():
    import streamlit as st
    tid     = get_tenant_id()
    user_id = st.session_state.get("user_id", 0)
    username= st.session_state.get("username", "")
    role    = st.session_state.get("user_role", "staff")

    page_header("🔐", t("2fa.page_title"), t("2fa.page_subtitle"))

    record = get_2fa_record(user_id) if user_id else None
    is_enabled = bool(record and record.get("totp_enabled"))

    # Status card
    status_color = "#2E7D32" if is_enabled else "#C62828"
    status_icon  = "🟢" if is_enabled else "🔴"
    status_text  = t("2fa.status_active") if is_enabled else t("2fa.status_inactive")
    status_desc  = t("2fa.desc_protected") if is_enabled else t("2fa.desc_enable_prompt")
    st.markdown(
        f"""<div style="background:{'#E8F5E9' if is_enabled else '#FFEBEE'};
                        border:1px solid {status_color}30;border-radius:10px;
                        padding:1rem 1.25rem;margin-bottom:1.5rem;
                        display:flex;align-items:center;gap:12px">
          <div style="font-size:2rem">{status_icon}</div>
          <div>
            <div style="font-weight:700;color:{status_color}">
              2FA {status_text}
            </div>
            <div style="font-size:0.8rem;color:#6B7A8D;margin-top:2px">
              {status_desc}
            </div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    tab_totp, tab_sms, tab_backup, tab_sessions = st.tabs([
        "📱 TOTP Setup", "📲 SMS OTP", "🔑 Backup Codes", "🛡️ Security Info"
    ])

    # ── TOTP Setup ──
    with tab_totp:
        if is_enabled:
            st.success(t("2fa.totp_already_active"))
            if st.button(t("2fa.btn_disable"), type="primary"):
                if disable_2fa(user_id):
                    st.warning(t("2fa.msg_disabled"))
                    st.rerun()
        else:
            st.markdown(f"#### {t('2fa.totp_setup_heading')}")

            if "totp_secret" not in st.session_state:
                st.session_state.totp_secret = _generate_totp_secret()

            secret = st.session_state.totp_secret

            st.markdown(t("2fa.step1"))
            st.markdown(t("2fa.step2"))

            qr_b64 = generate_totp_qr_base64(secret, username)
            if qr_b64:
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.markdown(
                        f'<img src="data:image/png;base64,{qr_b64}" '
                        f'style="width:180px;border:2px solid #0F4C5C;border-radius:8px">',
                        unsafe_allow_html=True,
                    )
                with col2:
                    st.markdown(f"**Manual Key:**")
                    st.code(secret, language=None)
                    st.caption(t("2fa.manual_key_caption"))
            else:
                st.code(secret, language=None)
                st.caption(t("2fa.qr_missing_caption"))

            st.markdown(t("2fa.step3"))
            with st.form("totp_verify_form"):
                code = st.text_input("6-digit TOTP Code", max_chars=6,
                                      placeholder="123456")
                if st.form_submit_button(t("2fa.btn_verify_activate"), type="primary"):
                    if not code.strip() or len(code.strip()) != 6:
                        st.error(t("2fa.err_enter_6digit"))
                    elif verify_totp(secret, code):
                        if enable_totp(user_id, tid, secret):
                            st.success(t("2fa.msg_activated"))
                            st.session_state.pop("totp_secret", None)
                            st.rerun()
                        else:
                            # Fix: enable_totp() failing (DB error, etc.)
                            # previously showed nothing at all -- the user
                            # just saw the same setup form again with no
                            # explanation, indistinguishable from a wrong
                            # code.
                            st.error(t("2fa.err_activation_failed"))
                    else:
                        st.error(t("2fa.err_wrong_code"))

    # ── SMS OTP ──
    with tab_sms:
        st.markdown("#### 📲 SMS OTP Test")
        user_info = fetchone(
            "SELECT mobile_no FROM app_users WHERE id=%s", (user_id,)
        ) if user_id else None
        phone = user_info.get("mobile_no", "") if user_info else ""

        with st.form("sms_otp_form"):
            phone_input = st.text_input(t("2fa.mobile_number"), value=phone or "",
                                         placeholder="01XXXXXXXXX")
            if st.form_submit_button(t("2fa.btn_send_otp"), type="primary"):
                if not phone_input.strip():
                    st.error(t("2fa.err_enter_mobile"))
                else:
                    otp = send_sms_otp(user_id, tid, phone_input.strip())
                    if otp:
                        st.success(t("2fa.msg_otp_sent"))
                        if os.environ.get("ENVIRONMENT") == "development":
                            st.info(f"🔧 Dev Mode OTP: **{otp}**")

        with st.form("sms_otp_verify_form"):
            otp_input = st.text_input(t("2fa.otp_code_label"), max_chars=6)
            if st.form_submit_button(t("2fa.btn_verify")):
                if verify_sms_otp(user_id, otp_input.strip()):
                    st.success(t("2fa.msg_otp_correct"))
                else:
                    st.error(t("2fa.err_otp_wrong_expired"))

    # ── Backup Codes ──
    with tab_backup:
        st.markdown("#### 🔑 Backup Codes")
        alert(
            t("2fa.backup_codes_notice"),
            "warning",
        )
        if record and record.get("backup_codes"):
            import json
            try:
                codes = json.loads(record["backup_codes"]) if isinstance(
                    record["backup_codes"], str) else record["backup_codes"]
                st.markdown(t("2fa.remaining_backup_codes", n=len(codes)))
                cols = st.columns(4)
                for i, code in enumerate(codes):
                    cols[i % 4].code(code, language=None)
            except Exception:
                st.caption(t("2fa.backup_codes_not_found"))
        else:
            st.caption(t("2fa.enable_2fa_for_codes"))

    # ── Security Info ──
    with tab_sessions:
        st.markdown("#### 🛡️ Security Overview")
        tenant_users = fetchall(
            """SELECT u.username, u.role, u.full_name,
                      f.totp_enabled, f.last_used_at
               FROM app_users u
               LEFT JOIN user_2fa f ON f.user_id=u.id
               WHERE u.tenant_id=%s AND u.is_active=TRUE
               ORDER BY u.role, u.username""",
            (tid,),
        ) if role == "admin" else []

        if tenant_users:
            col_user, col_role, col_last_used = t("2fa.col_user"), t("2fa.col_role"), t("2fa.col_last_used")
            rows = [{
                col_user: u["username"],
                col_role: u["role"],
                "2FA":     t("2fa.active_check") if u.get("totp_enabled") else t("2fa.inactive_cross"),
                col_last_used: str(u["last_used_at"])[:16] if u.get("last_used_at") else "—",
            } for u in tenant_users]
            st.dataframe(rows, use_container_width=True, hide_index=True)

            no_2fa = sum(1 for u in tenant_users if not u.get("totp_enabled"))
            if no_2fa > 0:
                alert(
                    t("2fa.warn_users_no_2fa", n=no_2fa),
                    "warning",
                )
        else:
            st.markdown(t("2fa.security_tips"))
