"""
digital_attendance.py — সম্পূর্ণ ডিজিটাল হাজিরা সিস্টেম
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ফিচার:
  ১. QR কোড ID কার্ড জেনারেটর (প্রতিটি ছাত্রের জন্য)
  ২. QR স্ক্যান সিমুলেটর — Roll/ID টাইপ করে তাৎক্ষণিক পাঞ্চ-ইন
  ৩. লাইভ হাজিরা ড্যাশবোর্ড — রিয়েলটাইম কাউন্টার
  ৪. ক্যালেন্ডার হিটম্যাপ — মাসের প্রতিদিন রঙিন ভিজ্যুয়াল
  ৫. একটানা অনুপস্থিতি অ্যালার্ট সিস্টেম
  ৬. ৩০-দিনের স্ট্রিক বার (ব্যক্তিগত ট্র্যাকার)
  ৭. শিক্ষকদের ডিজিটাল হাজিরা (আলাদা ট্যাব)
  ৮. প্রিন্টযোগ্য হাজিরা শিট
  ৯. মাসিক রিপোর্ট + CSV এক্সপোর্ট
"""

from error_handler import safe_db_error
import streamlit as st
import qrcode
import io
import base64
import calendar as cal_lib
from datetime import date, timedelta, datetime
from db import get_connection, release_connection, fetchall, fetchone
from utils import (
    page_header, kpi_row, alert, divider,
    get_tenant_id, PALETTE, flatten_html, print_button,
)
from sanitize import csv_safe
from i18n import t

# ─────────────────────────────────────────────────────────────────
# QR কোড জেনারেটর
# ─────────────────────────────────────────────────────────────────

def _generate_qr_base64(data: str) -> str:
    """QR কোড তৈরি করে base64 string ফেরত দেয়।"""
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0F4C5C", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _id_card_html(student, tenant, qr_b64: str) -> str:
    """প্রিন্টযোগ্য ডিজিটাল ID কার্ড HTML।"""
    return flatten_html(f"""
    <div style="width:320px;border:2px solid #0F4C5C;border-radius:14px;
                overflow:hidden;font-family:'Inter',sans-serif;
                box-shadow:0 4px 16px rgba(15,76,92,0.2)">

      <!-- হেডার ব্যানার -->
      <div style="background:linear-gradient(135deg,#0F4C5C,#1a7a96);
                  padding:14px 16px;color:white;text-align:center">
        <div style="font-size:13px;font-weight:700;letter-spacing:0.5px">
          🕌 {tenant.get('madrasa_name','Smart Madrasa')}
        </div>
        <div style="font-size:10px;opacity:0.8;margin-top:2px">
          {t("attn.id_card_subtitle")}
        </div>
      </div>

      <!-- বডি -->
      <div style="background:white;padding:14px 16px;
                  display:flex;gap:14px;align-items:center">

        <!-- QR কোড -->
        <div style="flex-shrink:0;border:2px solid #EEE;border-radius:8px;padding:4px">
          <img src="data:image/png;base64,{qr_b64}"
               style="width:90px;height:90px;display:block">
        </div>

        <!-- তথ্য -->
        <div style="flex:1;font-size:11px;line-height:1.8;color:#1A2332">
          <div style="font-size:14px;font-weight:700;color:#0F4C5C;margin-bottom:4px">
            {student['name']}
          </div>
          <div>{t("attn.id_card_father")}: <strong>{student.get('father_name') or '—'}</strong></div>
          <div>{t("attn.id_card_class")}: <strong>{student.get('class_name','')}</strong></div>
          <div>{t("attn.id_card_roll")}: <strong>{student.get('roll_no') or '—'}</strong></div>
          <div>{t("attn.id_card_session")}: <strong>{student.get('session_name','')}</strong></div>
          <div style="margin-top:4px;font-size:10px;color:#6B7A8D">
            ID: STU-{student['id']:05d}
          </div>
        </div>
      </div>

      <!-- ফুটার -->
      <div style="background:#F7F9FA;padding:8px 16px;
                  border-top:1px solid #EEE;text-align:center">
        <div style="font-size:9px;color:#6B7A8D;letter-spacing:0.3px">
          {t("attn.id_card_footer")}
        </div>
        <div style="font-family:monospace;font-size:10px;font-weight:700;
                    color:#0F4C5C;margin-top:2px;letter-spacing:1px">
          STU-{student['id']:05d}-{student.get('roll_no') or '000'}
        </div>
      </div>
    </div>""")


# ─────────────────────────────────────────────────────────────────
# হাজিরা DB অপারেশন
# ─────────────────────────────────────────────────────────────────

def _get_students_full(tid, session_id, class_id):
    return fetchall(
        """SELECT s.id AS student_id, s.id, s.name, s.father_name, s.mobile_no,
                  e.roll_no, e.id AS enrollment_id, c.class_name, sess.session_name
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s AND e.session_id=%s AND e.class_id=%s
             AND e.enrollment_status='active' AND s.status='active'
           ORDER BY e.roll_no NULLS LAST, s.name""",
        (tid, session_id, class_id),
    )


def _day_att(tid, date_str, class_id, session_id):
    rows = fetchall(
        """SELECT a.enrollment_id, a.status
           FROM attendance a
           JOIN student_enrollments e ON e.id=a.enrollment_id
           WHERE a.tenant_id=%s AND a.date=%s
             AND e.class_id=%s AND e.session_id=%s""",
        (tid, date_str, class_id, session_id),
    )
    return {r["enrollment_id"]: r["status"] for r in rows}


def _punch(tid, enrollment_id, date_str, status):
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


def _bulk_save(tid, date_str, records):
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        with conn.cursor() as cur:
            for r in records:
                cur.execute(
                    """INSERT INTO attendance (tenant_id, enrollment_id, date, status)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (enrollment_id, date)
                       DO UPDATE SET status=EXCLUDED.status""",
                    (tid, r["enrollment_id"], date_str, r["status"]),
                )
        conn.commit()
        return True, len(records)
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _get_holidays(tid):
    rows = fetchall(
        "SELECT holiday_date, description FROM holidays WHERE tenant_id=%s ORDER BY holiday_date",
        (tid,),
    )
    return {r["holiday_date"]: r["description"] for r in rows}


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


def _monthly_summary(tid, class_id, session_id, year, month):
    return fetchall(
        """SELECT s.name, e.roll_no, e.id AS enrollment_id,
                  COUNT(CASE WHEN a.status='present' THEN 1 END) AS present,
                  COUNT(CASE WHEN a.status='absent'  THEN 1 END) AS absent,
                  COUNT(CASE WHEN a.status='late'    THEN 1 END) AS late,
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


def _absent_streak(tid, enrollment_id, days=5):
    rows = fetchall(
        """SELECT status FROM attendance
           WHERE tenant_id=%s AND enrollment_id=%s
           ORDER BY date DESC LIMIT %s""",
        (tid, enrollment_id, days),
    )
    return len(rows) >= days and all(r["status"] == "absent" for r in rows)


def _late_alerts(tid, session_id, class_id):
    return fetchall(
        """SELECT s.name, e.roll_no, COUNT(a.id) AS late_count
           FROM attendance a
           JOIN student_enrollments e ON e.id=a.enrollment_id
           JOIN students s ON s.id=e.student_id
           WHERE a.tenant_id=%s AND e.session_id=%s AND e.class_id=%s
             AND a.status='late'
             AND a.date >= CURRENT_DATE - INTERVAL '7 days'
           GROUP BY s.name, e.roll_no
           HAVING COUNT(a.id) >= 2
           ORDER BY late_count DESC""",
        (tid, session_id, class_id),
    )


# Teacher attendance helpers
def _get_teachers_active(tid):
    try:
        return fetchall(
            "SELECT id, name, designation FROM teachers WHERE tenant_id=%s AND status='active' ORDER BY name",
            (tid,),
        )
    except Exception:
        return []


def _teacher_day_att(tid, date_str):
    try:
        rows = fetchall(
            "SELECT teacher_id, status, punch_time FROM teacher_attendance WHERE tenant_id=%s AND date=%s",
            (tid, date_str),
        )
        return {r["teacher_id"]: r for r in rows}
    except Exception:
        return {}


def _punch_teacher(tid, teacher_id, date_str, status):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO teacher_attendance (tenant_id, teacher_id, date, status, punch_time)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, teacher_id, date)
                   DO UPDATE SET status=EXCLUDED.status, punch_time=EXCLUDED.punch_time""",
                (tid, teacher_id, date_str, status,
                 datetime.now().strftime("%H:%M:%S")),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


# ─────────────────────────────────────────────────────────────────
# ক্যালেন্ডার হিটম্যাপ
# ─────────────────────────────────────────────────────────────────

STATUS_COLOR = {
    "present": "#2E7D32",
    "absent":  "#C62828",
    "late":    "#F57F17",
    "holiday": "#1565C0",
}
STATUS_BG = {
    "present": "#C8E6C9",
    "absent":  "#FFCDD2",
    "late":    "#FFF9C4",
    "holiday": "#BBDEFB",
}
STATUS_ICON = {
    "present": "✓",
    "absent":  "✗",
    "late":    "⏰",
    "holiday": "🌿",
}

def _bd_days():
    return [
        t("attn.day_sun"), t("attn.day_mon"), t("attn.day_tue"), t("attn.day_wed"),
        t("attn.day_thu"), t("attn.day_fri"), t("attn.day_sat"),
    ]


def _calendar_heatmap_html(tid, enrollment_id, year, month, holidays: dict) -> str:
    rows = fetchall(
        """SELECT date, status FROM attendance
           WHERE tenant_id=%s AND enrollment_id=%s
             AND EXTRACT(YEAR FROM date)=%s
             AND EXTRACT(MONTH FROM date)=%s""",
        (tid, enrollment_id, year, month),
    )
    att_map = {r["date"]: r["status"] for r in rows}

    last_day     = cal_lib.monthrange(year, month)[1]
    first_day    = date(year, month, 1)
    start_offset = first_day.isoweekday() % 7   # Sun=0

    cells = [None] * start_offset
    cells += [date(year, month, d) for d in range(1, last_day + 1)]
    while len(cells) % 7:
        cells.append(None)

    header = "".join(
        f'<th style="width:14%;text-align:center;padding:8px 2px;'
        f'font-size:11px;color:#6B7A8D;font-weight:700;'
        f'background:#F7F9FA">{d}</th>'
        for d in _bd_days()
    )

    body = ""
    for w in range(0, len(cells), 7):
        body += "<tr>"
        for d in cells[w:w+7]:
            if d is None:
                body += '<td style="padding:3px"></td>'
                continue

            if d in holidays:
                st_val = "holiday"
            elif d.isoweekday() == 5:      # Friday
                st_val = "holiday"
            else:
                st_val = att_map.get(d)

            bg    = STATUS_BG.get(st_val, "#F5F5F5")
            color = STATUS_COLOR.get(st_val, "#9E9E9E")
            icon  = STATUS_ICON.get(st_val, "·")
            is_today = d == date.today()
            border   = "border:2px solid #0F4C5C;" if is_today else "border:1px solid #EEE;"

            body += (
                f'<td style="padding:3px;text-align:center">'
                f'<div style="background:{bg};border-radius:8px;'
                f'{border}padding:5px 2px;cursor:default" '
                f'title="{d} — {st_val or t("attn.status_unmarked")}">'
                f'<div style="font-size:12px;font-weight:700;color:{color}">{d.day}</div>'
                f'<div style="font-size:11px;color:{color}">{icon}</div>'
                f'</div></td>'
            )
        body += "</tr>"

    legend_items = [
        ("present", t("attn.status_present")),
        ("absent",  t("attn.status_absent")),
        ("late",    t("attn.status_late")),
        ("holiday", t("attn.status_holiday")),
    ]
    legend = "".join(
        f'<span style="background:{STATUS_BG[s]};color:{STATUS_COLOR[s]};'
        f'border-radius:4px;padding:3px 10px;font-size:10px;'
        f'font-weight:700;margin-right:6px;border:1px solid {STATUS_COLOR[s]}30">'
        f'{STATUS_ICON[s]} {lbl}</span>'
        for s, lbl in legend_items
    )

    return flatten_html(f"""
    <div style="background:white;border:1px solid #DDE3E7;border-radius:12px;
                overflow:hidden;margin-bottom:1rem">
      <div style="background:#0F4C5C;color:white;padding:8px 14px;font-size:12px;font-weight:600">
        🗓 {t("attn.calendar_month_title", month_year=date(year, month, 1).strftime('%B %Y'))}
      </div>
      <table style="width:100%;border-collapse:collapse;padding:8px">
        <thead><tr>{header}</tr></thead>
        <tbody>{body}</tbody>
      </table>
      <div style="padding:10px 14px;border-top:1px solid #EEE;background:#FAFAFA">
        {legend}
      </div>
    </div>""")


# ─────────────────────────────────────────────────────────────────
# স্ট্রিক বার (৩০ দিন)
# ─────────────────────────────────────────────────────────────────

def _streak_bar_html(logs: list) -> str:
    today   = date.today()
    log_map = {r["date"]: r["status"] for r in logs}

    dots = ""
    streak_count = 0
    for i in range(29, -1, -1):
        d      = today - timedelta(days=i)
        status = log_map.get(d)
        color  = STATUS_COLOR.get(status, "#E0E0E0")
        icon   = STATUS_ICON.get(status, "·")
        if status == "present":
            streak_count += 1

        dots += (
            f'<div title="{d} — {status or t("attn.status_unmarked")}" '
            f'style="width:18px;height:18px;border-radius:4px;'
            f'background:{color};display:inline-flex;'
            f'align-items:center;justify-content:center;'
            f'font-size:9px;color:white;margin:1px;'
            f'cursor:help;font-weight:700">'
            f'{"" if status else "·"}</div>'
        )

    return flatten_html(f"""
    <div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                padding:12px 14px;margin-bottom:0.75rem">
      <div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <span style="font-size:12px;color:#6B7A8D;font-weight:600">
          {t("attn.last_30_days_streak_short")}
        </span>
        <span style="font-size:12px;font-weight:700;color:#0F4C5C">
          {t("attn.streak_days_present", n=streak_count)}
        </span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:2px">{dots}</div>
      <div style="display:flex;justify-content:space-between;
                  margin-top:6px;font-size:10px;color:#9E9E9E">
        <span>{t("attn.days_ago_30")}</span><span>{t("attn.today_label")}</span>
      </div>
    </div>""")


# ─────────────────────────────────────────────────────────────────
# প্রিন্টযোগ্য হাজিরা শিট HTML
# ─────────────────────────────────────────────────────────────────

def _printable_sheet_html(tenant, class_name, session_name, att_date, students, today_att):
    rows = ""
    STATUS_LABEL = {
        "present": t("attn.print_status_present"),
        "absent":  t("attn.print_status_absent"),
        "late":    t("attn.print_status_late"),
        "holiday": t("attn.print_status_holiday"),
    }
    for s in students:
        status     = today_att.get(s["enrollment_id"], "—")
        color      = STATUS_COLOR.get(status, "#555")
        status_lbl = STATUS_LABEL.get(status, t("attn.status_unmarked"))
        rows += f"""<tr>
          <td style="text-align:center">{s['roll_no'] or '—'}</td>
          <td>{s['name']}</td>
          <td>{s.get('father_name','') or '—'}</td>
          <td style="text-align:center;color:{color};font-weight:600">{status_lbl}</td>
          <td style="text-align:center">_______</td>
        </tr>"""

    return flatten_html(f"""
    <div style="font-family:Inter,sans-serif;color:#1A2332;max-width:700px;margin:0 auto">
      <div style="text-align:center;border-bottom:2px solid #0F4C5C;padding-bottom:12px;margin-bottom:12px">
        <div style="font-size:18px;font-weight:700;color:#0F4C5C">
          🕌 {tenant.get('madrasa_name','Smart Madrasa')}
        </div>
        <div style="font-size:13px;font-weight:600;margin-top:4px">{t("attn.print_sheet_title")}</div>
        <div style="font-size:11px;color:#555;margin-top:2px">
          {t("attn.print_sheet_meta", class_name=class_name, session_name=session_name, att_date=att_date)}
        </div>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="background:#0F4C5C;color:white">
            <th style="padding:6px;width:8%">{t("attn.col_roll")}</th>
            <th style="padding:6px;text-align:left;width:30%">{t("attn.col_name")}</th>
            <th style="padding:6px;text-align:left;width:27%">{t("attn.col_father_name")}</th>
            <th style="padding:6px;width:20%">{t("attn.col_status")}</th>
            <th style="padding:6px;width:15%">{t("attn.col_signature")}</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <table style="width:100%;margin-top:24px;font-size:11px;border-collapse:collapse">
        <tr>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:40px;padding-top:4px">
              {t("attn.class_teacher_signature")}
            </div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:40px;padding-top:4px">
              {t("attn.date_blank_line")}
            </div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:40px;padding-top:4px">
              {t("attn.headteacher_seal")}
            </div>
          </td>
        </tr>
      </table>
    </div>""")


# ─────────────────────────────────────────────────────────────────
# Main Render
# ─────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()

    page_header("📲", t("attn.da_page_title"),
                t("attn.da_page_subtitle"))

    # ── ফিল্টার বার ──
    sessions = fetchall(
        "SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC",
        (tid,),
    )
    classes = fetchall(
        "SELECT id, class_name, class_numeric FROM classes WHERE tenant_id=%s ORDER BY class_numeric",
        (tid,),
    )
    if not sessions or not classes:
        alert(t("attn.no_sessions_classes_msg"), "warning")
        return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]:   c["id"] for c in classes}

    fc1, fc2, fc3 = st.columns(3)
    sel_sess  = fc1.selectbox(t("attn.session_label"),  list(sess_map.keys()), key="da_sess")
    sel_class = fc2.selectbox(t("attn.class_label"), list(class_map.keys()), key="da_class")
    att_date  = fc3.date_input(t("attn.filter_date"), value=date.today(), key="da_date")

    session_id = sess_map[sel_sess]
    class_id   = class_map[sel_class]
    date_str   = str(att_date)
    students   = _get_students_full(tid, session_id, class_id)
    today_att  = _day_att(tid, date_str, class_id, session_id)
    all_hols   = _get_holidays(tid)
    holidays   = {k: v for k, v in all_hols.items()}

    # ── লাইভ KPI ──
    total    = len(students)
    present  = sum(1 for v in today_att.values() if v == "present")
    absent   = sum(1 for v in today_att.values() if v == "absent")
    late     = sum(1 for v in today_att.values() if v == "late")
    unmarked = total - len(today_att)
    pct_done = int(len(today_att) / total * 100) if total else 0

    st.markdown(
        flatten_html(f"""<div style="display:grid;grid-template-columns:repeat(5,1fr);
                        gap:10px;margin-bottom:1rem">
          <div style="background:#0F4C5C;color:white;border-radius:10px;
                      padding:12px;text-align:center">
            <div style="font-size:1.8rem;font-weight:700">{total}</div>
            <div style="font-size:0.7rem;opacity:0.8">{t("attn.total_students")}</div>
          </div>
          <div style="background:#E8F5E9;border:2px solid #2E7D32;border-radius:10px;
                      padding:12px;text-align:center">
            <div style="font-size:1.8rem;font-weight:700;color:#2E7D32">{present}</div>
            <div style="font-size:0.7rem;color:#2E7D32">{t("attn.opt_present")}</div>
          </div>
          <div style="background:#FFEBEE;border:2px solid #C62828;border-radius:10px;
                      padding:12px;text-align:center">
            <div style="font-size:1.8rem;font-weight:700;color:#C62828">{absent}</div>
            <div style="font-size:0.7rem;color:#C62828">{t("attn.opt_absent")}</div>
          </div>
          <div style="background:#FFF8E1;border:2px solid #F57F17;border-radius:10px;
                      padding:12px;text-align:center">
            <div style="font-size:1.8rem;font-weight:700;color:#F57F17">{late}</div>
            <div style="font-size:0.7rem;color:#F57F17">{t("attn.opt_late")}</div>
          </div>
          <div style="background:#F3F9F3;border:2px solid #DDD;border-radius:10px;
                      padding:12px;text-align:center">
            <div style="font-size:1.8rem;font-weight:700;color:#555">{unmarked}</div>
            <div style="font-size:0.7rem;color:#555">⬜ {t("attn.status_unmarked")}</div>
          </div>
        </div>

        <!-- Progress bar -->
        <div style="background:white;border:1px solid #DDE3E7;border-radius:8px;
                    padding:10px 14px;margin-bottom:1rem">
          <div style="display:flex;justify-content:space-between;
                      font-size:12px;color:#6B7A8D;margin-bottom:6px">
            <span>{t("attn.today_progress")}</span>
            <span style="font-weight:700;color:#0F4C5C">{t("attn.pct_complete", pct=pct_done)}</span>
          </div>
          <div style="background:#EEE;border-radius:20px;height:10px;overflow:hidden">
            <div style="background:{'#2E7D32' if pct_done==100 else '#0F4C5C'};
                        height:100%;width:{pct_done}%;border-radius:20px;
                        transition:width 0.5s"></div>
          </div>
          {"<div style='text-align:center;color:#2E7D32;font-size:11px;font-weight:700;margin-top:4px'>" + t("attn.all_marked_done") + "</div>" if pct_done == 100 else ""}
        </div>"""),
        unsafe_allow_html=True,
    )

    # ── ট্যাব ──
    tab_qr, tab_punch, tab_roll, tab_cal, tab_tracker, tab_teacher, tab_print, tab_holiday, tab_report = st.tabs([
        t("attn.tab_qr_card"),
        t("attn.tab_qr_punch"),
        t("attn.tab_roll_call"),
        t("attn.tab_calendar"),
        t("attn.tab_streak_tracker"),
        t("attn.tab_teacher_attendance"),
        t("attn.tab_print_sheet"),
        t("attn.tab_holiday_mgmt"),
        t("attn.tab_monthly_report"),
    ])

    # ════════════════════════════════
    # ট্যাব ১ — QR ID কার্ড
    # ════════════════════════════════
    with tab_qr:
        st.markdown("#### 🪪 QR কোড ID কার্ড জেনারেটর")
        alert("প্রতিটি ছাত্রের জন্য QR কোড সহ ID কার্ড তৈরি করুন। প্রিন্ট করে ল্যামিনেট করুন।", "info")

        if not students:
            alert("এই শ্রেণীতে কোনো ছাত্র নেই।", "warning")
        else:
            stu_map = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students}
            col_sel, col_all = st.columns([3, 1])
            sel_stu = col_sel.selectbox("ছাত্র নির্বাচন", list(stu_map.keys()), key="qr_stu")
            show_all = col_all.checkbox("সব ছাত্র একসাথে")

            tenant = fetchone("SELECT * FROM tenants WHERE id=%s", (tid,)) or {}

            if show_all:
                st.markdown("**সব ছাত্রের ID কার্ড (প্রিন্টের জন্য)**")
                cols = st.columns(2)
                for i, stu in enumerate(students):
                    qr_data = __import__("qr_token").sign(tid, stu["enrollment_id"])  # Fix: signed QR
                    try:
                        qr_b64 = _generate_qr_base64(qr_data)
                        cols[i % 2].markdown(
                            _id_card_html(stu, tenant, qr_b64),
                            unsafe_allow_html=True,
                        )
                        cols[i % 2].markdown("<br>", unsafe_allow_html=True)
                    except Exception:
                        cols[i % 2].warning(f"{stu['name']}: QR তৈরি ব্যর্থ")
            else:
                stu = stu_map[sel_stu]
                qr_data = __import__("qr_token").sign(tid, stu["enrollment_id"])  # Fix: signed QR
                try:
                    qr_b64 = _generate_qr_base64(qr_data)
                    c1, c2 = st.columns([1, 2])
                    with c1:
                        st.markdown(
                            _id_card_html(stu, tenant, qr_b64),
                            unsafe_allow_html=True,
                        )
                    with c2:
                        st.markdown(f"""
                        **QR কোড তথ্য:**
                        - **ছাত্র ID:** STU-{stu['id']:05d}
                        - **রোল নং:** {stu.get('roll_no') or '—'}
                        - **শ্রেণী:** {stu.get('class_name','')}
                        - **QR ডেটা:** `{qr_data}`

                        **ব্যবহার পদ্ধতি:**
                        ১. ID কার্ড প্রিন্ট করুন
                        ২. ল্যামিনেট করুন
                        ৩. QR Scanner দিয়ে স্ক্যান করুন
                        ৪. বা Roll No টাইপ করে পাঞ্চ-ইন করুন
                        """)
                except ImportError:
                    alert("QR তৈরির জন্য `pip install qrcode[pil]` চালান।", "warning")
                    st.code("pip install qrcode[pil]")

    # ════════════════════════════════
    # ট্যাব ২ — QR পাঞ্চ-ইন
    # ════════════════════════════════
    with tab_punch:
        st.markdown("#### 📲 ডিজিটাল QR পাঞ্চ-ইন সিস্টেম")
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0F4C5C,#1a7a96);
                            border-radius:12px;padding:16px 20px;color:white;margin-bottom:1rem">
              <div style="font-size:14px;font-weight:700;margin-bottom:4px">
                📡 লাইভ স্ক্যান মোড — {att_date.strftime('%d %B %Y, %A')}
              </div>
              <div style="font-size:11px;opacity:0.8">
                QR স্ক্যান করুন অথবা Roll No / নাম টাইপ করে তাৎক্ষণিক হাজিরা দিন
              </div>
            </div>""",
            unsafe_allow_html=True,
        )

        if not students:
            alert("এই শ্রেণীতে কোনো ছাত্র নেই।", "warning")
        else:
            # স্ক্যান ইনপুট
            p1, p2, p3 = st.columns([3, 2, 1])
            punch_q = p1.text_input(
                "🔍 Roll No / নাম / QR কোড",
                placeholder="Roll No বা নামের অংশ লিখুন…",
                key="punch_q",
                label_visibility="collapsed",
            )
            punch_status = p2.selectbox(
                "অবস্থা",
                ["present", "absent", "late", "holiday"],
                format_func=lambda x: {
                    "present": "✅ উপস্থিত",
                    "absent":  "❌ অনুপস্থিত",
                    "late":    "⏰ দেরিতে",
                    "holiday": "🌿 ছুটি",
                }[x],
                key="punch_status",
                label_visibility="collapsed",
            )
            auto_present = p3.checkbox("↵ Enter-এ Present", value=True, key="auto_p")

            if punch_q:
                q = punch_q.strip().lower()
                matched = [
                    s for s in students
                    if q == str(s["roll_no"] or "").lower()
                    or q in (s["name"] or "").lower()
                    or q in f"stu-{s['id']:05d}"
                ]

                if matched:
                    for m in matched:
                        curr   = today_att.get(m["enrollment_id"], None)
                        c_icon = {"present":"✅","absent":"❌","late":"⏰",
                                  "holiday":"🌿",None:"⬜"}.get(curr,"⬜")
                        c_text = curr or "অচিহ্নিত"

                        mc1, mc2, mc3 = st.columns([3, 2, 1])
                        mc1.markdown(
                            f"""<div style="background:white;border:1px solid #DDE3E7;
                                            border-radius:8px;padding:10px 14px">
                              <div style="font-weight:700;font-size:14px">{m['name']}</div>
                              <div style="color:#6B7A8D;font-size:12px">
                                Roll {m['roll_no'] or '—'} | {m.get('class_name','')}
                              </div>
                              <div style="margin-top:4px;font-size:12px">
                                বর্তমান: {c_icon} <strong>{c_text}</strong>
                              </div>
                            </div>""",
                            unsafe_allow_html=True,
                        )
                        if mc2.button(
                            f"✔ {punch_status.upper()}",
                            key=f"punch_{m['enrollment_id']}",
                            type="primary",
                            use_container_width=True,
                        ):
                            if _punch(tid, m["enrollment_id"], date_str, punch_status):
                                st.toast(f"✅ {m['name']} → {punch_status}", icon="✅")
                                st.rerun()
                else:
                    st.warning("কোনো ছাত্র পাওয়া যায়নি।")

            divider()
            # বাল্ক কুইক বাটন
            st.markdown("**⚡ বাল্ক কুইক সেট**")
            b1, b2, b3, b4 = st.columns(4)
            if b1.button("✅ সবাই উপস্থিত", use_container_width=True, type="primary"):
                bulk = [{"enrollment_id": s["enrollment_id"], "status": "present"} for s in students]
                ok, _ = _bulk_save(tid, date_str, bulk)
                if ok:
                    st.success("সবাইকে উপস্থিত চিহ্নিত করা হয়েছে!"); st.rerun()

            if b2.button("❌ সবাই অনুপস্থিত", use_container_width=True):
                bulk = [{"enrollment_id": s["enrollment_id"], "status": "absent"} for s in students]
                ok, _ = _bulk_save(tid, date_str, bulk)
                if ok:
                    st.warning("সবাইকে অনুপস্থিত করা হয়েছে।"); st.rerun()

            if b3.button("🌿 ছুটির দিন", use_container_width=True):
                bulk = [{"enrollment_id": s["enrollment_id"], "status": "holiday"} for s in students]
                ok, _ = _bulk_save(tid, date_str, bulk)
                if ok:
                    st.info("ছুটির দিন হিসেবে সেট।"); st.rerun()

            if b4.button("🔄 রিফ্রেশ", use_container_width=True):
                st.rerun()

            # স্ট্যাটাস লিস্ট
            divider()
            st.markdown("**আজকের হাজিরা তালিকা:**")
            for s in students:
                curr   = today_att.get(s["enrollment_id"])
                c_icon = {"present":"✅","absent":"❌","late":"⏰","holiday":"🌿"}.get(curr,"⬜")
                c_bg   = STATUS_BG.get(curr, "#F5F5F5")
                st.markdown(
                    f"""<div style="background:{c_bg};border-radius:6px;
                                    padding:7px 12px;margin-bottom:3px;
                                    display:flex;justify-content:space-between;align-items:center">
                      <span style="font-weight:600;font-size:13px">
                        <span style="color:#6B7A8D;margin-right:8px">Roll {s['roll_no'] or '—'}</span>
                        {s['name']}
                      </span>
                      <span style="font-size:13px">{c_icon} {curr or '⬜ অচিহ্নিত'}</span>
                    </div>""",
                    unsafe_allow_html=True,
                )

    # ════════════════════════════════
    # ট্যাব ৩ — ক্লাসিক রোল কল
    # ════════════════════════════════
    with tab_roll:
        st.markdown(f"#### 📋 রোল কল — {sel_class} | {att_date.strftime('%d %B %Y')}")

        if att_date in holidays:
            alert(f"🌿 আজ ছুটি: **{holidays[att_date]}**", "info")

        if not students:
            alert("ছাত্র নেই।", "info")
        else:
            STATUS_OPT = ["present", "absent", "late", "holiday"]
            STATUS_LABEL = {
                "present": "✅ উপস্থিত",
                "absent":  "❌ অনুপস্থিত",
                "late":    "⏰ দেরিতে",
                "holiday": "🌿 ছুটি",
            }
            with st.form("roll_form"):
                for s in students:
                    existing = today_att.get(s["enrollment_id"], "present")
                    row_bg   = STATUS_BG.get(existing, "#F5F5F5")
                    c1, c2, c3 = st.columns([1, 4, 3])
                    c1.markdown(
                        f'<div style="background:{row_bg};padding:8px;border-radius:6px;'
                        f'text-align:center;font-weight:700;color:#0F4C5C;font-size:13px">'
                        f'{s["roll_no"] or "—"}</div>',
                        unsafe_allow_html=True,
                    )
                    c2.markdown(
                        f'<div style="background:{row_bg};padding:8px 12px;border-radius:6px;'
                        f'font-size:13px">{s["name"]}</div>',
                        unsafe_allow_html=True,
                    )
                    c3.selectbox(
                        "status", STATUS_OPT,
                        index=STATUS_OPT.index(existing) if existing in STATUS_OPT else 0,
                        format_func=lambda x: STATUS_LABEL[x],
                        key=f"rc_{s['enrollment_id']}",
                        label_visibility="collapsed",
                    )

                if st.form_submit_button("💾 হাজিরা সেভ করুন", type="primary", use_container_width=True):
                    records = [
                        {
                            "enrollment_id": s["enrollment_id"],
                            "status": st.session_state.get(f"rc_{s['enrollment_id']}", "present"),
                        }
                        for s in students
                    ]
                    ok, result = _bulk_save(tid, date_str, records)
                    if ok:
                        p = sum(1 for r in records if r["status"] == "present")
                        a = sum(1 for r in records if r["status"] == "absent")
                        st.success(f"✅ হাজিরা সেভ! উপস্থিত: {p} | অনুপস্থিত: {a}")
                        st.rerun()
                    else:
                        st.error(f"ব্যর্থ: {result}")

    # ════════════════════════════════
    # ট্যাব ৪ — ক্যালেন্ডার হিটম্যাপ
    # ════════════════════════════════
    with tab_cal:
        st.markdown("#### 🗓 মাসিক ক্যালেন্ডার হিটম্যাপ")
        cc1, cc2 = st.columns(2)
        cal_yr  = cc1.number_input("বছর",  min_value=2020, max_value=2040,
                                    value=date.today().year,  key="cal_yr")
        cal_mon = cc2.selectbox("মাস", list(range(1, 13)),
                                 index=date.today().month - 1,
                                 format_func=lambda m: date(2000, m, 1).strftime("%B"),
                                 key="cal_mon")

        if not students:
            alert("ছাত্র নেই।", "info")
        else:
            stu_opts = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students}
            sel_cal  = st.selectbox("ছাত্র নির্বাচন", list(stu_opts.keys()), key="cal_stu")
            cal_stu  = stu_opts[sel_cal]

            st.markdown(
                _calendar_heatmap_html(
                    tid, cal_stu["enrollment_id"],
                    int(cal_yr), int(cal_mon), holidays,
                ),
                unsafe_allow_html=True,
            )

            # মাসিক সারসংক্ষেপ
            logs_cal = fetchall(
                """SELECT status, COUNT(*) AS n FROM attendance
                   WHERE tenant_id=%s AND enrollment_id=%s
                     AND EXTRACT(YEAR FROM date)=%s AND EXTRACT(MONTH FROM date)=%s
                   GROUP BY status""",
                (tid, cal_stu["enrollment_id"], int(cal_yr), int(cal_mon)),
            )
            if logs_cal:
                sd = {r["status"]: int(r["n"]) for r in logs_cal}
                tot = sum(sd.values())
                pre = sd.get("present", 0)
                pct = round(pre / tot * 100, 1) if tot else 0
                kpi_row([
                    {"label": "উপস্থিত",     "value": pre,                   "cls": "success"},
                    {"label": "অনুপস্থিত",   "value": sd.get("absent", 0),   "cls": "danger"},
                    {"label": "দেরিতে",       "value": sd.get("late", 0),     "cls": "warning"},
                    {"label": "হাজিরা হার",   "value": f"{pct}%",
                     "cls": "success" if pct >= 75 else "danger"},
                ])
                if pct < 75:
                    alert("⚠️ হাজিরা ৭৫%-এর নিচে! পরীক্ষায় অংশগ্রহণ ঝুঁকিতে পড়তে পারে।", "danger")

    # ════════════════════════════════
    # ট্যাব ৫ — স্ট্রিক ট্র্যাকার
    # ════════════════════════════════
    with tab_tracker:
        st.markdown("#### 👤 ব্যক্তিগত হাজিরা স্ট্রিক ট্র্যাকার")

        if not students:
            alert("ছাত্র নেই।", "info")
        else:
            stu_opts2 = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students}
            sel_trk   = st.selectbox("ছাত্র নির্বাচন", list(stu_opts2.keys()), key="trk_stu")
            trk_stu   = stu_opts2[sel_trk]
            logs_trk  = _student_log(tid, trk_stu["enrollment_id"], days=60)

            st.markdown(_streak_bar_html(logs_trk), unsafe_allow_html=True)

            if logs_trk:
                tot3  = len(logs_trk)
                pre3  = sum(1 for l in logs_trk if l["status"] == "present")
                abs3  = sum(1 for l in logs_trk if l["status"] == "absent")
                lat3  = sum(1 for l in logs_trk if l["status"] == "late")
                pct3  = round(pre3 / tot3 * 100, 1) if tot3 else 0

                kpi_row([
                    {"label": "মোট দিন",      "value": tot3,  "cls": ""},
                    {"label": "উপস্থিত",      "value": pre3,  "cls": "success"},
                    {"label": "অনুপস্থিত",    "value": abs3,  "cls": "danger"},
                    {"label": "দেরিতে",        "value": lat3,  "cls": "warning"},
                    {"label": "হাজিরা হার",   "value": f"{pct3}%",
                     "cls": "success" if pct3 >= 75 else "danger"},
                ])

                if _absent_streak(tid, trk_stu["enrollment_id"], days=5):
                    alert(
                        f"🚨 <b>{trk_stu['name']}</b> গত ৫ দিন ধরে একটানা অনুপস্থিত! "
                        "অভিভাবকের সাথে দ্রুত যোগাযোগ করুন।",
                        "danger",
                    )

                # Late alerts
                late_week = _late_alerts(tid, session_id, class_id)
                if late_week:
                    divider()
                    st.markdown("**⚠️ গত সপ্তাহে বারবার দেরি করেছে:**")
                    for l in late_week:
                        st.markdown(
                            f'<div style="background:#FFF8E1;border-left:4px solid #F57F17;'
                            f'border-radius:6px;padding:8px 14px;margin-bottom:6px">'
                            f'⏰ <b>{l["name"]}</b> (Roll {l["roll_no"] or "—"}) — '
                            f'<span style="color:#F57F17;font-weight:700">{l["late_count"]} বার দেরি</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                import pandas as pd
                divider()
                st.markdown("**সর্বশেষ ৬০ দিনের লগ**")
                df = pd.DataFrame([
                    {
                        "তারিখ":  str(l["date"]),
                        "বার":    l["date"].strftime("%A") if hasattr(l["date"], "strftime") else "",
                        "অবস্থা": l["status"].title(),
                        "চিহ্ন":  STATUS_ICON.get(l["status"], "—"),
                    }
                    for l in logs_trk
                ])
                st.dataframe(df, use_container_width=True, hide_index=True, height=300)

    # ════════════════════════════════
    # ট্যাব ৬ — শিক্ষক হাজিরা
    # ════════════════════════════════
    with tab_teacher:
        st.markdown(f"#### 👨‍🏫 শিক্ষক হাজিরা — {att_date.strftime('%d %B %Y')}")

        teachers = _get_teachers_active(tid)
        if not teachers:
            alert("কোনো সক্রিয় শিক্ষক নেই। প্রথমে Teacher মডিউলে শিক্ষক যোগ করুন।", "warning")
        else:
            tch_att = _teacher_day_att(tid, date_str)
            t_present = sum(1 for v in tch_att.values() if v["status"] == "present")
            t_absent  = sum(1 for v in tch_att.values() if v["status"] == "absent")

            kpi_row([
                {"label": "মোট শিক্ষক",  "value": len(teachers), "cls": ""},
                {"label": "উপস্থিত",     "value": t_present,     "cls": "success"},
                {"label": "অনুপস্থিত",   "value": t_absent,      "cls": "danger"},
                {"label": "অচিহ্নিত",    "value": len(teachers) - len(tch_att), "cls": ""},
            ])

            with st.form("teacher_att_form"):
                for tch in teachers:
                    existing_t = tch_att.get(tch["id"], {}).get("status", "present")
                    punch_t    = tch_att.get(tch["id"], {}).get("punch_time", "—")

                    c1, c2, c3 = st.columns([3, 2, 2])
                    c1.markdown(
                        f'<div style="padding:8px 12px;background:#F7F9FA;'
                        f'border-radius:6px;font-weight:600;font-size:13px">'
                        f'{tch["name"]}<br>'
                        f'<span style="color:#6B7A8D;font-size:11px;font-weight:400">'
                        f'{tch.get("designation","Teacher")}</span></div>',
                        unsafe_allow_html=True,
                    )
                    c2.selectbox(
                        "status",
                        ["present", "absent", "late", "leave"],
                        index=["present", "absent", "late", "leave"].index(existing_t)
                        if existing_t in ["present", "absent", "late", "leave"] else 0,
                        format_func=lambda x: {
                            "present": "✅ উপস্থিত",
                            "absent":  "❌ অনুপস্থিত",
                            "late":    "⏰ দেরিতে",
                            "leave":   "📋 ছুটিতে",
                        }[x],
                        key=f"tch_att_{tch['id']}",
                        label_visibility="collapsed",
                    )
                    c3.markdown(
                        f'<div style="padding:8px;font-size:11px;color:#6B7A8D">'
                        f'পাঞ্চ সময়: {str(punch_t)[:5] if punch_t and punch_t != "—" else "—"}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                if st.form_submit_button("💾 শিক্ষক হাজিরা সেভ করুন", type="primary", use_container_width=True):
                    saved = 0
                    for tch in teachers:
                        st_val = st.session_state.get(f"tch_att_{tch['id']}", "present")
                        if _punch_teacher(tid, tch["id"], date_str, st_val):
                            saved += 1
                    st.success(f"✅ {saved} জন শিক্ষকের হাজিরা সেভ হয়েছে!")
                    st.rerun()

    # ════════════════════════════════
    # ট্যাব ৭ — প্রিন্টযোগ্য হাজিরা শিট
    # ════════════════════════════════
    with tab_print:
        st.markdown("#### 🖨️ প্রিন্টযোগ্য হাজিরা শিট")

        if not students:
            alert("ছাত্র নেই।", "info")
        else:
            tenant = fetchone("SELECT * FROM tenants WHERE id=%s", (tid,)) or {}

            # নোট: raw onclick="..." স্ট্রিং st.markdown() দিয়ে রেন্ডার করলে
            # ক্লিকে React error #231 ("Expected onClick listener to be a
            # function") ছোঁড়ে — তাই real <iframe> ভিত্তিক print_button()
            # ব্যবহার করা হয়েছে (utils.py, st.components.v1.html())।
            print_button("🖨️ প্রিন্ট / PDF সেভ করুন", doc_id="hajira_print")

            st.markdown(
                f'<div id="hajira_print">'
                f'{_printable_sheet_html(tenant, sel_class, sel_sess, att_date, students, today_att)}'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ════════════════════════════════
    # ট্যাব ৮ — ছুটি ব্যবস্থাপনা
    # ════════════════════════════════
    with tab_holiday:
        st.markdown("#### 🌿 ছুটির দিন ব্যবস্থাপনা")

        with st.form("holiday_form"):
            h1, h2 = st.columns(2)
            h_date = h1.date_input("তারিখ", value=date.today(), key="hol_date")
            h_desc = h2.text_input("বিবরণ", placeholder="যেমন: ঈদুল ফিতর", key="hol_desc")
            if st.form_submit_button("➕ ছুটি যোগ করুন", type="primary"):
                if not h_desc.strip():
                    st.error("বিবরণ দিন।")
                elif _save_holiday(tid, str(h_date), h_desc.strip()):
                    st.success(f"✅ {h_date} — {h_desc} যোগ হয়েছে!")
                    st.rerun()

        divider()
        all_hols_list = fetchall(
            "SELECT holiday_date, description FROM holidays WHERE tenant_id=%s ORDER BY holiday_date DESC",
            (tid,),
        )
        if not all_hols_list:
            alert("কোনো ছুটির দিন যোগ করা হয়নি।", "info")
        else:
            for h in all_hols_list:
                hc1, hc2 = st.columns([4, 1])
                hc1.markdown(f"🌿 **{str(h['holiday_date'])}** — {h['description']}")
                if hc2.button("🗑 মুছুন", key=f"dh_{h['holiday_date']}"):
                    _delete_holiday(tid, str(h["holiday_date"]))
                    st.rerun()

    # ════════════════════════════════
    # ট্যাব ৯ — মাসিক রিপোর্ট
    # ════════════════════════════════
    with tab_report:
        st.markdown("#### 📊 মাসিক হাজিরা রিপোর্ট")
        rc1, rc2 = st.columns(2)
        rep_yr  = rc1.number_input("বছর",  min_value=2020, max_value=2040,
                                    value=date.today().year,  key="rep2_yr")
        rep_mon = rc2.selectbox("মাস", list(range(1, 13)),
                                 index=date.today().month - 1,
                                 format_func=lambda m: date(2000, m, 1).strftime("%B"),
                                 key="rep2_mon")

        if st.button("🔄 রিপোর্ট তৈরি করুন", type="primary", key="gen_rep2"):
            summary = _monthly_summary(
                tid, class_id, session_id, int(rep_yr), int(rep_mon)
            )
            if not summary:
                alert("এই মাসে কোনো হাজিরা ডেটা নেই।", "info")
            else:
                import pandas as pd
                rows = []
                for r in summary:
                    tot = int(r["total_marked"] or 0)
                    pre = int(r["present"] or 0)
                    pct = round(pre / tot * 100, 1) if tot else 0
                    rows.append({
                        "রোল":       r["roll_no"] or "—",
                        "নাম":       r["name"],
                        "উপস্থিত":  pre,
                        "অনুপস্থিত": int(r["absent"] or 0),
                        "দেরিতে":   int(r["late"] or 0),
                        "মোট দিন":  tot,
                        "হার %":    f"{pct}%",
                        "মন্তব্য":  "✅ ভালো" if pct >= 75 else ("⚠️ কম" if pct >= 50 else "❌ সংকটাপন্ন"),
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)

                df = pd.DataFrame(rows)
                df["হার"] = df["হার %"].str.replace("%", "").astype(float)
                st.bar_chart(df.set_index("নাম")[["হার"]],
                             color=PALETTE["primary"], height=280)

                # CSV এক্সপোর্ট — formula-injection-safe কপি (উপরের টেবিল/চার্ট অপরিবর্তিত রাখতে)
                import csv, io as sio
                csv_rows = [{**r, "নাম": csv_safe(r["নাম"])} for r in rows]
                buf = sio.StringIO()
                writer = csv.DictWriter(buf, fieldnames=csv_rows[0].keys())
                writer.writeheader()
                writer.writerows(csv_rows)
                st.download_button(
                    "📥 CSV ডাউনলোড করুন",
                    buf.getvalue().encode("utf-8-sig"),
                    file_name=f"hajira_{sel_class}_{rep_yr}_{rep_mon:02d}.csv",
                    mime="text/csv",
                    type="primary",
                )
