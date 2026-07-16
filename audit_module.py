"""
audit_module.py — Enterprise Audit Trail System
কে, কখন, কী করেছে — সব কিছুর সম্পূর্ণ লগ।
"""

import streamlit as st
from datetime import date, datetime
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id
from i18n import t
from sanitize import csv_safe


# ─────────────────────────────────────────────
# Public API — log() ফাংশন সব মডিউল থেকে কল করে
# ─────────────────────────────────────────────

def log(action: str, module: str, description: str,
        record_type: str = None, record_id: int = None,
        old_value: dict = None, new_value: dict = None,
        tenant_id_override: int = None, actor_username: str = None):
    """
    যেকোনো মডিউল থেকে call করুন:
    audit_module.log("CREATE", "Finance", "ভাউচার তৈরি হয়েছে", "fee_voucher", voucher_id)

    tenant_id_override/actor_username: platform_admin.py-এর মতো platform-admin
    action-এর জন্য (suspend/reactivate/impersonate) — এগুলো ঐ tenant-এর session-এর
    ভেতর থেকে চলে না, তাই tenant_id/username সাধারণ st.session_state থেকে
    পাওয়া যায় না। বাকি সব call site (কোনো override দেয় না) আগের মতোই কাজ করে।
    """
    tid      = tenant_id_override if tenant_id_override is not None else st.session_state.get("tenant_id", 1)
    user_id  = st.session_state.get("user_id")
    username = actor_username if actor_username is not None else st.session_state.get("username", "system")

    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit_logs
                   (tenant_id, user_id, username, action, module,
                    record_type, record_id, old_value, new_value, description)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    tid, user_id, username, action, module,
                    record_type, record_id,
                    __import__("json").dumps(old_value) if old_value else None,
                    __import__("json").dumps(new_value) if new_value else None,
                    description,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


# ─────────────────────────────────────────────
# Render
# ─────────────────────────────────────────────

ACTION_COLOR = {
    "CREATE": "#2E7D32", "UPDATE": "#1565C0",
    "DELETE": "#C62828", "LOGIN":  "#6A1B9A",
    "LOGOUT": "#555",    "PRINT":  "#E65100",
    "EXPORT": "#00695C", "APPROVE":"#2E7D32",
    "REJECT": "#C62828",
}
ACTION_ICON = {
    "CREATE": "➕", "UPDATE": "✏️", "DELETE": "🗑️",
    "LOGIN":  "🔐", "LOGOUT": "🚪", "PRINT":  "🖨️",
    "EXPORT": "📥", "APPROVE":"✅", "REJECT": "❌",
}


def render():
    tid = get_tenant_id()
    role = st.session_state.get("user_role", "staff")

    page_header("🔍", t("audit.page_title"), t("audit.page_subtitle"))

    if role != "admin":
        alert(t("audit.admin_only"), "danger")
        return

    # KPIs
    total  = fetchone("SELECT COUNT(*) AS n FROM audit_logs WHERE tenant_id=%s", (tid,))
    today_n= fetchone(
        "SELECT COUNT(*) AS n FROM audit_logs WHERE tenant_id=%s AND DATE(created_at)=CURRENT_DATE",
        (tid,),
    )
    users_n= fetchone(
        "SELECT COUNT(DISTINCT username) AS n FROM audit_logs WHERE tenant_id=%s", (tid,)
    )
    kpi_row([
        {"label": t("audit.kpi_total_logs"), "value": int(total["n"]) if total else 0, "cls": ""},
        {"label": t("audit.kpi_today_activity"), "value": int(today_n["n"]) if today_n else 0, "cls": "accent"},
        {"label": t("audit.kpi_active_users"), "value": int(users_n["n"]) if users_n else 0, "cls": ""},
    ])

    tab_live, tab_search, tab_user, tab_security = st.tabs([
        t("audit.tab_live"), t("audit.tab_search"), t("audit.tab_user"), t("audit.tab_security")
    ])

    # ── লাইভ লগ ──
    with tab_live:
        st.markdown(f"#### {t('audit.recent_activity_heading')}")
        if st.button(t("audit.refresh_btn"), key="audit_refresh"):
            st.rerun()

        logs = fetchall(
            """SELECT username, action, module, description,
                      record_type, record_id, created_at
               FROM audit_logs WHERE tenant_id=%s
               ORDER BY created_at DESC LIMIT 100""",
            (tid,),
        )
        if not logs:
            alert(t("audit.no_logs"), "info")
        else:
            for l in logs:
                color = ACTION_COLOR.get(l["action"], "#555")
                icon  = ACTION_ICON.get(l["action"], "📌")
                ts    = str(l["created_at"])[:16]
                st.markdown(
                    f"""<div style="display:flex;align-items:center;gap:10px;
                                    padding:7px 12px;border-bottom:1px solid #F0F0F0;
                                    font-size:12px">
                      <span style="background:{color}20;color:{color};
                                   border-radius:4px;padding:2px 7px;
                                   font-weight:700;min-width:70px;text-align:center;
                                   font-size:10px">{icon} {l['action']}</span>
                      <span style="color:#6B7A8D;min-width:80px">{l['module'] or '—'}</span>
                      <span style="flex:1">{l['description'] or '—'}</span>
                      <span style="color:#6B7A8D;font-size:10px">{l['username']}</span>
                      <span style="color:#9E9E9E;font-size:10px;min-width:115px;
                                   text-align:right">{ts}</span>
                    </div>""",
                    unsafe_allow_html=True,
                )

    # ── অনুসন্ধান ──
    with tab_search:
        st.markdown(f"#### {t('audit.search_heading')}")
        ALL_LABEL = t("audit.all")
        c1, c2, c3, c4 = st.columns(4)
        f_module = c1.selectbox(t("audit.module_label"), [ALL_LABEL,"Admission","Finance","Academic",
                                           "Attendance","Teacher","Settings","Auth"],
                                key="af_mod")
        f_action = c2.selectbox(t("audit.action_label"),
                                [ALL_LABEL,"CREATE","UPDATE","DELETE","LOGIN","LOGOUT",
                                 "APPROVE","REJECT","PRINT","EXPORT"],
                                key="af_act")
        f_from = c3.date_input(t("audit.from_label"), value=date.today(), key="af_from")
        f_to   = c4.date_input(t("audit.to_label"),  value=date.today(), key="af_to")
        f_user = st.text_input(t("audit.username_filter_label"), key="af_user")

        params = [tid, str(f_from), str(f_to)]
        clauses = ["tenant_id=%s", "DATE(created_at) BETWEEN %s AND %s"]

        # SQL Injection fix (v9.0):
        # f_module ও f_action UI selectbox থেকে আসে (whitelist) — safe।
        # f_user text input — ILIKE %s parameterized, value ইনজেক্ট হওয়ার সুযোগ নেই।
        # f-string শুধু clause গঠনে — value কখনো f-string-এ নেই।
        ALLOWED_MODULES = {
            ALL_LABEL, "Finance", "Admissions", "Academics", "Attendance",
            "Teachers", "Reports", "System", "Settings", "Notice",
        }
        ALLOWED_ACTIONS = {
            ALL_LABEL, "CREATE", "UPDATE", "DELETE", "LOGIN", "LOGOUT",
            "APPROVE", "REJECT", "PRINT", "EXPORT",
        }

        if f_module != ALL_LABEL and f_module in ALLOWED_MODULES:
            clauses.append("module=%s")
            params.append(f_module)
        if f_action != ALL_LABEL and f_action in ALLOWED_ACTIONS:
            clauses.append("action=%s")
            params.append(f_action)
        if f_user.strip():
            clauses.append("username ILIKE %s")
            params.append(f"%{f_user.strip()}%")

        results = fetchall(
            "SELECT * FROM audit_logs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT 200",
            tuple(params),
        )
        st.caption(t("audit.results_count", n=len(results)))
        if results:
            import pandas as pd
            df = pd.DataFrame([{
                t("audit.col_time"):        str(r["created_at"])[:16],
                t("audit.col_username"): r["username"],
                t("audit.col_action"):   r["action"],
                t("audit.col_module"):      r["module"] or "—",
                t("audit.col_description"):      r["description"] or "—",
                t("audit.col_record"):     f"{r['record_type']} #{r['record_id']}" if r["record_id"] else "—",
            } for r in results])
            st.dataframe(df, use_container_width=True, hide_index=True, height=400)

            # CSV export — formula-injection-safe কপি (on-screen table অপরিবর্তিত রাখতে)
            import io
            csv_df = df.copy()
            csv_df[t("audit.col_username")]    = csv_df[t("audit.col_username")].map(csv_safe)
            csv_df[t("audit.col_description")] = csv_df[t("audit.col_description")].map(csv_safe)
            buf = io.StringIO()
            csv_df.to_csv(buf, index=False)
            st.download_button(t("audit.csv_download_btn"), buf.getvalue().encode("utf-8-sig"),
                               f"audit_{date.today()}.csv", "text/csv")

    # ── ব্যবহারকারী ইতিহাস ──
    with tab_user:
        st.markdown(f"#### {t('audit.user_activity_heading')}")
        users = fetchall(
            "SELECT DISTINCT username FROM audit_logs WHERE tenant_id=%s ORDER BY username",
            (tid,),
        )
        if not users:
            alert(t("audit.no_logs"), "info")
        else:
            sel_u = st.selectbox(t("audit.username_label"), [u["username"] for u in users])
            u_logs = fetchall(
                """SELECT action, module, description, created_at
                   FROM audit_logs WHERE tenant_id=%s AND username=%s
                   ORDER BY created_at DESC LIMIT 50""",
                (tid, sel_u),
            )
            # Action summary
            action_counts = {}
            for l in u_logs:
                action_counts[l["action"]] = action_counts.get(l["action"], 0) + 1

            summary_html = "".join(
                f'<span style="background:{ACTION_COLOR.get(a,"#555")}20;'
                f'color:{ACTION_COLOR.get(a,"#555")};border-radius:4px;'
                f'padding:3px 10px;font-size:11px;font-weight:700;margin-right:6px">'
                f'{ACTION_ICON.get(a,"📌")} {a}: {n}</span>'
                for a, n in action_counts.items()
            )
            st.markdown(
                f'<div style="background:#F7F9FA;border-radius:8px;padding:10px 14px;'
                f'margin-bottom:1rem">{summary_html}</div>',
                unsafe_allow_html=True,
            )

            import pandas as pd
            df2 = pd.DataFrame([{
                t("audit.col_time"):      str(l["created_at"])[:16],
                t("audit.col_action"): l["action"],
                t("audit.col_module"):    l["module"] or "—",
                t("audit.col_description"):    l["description"] or "—",
            } for l in u_logs])
            st.dataframe(df2, use_container_width=True, hide_index=True)

    # ── নিরাপত্তা লগ ──
    with tab_security:
        st.markdown(f"#### {t('audit.security_heading')}")
        sec_logs = fetchall(
            """SELECT username, action, description, created_at
               FROM audit_logs
               WHERE tenant_id=%s AND action IN ('LOGIN','LOGOUT','FAILED_LOGIN')
               ORDER BY created_at DESC LIMIT 50""",
            (tid,),
        )
        if not sec_logs:
            alert(t("audit.no_login_records"), "info")
        else:
            for l in sec_logs:
                color = "#2E7D32" if l["action"] == "LOGIN" else \
                        "#C62828" if l["action"] == "FAILED_LOGIN" else "#555"
                icon  = "🔐" if l["action"] == "LOGIN" else \
                        "🚫" if l["action"] == "FAILED_LOGIN" else "🚪"
                st.markdown(
                    f'<div style="padding:8px 12px;border-left:3px solid {color};'
                    f'margin-bottom:4px;font-size:12px;background:#FAFAFA;border-radius:0 6px 6px 0">'
                    f'{icon} <strong style="color:{color}">{l["action"]}</strong> — '
                    f'{l["username"]} | {l["description"] or ""} | '
                    f'<span style="color:#9E9E9E">{str(l["created_at"])[:16]}</span></div>',
                    unsafe_allow_html=True,
                )
