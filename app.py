"""
app.py — Smart Madrasa ERP v9.0
সব Priority 1 + 2 + 3 একত্রিত।
Critical fixes: argon2 hashing, Claude API key, connection pool, version consistency।
"""
import streamlit as st
from __version__ import VERSION, VERSION_LABEL, APP_NAME

st.set_page_config(
    page_title="Smart Madrasa ERP",
    page_icon="🕌",
    layout="wide",
    initial_sidebar_state="expanded",
)

from db import bootstrap_schema, fetchone
from utils import inject_css, get_tenant_id
from auth import check_auth, logout, can_access, update_language
from i18n import t
import monitoring

monitoring.init_sentry()

# Standard modules
import dashboard, admission_module, finance_module, academic_module
import student_portal, attendance_module, reports_module, settings_module
import teacher_module, notice_module, print_module, digital_attendance

# Enterprise P1
import audit_module, document_module
import enterprise_modules, whatsapp_module, online_admission

# Priority 2
import role_dashboard, timetable_module, ai_analytics
import parent_portal, zakat_module, donor_module
import financial_dashboard
import donor_portal
import tenant_registration

# Priority 3
import online_exam, govt_report, backup_module

# Redis + Celery
import cache_module

# Enterprise Security & Payments
import security_2fa, payment_gateway, realtime_dashboard

from auth import render_user_management

# ── Bootstrap (must run before auth/public pages — login page itself
#    queries the tenants table, so schema has to exist first) ──
if "schema_ready" not in st.session_state:
    with st.spinner(t("app.preparing")):
        ok = bootstrap_schema()
    if ok:
        st.session_state.schema_ready = True
    else:
        st.error(t("app.db_error"))
        st.stop()

# ── Public pages ──
qp = st.query_params
if qp.get("page") == "admission":
    inject_css()
    online_admission.render_public(int(qp.get("tenant", 1)))
    st.stop()
if qp.get("page") == "parent":
    inject_css()
    parent_portal.render()
    st.stop()
if qp.get("page") == "donor":
    inject_css()
    donor_portal.render(int(qp.get("tenant", 1)))
    st.stop()
if qp.get("page") == "register":
    tenant_registration.render()
    st.stop()
if qp.get("page") == "terms":
    import legal_pages
    legal_pages.render_terms()
    st.stop()
if qp.get("page") == "privacy":
    import legal_pages
    legal_pages.render_privacy()
    st.stop()
if qp.get("page") == "platform":
    import platform_admin
    platform_admin.render()
    st.stop()

# ── Auth ──
if not check_auth():
    st.stop()

# ── Impersonation banner (platform admin logged in as this tenant) ──
if st.session_state.get("impersonating"):
    import platform_admin
    platform_admin.render_impersonation_banner()

# ── Mandatory 2FA gate — grace period expired for this admin ──
if st.session_state.get("_2fa_setup_mandatory"):
    from security_2fa import get_2fa_record
    rec = get_2fa_record(st.session_state.get("user_id"))
    if rec and (rec.get("totp_enabled") or rec.get("sms_otp_enabled")):
        st.session_state.pop("_2fa_setup_mandatory", None)
    else:
        inject_css()
        st.warning(t("auth.2fa_mandatory_notice"))
        security_2fa.render()
        st.stop()

# ── Post-login, once per session ──
if "post_login_init_done" not in st.session_state:
    st.session_state.post_login_init_done = True
    tid = st.session_state.get("tenant_id", 1)
    active = fetchone(
        "SELECT id FROM academic_sessions WHERE tenant_id=%s AND is_active=TRUE LIMIT 1", (tid,)
    )
    if active:
        st.session_state.active_session_id = active["id"]
    audit_module.log("LOGIN","Auth",f"লগইন: {st.session_state.get('username','')}")

inject_css()
if "nav_page" not in st.session_state:
    st.session_state.nav_page = "Dashboard"

# ── 2FA grace-period warning banner (admin, not yet expired) ──
if st.session_state.get("_2fa_grace_days_left"):
    from security_2fa import get_2fa_record as _get_2fa_record
    _rec = _get_2fa_record(st.session_state.get("user_id"))
    if _rec and (_rec.get("totp_enabled") or _rec.get("sms_otp_enabled")):
        st.session_state.pop("_2fa_grace_days_left", None)
    else:
        st.warning(t("auth.2fa_grace_warning", days=st.session_state["_2fa_grace_days_left"]))

# ── Sidebar ──
tid       = get_tenant_id()
role      = st.session_state.get("user_role","staff")
user_name = st.session_state.get("user_name","ব্যবহারকারী")
madrasa   = st.session_state.get("madrasa_name","Smart Madrasa")

# Notification cache (v9.0 fix):
# আগে প্রতিটি page reload-এ DB query হত।
# এখন session_state-এ 60 সেকেন্ড cache করা হচ্ছে।
# ১০০ user x 5 reload/min = আগে ৫০০ query/min → এখন মাত্র ১/min per user
import time as _time
_now = _time.time()
_cache_ttl = 60  # seconds
_cache_valid = (
    st.session_state.get("schema_ready")
    and (_now - st.session_state.get("_notif_cached_at", 0)) < _cache_ttl
)
if _cache_valid:
    unread = st.session_state.get("_notif_unread", 0)
else:
    _row = fetchone(
        "SELECT COUNT(*) AS n FROM notifications WHERE tenant_id=%s AND is_read=FALSE",
        (tid,),
    ) if st.session_state.get("schema_ready") else None
    unread = int(_row["n"]) if _row else 0
    st.session_state["_notif_unread"]    = unread
    st.session_state["_notif_cached_at"] = _now

NAV_GROUPS = {
    t("group.main"): {
        t("nav.dashboard"): "Dashboard",
    },
    t("group.students"): {
        t("nav.admissions"):       "Admissions",
        t("nav.online_admission"):"Online Admission",
        t("nav.documents"):       "Documents",
        t("nav.student_portal"):  "Student Portal",
    },
    t("group.finance"): {
        t("nav.finance"): "Finance",
        t("nav.expense"): "Expense",
        t("nav.zakat"):   "Zakat",
        t("nav.donors"):  "Donors",
        t("nav.financial_overview"): "Financial Overview",
    },
    t("group.academic"): {
        t("nav.academics"):          "Academics",
        t("nav.online_exam"):        "Online Exam",
        t("nav.timetable"):          "Timetable",
        t("nav.ai_analytics"):       "AI Analytics",
        t("nav.digital_attendance"): "Digital Attendance",
        t("nav.attendance"):         "Attendance",
    },
    t("group.staff_facilities"): {
        t("nav.teachers"): "Teachers",
        t("nav.hostel"):   "Hostel",
        t("nav.library"):  "Library",
    },
    t("group.reports_comm"): {
        t("nav.reports"):     "Reports",
        t("nav.govt_report"): "Govt Report",
        f"{t('nav.notice_board')} {'🔴' if unread else ''}": "Notice Board",
        t("nav.whatsapp"):     "WhatsApp",
        t("nav.print_center"): "Print Center",
    },
    t("group.enterprise"): {
        t("nav.live_dashboard"):  "Live Dashboard",
        t("nav.cache"):           "Cache",
        t("nav.backup"):          "Backup",
        t("nav.security_2fa"):    "Security 2FA",
        t("nav.payment_gateway"): "Payment Gateway",
        t("nav.audit_trail"):     "Audit Trail",
        t("nav.user_management"):"User Management",
        t("nav.settings"):        "Settings",
    },
}

with st.sidebar:
    st.markdown(
        f"""<div style="padding:.9rem 0 .3rem;text-align:center">
            <div style="font-size:1.9rem">🕌</div>
            <div class="brand-calligraphy" style="font-weight:700;font-size:.88rem;color:#EAF4F8;margin-top:2px">{madrasa}</div>
            <div style="font-size:.62rem;color:rgba(234,244,248,.4)">{VERSION_LABEL}</div>
        </div>
        <div class="sb-profile-card">
            <div class="sb-profile-avatar">{(user_name or "?")[0].upper()}</div>
            <div>
                <div class="sb-profile-name">{user_name}</div>
                <div class="sb-profile-role">{role.title()}</div>
            </div>
        </div>
        <hr class="sb-divider">""",
        unsafe_allow_html=True,
    )

    _lang_opts  = {"bn": "বাংলা", "en": "English"}
    _cur_lang   = st.session_state.get("language", "bn")
    _lang_codes = list(_lang_opts.keys())
    _chosen = st.segmented_control(
        t("app.language_label"),
        options=_lang_codes,
        format_func=lambda code: _lang_opts[code],
        default=_cur_lang,
        key="lang_switcher",
    )
    if _chosen and _chosen != _cur_lang:
        st.session_state["language"] = _chosen
        update_language(st.session_state.get("tenant_id"), st.session_state.get("user_id"), _chosen)
        st.rerun()

    for grp, items in NAV_GROUPS.items():
        accessible = {k: v for k, v in items.items() if can_access(role, v)}
        if not accessible:
            continue
        st.markdown(f'<div class="sb-nav-group">{grp}</div>', unsafe_allow_html=True)
        for label, page_key in accessible.items():
            if st.session_state.nav_page == page_key:
                st.markdown(f'<div class="sb-nav-active">{label}</div>', unsafe_allow_html=True)
            else:
                if st.button(label, key=f"nav_{page_key}", use_container_width=True):
                    st.session_state.nav_page = page_key
                    st.rerun()

    st.markdown("<hr class='sb-divider'>", unsafe_allow_html=True)
    if st.button(t("nav.logout"), use_container_width=True, key="logout_btn"):
        audit_module.log("LOGOUT","Auth",f"লগআউট: {st.session_state.get('username','')}")
        logout()
    st.markdown(
        f'<div class="sb-footer">Tenant #{tid} · {VERSION_LABEL}</div>',
        unsafe_allow_html=True,
    )

# ── Route ──
PAGE = st.session_state.nav_page
if not can_access(role, PAGE):
    st.error(t("app.no_access", role=role))
    st.stop()

ROUTES = {
    # Core
    "Dashboard":          role_dashboard.render,
    "Admissions":         admission_module.render,
    "Finance":            finance_module.render,
    "Academics":          academic_module.render,
    "Attendance":         attendance_module.render,
    "Digital Attendance": digital_attendance.render,
    "Reports":            reports_module.render,
    "Student Portal":     student_portal.render,
    "Settings":           settings_module.render,
    "Notice Board":       notice_module.render,
    "Teachers":           teacher_module.render,
    "Print Center":       print_module.render,
    # P1 Enterprise
    "Audit Trail":        audit_module.render,
    "Documents":          document_module.render,
    "Expense":            enterprise_modules.render_expense,
    "Hostel":             enterprise_modules.render_hostel,
    "Library":            enterprise_modules.render_library,
    "WhatsApp":           whatsapp_module.render,
    "Online Admission":   online_admission.render_admin,
    "User Management":    render_user_management,
    # P2
    "Timetable":          timetable_module.render,
    "AI Analytics":       ai_analytics.render,
    "Zakat":              zakat_module.render,
    "Donors":             donor_module.render,
    "Financial Overview": financial_dashboard.render,
    # P3
    "Online Exam":        online_exam.render,
    "Govt Report":        govt_report.render,
    "Live Dashboard":    realtime_dashboard.render,
    "Cache":             cache_module.render,
    "Backup":             backup_module.render,
    "Security 2FA":      security_2fa.render,
    "Payment Gateway":   payment_gateway.render,
}

fn = ROUTES.get(PAGE)
if fn:
    try:
        fn()
    except Exception as ex:
        monitoring.capture_exception(ex, {
            "page": PAGE,
            "tenant_id": st.session_state.get("tenant_id"),
            "username": st.session_state.get("username"),
        })
        st.error(t("app.render_error"))
else:
    st.error(t("app.unknown_page", page=PAGE))
