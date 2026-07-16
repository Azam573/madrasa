"""
realtime_dashboard.py — Real-time Live Dashboard
Auto-refresh KPIs, live attendance feed, payment ticker।
Streamlit auto-rerun + Redis pub/sub simulation।
"""

import streamlit as st
import time
from datetime import date, datetime
from db import fetchone, fetchall
from utils import get_tenant_id, PALETTE, flatten_html
from i18n import t


def _live_kpis(tid):
    yr = date.today().year
    return {
        "students":    fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='active'", (tid,)),
        "present":     fetchone("SELECT COUNT(*) AS n FROM attendance WHERE tenant_id=%s AND date=CURRENT_DATE AND status='present'", (tid,)),
        "absent":      fetchone("SELECT COUNT(*) AS n FROM attendance WHERE tenant_id=%s AND date=CURRENT_DATE AND status='absent'", (tid,)),
        "collected":   fetchone("SELECT COALESCE(SUM(amount_paid),0) AS n FROM fee_payments WHERE tenant_id=%s AND payment_date=CURRENT_DATE", (tid,)),
        "pending_adm": fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='pending'", (tid,)),
        "unread":      fetchone("SELECT COUNT(*) AS n FROM notifications WHERE tenant_id=%s AND is_read=FALSE", (tid,)),
    }


def _recent_payments(tid, limit=8):
    return fetchall(
        """SELECT s.name, p.amount_paid, p.payment_method, p.payment_date
           FROM fee_payments p
           JOIN fee_vouchers v ON v.id=p.voucher_id
           JOIN students s ON s.id=v.student_id
           WHERE p.tenant_id=%s
           ORDER BY p.created_at DESC LIMIT %s""",
        (tid, limit),
    )


def _recent_attendance(tid, limit=8):
    return fetchall(
        """SELECT s.name, a.status, a.date, c.class_name
           FROM attendance a
           JOIN student_enrollments e ON e.id=a.enrollment_id
           JOIN students s ON s.id=e.student_id
           JOIN classes c ON c.id=e.class_id
           WHERE a.tenant_id=%s AND a.date=CURRENT_DATE
           ORDER BY a.enrollment_id DESC LIMIT %s""",
        (tid, limit),
    )


def _hourly_collection(tid):
    return fetchall(
        """SELECT EXTRACT(HOUR FROM p.created_at) AS hour,
                  COUNT(*) AS count,
                  SUM(p.amount_paid) AS total
           FROM fee_payments p
           WHERE p.tenant_id=%s AND p.payment_date=CURRENT_DATE
           GROUP BY EXTRACT(HOUR FROM p.created_at)
           ORDER BY hour""",
        (tid,),
    )


def render():
    tid = get_tenant_id()

    # Header with live clock
    now = datetime.now()
    st.markdown(
        flatten_html(f"""<div style="background:linear-gradient(135deg,#0F4C5C,#1a7a96);
                        border-radius:14px;padding:1.25rem 1.5rem;
                        color:white;margin-bottom:1.5rem;
                        display:flex;justify-content:space-between;align-items:center">
          <div>
            <div style="font-size:1.2rem;font-weight:700">📡 {t('rt.title')}</div>
            <div style="font-size:0.75rem;opacity:0.75;margin-top:2px">
              {t('rt.today_realtime', date=now.strftime('%d %B %Y'))}
            </div>
          </div>
          <div style="text-align:right">
            <div style="font-size:1.8rem;font-weight:700;font-family:monospace">
              {now.strftime('%H:%M:%S')}
            </div>
            <div style="font-size:0.7rem;opacity:0.7">
              {t('rt.last_updated')}
            </div>
          </div>
        </div>"""),
        unsafe_allow_html=True,
    )

    # Auto-refresh controls
    c1, c2, c3 = st.columns([2, 1, 1])
    auto_refresh = c1.checkbox(t("rt.auto_refresh"), value=True)
    refresh_interval = c2.selectbox(t("rt.interval"), [15, 30, 60, 120],
                                      format_func=lambda x: t("rt.seconds_fmt", n=x),
                                      index=1, key="rt_interval")
    if c3.button(t("rt.refresh_now"), use_container_width=True):
        st.rerun()

    # Live KPIs
    kpis = _live_kpis(tid)

    n_students  = int(kpis["students"]["n"])  if kpis["students"]  else 0
    n_present   = int(kpis["present"]["n"])   if kpis["present"]   else 0
    n_absent    = int(kpis["absent"]["n"])    if kpis["absent"]    else 0
    n_collected = float(kpis["collected"]["n"]) if kpis["collected"] else 0.0
    n_pending   = int(kpis["pending_adm"]["n"]) if kpis["pending_adm"] else 0
    n_unread    = int(kpis["unread"]["n"])    if kpis["unread"]    else 0
    att_pct     = round(n_present / n_students * 100, 1) if n_students else 0
    n_unmarked  = n_students - n_present - n_absent

    # KPI Grid
    st.markdown(
        flatten_html(f"""<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:1rem">

          <div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                      padding:14px;border-top:3px solid #0F4C5C;text-align:center">
            <div style="font-size:2rem;font-weight:700;color:#0F4C5C">{n_students}</div>
            <div style="font-size:0.72rem;color:#6B7A8D;margin-top:3px">{t('rt.kpi_total_students')}</div>
          </div>

          <div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                      padding:14px;border-top:3px solid #2E7D32;text-align:center">
            <div style="font-size:2rem;font-weight:700;color:#2E7D32">{n_present}</div>
            <div style="font-size:0.72rem;color:#6B7A8D;margin-top:3px">{t('rt.kpi_present_today')}</div>
          </div>

          <div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                      padding:14px;border-top:3px solid #C62828;text-align:center">
            <div style="font-size:2rem;font-weight:700;color:#C62828">{n_absent}</div>
            <div style="font-size:0.72rem;color:#6B7A8D;margin-top:3px">{t('rt.kpi_absent_today')}</div>
          </div>

          <div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                      padding:14px;border-top:3px solid #E8A838;text-align:center">
            <div style="font-size:2rem;font-weight:700;color:#9a6800">৳{n_collected:,.0f}</div>
            <div style="font-size:0.72rem;color:#6B7A8D;margin-top:3px">{t('rt.kpi_collected_today')}</div>
          </div>

          <div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                      padding:14px;border-top:3px solid #F57F17;text-align:center">
            <div style="font-size:2rem;font-weight:700;color:#F57F17">{n_pending}</div>
            <div style="font-size:0.72rem;color:#6B7A8D;margin-top:3px">{t('rt.kpi_pending_approval')}</div>
          </div>

          <div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                      padding:14px;border-top:3px solid #1565C0;text-align:center">
            <div style="font-size:2rem;font-weight:700;color:#1565C0">{n_unread}</div>
            <div style="font-size:0.72rem;color:#6B7A8D;margin-top:3px">{t('rt.kpi_unread')}</div>
          </div>

        </div>"""),
        unsafe_allow_html=True,
    )

    # Attendance progress bar
    att_color = "#2E7D32" if att_pct >= 75 else ("#F57F17" if att_pct >= 50 else "#C62828")
    st.markdown(
        flatten_html(f"""<div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                        padding:14px;margin-bottom:1rem">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px">
            <span style="font-size:13px;font-weight:600">{t('rt.attendance_rate_today')}</span>
            <span style="font-size:15px;font-weight:700;color:{att_color}">{att_pct}%</span>
          </div>
          <div style="background:#EEE;border-radius:20px;height:12px;overflow:hidden">
            <div style="background:{att_color};height:100%;width:{att_pct}%;
                        border-radius:20px;transition:width 0.5s"></div>
          </div>
          <div style="display:flex;gap:16px;margin-top:8px;font-size:11px;color:#6B7A8D">
            <span>✅ {t('rt.present_count', n=n_present)}</span>
            <span>❌ {t('rt.absent_count', n=n_absent)}</span>
            <span>⬜ {t('rt.unmarked_count', n=n_unmarked)}</span>
          </div>
        </div>"""),
        unsafe_allow_html=True,
    )

    # Live feeds
    col_pay, col_att = st.columns(2)

    with col_pay:
        st.markdown(f"**{t('rt.payment_feed_title')}**")
        payments = _recent_payments(tid, 8)
        if not payments:
            st.markdown(
                '<div style="background:#F7F9FA;border-radius:8px;padding:1rem;'
                f'text-align:center;color:#9E9E9E;font-size:13px">{t("rt.no_payments_today")}</div>',
                unsafe_allow_html=True,
            )
        else:
            for p in payments:
                method_icon = {"cash":"💵","bkash":"💚","nagad":"🟠","bank":"🏦"}.get(
                    (p["payment_method"] or "").lower(), "💳"
                )
                st.markdown(
                    flatten_html(f"""<div style="display:flex;justify-content:space-between;
                                    align-items:center;padding:7px 10px;
                                    border-bottom:1px solid #F0F0F0;font-size:12px">
                      <div>
                        <span style="font-weight:600">{p['name']}</span>
                      </div>
                      <div style="text-align:right">
                        <span style="font-weight:700;color:#2E7D32">
                          {method_icon} ৳{float(p['amount_paid']):,.0f}
                        </span>
                      </div>
                    </div>"""),
                    unsafe_allow_html=True,
                )

    with col_att:
        st.markdown(f"**{t('rt.attendance_feed_title')}**")
        att_feed = _recent_attendance(tid, 8)
        if not att_feed:
            st.markdown(
                '<div style="background:#F7F9FA;border-radius:8px;padding:1rem;'
                f'text-align:center;color:#9E9E9E;font-size:13px">{t("rt.no_attendance_today")}</div>',
                unsafe_allow_html=True,
            )
        else:
            for a in att_feed:
                s_icon  = {"present":"✅","absent":"❌","late":"⏰","holiday":"🌿"}.get(a["status"],"⬜")
                s_color = {"present":"#2E7D32","absent":"#C62828",
                           "late":"#F57F17","holiday":"#1565C0"}.get(a["status"],"#555")
                st.markdown(
                    flatten_html(f"""<div style="display:flex;justify-content:space-between;
                                    align-items:center;padding:7px 10px;
                                    border-bottom:1px solid #F0F0F0;font-size:12px">
                      <div>
                        <span style="font-weight:600">{a['name']}</span>
                        <span style="color:#9E9E9E;font-size:10px"> — {a['class_name']}</span>
                      </div>
                      <span style="color:{s_color};font-weight:600">
                        {s_icon} {a['status'].title()}
                      </span>
                    </div>"""),
                    unsafe_allow_html=True,
                )

    # Hourly collection chart
    hourly = _hourly_collection(tid)
    if hourly:
        st.markdown(f"**{t('rt.hourly_collection_title')}**")
        import pandas as pd
        hours = list(range(8, 17))
        hour_map = {int(r["hour"]): float(r["total"] or 0) for r in hourly}
        time_col = t("rt.chart_time_col")
        df = pd.DataFrame({
            time_col:      [f"{h}:00" for h in hours],
            t("rt.chart_collected_col"): [hour_map.get(h, 0) for h in hours],
        })
        st.bar_chart(df.set_index(time_col), color=PALETTE["success"], height=180)

    # Auto-refresh logic
    if auto_refresh:
        st.markdown(
            f'<div style="text-align:center;font-size:11px;color:#9E9E9E;margin-top:1rem">'
            f'⏱️ {t("rt.auto_refresh_notice", n=refresh_interval)}</div>',
            unsafe_allow_html=True,
        )
        time.sleep(refresh_interval)
        st.rerun()
