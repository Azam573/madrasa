"""
academic_module.py — Exam management, custom marks distribution,
result processing, and dynamic marksheet visualization.
"""

from error_handler import safe_db_error
import streamlit as st
from db import get_connection, release_connection, fetchall, fetchone
from utils import (
    page_header, kpi_row, alert, divider, badge,
    get_tenant_id, get_grade, PALETTE, flatten_html,
)
import audit_module
from i18n import t

# ──────────────────────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_sessions(tid):
    return fetchall(
        "SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC", (tid,)
    )


def _get_classes(tid):
    return fetchall(
        "SELECT id, class_name, class_numeric FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,)
    )


def _get_exams(tid, session_id):
    return fetchall(
        "SELECT id, exam_name, exam_date FROM exams WHERE tenant_id=%s AND session_id=%s ORDER BY exam_date",
        (tid, session_id),
    )


def _get_subjects(tid, class_id):
    return fetchall(
        "SELECT id, subject_name, subject_code, full_marks, pass_marks FROM subjects "
        "WHERE tenant_id=%s AND (class_id=%s OR class_id IS NULL) ORDER BY id",
        (tid, class_id),
    )


def _get_enrolled_students(tid, session_id, class_id):
    return fetchall(
        """SELECT s.id AS student_id, s.name, e.roll_no, e.id AS enrollment_id
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           WHERE s.tenant_id=%s AND e.session_id=%s AND e.class_id=%s
             AND e.enrollment_status='active'
           ORDER BY e.roll_no""",
        (tid, session_id, class_id),
    )


def _get_marks_dist(tid, exam_id, subject_id):
    return fetchone(
        "SELECT * FROM marks_distribution WHERE tenant_id=%s AND exam_id=%s AND subject_id=%s",
        (tid, exam_id, subject_id),
    )


def _get_student_marks(tid, enrollment_id, exam_id):
    return fetchall(
        """SELECT sm.*, subj.subject_name, subj.full_marks, subj.pass_marks
           FROM student_marks sm
           JOIN subjects subj ON subj.id=sm.subject_id
           WHERE sm.tenant_id=%s AND sm.enrollment_id=%s AND sm.exam_id=%s
           ORDER BY subj.id""",
        (tid, enrollment_id, exam_id),
    )


def _get_class_marks_bulk(tid, exam_id, class_id, session_id):
    """সম্পূর্ণ ক্লাসের সব ছাত্রের marks একটা query-তে — dict[enrollment_id] -> list[mark rows]
    (_get_student_marks()-এর মতোই row shape), যাতে _class_result_summary() ছাত্র-প্রতি
    আলাদা query না চালিয়ে একবারে সব আনতে পারে।"""
    rows = fetchall(
        """SELECT sm.*, subj.subject_name, subj.full_marks, subj.pass_marks
           FROM student_marks sm
           JOIN subjects subj ON subj.id = sm.subject_id
           JOIN student_enrollments e ON e.id = sm.enrollment_id
           WHERE sm.tenant_id=%s AND sm.exam_id=%s
             AND e.class_id=%s AND e.session_id=%s
           ORDER BY sm.enrollment_id, subj.id""",
        (tid, exam_id, class_id, session_id),
    )
    marks_by_enrollment = {}
    for row in rows:
        marks_by_enrollment.setdefault(row["enrollment_id"], []).append(row)
    return marks_by_enrollment


def _class_result_summary(tid, exam_id, class_id, session_id):
    """Returns per-student aggregate for result sheet."""
    students = _get_enrolled_students(tid, session_id, class_id)
    subjects = _get_subjects(tid, class_id)
    if not students or not subjects:
        return []

    marks_by_enrollment = _get_class_marks_bulk(tid, exam_id, class_id, session_id)

    results = []
    for stu in students:
        marks = marks_by_enrollment.get(stu["enrollment_id"], [])
        total_full = sum(s["full_marks"] for s in subjects)
        total_obtained = sum(int(m["total_obtained"] or 0) for m in marks)
        failed_subjects = [
            m for m in marks
            if not m["is_absent"] and int(m["total_obtained"] or 0) < int(m["pass_marks"])
        ]
        absent_count = sum(1 for m in marks if m["is_absent"])
        pct = round(total_obtained / total_full * 100, 2) if total_full else 0
        grade, gpa = get_grade(pct)
        results.append({
            "roll_no": stu["roll_no"],
            "name": stu["name"],
            "total_full": total_full,
            "total_obtained": total_obtained,
            "percentage": pct,
            "grade": grade,
            "gpa": gpa,
            "failed": len(failed_subjects),
            "absent": absent_count,
            "marks": marks,
            "enrollment_id": stu["enrollment_id"],
            "student_id": stu["student_id"],
        })

    results.sort(key=lambda x: (-x["total_obtained"]))
    for rank, r in enumerate(results, 1):
        r["rank"] = rank
    return results


@st.cache_data(ttl=300)
def _tenant_name(tid):
    row = fetchone("SELECT madrasa_name FROM tenants WHERE id=%s", (tid,))
    return row["madrasa_name"] if row else "Smart Madrasa ERP"


# ──────────────────────────────────────────────────────────────────────────────
# DB writes
# ──────────────────────────────────────────────────────────────────────────────

def _create_exam(tid, session_id, exam_name, exam_date):
    conn = get_connection()
    if not conn:
        return False, t("acad.err_db_connection")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO exams (tenant_id, session_id, exam_name, exam_date)
                   VALUES (%s,%s,%s,%s) RETURNING id""",
                (tid, session_id, exam_name, exam_date),
            )
            eid = cur.fetchone()["id"]
        conn.commit()
        return True, eid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _create_subject(tid, class_id, name, code, full, pass_m):
    conn = get_connection()
    if not conn:
        return False, t("acad.err_db_connection")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO subjects (tenant_id, class_id, subject_name, subject_code, full_marks, pass_marks)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, class_id, name, code, full, pass_m),
            )
            sid = cur.fetchone()["id"]
        conn.commit()
        return True, sid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _save_marks_distribution(tid, exam_id, subject_id, written, mcq, practical):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO marks_distribution (tenant_id, exam_id, subject_id, written_marks, mcq_marks, practical_marks)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (exam_id, subject_id) DO UPDATE
                     SET written_marks=%s, mcq_marks=%s, practical_marks=%s""",
                (tid, exam_id, subject_id, written, mcq, practical, written, mcq, practical),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _save_student_mark(tid, enrollment_id, exam_id, subject_id,
                       written, mcq, practical, is_absent):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO student_marks
                   (tenant_id, enrollment_id, exam_id, subject_id,
                    written_obtained, mcq_obtained, practical_obtained, is_absent)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (enrollment_id, exam_id, subject_id) DO UPDATE
                     SET written_obtained=%s, mcq_obtained=%s,
                         practical_obtained=%s, is_absent=%s""",
                (tid, enrollment_id, exam_id, subject_id,
                 written, mcq, practical, is_absent,
                 written, mcq, practical, is_absent),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


# ──────────────────────────────────────────────────────────────────────────────
# Marksheet HTML
# ──────────────────────────────────────────────────────────────────────────────

def _marksheet_html(student_name, roll_no, class_name, exam_name, session_name,
                    marks_list, total_obt, total_full, pct, grade, gpa, rank,
                    madrasa_name):
    rows_html = ""
    for m in marks_list:
        obt = m["total_obtained"] or 0
        pm  = m["pass_marks"]
        status = (
            t("acad.ms_status_pass") if not m["is_absent"] and int(obt) >= int(pm)
            else (t("acad.ms_status_abs") if m["is_absent"] else t("acad.ms_status_fail"))
        )
        color = "#2E7D32" if not m["is_absent"] and int(obt) >= int(pm) else (
            "#F57F17" if m["is_absent"] else "#C62828"
        )
        rows_html += f"""
        <tr>
            <td>{m['subject_name']}</td>
            <td style="text-align:center">{m['full_marks']}</td>
            <td style="text-align:center">{m['pass_marks']}</td>
            <td style="text-align:center">{'—' if m['is_absent'] else m['written_obtained']}</td>
            <td style="text-align:center">{'—' if m['is_absent'] else m['mcq_obtained']}</td>
            <td style="text-align:center">{'—' if m['is_absent'] else m['practical_obtained']}</td>
            <td style="text-align:center;font-weight:600">{'—' if m['is_absent'] else obt}</td>
            <td style="text-align:center;color:{color};font-weight:700">{status}</td>
        </tr>"""

    grade_color = "#2E7D32" if grade not in ("D", "F") else "#C62828"
    return flatten_html(f"""
    <div style="border:2px solid #0F4C5C;border-radius:12px;padding:1.5rem;font-family:'Inter',sans-serif;max-width:720px">
      <div style="text-align:center;border-bottom:1px solid #DDE3E7;padding-bottom:1rem;margin-bottom:1rem">
        <div class="brand-calligraphy" style="font-size:1.3rem;font-weight:700;color:#0F4C5C">{madrasa_name}</div>
        <div style="font-size:1rem;font-weight:600;margin-top:0.25rem">{t('acad.ms_result_title', exam=exam_name, session=session_name)}</div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-bottom:1rem">
        <div><strong>{t('acad.ms_student_label')}:</strong> {student_name}</div>
        <div><strong>{t('acad.ms_roll_label')}:</strong> {roll_no or '—'}</div>
        <div><strong>{t('acad.ms_class_label')}:</strong> {class_name}</div>
        <div><strong>{t('acad.ms_rank_label')}:</strong> {rank}</div>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:0.82rem">
        <thead>
          <tr style="background:#0F4C5C;color:white">
            <th style="padding:0.4rem 0.5rem;text-align:left">{t('acad.ms_col_subject')}</th>
            <th>{t('acad.ms_col_full')}</th><th>{t('acad.ms_col_pass')}</th>
            <th>{t('acad.ms_col_written')}</th><th>{t('acad.ms_col_mcq')}</th><th>{t('acad.ms_col_practical')}</th>
            <th>{t('acad.ms_col_total')}</th><th>{t('acad.ms_col_result')}</th>
          </tr>
        </thead>
        <tbody style="background:#fff">
          {rows_html}
        </tbody>
        <tfoot>
          <tr style="background:#F7F9FA;font-weight:700">
            <td style="padding:0.4rem 0.5rem">{t('acad.ms_total_row')}</td>
            <td style="text-align:center">{total_full}</td>
            <td colspan="4"></td>
            <td style="text-align:center">{total_obt}</td>
            <td></td>
          </tr>
        </tfoot>
      </table>
      <div style="display:flex;gap:2rem;margin-top:1rem;padding-top:1rem;border-top:1px solid #DDE3E7">
        <div><span style="color:#6B7A8D;font-size:0.8rem">{t('acad.ms_percentage_label')}</span>
             <div style="font-size:1.4rem;font-weight:700;color:#0F4C5C">{pct:.1f}%</div></div>
        <div><span style="color:#6B7A8D;font-size:0.8rem">{t('acad.ms_grade_label')}</span>
             <div style="font-size:1.4rem;font-weight:700;color:{grade_color}">{grade}</div></div>
        <div><span style="color:#6B7A8D;font-size:0.8rem">{t('acad.ms_gpa_label')}</span>
             <div style="font-size:1.4rem;font-weight:700;color:{grade_color}">{gpa:.2f}</div></div>
      </div>
    </div>""")


# ──────────────────────────────────────────────────────────────────────────────
# Main render
# ──────────────────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("📚", t("acad.page_title"), t("acad.page_subtitle"))

    sessions = _get_sessions(tid)
    classes  = _get_classes(tid)

    if not sessions:
        alert(t("acad.warn_no_session"), "warning")
        return
    if not classes:
        alert(t("acad.warn_no_classes"), "warning")
        return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]: c["id"] for c in classes}

    tab_setup, tab_marks, tab_results, tab_marksheet = st.tabs([
        t("acad.tab_setup"), t("acad.tab_enter_marks"), t("acad.tab_results"), t("acad.tab_marksheet")
    ])

    # ── Tab 1: Setup (Exams, Subjects, Distribution) ──
    with tab_setup:
        col_exam, col_subj = st.columns(2)

        with col_exam:
            st.markdown(f"##### {t('acad.create_exam_header')}")
            with st.form("exam_form"):
                e_sess = st.selectbox(t("acad.session_label"), list(sess_map.keys()), key="ef_sess")
                e_name = st.text_input(t("acad.exam_name_label"), placeholder=t("acad.exam_name_placeholder"))
                e_date = st.date_input(t("acad.exam_date_label"), value=None)
                if st.form_submit_button(t("acad.create_exam_button"), type="primary"):
                    if not e_name.strip():
                        st.error(t("acad.err_exam_name_required"))
                    else:
                        ok, result = _create_exam(tid, sess_map[e_sess], e_name.strip(), str(e_date))
                        if ok:
                            st.success(t("acad.exam_created_success", id=result))
                            audit_module.log("CREATE", "Academics",
                                t("acad.audit_exam_created", id=result))
                            st.rerun()
                        else:
                            st.error(result)

        with col_subj:
            st.markdown(f"##### {t('acad.add_subject_header')}")
            with st.form("subj_form"):
                s_class = st.selectbox(t("acad.class_label"), list(class_map.keys()), key="sf_class")
                s_name  = st.text_input(t("acad.subject_name_label"), placeholder=t("acad.subject_name_placeholder"))
                s_code  = st.text_input(t("acad.subject_code_label"), placeholder=t("acad.subject_code_placeholder"))
                c1, c2 = st.columns(2)
                s_full = c1.number_input(t("acad.full_marks_label"), min_value=10, value=100)
                s_pass = c2.number_input(t("acad.pass_marks_label"), min_value=1, value=33)
                if st.form_submit_button(t("acad.add_subject_button"), type="primary"):
                    if not s_name.strip():
                        st.error(t("acad.err_subject_name_required"))
                    else:
                        ok, result = _create_subject(
                            tid, class_map[s_class], s_name.strip(), s_code.strip(),
                            int(s_full), int(s_pass)
                        )
                        if ok:
                            st.success(t("acad.subject_added_success", id=result))
                            st.rerun()
                        else:
                            st.error(result)

        divider()
        st.markdown(f"##### {t('acad.marks_dist_header')}")
        c1, c2 = st.columns(2)
        md_sess  = c1.selectbox(t("acad.session_label"), list(sess_map.keys()), key="md_sess")
        md_class = c2.selectbox(t("acad.class_label"),   list(class_map.keys()), key="md_class")
        exams    = _get_exams(tid, sess_map[md_sess])
        subjects = _get_subjects(tid, class_map[md_class])

        if not exams:
            alert(t("acad.info_no_exams_session"), "info")
        elif not subjects:
            alert(t("acad.info_no_subjects_class"), "info")
        else:
            exam_map = {e["exam_name"]: e["id"] for e in exams}
            md_exam  = st.selectbox(t("acad.exam_label"), list(exam_map.keys()), key="md_exam")

            st.caption(t("acad.dist_caption"))
            with st.form("dist_form"):
                for subj in subjects:
                    existing = _get_marks_dist(tid, exam_map[md_exam], subj["id"])
                    st.markdown(t("acad.dist_subject_total", name=subj["subject_name"], total=subj["full_marks"]))
                    c1, c2, c3 = st.columns(3)
                    w = c1.number_input(t("acad.written_label"), key=f"w_{subj['id']}", min_value=0,
                                        value=int(existing["written_marks"] if existing else subj["full_marks"] * 0.8))
                    m = c2.number_input(t("acad.mcq_label"),     key=f"m_{subj['id']}", min_value=0,
                                        value=int(existing["mcq_marks"] if existing else subj["full_marks"] * 0.1))
                    p = c3.number_input(t("acad.practical_label"), key=f"p_{subj['id']}", min_value=0,
                                        value=int(existing["practical_marks"] if existing else subj["full_marks"] * 0.1))
                    if (w + m + p) != subj["full_marks"]:
                        st.warning(t("acad.warn_dist_mismatch", sum=w + m + p, full=subj["full_marks"]))

                if st.form_submit_button(t("acad.save_dist_button"), type="primary"):
                    all_ok = True
                    for subj in subjects:
                        wv = st.session_state.get(f"w_{subj['id']}", 0)
                        mv = st.session_state.get(f"m_{subj['id']}", 0)
                        pv = st.session_state.get(f"p_{subj['id']}", 0)
                        if not _save_marks_distribution(tid, exam_map[md_exam], subj["id"], wv, mv, pv):
                            all_ok = False
                    if all_ok:
                        st.success(t("acad.dist_saved_success"))
                    else:
                        st.error(t("acad.err_dist_save_failed"))

    # ── Tab 2: Enter Marks ──
    with tab_marks:
        st.markdown(f"##### {t('acad.enter_marks_header')}")
        c1, c2 = st.columns(2)
        em_sess  = c1.selectbox(t("acad.session_label"), list(sess_map.keys()), key="em_sess")
        em_class = c2.selectbox(t("acad.class_label"),   list(class_map.keys()), key="em_class")

        exams2   = _get_exams(tid, sess_map[em_sess])
        subjects2 = _get_subjects(tid, class_map[em_class])
        students2 = _get_enrolled_students(tid, sess_map[em_sess], class_map[em_class])

        if not exams2:
            alert(t("acad.info_no_exams_found"), "info")
        elif not subjects2:
            alert(t("acad.info_no_subjects"), "info")
        elif not students2:
            alert(t("acad.info_no_active_students"), "info")
        else:
            exam_map2 = {e["exam_name"]: e["id"] for e in exams2}
            em_exam   = st.selectbox(t("acad.exam_label"), list(exam_map2.keys()), key="em_exam")

            stu_map2 = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students2}
            sel_stu  = st.selectbox(t("acad.student_label"), list(stu_map2.keys()), key="em_stu")
            stu_data = stu_map2[sel_stu]

            st.markdown(t("acad.entering_marks_for", name=stu_data["name"]))
            with st.form("marks_form"):
                for subj in subjects2:
                    dist = _get_marks_dist(tid, exam_map2[em_exam], subj["id"])
                    w_max = int(dist["written_marks"])  if dist else int(subj["full_marks"] * 0.8)
                    m_max = int(dist["mcq_marks"])      if dist else int(subj["full_marks"] * 0.1)
                    p_max = int(dist["practical_marks"]) if dist else int(subj["full_marks"] * 0.1)

                    existing_marks = fetchone(
                        """SELECT * FROM student_marks
                           WHERE tenant_id=%s AND enrollment_id=%s AND exam_id=%s AND subject_id=%s""",
                        (tid, stu_data["enrollment_id"], exam_map2[em_exam], subj["id"]),
                    )

                    st.markdown(t("acad.subject_full_pass", name=subj["subject_name"],
                                   full=subj["full_marks"], pass_marks=subj["pass_marks"]))
                    abs_key = f"abs_{subj['id']}"
                    is_abs  = st.checkbox(t("acad.absent_label"), key=abs_key,
                                          value=bool(existing_marks and existing_marks["is_absent"]))
                    if not is_abs:
                        c1, c2, c3 = st.columns(3)
                        c1.number_input(t("acad.written_max_label", max=w_max),   key=f"wr_{subj['id']}", min_value=0, max_value=w_max,
                                        value=int(existing_marks["written_obtained"] if existing_marks else 0))
                        c2.number_input(t("acad.mcq_max_label", max=m_max),       key=f"mq_{subj['id']}", min_value=0, max_value=m_max,
                                        value=int(existing_marks["mcq_obtained"] if existing_marks else 0))
                        c3.number_input(t("acad.practical_max_label", max=p_max), key=f"pr_{subj['id']}", min_value=0, max_value=p_max,
                                        value=int(existing_marks["practical_obtained"] if existing_marks else 0))

                if st.form_submit_button(t("acad.save_marks_button"), type="primary"):
                    all_ok = True
                    for subj in subjects2:
                        abs_v = st.session_state.get(f"abs_{subj['id']}", False)
                        wr_v  = st.session_state.get(f"wr_{subj['id']}", 0) if not abs_v else 0
                        mq_v  = st.session_state.get(f"mq_{subj['id']}", 0) if not abs_v else 0
                        pr_v  = st.session_state.get(f"pr_{subj['id']}", 0) if not abs_v else 0
                        if not _save_student_mark(
                            tid, stu_data["enrollment_id"], exam_map2[em_exam], subj["id"],
                            wr_v, mq_v, pr_v, abs_v
                        ):
                            all_ok = False
                    if all_ok:
                        st.success(t("acad.marks_saved_success"))
                        audit_module.log("UPDATE", "Academics",
                            t("acad.audit_marks_saved"))
                    else:
                        st.error(t("acad.err_marks_save_failed"))

    # ── Tab 3: Results ──
    with tab_results:
        st.markdown(f"##### {t('acad.results_header')}")
        c1, c2 = st.columns(2)
        rs_sess  = c1.selectbox(t("acad.session_label"), list(sess_map.keys()), key="rs_sess")
        rs_class = c2.selectbox(t("acad.class_label"),   list(class_map.keys()), key="rs_class")
        exams3   = _get_exams(tid, sess_map[rs_sess])

        if not exams3:
            alert(t("acad.info_no_exams_session"), "info")
        else:
            exam_map3 = {e["exam_name"]: e["id"] for e in exams3}
            rs_exam   = st.selectbox(t("acad.exam_label"), list(exam_map3.keys()), key="rs_exam")

            if st.button(t("acad.generate_result_button"), type="primary"):
                with st.spinner(t("acad.processing_results")):
                    results = _class_result_summary(
                        tid, exam_map3[rs_exam], class_map[rs_class], sess_map[rs_sess]
                    )
                if not results:
                    alert(t("acad.warn_no_marks_data"), "warning")
                else:
                    # KPIs
                    passed = sum(1 for r in results if r["failed"] == 0 and r["absent"] == 0)
                    avg_pct = sum(r["percentage"] for r in results) / len(results)
                    kpi_row([
                        {"label": t("acad.kpi_total_appeared"), "value": len(results), "cls": ""},
                        {"label": t("acad.kpi_passed"),         "value": passed,        "cls": "success"},
                        {"label": t("acad.kpi_failed"),         "value": len(results) - passed, "cls": "danger"},
                        {"label": t("acad.kpi_avg_pct"),        "value": f"{avg_pct:.1f}%", "cls": "accent"},
                    ])

                    display = []
                    for r in results:
                        display.append({
                            t("acad.col_rank"): r["rank"],
                            t("acad.col_roll"): r["roll_no"] or "—",
                            t("acad.col_name"): r["name"],
                            t("acad.col_total"): f"{r['total_obtained']}/{r['total_full']}",
                            t("acad.col_pct"): f"{r['percentage']:.1f}",
                            t("acad.col_grade"): r["grade"],
                            t("acad.col_gpa"): r["gpa"],
                            t("acad.col_failed_subj"): r["failed"],
                            t("acad.col_absent"): r["absent"],
                        })
                    st.dataframe(display, use_container_width=True, hide_index=True)

                    # Grade distribution chart
                    divider()
                    st.markdown(t("acad.grade_dist_header"))
                    grade_counts = {}
                    for r in results:
                        g = r["grade"]
                        grade_counts[g] = grade_counts.get(g, 0) + 1
                    import pandas as pd
                    gdf = pd.DataFrame(list(grade_counts.items()), columns=[t("acad.col_grade"), t("acad.col_total")])
                    gdf = gdf.sort_values(t("acad.col_grade"))
                    st.bar_chart(gdf.set_index(t("acad.col_grade")), color=PALETTE["primary"], height=220)

    # ── Tab 4: Marksheet ──
    with tab_marksheet:
        st.markdown(f"##### {t('acad.marksheet_header')}")
        c1, c2 = st.columns(2)
        ms_sess  = c1.selectbox(t("acad.session_label"), list(sess_map.keys()), key="ms_sess")
        ms_class = c2.selectbox(t("acad.class_label"),   list(class_map.keys()), key="ms_class")
        exams4   = _get_exams(tid, sess_map[ms_sess])

        if not exams4:
            alert(t("acad.info_no_exams_found"), "info")
        else:
            exam_map4 = {e["exam_name"]: e["id"] for e in exams4}
            ms_exam   = st.selectbox(t("acad.exam_label"), list(exam_map4.keys()), key="ms_exam")
            students4 = _get_enrolled_students(tid, sess_map[ms_sess], class_map[ms_class])

            if not students4:
                alert(t("acad.info_no_students"), "info")
            else:
                stu_map4 = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students4}
                sel_stu4 = st.selectbox(t("acad.student_label"), list(stu_map4.keys()), key="ms_stu")
                stu4     = stu_map4[sel_stu4]

                if st.button(t("acad.generate_marksheet_button"), type="primary"):
                    marks4 = _get_student_marks(tid, stu4["enrollment_id"], exam_map4[ms_exam])
                    if not marks4:
                        alert(t("acad.warn_no_marks_for_student"), "warning")
                    else:
                        all_results = _class_result_summary(
                            tid, exam_map4[ms_exam], class_map[ms_class], sess_map[ms_sess]
                        )
                        stu_result = next(
                            (r for r in all_results if r["student_id"] == stu4["student_id"]), None
                        )
                        if stu_result:
                            st.markdown(
                                _marksheet_html(
                                    stu4["name"], stu4["roll_no"], ms_class,
                                    ms_exam, ms_sess,
                                    stu_result["marks"],
                                    stu_result["total_obtained"],
                                    stu_result["total_full"],
                                    stu_result["percentage"],
                                    stu_result["grade"],
                                    stu_result["gpa"],
                                    stu_result["rank"],
                                    _tenant_name(tid),
                                ),
                                unsafe_allow_html=True,
                            )

                            # Progress bar per subject
                            divider()
                            st.markdown(t("acad.subject_perf_header"))
                            import pandas as pd
                            subj_df = pd.DataFrame([
                                {
                                    t("acad.col_subject"): m["subject_name"],
                                    t("acad.col_obtained"): int(m["total_obtained"] or 0),
                                    t("acad.col_full_marks"): int(m["full_marks"]),
                                    t("acad.col_pct_score"): round(
                                        int(m["total_obtained"] or 0) / int(m["full_marks"]) * 100, 1
                                    ) if not m["is_absent"] else 0,
                                }
                                for m in stu_result["marks"]
                            ])
                            st.dataframe(subj_df, use_container_width=True, hide_index=True)
                            if not subj_df.empty:
                                st.bar_chart(
                                    subj_df.set_index(t("acad.col_subject"))[[t("acad.col_pct_score")]],
                                    color=PALETTE["accent"], height=240
                                )
