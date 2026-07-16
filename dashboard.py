"""
dashboard.py — Main ERP Dashboard with system-wide KPIs,
recent activity feed, and quick action shortcuts.
"""

import streamlit as st
from datetime import date
from db import fetchone, fetchall
from utils import page_header, kpi_row, alert, divider, get_tenant_id, current_year, PALETTE
from i18n import t

_METHOD_LABEL_MAP_KEYS = {
    "cash": "fin.method_cash", "bkash": "fin.method_bkash",
    "nagad": "fin.method_nagad", "bank transfer": "fin.method_bank",
}


def _system_kpis(tid):
    yr = current_year()
    total_students = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='active'", (tid,))
    pending        = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='pending'", (tid,))
    total_classes  = fetchone("SELECT COUNT(*) AS n FROM classes WHERE tenant_id=%s", (tid,))
    monthly_due    = fetchone(
        "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='unpaid' AND year=%s",
        (tid, yr),
    )
    collected_yr   = fetchone(
        "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='paid' AND year=%s",
        (tid, yr),
    )
    total_exams    = fetchone("SELECT COUNT(*) AS n FROM exams WHERE tenant_id=%s", (tid,))
    return {
        "active_students": int(total_students["n"]) if total_students else 0,
        "pending":         int(pending["n"]) if pending else 0,
        "classes":         int(total_classes["n"]) if total_classes else 0,
        "outstanding":     float(monthly_due["n"]) if monthly_due else 0.0,
        "collected":       float(collected_yr["n"]) if collected_yr else 0.0,
        "exams":           int(total_exams["n"]) if total_exams else 0,
    }


def _recent_admissions(tid):
    return fetchall(
        """SELECT s.name, s.father_name, s.created_at, s.status,
                  c.class_name, sess.session_name
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s
           ORDER BY s.created_at DESC LIMIT 5""",
        (tid,),
    )


def _recent_payments(tid):
    return fetchall(
        """SELECT p.amount_paid, p.payment_date, p.payment_method,
                  s.name AS student_name, v.month_name, v.year
           FROM fee_payments p
           JOIN fee_vouchers v ON v.id=p.voucher_id
           JOIN students s ON s.id=v.student_id
           WHERE p.tenant_id=%s
           ORDER BY p.created_at DESC LIMIT 5""",
        (tid,),
    )


def _class_enrollment_breakdown(tid):
    return fetchall(
        """SELECT c.class_name, COUNT(e.id) AS count
           FROM classes c
           LEFT JOIN student_enrollments e ON e.class_id=c.id AND e.tenant_id=c.tenant_id
             AND e.enrollment_status='active'
           WHERE c.tenant_id=%s
           GROUP BY c.id, c.class_name, c.class_numeric
           ORDER BY c.class_numeric""",
        (tid,),
    )


def _monthly_collection_trend(tid):
    yr = current_year()
    rows = fetchall(
        """SELECT month_name, SUM(amount) AS total
           FROM fee_vouchers
           WHERE tenant_id=%s AND year=%s AND status='paid'
           GROUP BY month_name ORDER BY MIN(id)""",
        (tid, yr),
    )
    return {r["month_name"]: float(r["total"]) for r in rows}


def render():
    tid = get_tenant_id()
    tenant = fetchone("SELECT madrasa_name FROM tenants WHERE id=%s", (tid,))
    madrasa = tenant["madrasa_name"] if tenant else "Smart Madrasa"

    page_header(
        "🕌",
        t("dash.title", madrasa=madrasa),
        t("dash.subtitle", date=date.today().strftime('%A, %d %B %Y')),
    )

    kpis = _system_kpis(tid)

    # Row 1: Core KPIs
    kpi_row([
        {"label": t("dash.kpi_active_students"),   "value": kpis["active_students"], "cls": "success"},
        {"label": t("dash.kpi_pending_approvals"), "value": kpis["pending"],         "cls": "warning"},
        {"label": t("dash.kpi_total_classes"),     "value": kpis["classes"],         "cls": ""},
        {"label": t("dash.kpi_exams_created"),     "value": kpis["exams"],           "cls": ""},
    ])

    # Row 2: Financial KPIs
    kpi_row([
        {"label": t("dash.kpi_collected", year=current_year()),
         "value": f"৳{kpis['collected']:,.0f}", "cls": "success"},
        {"label": t("dash.kpi_outstanding"),
         "value": f"৳{kpis['outstanding']:,.0f}", "cls": "danger"},
        {"label": t("dash.kpi_collection_rate"),
         "value": (
             f"{kpis['collected'] / (kpis['collected'] + kpis['outstanding']) * 100:.0f}%"
             if (kpis['collected'] + kpis['outstanding']) > 0 else t("dash.value_na")
         ),
         "cls": "accent"},
    ])

    # ── Charts Row ──
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown(f"**{t('dash.chart_monthly_collection')}**")
        trend = _monthly_collection_trend(tid)
        if trend:
            import pandas as pd
            from utils import months_list
            months = months_list()
            df = pd.DataFrame({
                "Month": [m[:3] for m in months],
                "৳ Collected": [trend.get(m, 0) for m in months],
            })
            st.bar_chart(df.set_index("Month"), color=PALETTE["primary"], height=220)
        else:
            st.caption(t("dash.info_no_payment_data"))

    with col_right:
        st.markdown(f"**{t('dash.chart_class_enrollment')}**")
        breakdown = _class_enrollment_breakdown(tid)
        if breakdown:
            import pandas as pd
            df2 = pd.DataFrame([
                {"Class": r["class_name"], "Students": int(r["count"])}
                for r in breakdown
            ])
            st.bar_chart(df2.set_index("Class"), color=PALETTE["accent"], height=220)
        else:
            st.caption(t("dash.info_no_enrollment_data"))

    divider()

    # ── Activity Feed ──
    col_adm, col_pay = st.columns(2)

    with col_adm:
        st.markdown(f"**{t('dash.recent_admissions_heading')}**")
        admissions = _recent_admissions(tid)
        if admissions:
            for a in admissions:
                status_icon = "🟢" if a["status"] == "active" else "🟡"
                st.markdown(
                    f"{status_icon} **{a['name']}** — {a['class_name']}  \n"
                    f"<span style='color:#6B7A8D;font-size:0.78rem'>"
                    f"{t('dash.father_label', name=a['father_name'] or '—')} · {str(a['created_at'])[:10]}</span>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption(t("dash.info_no_admissions"))

    with col_pay:
        st.markdown(f"**{t('dash.recent_payments_heading')}**")
        payments = _recent_payments(tid)
        if payments:
            for p in payments:
                method_key = (p["payment_method"] or "cash").lower()
                method_label = t(_METHOD_LABEL_MAP_KEYS.get(method_key, "fin.method_cash"))
                st.markdown(
                    f"✅ **{p['student_name']}** — ৳{float(p['amount_paid']):,.0f}  \n"
                    f"<span style='color:#6B7A8D;font-size:0.78rem'>"
                    f"{p['month_name']} {p['year']} · {str(p['payment_date'])} · {method_label}</span>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption(t("dash.info_no_payments"))

    divider()

    # ── Quick Actions ──
    st.markdown(f"**{t('dash.quick_actions_heading')}**")
    c1, c2, c3, c4 = st.columns(4)
    if c1.button(t("dash.btn_new_admission"), use_container_width=True):
        st.session_state["nav_page"] = "Admissions"
        st.rerun()
    if c2.button(t("dash.btn_collect_fee"), use_container_width=True):
        st.session_state["nav_page"] = "Finance"
        st.rerun()
    if c3.button(t("dash.btn_enter_marks"), use_container_width=True):
        st.session_state["nav_page"] = "Academics"
        st.rerun()
    if c4.button(t("dash.btn_student_portal"), use_container_width=True):
        st.session_state["nav_page"] = "Student Portal"
        st.rerun()
