"""
attendance_module.py — ডিজিটাল হাজিরা সিস্টেম
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ ফিচার সমূহ:
  ১. লাইভ রোল কল  — ওয়ান-ক্লিক Present/Absent/Late/Holiday
  ২. QR / ID কার্ড স্ক্যান সিমুলেশন  (ডিজিটাল পাঞ্চ-ইন)
  ৩. ক্যালেন্ডার হিটম্যাপ  — মাসের প্রতিটি দিনের রঙিন ভিজ্যুয়াল
  ৪. মাসিক রিপোর্ট + ক্লাসওয়াইজ চার্ট
  ৫. ইন্ডিভিজ্যুয়াল ছাত্র ট্র্যাকার  — ৩০-দিনের স্ট্রিক বার
  ৬. ছুটির দিন ম্যানেজার
  ৭. CSV এক্সপোর্ট
  ৮. দেরি-আসার (Late) অ্যালার্ট প্যানেল
"""

from error_handler import safe_db_error
import streamlit as st
from datetime import date, timedelta, datetime
import calendar as cal_lib
from db import get_connection, release_connection, fetchall, fetchone
from utils import (
    page_header, kpi_row, alert, divider,
    get_tenant_id, PALETTE, flatten_html,
)
from i18n import t
from sanitize import csv_safe

# ─────────────────────────────────────────────────────────────────
# ডেটা হেল্পার
# ─────────────────────────────────────────────────────────────────

def _get_sessions(tid):
    return fetchall(
        "SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC",
        (tid,),
    )

def _get_classes(tid):
    return fetchall(
        "SELECT id, class_name, class_numeric FROM classes WHERE tenant_id=%s ORDER BY class_numeric",
        (tid,),
    )

def _get_students(tid, session_id, class_id):
    return fetchall(
        """SELECT s.id AS student_id, s.name, e.roll_no, e.id AS enrollment_id
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           WHERE s.tenant_id=%s AND e.session_id=%s AND e.class_id=%s
             AND e.enrollment_status='active' AND s.status='active'
           ORDER BY e.roll_no NULLS LAST, s.name""",
        (tid, session_id, class_id),
    )

def _get_day_attendance(tid, date_str, class_id, session_id):
    rows = fetchall(
        """SELECT a.enrollment_id, a.status
           FROM attendance a
           JOIN student_enrollments e ON e.id=a.enrollment_id
           WHERE a.tenant_id=%s AND a.date=%s AND e.class_id=%s AND e.session_id=%s""",
        (tid, date_str, class_id, session_id),
    )
    return {r["enrollment_id"]: r["status"] for r in rows}

def _get_holidays(tid, year, month):
    rows = fetchall(
        """SELECT holiday_date, description FROM holidays
           WHERE tenant_id=%s
             AND EXTRACT(YEAR FROM holiday_date)=%s
             AND EXTRACT(MONTH FROM holiday_date)=%s
           ORDER BY holiday_date""",
        (tid, year, month),
    )
    return {r["holiday_date"]: r["description"] for r in rows}

def _all_holidays(tid):
    return fetchall(
        "SELECT holiday_date, description FROM holidays WHERE tenant_id=%s ORDER BY holiday_date DESC",
        (tid,),
    )

def _save_holiday(tid, hdate, desc):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO holidays (tenant_id, holiday_date, description)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (tenant_id, holiday_date) DO UPDATE SET description=%s""",
                (tid, hdate, desc, desc),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)

def _delete_holiday(tid, hdate):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM holidays WHERE tenant_id=%s AND holiday_date=%s",
                (tid, hdate),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)

def _save_bulk(tid, date_str, records):
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        with conn.cursor() as cur:
            for rec in records:
                cur.execute(
                    """INSERT INTO attendance (tenant_id, enrollment_id, date, status)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (enrollment_id, date)
                       DO UPDATE SET status=EXCLUDED.status""",
                    (tid, rec["enrollment_id"], date_str, rec["status"]),
                )
        conn.commit()
        return True, len(records)
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)

def _single_punch(tid, enrollment_id, date_str, status):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO attendance (tenant_id, enrollment_id, date, status)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (enrollment_id, date)
                   DO UPDATE SET status=EXCLUDED.status""",
                (tid, enrollment_id, date_str, status),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)

def _monthly_summary(tid, class_id, session_id, year, month):
    return fetchall(
        """SELECT s.name, e.roll_no, e.id AS enrollment_id,
                  COUNT(CASE WHEN a.status='present' THEN 1 END) AS present,
                  COUNT(CASE WHEN a.status='absent'  THEN 1 END) AS absent,
                  COUNT(CASE WHEN a.status='late'    THEN 1 END) AS late,
                  COUNT(CASE WHEN a.status='holiday' THEN 1 END) AS holiday,
                  COUNT(a.id) AS total_marked
           FROM student_enrollments e
           JOIN students s ON s.id=e.student_id
           LEFT JOIN attendance a ON a.enrollment_id=e.id
             AND a.tenant_id=e.tenant_id
             AND EXTRACT(YEAR FROM a.date)=%s
             AND EXTRACT(MONTH FROM a.date)=%s
           WHERE e.tenant_id=%s AND e.class_id=%s AND e.session_id=%s
             AND e.enrollment_status='active' AND s.status='active'
           GROUP BY s.name, e.roll_no, e.id
           ORDER BY e.roll_no NULLS LAST, s.name""",
        (year, month, tid, class_id, session_id),
    )

def _student_log(tid, enrollment_id, days=60):
    return fetchall(
        """SELECT date, status FROM attendance
           WHERE tenant_id=%s AND enrollment_id=%s
           ORDER BY date DESC LIMIT %s""",
        (tid, enrollment_id, days),
    )

def _late_alerts(tid, session_id, class_id):
    """ছাত্র যারা গত ৭ দিনে ২+ বার late"""
    return fetchall(
        """SELECT s.name, e.roll_no, e.id AS enrollment_id,
                  COUNT(a.id) AS late_count
           FROM attendance a
           JOIN student_enrollments e ON e.id=a.enrollment_id
           JOIN students s ON s.id=e.student_id
           WHERE a.tenant_id=%s AND e.session_id=%s AND e.class_id=%s
             AND a.status='late'
             AND a.date >= CURRENT_DATE - INTERVAL '7 days'
           GROUP BY s.name, e.roll_no, e.id
           HAVING COUNT(a.id) >= 2
           ORDER BY late_count DESC""",
        (tid, session_id, class_id),
    )

def _absent_streak(tid, enrollment_id, days=5):
    """শেষ N দিন একটানা absent কিনা"""
    rows = fetchall(
        """SELECT status FROM attendance
           WHERE tenant_id=%s AND enrollment_id=%s
           ORDER BY date DESC LIMIT %s""",
        (tid, enrollment_id, days),
    )
    if len(rows) < days:
        return False
    return all(r["status"] == "absent" for r in rows)


# ─────────────────────────────────────────────────────────────────
# ক্যালেন্ডার হিটম্যাপ HTML
# ─────────────────────────────────────────────────────────────────

STATUS_COLOR = {
    "present": "#2E7D32",
    "absent":  "#C62828",
    "late":    "#F57F17",
    "holiday": "#1565C0",
    None:      "#E0E0E0",
}
STATUS_BG = {
    "present": "#E8F5E9",
    "absent":  "#FFEBEE",
    "late":    "#FFF8E1",
    "holiday": "#E3F2FD",
    None:      "#F5F5F5",
}
def _bd_days():
    return [
        t("attn.day_sun"), t("attn.day_mon"), t("attn.day_tue"), t("attn.day_wed"),
        t("attn.day_thu"), t("attn.day_fri"), t("attn.day_sat"),
    ]

def _calendar_heatmap(tid, enrollment_id, year, month, holidays):
    """মাসের ক্যালেন্ডার ভিউ — প্রতিটি দিন রঙিন সেল।"""
    rows = fetchall(
        """SELECT date, status FROM attendance
           WHERE tenant_id=%s AND enrollment_id=%s
             AND EXTRACT(YEAR FROM date)=%s
             AND EXTRACT(MONTH FROM date)=%s""",
        (tid, enrollment_id, year, month),
    )
    att_map = {r["date"]: r["status"] for r in rows}

    # Build calendar matrix (Sun=0 … Sat=6 using isoweekday shift)
    first_day = date(year, month, 1)
    last_day  = cal_lib.monthrange(year, month)[1]
    # Sunday-first offset
    start_offset = (first_day.isoweekday() % 7)  # Sun=0

    cells = []
    for _ in range(start_offset):
        cells.append(None)
    for d in range(1, last_day + 1):
        cells.append(date(year, month, d))

    # Pad to complete weeks
    while len(cells) % 7:
        cells.append(None)

    header = "".join(
        f'<th style="width:14%;text-align:center;padding:6px 2px;'
        f'font-size:11px;color:#6B7A8D;font-weight:600">{d}</th>'
        for d in _bd_days()
    )

    rows_html = ""
    for week_start in range(0, len(cells), 7):
        week = cells[week_start:week_start+7]
        row_html = "<tr>"
        for day_date in week:
            if day_date is None:
                row_html += '<td style="padding:4px"></td>'
            else:
                # Holiday override
                if day_date in holidays:
                    status = "holiday"
                elif day_date.isoweekday() == 5:   # Friday
                    status = "holiday"
                else:
                    status = att_map.get(day_date)

                bg    = STATUS_BG.get(status, "#F5F5F5")
                color = STATUS_COLOR.get(status, "#9E9E9E")
                icons_map = {"present":"✓","absent":"✗","late":"⏰","holiday":"🌿",None:"·"}
                icon  = icons_map.get(status, "·")
                today_border = "border:2px solid #0F4C5C;" if day_date == date.today() else ""

                row_html += (
                    f'<td style="padding:3px;text-align:center">'
                    f'<div style="background:{bg};color:{color};border-radius:6px;'
                    f'padding:5px 2px;{today_border}cursor:default;'
                    f'font-size:11px;line-height:1.3">'
                    f'<div style="font-weight:600">{day_date.day}</div>'
                    f'<div style="font-size:10px">{icon}</div>'
                    f'</div></td>'
                )
        row_html += "</tr>"
        rows_html += row_html

    legend = "".join(
        f'<span style="background:{STATUS_BG[s]};color:{STATUS_COLOR[s]};'
        f'border-radius:4px;padding:2px 8px;font-size:10px;font-weight:600;margin-right:6px">'
        f'{icons[s]} {label}</span>'
        for s, label, icons in [
            ("present", t("attn.status_present"), {"present":"✓"}),
            ("absent",  t("attn.status_absent"),  {"absent":"✗"}),
            ("late",    t("attn.status_late"),    {"late":"⏰"}),
            ("holiday", t("attn.status_holiday"), {"holiday":"🌿"}),
            (None,      t("attn.status_unmarked"),{None:"·"}),
        ]
    )

    return flatten_html(f"""
    <div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                padding:12px;margin-bottom:1rem">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>{header}</tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid #EEE">
        {legend}
      </div>
    </div>""")


# ─────────────────────────────────────────────────────────────────
# স্ট্রিক বার (৩০ দিন)
# ─────────────────────────────────────────────────────────────────

def _streak_bar_html(logs):
    """সর্বশেষ ৩০ দিনের রঙিন dot স্ট্রিক।"""
    today = date.today()
    log_map = {r["date"]: r["status"] for r in logs}
    dots = ""
    for i in range(29, -1, -1):
        d      = today - timedelta(days=i)
        status = log_map.get(d)
        color  = STATUS_COLOR.get(status, "#E0E0E0")
        title  = f"{d} — {status or t('attn.status_unmarked')}"
        dots  += (
            f'<div title="{title}" style="width:16px;height:16px;border-radius:3px;'
            f'background:{color};display:inline-block;margin:1px;cursor:help"></div>'
        )
    return flatten_html(f"""
    <div style="background:white;border:1px solid #DDE3E7;border-radius:8px;padding:10px 14px;margin-bottom:0.75rem">
      <div style="font-size:11px;color:#6B7A8D;margin-bottom:6px;font-weight:600">
        {t("attn.last_30_days_streak")}
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:1px">{dots}</div>
      <div style="margin-top:6px;font-size:10px;color:#9E9E9E">
        {t("attn.streak_range_hint")}
      </div>
    </div>""")


# ─────────────────────────────────────────────────────────────────
# ডিজিটাল পাঞ্চ-ইন (QR সিমুলেশন)
# ─────────────────────────────────────────────────────────────────

def _render_digital_punch(tid, students, date_str, today_att):
    st.markdown(
        f"""<div style="background:linear-gradient(135deg,#0F4C5C,#1a6b80);
                       border-radius:12px;padding:1rem 1.25rem;margin-bottom:1rem;color:white">
          <div style="font-size:1rem;font-weight:700">{t("attn.digital_punch_title")}</div>
          <div style="font-size:0.78rem;opacity:0.8;margin-top:2px">
            {t("attn.digital_punch_subtitle")}
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # এখন পর্যন্ত সারাংশ
    present = sum(1 for v in today_att.values() if v == "present")
    absent  = sum(1 for v in today_att.values() if v == "absent")
    late    = sum(1 for v in today_att.values() if v == "late")
    total   = len(students)
    unmarked = total - len(today_att)

    st.markdown(
        f"""<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:1rem">
          <div style="background:#E8F5E9;border-radius:8px;padding:10px;text-align:center">
            <div style="font-size:1.4rem;font-weight:700;color:#2E7D32">{present}</div>
            <div style="font-size:0.7rem;color:#2E7D32">{t("attn.status_present")}</div>
          </div>
          <div style="background:#FFEBEE;border-radius:8px;padding:10px;text-align:center">
            <div style="font-size:1.4rem;font-weight:700;color:#C62828">{absent}</div>
            <div style="font-size:0.7rem;color:#C62828">{t("attn.status_absent")}</div>
          </div>
          <div style="background:#FFF8E1;border-radius:8px;padding:10px;text-align:center">
            <div style="font-size:1.4rem;font-weight:700;color:#F57F17">{late}</div>
            <div style="font-size:0.7rem;color:#F57F17">{t("attn.status_late")}</div>
          </div>
          <div style="background:#F5F5F5;border-radius:8px;padding:10px;text-align:center">
            <div style="font-size:1.4rem;font-weight:700;color:#555">{unmarked}</div>
            <div style="font-size:0.7rem;color:#555">{t("attn.status_unmarked")}</div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # Quick punch
    punch_col1, punch_col2 = st.columns([2, 1])
    punch_query = punch_col1.text_input(
        t("attn.punch_search_label"),
        placeholder=t("attn.punch_search_placeholder"),
        key="punch_query",
        label_visibility="collapsed",
    )
    punch_status = punch_col2.selectbox(
        t("attn.status_label"), ["present", "absent", "late", "holiday"],
        format_func=lambda x: {"present":t("attn.opt_present"),"absent":t("attn.opt_absent"),
                                "late":t("attn.opt_late"),"holiday":t("attn.opt_holiday")}[x],
        key="punch_status",
        label_visibility="collapsed",
    )

    if punch_query:
        q = punch_query.strip().lower()
        matched = [
            s for s in students
            if q == str(s["roll_no"] or "").lower()
            or q in (s["name"] or "").lower()
        ]
        if matched:
            for m in matched:
                curr = today_att.get(m["enrollment_id"], "—")
                curr_icon = {"present":"✅","absent":"❌","late":"⏰","holiday":"🌿","—":"⬜"}.get(curr,"⬜")
                col_info, col_btn = st.columns([3,1])
                col_info.markdown(
                    f"**{m['name']}** — Roll {m['roll_no'] or '—'}  \n"
                    f"{t('attn.current_status')}: {curr_icon} `{curr}`"
                )
                if col_btn.button(
                    t("attn.set_button"),
                    key=f"punch_{m['enrollment_id']}",
                    type="primary",
                ):
                    if _single_punch(tid, m["enrollment_id"], date_str, punch_status):
                        st.toast(f"✅ {m['name']} → {punch_status}", icon="✅")
                        st.rerun()
        else:
            st.warning(t("attn.no_student_found"))


# ─────────────────────────────────────────────────────────────────
# প্রধান রেন্ডার
# ─────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("📅", t("attn.page_title"), t("attn.page_subtitle"))

    sessions = _get_sessions(tid)
    classes  = _get_classes(tid)

    if not sessions:
        alert(t("attn.no_sessions_msg"), "warning"); return
    if not classes:
        alert(t("attn.no_classes_msg"), "warning"); return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]: c["id"] for c in classes}

    # ── টপ ফিল্টার বার ──
    fc1, fc2, fc3 = st.columns(3)
    sel_sess  = fc1.selectbox(t("attn.session_label"),  list(sess_map.keys()), key="att_sess")
    sel_class = fc2.selectbox(t("attn.class_label"), list(class_map.keys()), key="att_class")
    att_date  = fc3.date_input(t("attn.filter_date"), value=date.today(), key="att_date")

    session_id = sess_map[sel_sess]
    class_id   = class_map[sel_class]
    date_str   = str(att_date)
    students   = _get_students(tid, session_id, class_id)
    today_att  = _get_day_attendance(tid, date_str, class_id, session_id)
    holidays   = _get_holidays(tid, att_date.year, att_date.month)

    # ট্যাব
    tab_punch, tab_roll, tab_cal, tab_student, tab_monthly, tab_late, tab_holiday, tab_export = st.tabs([
        t("attn.tab_punch"),
        t("attn.tab_roll_call"),
        t("attn.tab_calendar"),
        t("attn.tab_student_tracker"),
        t("attn.tab_monthly_report"),
        t("attn.tab_late_alert"),
        t("attn.tab_holidays"),
        t("attn.tab_export"),
    ])

    # ══════════════════════════════════════════
    # ট্যাব ১ — ডিজিটাল পাঞ্চ-ইন
    # ══════════════════════════════════════════
    with tab_punch:
        if not students:
            alert(t("attn.no_students_in_class"), "info")
        else:
            _render_digital_punch(tid, students, date_str, today_att)

            # অ্যাটেনড্যান্স প্রগ্রেস রিং
            total   = len(students)
            marked  = len(today_att)
            pct_done = int(marked / total * 100) if total else 0
            st.markdown(
                flatten_html(f"""<div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                                padding:1rem;text-align:center;margin-top:0.5rem">
                  <div style="font-size:0.8rem;color:#6B7A8D;margin-bottom:6px">
                    {t("attn.today_progress")}
                  </div>
                  <div style="background:#EEE;border-radius:20px;height:12px;overflow:hidden">
                    <div style="background:{'#2E7D32' if pct_done==100 else '#0F4C5C'};
                                height:100%;width:{pct_done}%;border-radius:20px;
                                transition:width 0.5s"></div>
                  </div>
                  <div style="font-size:0.85rem;font-weight:700;color:#0F4C5C;margin-top:6px">
                    {t("attn.progress_count", marked=marked, total=total, pct=pct_done)}
                  </div>
                  {"<div style='color:#2E7D32;font-size:0.8rem'>" + t("attn.all_marked_done") + "</div>" if pct_done==100 else ""}
                </div>"""),
                unsafe_allow_html=True,
            )

    # ══════════════════════════════════════════
    # ট্যাব ২ — ক্লাসিক রোল কল
    # ══════════════════════════════════════════
    with tab_roll:
        st.markdown(f"#### {t('attn.roll_call_header', class_name=sel_class, date_str=att_date.strftime('%d %B %Y, %A'))}")

        if att_date in holidays:
            alert(t("attn.today_holiday", desc=holidays[att_date]), "info")

        if not students:
            alert(t("attn.no_active_students"), "info")
        else:
            # কুইক বাল্ক বাটন
            qc1, qc2, qc3, qc4 = st.columns(4)
            if qc1.button(t("attn.mark_all_present"), use_container_width=True):
                bulk = [{"enrollment_id": s["enrollment_id"], "status": "present"} for s in students]
                ok, _ = _save_bulk(tid, date_str, bulk)
                if ok: st.success(t("attn.all_marked_present_msg")); st.rerun()

            if qc2.button(t("attn.mark_all_absent"), use_container_width=True):
                bulk = [{"enrollment_id": s["enrollment_id"], "status": "absent"} for s in students]
                ok, _ = _save_bulk(tid, date_str, bulk)
                if ok: st.warning(t("attn.all_marked_absent_msg")); st.rerun()

            if qc3.button(t("attn.mark_holiday"), use_container_width=True):
                bulk = [{"enrollment_id": s["enrollment_id"], "status": "holiday"} for s in students]
                ok, _ = _save_bulk(tid, date_str, bulk)
                if ok: st.info(t("attn.marked_as_holiday_msg")); st.rerun()

            if qc4.button(t("attn.refresh"), use_container_width=True):
                st.rerun()

            divider()

            # রোল কল ফর্ম
            with st.form("roll_call_form", clear_on_submit=False):
                # টেবিল হেডার
                st.markdown(
                    f"""<div style="display:grid;grid-template-columns:60px 1fr 160px;
                                   gap:8px;padding:6px 8px;background:#0F4C5C;
                                   border-radius:8px 8px 0 0;color:white;font-size:12px;
                                   font-weight:600;margin-bottom:2px">
                         <div>{t("attn.col_roll")}</div><div>{t("attn.col_name")}</div><div style="text-align:center">{t("attn.col_status")}</div>
                       </div>""",
                    unsafe_allow_html=True,
                )

                records = []
                STATUS_OPT = ["present","absent","late","holiday"]
                STATUS_LABEL = {
                    "present":t("attn.opt_present"),
                    "absent": t("attn.opt_absent"),
                    "late":   t("attn.opt_late"),
                    "holiday":t("attn.opt_holiday"),
                }

                for i, stu in enumerate(students):
                    existing = today_att.get(stu["enrollment_id"], "present")
                    row_bg   = "#F7FBF7" if existing == "present" else \
                               "#FFF5F5" if existing == "absent"  else \
                               "#FFFBF0" if existing == "late"    else "#F0F5FF"

                    c1, c2, c3 = st.columns([1, 4, 3])
                    c1.markdown(
                        f'<div style="background:{row_bg};padding:8px 6px;border-radius:6px;'
                        f'text-align:center;font-weight:700;color:#0F4C5C">'
                        f'{stu["roll_no"] or "—"}</div>',
                        unsafe_allow_html=True,
                    )
                    c2.markdown(
                        f'<div style="background:{row_bg};padding:8px 10px;border-radius:6px">'
                        f'{stu["name"]}</div>',
                        unsafe_allow_html=True,
                    )
                    status = c3.selectbox(
                        "status",
                        STATUS_OPT,
                        index=STATUS_OPT.index(existing) if existing in STATUS_OPT else 0,
                        format_func=lambda x: STATUS_LABEL[x],
                        key=f"rc_{stu['enrollment_id']}",
                        label_visibility="collapsed",
                    )
                    records.append({"enrollment_id": stu["enrollment_id"], "status": status})

                submitted = st.form_submit_button(t("attn.save_attendance"), type="primary",
                                                   use_container_width=True)
                if submitted:
                    ok, result = _save_bulk(tid, date_str, records)
                    if ok:
                        present_c = sum(1 for r in records if r["status"] == "present")
                        absent_c  = sum(1 for r in records if r["status"] == "absent")
                        st.success(t("attn.attendance_saved_msg", present=present_c, absent=absent_c))
                        st.rerun()
                    else:
                        st.error(t("attn.failed_msg", result=result))

    # ══════════════════════════════════════════
    # ট্যাব ৩ — ক্যালেন্ডার হিটম্যাপ
    # ══════════════════════════════════════════
    with tab_cal:
        st.markdown(f"#### {t('attn.monthly_calendar_heatmap')}")

        cc1, cc2 = st.columns(2)
        cal_year  = cc1.number_input(t("attn.year_label"),  min_value=2020, max_value=2040,
                                      value=date.today().year,  key="cal_yr")
        cal_month = cc2.selectbox(t("attn.month_label"), list(range(1,13)),
                                   index=date.today().month - 1,
                                   format_func=lambda m: date(2000,m,1).strftime("%B"),
                                   key="cal_month")

        if not students:
            alert(t("attn.no_students_in_class"), "info")
        else:
            stu_map2 = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students}
            sel_cal  = st.selectbox(t("attn.select_student"), list(stu_map2.keys()), key="cal_stu")
            cal_stu  = stu_map2[sel_cal]
            holidays2 = _get_holidays(tid, int(cal_year), int(cal_month))

            # Calendar
            st.markdown(
                f"**{date(int(cal_year), int(cal_month), 1).strftime('%B %Y')} — "
                f"{cal_stu['name']}**"
            )
            st.markdown(
                _calendar_heatmap(
                    tid, cal_stu["enrollment_id"],
                    int(cal_year), int(cal_month), holidays2,
                ),
                unsafe_allow_html=True,
            )

            # মাসিক সারসংক্ষেপ
            logs = fetchall(
                """SELECT status, COUNT(*) AS n FROM attendance
                   WHERE tenant_id=%s AND enrollment_id=%s
                     AND EXTRACT(YEAR FROM date)=%s
                     AND EXTRACT(MONTH FROM date)=%s
                   GROUP BY status""",
                (tid, cal_stu["enrollment_id"], int(cal_year), int(cal_month)),
            )
            if logs:
                stat_dict = {r["status"]: int(r["n"]) for r in logs}
                total_m   = sum(stat_dict.values())
                present_m = stat_dict.get("present", 0)
                pct_m     = round(present_m / total_m * 100, 1) if total_m else 0
                kpi_row([
                    {"label":t("attn.status_present"),     "value": present_m, "cls":"success"},
                    {"label":t("attn.status_absent"),   "value": stat_dict.get("absent",0), "cls":"danger"},
                    {"label":t("attn.status_late"),      "value": stat_dict.get("late",0),   "cls":"warning"},
                    {"label":t("attn.attendance_rate"),"value": f"{pct_m}%",
                     "cls": "success" if pct_m >= 75 else "danger"},
                ])
                if pct_m < 75:
                    alert(t("attn.low_attendance_warning"), "danger")

    # ══════════════════════════════════════════
    # ট্যাব ৪ — ব্যক্তিগত ট্র্যাকার
    # ══════════════════════════════════════════
    with tab_student:
        st.markdown(f"#### {t('attn.individual_tracker_title')}")

        if not students:
            alert(t("attn.no_students"), "info")
        else:
            stu_map3 = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students}
            sel_s3   = st.selectbox(t("attn.select_student"), list(stu_map3.keys()), key="trk_stu")
            stu3     = stu_map3[sel_s3]

            logs3    = _student_log(tid, stu3["enrollment_id"], days=60)

            # স্ট্রিক বার
            st.markdown(_streak_bar_html(logs3), unsafe_allow_html=True)

            if logs3:
                total3   = len(logs3)
                present3 = sum(1 for l in logs3 if l["status"] == "present")
                absent3  = sum(1 for l in logs3 if l["status"] == "absent")
                late3    = sum(1 for l in logs3 if l["status"] == "late")
                pct3     = round(present3 / total3 * 100, 1) if total3 else 0

                kpi_row([
                    {"label":t("attn.total_days"),      "value": total3,   "cls":""},
                    {"label":t("attn.status_present"),      "value": present3, "cls":"success"},
                    {"label":t("attn.status_absent"),    "value": absent3,  "cls":"danger"},
                    {"label":t("attn.status_late"),        "value": late3,    "cls":"warning"},
                    {"label":t("attn.attendance_rate"), "value": f"{pct3}%",
                     "cls":"success" if pct3>=75 else "danger"},
                ])

                # একটানা absent সতর্কতা
                if _absent_streak(tid, stu3["enrollment_id"], days=5):
                    alert(
                        t("attn.absent_streak_alert", name=stu3["name"]),
                        "danger",
                    )

                divider()
                st.markdown(f"**{t('attn.last_60_days_log')}**")
                import pandas as pd
                df = pd.DataFrame([
                    {
                        t("attn.col_date"):  str(l["date"]),
                        t("attn.col_weekday"):    l["date"].strftime("%A") if hasattr(l["date"],"strftime") else "—",
                        t("attn.col_status"): l["status"].title(),
                        t("attn.col_icon"): {"present":"✅","absent":"❌","late":"⏰","holiday":"🌿"}.get(l["status"],"—"),
                    }
                    for l in logs3
                ])
                st.dataframe(df, use_container_width=True, hide_index=True, height=320)

    # ══════════════════════════════════════════
    # ট্যাব ৫ — মাসিক রিপোর্ট
    # ══════════════════════════════════════════
    with tab_monthly:
        st.markdown(f"#### {t('attn.monthly_report_title')}")
        mc1, mc2 = st.columns(2)
        rep_yr  = mc1.number_input(t("attn.year_label"),  min_value=2020, max_value=2040,
                                    value=date.today().year,  key="rep_yr")
        rep_mon = mc2.selectbox(t("attn.month_label"), list(range(1,13)),
                                 index=date.today().month - 1,
                                 format_func=lambda m: date(2000,m,1).strftime("%B"),
                                 key="rep_mon")

        if st.button(t("attn.generate_report"), type="primary", key="gen_rep"):
            summary = _monthly_summary(
                tid, class_id, session_id, int(rep_yr), int(rep_mon)
            )
            if not summary:
                alert(t("attn.no_monthly_data"), "info")
            else:
                import pandas as pd
                rows = []
                for r in summary:
                    tot = int(r["total_marked"] or 0)
                    pre = int(r["present"] or 0)
                    pct = round(pre / tot * 100, 1) if tot else 0
                    rows.append({
                        t("attn.col_roll"):      r["roll_no"] or "—",
                        t("attn.col_name"):      r["name"],
                        t("attn.col_present"): pre,
                        t("attn.col_absent"): int(r["absent"] or 0),
                        t("attn.col_late"):   int(r["late"] or 0),
                        t("attn.col_total_days"): tot,
                        t("attn.col_rate"):    f"{pct}%",
                        t("attn.col_remark"):  t("attn.remark_good") if pct>=75 else (t("attn.remark_low") if pct>=50 else t("attn.remark_critical")),
                    })

                st.dataframe(rows, use_container_width=True, hide_index=True)

                # বার চার্ট
                df = pd.DataFrame(rows)
                rate_col  = t("attn.col_rate")
                name_col  = t("attn.col_name")
                chart_col = t("attn.chart_rate_col")
                df[chart_col] = df[rate_col].str.replace("%","").astype(float)
                st.markdown(f"**{t('attn.class_attendance_chart')}**")
                st.bar_chart(
                    df.set_index(name_col)[[chart_col]],
                    color=PALETTE["primary"], height=300,
                )

                # ক্লাস এগ্রিগেট
                total_p = sum(int(r["present"] or 0) for r in summary)
                total_a = sum(int(r["absent"]  or 0) for r in summary)
                total_l = sum(int(r["late"]    or 0) for r in summary)
                total_d = sum(int(r["total_marked"] or 0) for r in summary)
                avg_pct = round(total_p / total_d * 100, 1) if total_d else 0
                divider()
                kpi_row([
                    {"label":t("attn.total_present"),    "value": total_p, "cls":"success"},
                    {"label":t("attn.total_absent"),  "value": total_a, "cls":"danger"},
                    {"label":t("attn.total_late"),         "value": total_l, "cls":"warning"},
                    {"label":t("attn.avg_attendance_rate"),   "value": f"{avg_pct}%",
                     "cls":"success" if avg_pct>=75 else "danger"},
                ])

    # ══════════════════════════════════════════
    # ট্যাব ৬ — দেরি অ্যালার্ট
    # ══════════════════════════════════════════
    with tab_late:
        st.markdown(f"#### {t('attn.late_alert_panel_title')}")
        alert(t("attn.late_alert_desc"), "warning")

        late_list = _late_alerts(tid, session_id, class_id)
        if not late_list:
            alert(t("attn.no_repeat_late"), "success")
        else:
            for l in late_list:
                st.markdown(
                    f"""<div style="background:#FFF8E1;border-left:4px solid #F57F17;
                                    border-radius:6px;padding:10px 14px;margin-bottom:6px;
                                    display:flex;justify-content:space-between;align-items:center">
                      <div>
                        <strong>⏰ {l['name']}</strong>
                        <span style="color:#6B7A8D;font-size:0.8rem"> — Roll {l['roll_no'] or '—'}</span>
                      </div>
                      <div style="background:#F57F17;color:white;border-radius:20px;
                                  padding:2px 12px;font-size:0.8rem;font-weight:700">
                        {t("attn.late_count_times", n=l['late_count'])}
                      </div>
                    </div>""",
                    unsafe_allow_html=True,
                )

        divider()
        st.markdown(f"**{t('attn.streak_absent_title')}**")
        absent_streaks = [
            s for s in students
            if _absent_streak(tid, s["enrollment_id"], days=5)
        ]
        if not absent_streaks:
            alert(t("attn.no_absent_streak"), "success")
        else:
            for s in absent_streaks:
                st.markdown(
                    f"""<div style="background:#FFEBEE;border-left:4px solid #C62828;
                                    border-radius:6px;padding:10px 14px;margin-bottom:6px">
                      🚨 <strong>{s['name']}</strong> (Roll {s['roll_no'] or '—'}) —
                      <span style="color:#C62828">{t("attn.absent_streak_item_note")}</span>
                    </div>""",
                    unsafe_allow_html=True,
                )

    # ══════════════════════════════════════════
    # ট্যাব ৭ — ছুটির দিন ম্যানেজার
    # ══════════════════════════════════════════
    with tab_holiday:
        st.markdown(f"#### {t('attn.holiday_manager_title')}")
        with st.form("holiday_form"):
            h1, h2 = st.columns(2)
            h_date = h1.date_input(t("attn.date_plain"), value=date.today())
            h_desc = h2.text_input(t("attn.description_label"), placeholder=t("attn.holiday_desc_placeholder"))
            if st.form_submit_button(t("attn.add_holiday"), type="primary"):
                if not h_desc.strip():
                    st.error(t("attn.desc_required"))
                elif _save_holiday(tid, str(h_date), h_desc.strip()):
                    st.success(t("attn.holiday_added_msg", date=h_date, desc=h_desc))
                    st.rerun()

        divider()
        all_hols = _all_holidays(tid)
        if not all_hols:
            alert(t("attn.no_holidays"), "info")
        else:
            st.markdown(f"**{t('attn.holidays_list_title')}**")
            for h in all_hols:
                hc1, hc2 = st.columns([4, 1])
                hc1.markdown(
                    f"🌿 **{str(h['holiday_date'])}** — {h['description']}"
                )
                if hc2.button(t("attn.delete_button"), key=f"del_hol_{h['holiday_date']}"):
                    _delete_holiday(tid, str(h["holiday_date"]))
                    st.rerun()

    # ══════════════════════════════════════════
    # ট্যাব ৮ — CSV এক্সপোর্ট
    # ══════════════════════════════════════════
    with tab_export:
        st.markdown(f"#### {t('attn.export_title')}")
        ex1, ex2 = st.columns(2)
        ex_yr  = ex1.number_input(t("attn.year_label"),  min_value=2020, max_value=2040,
                                   value=date.today().year,  key="ex_yr")
        ex_mon = ex2.selectbox(t("attn.month_label"), list(range(1,13)),
                                index=date.today().month - 1,
                                format_func=lambda m: date(2000,m,1).strftime("%B"),
                                key="ex_mon")

        if st.button(t("attn.prepare_data"), type="primary", key="prep_export"):
            rows = fetchall(
                """SELECT s.name, e.roll_no, c.class_name,
                          a.date, a.status
                   FROM attendance a
                   JOIN student_enrollments e ON e.id=a.enrollment_id
                   JOIN students s ON s.id=e.student_id
                   JOIN classes c ON c.id=e.class_id
                   WHERE a.tenant_id=%s AND e.session_id=%s AND e.class_id=%s
                     AND EXTRACT(YEAR FROM a.date)=%s
                     AND EXTRACT(MONTH FROM a.date)=%s
                   ORDER BY a.date, e.roll_no""",
                (tid, session_id, class_id, int(ex_yr), int(ex_mon)),
            )
            if not rows:
                alert(t("attn.no_export_data"), "info")
            else:
                import csv, io
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow([t("attn.col_name"),t("attn.col_roll"),t("attn.col_class"),t("attn.col_date"),t("attn.col_status")])
                for r in rows:
                    writer.writerow([csv_safe(r["name"]),r["roll_no"],r["class_name"],
                                     str(r["date"]),r["status"]])
                csv_bytes = buf.getvalue().encode("utf-8-sig")
                st.download_button(
                    t("attn.download_csv", n=len(rows)),
                    data=csv_bytes,
                    file_name=f"hajira_{sel_class}_{ex_yr}_{ex_mon:02d}.csv",
                    mime="text/csv",
                    type="primary",
                )
                st.caption(t("attn.total_records_found", n=len(rows)))
