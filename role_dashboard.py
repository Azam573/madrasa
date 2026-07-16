"""
role_dashboard.py — Role-Based Custom Dashboard
প্রতিটি role-এর জন্য আলাদা, কাস্টমাইজড হোম স্ক্রিন।
Admin / Staff / Teacher / Accountant — সবার আলাদা ভিউ।
"""

import streamlit as st
from datetime import date, timedelta
from db import fetchone, fetchall
from utils import kpi_row, hero_kpi_row, alert, divider, get_tenant_id, PALETTE
from i18n import t, weekday_name, month_name as i18n_month_name


# ─────────────────────────────────────────────
# Shared widgets
# ─────────────────────────────────────────────

def _greeting():
    hour = date.today().timetuple().tm_hour
    name = st.session_state.get("user_name", "")
    if hour < 12:
        return t("greeting.morning", name=name)
    elif hour < 17:
        return t("greeting.afternoon", name=name)
    else:
        return t("greeting.evening", name=name)


def _today_banner():
    today = date.today()
    day_name   = weekday_name(today.weekday())
    month_name = i18n_month_name(today.month - 1)
    return f"{today.day} {month_name} {today.year}, {day_name}"


def _quick_nav_card(icon, title, subtitle, page_key, color="#0F4C5C"):
    if st.button(
        f"{icon}  {title}\n{subtitle}",
        key=f"qnav_{page_key}",
        use_container_width=True,
    ):
        st.session_state.nav_page = page_key
        st.rerun()


# ─────────────────────────────────────────────
# ADMIN Dashboard
# ─────────────────────────────────────────────

def _admin_dashboard(tid):
    # System-wide KPIs
    students = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='active'", (tid,))
    pending  = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='pending'", (tid,))
    teachers = fetchone("SELECT COUNT(*) AS n FROM teachers WHERE tenant_id=%s AND status='active'", (tid,)) if _table_exists("teachers") else None
    yr = date.today().year

    collected = fetchone(
        "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='paid' AND year=%s",
        (tid, yr),
    )
    outstanding = fetchone(
        "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='unpaid' AND year=%s",
        (tid, yr),
    )
    expenses = fetchone(
        "SELECT COALESCE(SUM(amount),0) AS n FROM expenses WHERE tenant_id=%s AND year=%s",
        (tid, yr),
    ) if _table_exists("expenses") else None

    hero_kpi_row([
        {"icon": "👥", "label": t("kpi.active_students"),  "value": int(students["n"]) if students else 0},
        {"icon": "⏳", "label": t("kpi.pending_approval"), "value": int(pending["n"])  if pending  else 0},
        {"icon": "👨‍🏫", "label": t("kpi.teachers"),         "value": int(teachers["n"]) if teachers else 0},
        {"icon": "💰", "label": t("kpi.collected_year", year=yr), "value": f"৳{float(collected['n']):,.0f}" if collected else "—"},
        {"icon": "⚠️", "label": t("kpi.outstanding"),      "value": f"৳{float(outstanding['n']):,.0f}" if outstanding else "—"},
        {"icon": "💸", "label": t("kpi.expense_year", year=yr),   "value": f"৳{float(expenses['n']):,.0f}" if expenses else "—"},
    ])

    divider()

    col_left, col_right = st.columns(2)
    with col_left:
        # Monthly collection chart
        st.markdown(f"**{t('dash.monthly_collection')}**")
        from utils import months_list
        monthly = fetchall(
            "SELECT month_name, SUM(amount) AS total FROM fee_vouchers WHERE tenant_id=%s AND year=%s AND status='paid' GROUP BY month_name ORDER BY MIN(id)",
            (tid, yr),
        )
        if monthly:
            import pandas as pd
            months = months_list()
            mdict  = {r["month_name"]: float(r["total"]) for r in monthly}
            month_col = t("dash.chart_month_col")
            df = pd.DataFrame({
                month_col:    [m[:3] for m in months],
                t("dash.chart_collected_col"): [mdict.get(m, 0) for m in months],
            })
            st.bar_chart(df.set_index(month_col), color=PALETTE["primary"], height=200)
        else:
            st.caption(t("dash.no_collection_data"))

    with col_right:
        # Pending alerts
        st.markdown(f"**{t('dash.urgent_matters')}**")
        alerts = []

        if pending and int(pending["n"]) > 0:
            alerts.append(t("dash.alert_pending_students", n=int(pending["n"])))

        due_count = fetchone(
            "SELECT COUNT(DISTINCT student_id) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='unpaid'",
            (tid,),
        )
        if due_count and int(due_count["n"]) > 0:
            alerts.append(t("dash.alert_fee_due", n=int(due_count["n"])))

        absent_today = fetchone(
            "SELECT COUNT(*) AS n FROM attendance WHERE tenant_id=%s AND date=CURRENT_DATE AND status='absent'",
            (tid,),
        )
        if absent_today and int(absent_today["n"]) > 0:
            alerts.append(t("dash.alert_absent_today", n=int(absent_today["n"])))

        unread_notif = fetchone(
            "SELECT COUNT(*) AS n FROM notifications WHERE tenant_id=%s AND is_read=FALSE",
            (tid,),
        )
        if unread_notif and int(unread_notif["n"]) > 0:
            alerts.append(t("dash.alert_unread_notif", n=int(unread_notif["n"])))

        if alerts:
            for a in alerts:
                st.markdown(
                    f'<div style="background:#FFF8E1;border-left:3px solid #F57F17;'
                    f'border-radius:6px;padding:8px 12px;margin-bottom:6px;font-size:13px">'
                    f'{a}</div>',
                    unsafe_allow_html=True,
                )
        else:
            alert(t("dash.all_good"), "success")

    divider()

    # Quick Actions
    st.markdown(f"**{t('dash.quick_actions')}**")
    cols = st.columns(4)
    actions = [
        ("➕", t("qa.new_admission"),   t("qa.new_admission_sub"),  "Admissions"),
        ("💰", t("qa.fee_collection"), t("qa.fee_collection_sub"), "Finance"),
        ("📲", t("qa.attendance"),     t("qa.attendance_sub"),     "Digital Attendance"),
        ("📊", t("qa.reports"),        t("qa.reports_sub"),        "Reports"),
    ]
    for col, (icon, title, sub, page) in zip(cols, actions):
        with col:
            _quick_nav_card(icon, title, sub, page)

    divider()

    # Recent activity feed
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**{t('dash.recent_admissions')}**")
        recent_adm = fetchall(
            """SELECT s.name, c.class_name, s.created_at
               FROM students s
               JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
               JOIN classes c ON c.id=e.class_id
               WHERE s.tenant_id=%s ORDER BY s.created_at DESC LIMIT 5""",
            (tid,),
        )
        for r in recent_adm:
            st.markdown(
                f'<div style="padding:6px 0;border-bottom:1px solid #F0F0F0;font-size:12px">'
                f'🎓 <b>{r["name"]}</b> — {r["class_name"]} '
                f'<span style="color:#9E9E9E">{str(r["created_at"])[:10]}</span></div>',
                unsafe_allow_html=True,
            )

    with col_b:
        st.markdown(f"**{t('dash.recent_payments')}**")
        recent_pay = fetchall(
            """SELECT s.name, p.amount_paid, p.payment_date
               FROM fee_payments p
               JOIN fee_vouchers v ON v.id=p.voucher_id
               JOIN students s ON s.id=v.student_id
               WHERE p.tenant_id=%s ORDER BY p.created_at DESC LIMIT 5""",
            (tid,),
        )
        for r in recent_pay:
            st.markdown(
                f'<div style="padding:6px 0;border-bottom:1px solid #F0F0F0;font-size:12px">'
                f'💳 <b>{r["name"]}</b> — ৳{float(r["amount_paid"]):,.0f} '
                f'<span style="color:#9E9E9E">{str(r["payment_date"])}</span></div>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────
# TEACHER Dashboard
# ─────────────────────────────────────────────

def _teacher_dashboard(tid):
    username = st.session_state.get("username","")
    today = date.today()

    # Find teacher record
    teacher = fetchone(
        "SELECT * FROM teachers WHERE tenant_id=%s AND mobile_no=(SELECT mobile_no FROM app_users WHERE username=%s AND tenant_id=%s LIMIT 1) LIMIT 1",
        (tid, username, tid),
    ) if _table_exists("teachers") else None

    kpi_row([
        {"label": t("dash.today_date"), "value": today.strftime("%d/%m/%Y"), "cls": ""},
        {"label": t("dash.day"),        "value": today.strftime("%A"), "cls": "accent"},
    ])

    divider()
    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"**{t('dash.my_class_assignments')}**")
        if teacher and _table_exists("teacher_assignments"):
            assignments = fetchall(
                """SELECT c.class_name, subj.subject_name, sess.session_name,
                          ta.is_class_teacher
                   FROM teacher_assignments ta
                   LEFT JOIN classes c ON c.id=ta.class_id
                   LEFT JOIN subjects subj ON subj.id=ta.subject_id
                   LEFT JOIN academic_sessions sess ON sess.id=ta.session_id
                   WHERE ta.tenant_id=%s AND ta.teacher_id=%s
                   ORDER BY c.class_numeric""",
                (tid, teacher["id"]),
            )
            if assignments:
                for a in assignments:
                    ct = t("dash.class_teacher_badge") if a["is_class_teacher"] else ""
                    st.markdown(
                        f'<div style="background:#EAF4F8;border-radius:6px;'
                        f'padding:8px 12px;margin-bottom:4px;font-size:12px">'
                        f'🏛 <b>{a["class_name"] or "—"}</b> — {a["subject_name"] or "—"}{ct}</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption(t("dash.no_assignments"))
        else:
            st.caption(t("dash.teacher_not_linked"))

    with col2:
        st.markdown(f"**{t('dash.today_attendance_summary')}**")
        today_att = fetchone(
            "SELECT COUNT(*) AS n FROM attendance WHERE tenant_id=%s AND date=CURRENT_DATE AND status='present'",
            (tid,),
        )
        today_abs = fetchone(
            "SELECT COUNT(*) AS n FROM attendance WHERE tenant_id=%s AND date=CURRENT_DATE AND status='absent'",
            (tid,),
        )
        if today_att:
            kpi_row([
                {"label": t("kpi.present"), "value": int(today_att["n"]), "cls": "success"},
                {"label": t("kpi.absent"), "value": int(today_abs["n"]) if today_abs else 0, "cls": "danger"},
            ])
        else:
            st.caption(t("dash.attendance_not_taken"))

    divider()
    st.markdown(f"**{t('dash.quick_actions')}**")
    c1, c2, c3 = st.columns(3)
    with c1: _quick_nav_card("📲", t("qa.take_attendance"), t("qa.take_attendance_sub"), "Digital Attendance")
    with c2: _quick_nav_card("✏️", t("qa.enter_marks"),     t("qa.enter_marks_sub"),     "Academics")
    with c3: _quick_nav_card("📚", t("qa.library"),          t("qa.library_sub"),         "Library")


# ─────────────────────────────────────────────
# ACCOUNTANT Dashboard
# ─────────────────────────────────────────────

def _accountant_dashboard(tid):
    yr = date.today().year
    month = date.today().strftime("%B")

    collected_mo = fetchone(
        "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='paid' AND month_name=%s AND year=%s",
        (tid, month, yr),
    )
    outstanding = fetchone(
        "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='unpaid'",
        (tid,),
    )
    due_students = fetchone(
        "SELECT COUNT(DISTINCT student_id) AS n FROM fee_vouchers WHERE tenant_id=%s AND status='unpaid'",
        (tid,),
    )
    today_payments = fetchone(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount_paid),0) AS total FROM fee_payments WHERE tenant_id=%s AND payment_date=CURRENT_DATE",
        (tid,),
    )
    expense_mo = fetchone(
        "SELECT COALESCE(SUM(amount),0) AS n FROM expenses WHERE tenant_id=%s AND month_name=%s AND year=%s",
        (tid, month, yr),
    ) if _table_exists("expenses") else None

    hero_kpi_row([
        {"icon": "💰", "label": t("kpi.collection_month", month=month), "value": f"৳{float(collected_mo['n']):,.0f}" if collected_mo else "—"},
        {"icon": "📥", "label": t("kpi.collection_today"),  "value": f"৳{float(today_payments['total'] if today_payments else 0):,.0f}"},
        {"icon": "🧑‍🎓", "label": t("kpi.due_students"),      "value": int(due_students["n"]) if due_students else 0},
        {"icon": "⚠️", "label": t("kpi.total_outstanding"), "value": f"৳{float(outstanding['n']):,.0f}" if outstanding else "—"},
        {"icon": "💸", "label": t("kpi.expense_month", month=month), "value": f"৳{float(expense_mo['n']):,.0f}" if expense_mo else "—"},
    ])

    divider()
    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"**{t('dash.today_payments', n=today_payments['n'] if today_payments else 0)}**")
        today_pay_list = fetchall(
            """SELECT s.name, p.amount_paid, p.payment_method, v.month_name
               FROM fee_payments p
               JOIN fee_vouchers v ON v.id=p.voucher_id
               JOIN students s ON s.id=v.student_id
               WHERE p.tenant_id=%s AND p.payment_date=CURRENT_DATE
               ORDER BY p.created_at DESC LIMIT 10""",
            (tid,),
        )
        if today_pay_list:
            for r in today_pay_list:
                st.markdown(
                    f'<div style="padding:6px 0;border-bottom:1px solid #F0F0F0;font-size:12px">'
                    f'✅ <b>{r["name"]}</b> — ৳{float(r["amount_paid"]):,.0f} '
                    f'({r["month_name"]}) · {r["payment_method"].title()}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption(t("dash.no_payments_today"))

    with col2:
        st.markdown(f"**{t('dash.top_due_students')}**")
        top_due = fetchall(
            """SELECT s.name, SUM(v.amount) AS total_due
               FROM fee_vouchers v
               JOIN students s ON s.id=v.student_id
               WHERE v.tenant_id=%s AND v.status='unpaid'
               GROUP BY s.name ORDER BY total_due DESC LIMIT 8""",
            (tid,),
        )
        for r in top_due:
            st.markdown(
                f'<div style="padding:5px 0;border-bottom:1px solid #F0F0F0;font-size:12px;'
                f'display:flex;justify-content:space-between">'
                f'<span>{r["name"]}</span>'
                f'<span style="color:#C62828;font-weight:700">৳{float(r["total_due"]):,.0f}</span></div>',
                unsafe_allow_html=True,
            )

    divider()
    st.markdown(f"**{t('dash.quick_actions')}**")
    c1, c2, c3, c4 = st.columns(4)
    with c1: _quick_nav_card("💰", t("qa.fee_collection"), t("qa.fee_collection_sub"), "Finance")
    with c2: _quick_nav_card("🧾", t("qa.voucher"),         t("qa.voucher_sub"),        "Finance")
    with c3: _quick_nav_card("📊", t("qa.reports"),         t("qa.reports_sub2"),       "Reports")
    with c4: _quick_nav_card("💸", t("qa.expense"),         t("qa.expense_sub"),        "Expense")


# ─────────────────────────────────────────────
# STAFF Dashboard
# ─────────────────────────────────────────────

def _staff_dashboard(tid):
    students = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='active'", (tid,))
    pending  = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='pending'", (tid,))
    today_present = fetchone(
        "SELECT COUNT(*) AS n FROM attendance WHERE tenant_id=%s AND date=CURRENT_DATE AND status='present'",
        (tid,),
    )

    hero_kpi_row([
        {"icon": "👥", "label": t("kpi.active_students"),  "value": int(students["n"]) if students else 0},
        {"icon": "⏳", "label": t("kpi.pending_approval"), "value": int(pending["n"])  if pending  else 0},
        {"icon": "✅", "label": t("kpi.present_today"),    "value": int(today_present["n"]) if today_present else 0},
    ])

    divider()
    st.markdown(f"**{t('dash.quick_actions')}**")
    cols = st.columns(3)
    with cols[0]: _quick_nav_card("➕", t("qa.new_admission"), t("qa.new_admission_sub2"), "Admissions")
    with cols[1]: _quick_nav_card("📲", t("qa.attendance"),    t("qa.attendance_sub2"),    "Digital Attendance")
    with cols[2]: _quick_nav_card("📢", t("qa.notice"),        t("qa.notice_sub"),         "Notice Board")

    divider()
    st.markdown(f"**{t('dash.recent_notices')}**")
    notices = fetchall(
        "SELECT title, category, created_at FROM notices WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 5",
        (tid,),
    ) if _table_exists("notices") else []
    for n in notices:
        st.markdown(
            f'<div style="padding:6px 0;border-bottom:1px solid #F0F0F0;font-size:12px">'
            f'📢 <b>{n["title"]}</b> <span style="color:#9E9E9E">— {str(n["created_at"])[:10]}</span></div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _table_exists(table_name: str) -> bool:
    """
    SQL Injection fix (v9.0):
    f"SELECT 1 FROM {table_name}" বাদ দিয়ে
    information_schema দিয়ে check করা হচ্ছে।
    """
    from sql_safe import table_exists_safe
    return table_exists_safe(table_name)


# ─────────────────────────────────────────────
# Main render
# ─────────────────────────────────────────────

def render():
    tid  = get_tenant_id()
    role = st.session_state.get("user_role", "staff")

    # Header
    st.markdown(
        f"""<div style="background:linear-gradient(135deg,{PALETTE['primary']},{PALETTE['primary_lt']});
                        border-radius:14px;padding:1.25rem 1.5rem;
                        color:white;margin-bottom:1.5rem;
                        box-shadow:0 6px 24px rgba(15,76,92,0.22);
                        display:flex;justify-content:space-between;align-items:center">
          <div>
            <div style="font-size:1.1rem;font-weight:700">{_greeting()}</div>
            <div style="font-size:0.78rem;opacity:0.8;margin-top:3px">
              📅 {_today_banner()}
            </div>
          </div>
          <div style="text-align:right;font-size:0.78rem;opacity:0.8">
            <div>🕌 {st.session_state.get('madrasa_name','')}</div>
            <div style="margin-top:3px">👤 {role.title()}</div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # Role routing
    if role == "admin":
        _admin_dashboard(tid)
    elif role == "teacher":
        _teacher_dashboard(tid)
    elif role == "accountant":
        _accountant_dashboard(tid)
    else:  # staff
        _staff_dashboard(tid)
