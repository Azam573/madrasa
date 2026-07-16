"""
timetable_module.py — ক্লাস রুটিন বিল্ডার
ড্র্যাগ-অ্যান্ড-ড্রপ স্টাইল রুটিন তৈরি, প্রিন্টযোগ্য।
"""

import streamlit as st
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id, flatten_html
from i18n import t, weekday_name
import audit_module

DAYS_EN = ["Saturday","Sunday","Monday","Tuesday","Wednesday","Thursday"]
_DAY_WD_IDX = {"Saturday": 5, "Sunday": 6, "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3}

def _display_days():
    """DAYS_EN-এর ক্রম অনুযায়ী বর্তমান ভাষায় দিনের নাম (weekday_name ব্যবহার করে)।"""
    return [weekday_name(_DAY_WD_IDX[d]) for d in DAYS_EN]

def _get_timetable(tid, class_id, session_id):
    rows = fetchall(
        """SELECT t.day_of_week, t.period_no, t.start_time, t.end_time,
                  s.subject_name, tc.name AS teacher_name, t.room_no
           FROM timetable t
           LEFT JOIN subjects s ON s.id=t.subject_id
           LEFT JOIN teachers tc ON tc.id=t.teacher_id
           WHERE t.tenant_id=%s AND t.class_id=%s AND t.session_id=%s
           ORDER BY t.day_of_week, t.period_no""",
        (tid, class_id, session_id),
    )
    # Build dict: day -> {period -> data}
    grid = {d: {} for d in DAYS_EN}
    for r in rows:
        grid[r["day_of_week"]][r["period_no"]] = r
    return grid

def _save_slot(tid, class_id, session_id, day, period, start, end, subj_id, teacher_id, room):
    conn = get_connection()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO timetable
                   (tenant_id, class_id, session_id, day_of_week, period_no,
                    start_time, end_time, subject_id, teacher_id, room_no)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, class_id, session_id, day_of_week, period_no)
                   DO UPDATE SET start_time=%s, end_time=%s,
                     subject_id=%s, teacher_id=%s, room_no=%s""",
                (tid, class_id, session_id, day, period, start, end,
                 subj_id, teacher_id, room,
                 start, end, subj_id, teacher_id, room),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)

def _delete_slot(tid, class_id, session_id, day, period):
    conn = get_connection()
    if not conn: return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM timetable WHERE tenant_id=%s AND class_id=%s AND session_id=%s AND day_of_week=%s AND period_no=%s",
                (tid, class_id, session_id, day, period),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)

def _routine_html(class_name, session_name, grid, periods=7):
    """প্রিন্টযোগ্য রুটিন HTML"""
    header = "".join(
        f'<th style="background:#0F4C5C;color:white;padding:8px;font-size:11px;'
        f'min-width:100px">{d}</th>'
        for d in _display_days()
    )
    period_label = t("tt.period_col")
    rows_html = ""
    for p in range(1, periods+1):
        row = f'<tr><td style="background:#F7F9FA;padding:7px 10px;font-weight:700;'
        row += f'text-align:center;font-size:11px;border:1px solid #DDD">{period_label} {p}</td>'
        for d_en in DAYS_EN:
            slot = grid.get(d_en, {}).get(p)
            if slot and slot.get("subject_name"):
                st_str = str(slot.get("start_time","") or "")[:5]
                en_str = str(slot.get("end_time","")   or "")[:5]
                time_str = f"<div style='font-size:9px;color:#9E9E9E'>{st_str}–{en_str}</div>" if st_str else ""
                row += (
                    f'<td style="background:#EAF4F8;border:1px solid #DDD;'
                    f'padding:6px 8px;font-size:11px;text-align:center">'
                    f'<div style="font-weight:600;color:#0F4C5C">{slot["subject_name"]}</div>'
                    f'<div style="font-size:10px;color:#555">{slot.get("teacher_name") or ""}</div>'
                    f'{time_str}</td>'
                )
            elif d_en == "Thursday":  # Friday break
                row += f'<td style="background:#E8F5E9;border:1px solid #DDD;text-align:center;font-size:10px;color:#2E7D32">{t("tt.friday_jumma")}</td>'
            else:
                row += '<td style="border:1px solid #DDD;background:#FAFAFA"></td>'
        row += "</tr>"
        rows_html += row

    return flatten_html(f"""
    <div style="font-family:Inter,sans-serif;max-width:900px;margin:0 auto">
      <div style="text-align:center;margin-bottom:12px">
        <div style="font-size:16px;font-weight:700;color:#0F4C5C">{t("tt.routine_title")}</div>
        <div style="font-size:12px;color:#555">{class_name} | {t("tt.session_colon", session=session_name)}</div>
      </div>
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="background:#0F4C5C;color:white;padding:8px;font-size:11px;width:80px">{period_label}</th>
          {header}
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>""")


def render():
    tid = get_tenant_id()
    page_header("🗓️", t("tt.header_title"), t("tt.header_subtitle"))

    sessions = fetchall("SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC", (tid,))
    classes  = fetchall("SELECT id, class_name FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,))
    subjects = fetchall("SELECT id, subject_name FROM subjects WHERE tenant_id=%s ORDER BY subject_name", (tid,))
    teachers = fetchall("SELECT id, name FROM teachers WHERE tenant_id=%s AND status='active' ORDER BY name", (tid,)) if True else []

    if not sessions or not classes:
        alert(t("tt.no_session_class"),"warning"); return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]:   c["id"] for c in classes}
    subj_map  = {s["subject_name"]: s["id"] for s in subjects}
    tch_map   = {tc["name"]: tc["id"] for tc in teachers} if teachers else {}

    c1, c2 = st.columns(2)
    sel_sess  = c1.selectbox(t("tt.session_label"),  list(sess_map.keys()), key="tt_sess")
    sel_class = c2.selectbox(t("tt.class_label"), list(class_map.keys()), key="tt_class")

    session_id = sess_map[sel_sess]
    class_id   = class_map[sel_class]
    grid       = _get_timetable(tid, class_id, session_id)

    tab_view, tab_edit, tab_print = st.tabs([t("tt.tab_view"), t("tt.tab_edit"), t("tt.tab_print")])

    display_days = _display_days()

    with tab_view:
        periods = st.slider(t("tt.period_count"), 5, 10, 7, key="tt_periods")
        # Visual grid
        cols = st.columns(len(display_days) + 1)
        cols[0].markdown(f"**{t('tt.period_col')}**")
        for i, d in enumerate(display_days):
            cols[i+1].markdown(f"**{d}**")

        for p in range(1, periods+1):
            cols = st.columns(len(display_days) + 1)
            cols[0].markdown(
                f'<div style="background:#F7F9FA;border-radius:6px;padding:6px;'
                f'text-align:center;font-weight:700;font-size:12px">{p}</div>',
                unsafe_allow_html=True,
            )
            for i, d_en in enumerate(DAYS_EN):
                slot = grid.get(d_en, {}).get(p)
                if slot and slot.get("subject_name"):
                    cols[i+1].markdown(
                        f'<div style="background:#EAF4F8;border-radius:6px;padding:6px;'
                        f'text-align:center;font-size:11px;border:1px solid #B0D4E0">'
                        f'<b style="color:#0F4C5C">{slot["subject_name"]}</b><br>'
                        f'<span style="color:#555;font-size:10px">'
                        f'{slot.get("teacher_name") or ""}</span></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    cols[i+1].markdown(
                        '<div style="background:#F5F5F5;border-radius:6px;'
                        'padding:6px;text-align:center;font-size:10px;color:#CCC">—</div>',
                        unsafe_allow_html=True,
                    )

    with tab_edit:
        st.markdown(f"#### {t('tt.edit_slot_header')}")
        with st.form("tt_edit_form"):
            c1, c2, c3 = st.columns(3)
            sel_day    = c1.selectbox(t("tt.day_label"), DAYS_EN,
                                       format_func=lambda x: display_days[DAYS_EN.index(x)],
                                       key="tt_edit_day")
            sel_period = c2.number_input(t("tt.period_no_label"), min_value=1, max_value=10, value=1)
            room_no    = c3.text_input(t("tt.room_no_label"), placeholder="101")

            c4, c5 = st.columns(2)
            start_t = c4.time_input(t("tt.start_time_label"))
            end_t   = c5.time_input(t("tt.end_time_label"))

            empty_option = t("tt.empty_option")
            none_option  = t("tt.none_option")
            sel_subj = st.selectbox(t("tt.subject_label"), [empty_option] + list(subj_map.keys()))
            sel_tch  = st.selectbox(t("tt.teacher_label"), [none_option] + list(tch_map.keys())) if tch_map else None

            c_save, c_del = st.columns(2)
            saved   = c_save.form_submit_button(t("tt.save_btn"), type="primary", use_container_width=True)
            deleted = c_del.form_submit_button(t("tt.delete_btn"), use_container_width=True)

            if saved:
                subj_id = subj_map.get(sel_subj)
                tch_id  = tch_map.get(sel_tch) if sel_tch else None
                ok = _save_slot(tid, class_id, session_id, sel_day, int(sel_period),
                                str(start_t), str(end_t), subj_id, tch_id, room_no.strip())
                if ok:
                    audit_module.log("UPDATE","Timetable", t("tt.audit_routine_update", class_name=sel_class, day=sel_day, period=sel_period))
                    st.success(t("tt.slot_saved"))
                    st.rerun()
            if deleted:
                _delete_slot(tid, class_id, session_id, sel_day, int(sel_period))
                st.success(t("tt.slot_deleted"))
                st.rerun()

    with tab_print:
        periods_p = st.slider(t("tt.period_count"), 5, 10, 7, key="tt_print_p")
        st.markdown(
            f'<button onclick="window.print()" style="background:#0F4C5C;color:white;'
            f'border:none;padding:8px 20px;border-radius:8px;cursor:pointer;'
            f'font-weight:600;margin-bottom:1rem">{t("tt.print_pdf_btn")}</button>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div id="routine_print">'
            f'{_routine_html(sel_class, sel_sess, grid, periods_p)}'
            f'</div>',
            unsafe_allow_html=True,
        )
