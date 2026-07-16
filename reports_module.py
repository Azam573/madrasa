"""
reports_module.py — Comprehensive Analytics & Reports
Fee collection reports, student statistics, exam analysis,
defaulter lists, and CSV export for Smart Madrasa ERP.
"""

import streamlit as st
import io
from datetime import date
from db import fetchall, fetchone
from utils import (
    page_header, kpi_row, alert, divider,
    get_tenant_id, months_list, current_year, get_grade, PALETTE,
)
from i18n import t
from sanitize import csv_safe


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


# ── Fee Defaulter List ──
def _defaulter_list(tid, session_id=None, class_id=None):
    # SQL Injection fix (v9.0): build_where দিয়ে safe clause তৈরি
    from sql_safe import build_where
    where, extra_params = build_where(
        ["s.tenant_id=%s", "s.status='active'", "v.status IN ('unpaid','partial')"],
        {"e.session_id": session_id, "e.class_id": class_id},
    )
    params = tuple([tid] + extra_params)

    return fetchall(
        f"""SELECT s.id AS student_id, s.name, s.mobile_no,
                   c.class_name, sess.session_name,
                   e.roll_no, e.monthly_fee,
                   COUNT(v.id) AS unpaid_count,
                   SUM(v.amount) AS total_due
            FROM students s
            JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
            JOIN classes c ON c.id=e.class_id
            JOIN academic_sessions sess ON sess.id=e.session_id
            JOIN fee_vouchers v ON v.student_id=s.id AND v.tenant_id=s.tenant_id
            WHERE {where}
            GROUP BY s.id, s.name, s.mobile_no, c.class_name,
                     sess.session_name, e.roll_no, e.monthly_fee
            ORDER BY total_due DESC""",
        tuple(params),
    )


# ── Collection summary per class ──
def _collection_by_class(tid, year):
    return fetchall(
        """SELECT c.class_name,
                  COUNT(DISTINCT e.student_id) AS students,
                  SUM(CASE WHEN v.status='paid' THEN v.amount ELSE 0 END) AS collected,
                  SUM(CASE WHEN v.status='unpaid' THEN v.amount ELSE 0 END) AS outstanding
           FROM classes c
           LEFT JOIN student_enrollments e ON e.class_id=c.id AND e.tenant_id=c.tenant_id
           LEFT JOIN fee_vouchers v ON v.student_id=e.student_id
             AND v.tenant_id=c.tenant_id AND v.year=%s
           WHERE c.tenant_id=%s
           GROUP BY c.class_name, c.class_numeric
           ORDER BY c.class_numeric""",
        (year, tid),
    )


# ── Fund-wise annual summary ──
def _fund_annual(tid, year):
    return fetchall(
        """SELECT fund_type,
                  COUNT(*) AS vouchers,
                  SUM(amount) AS billed,
                  SUM(CASE WHEN status='paid' THEN amount ELSE 0 END) AS paid,
                  SUM(CASE WHEN status='unpaid' THEN amount ELSE 0 END) AS unpaid
           FROM fee_vouchers
           WHERE tenant_id=%s AND year=%s
           GROUP BY fund_type""",
        (tid, year),
    )


# ── Month-wise collection ──
def _month_wise(tid, year, fund_type=None):
    # SQL Injection fix (v9.0): optional fund_type clause safe ভাবে যোগ
    params: list = [tid, year]
    ft_clause = ""
    if fund_type and fund_type != "All":
        # fund_type শুধু predefined list থেকে আসে UI-তে,
        # তবু hardcoded placeholder ব্যবহার করা হচ্ছে
        ft_clause = "AND fund_type=%s"
        params.append(fund_type.lower().replace(" ", "_"))

    return fetchall(
        f"""SELECT month_name,
                   SUM(CASE WHEN status='paid'   THEN amount ELSE 0 END) AS paid,
                   SUM(CASE WHEN status='unpaid' THEN amount ELSE 0 END) AS unpaid,
                   COUNT(*) AS vouchers
            FROM fee_vouchers
            WHERE tenant_id=%s AND year=%s {ft_clause}
            GROUP BY month_name ORDER BY MIN(id)""",
        tuple(params),
    )


# ── Student strength by class ──
def _strength_report(tid, session_id):
    return fetchall(
        """SELECT c.class_name,
                  COUNT(CASE WHEN s.gender='Male'   THEN 1 END) AS male,
                  COUNT(CASE WHEN s.gender='Female' THEN 1 END) AS female,
                  COUNT(s.id) AS total
           FROM classes c
           LEFT JOIN student_enrollments e ON e.class_id=c.id AND e.tenant_id=c.tenant_id
             AND e.session_id=%s AND e.enrollment_status='active'
           LEFT JOIN students s ON s.id=e.student_id AND s.status='active'
           WHERE c.tenant_id=%s
           GROUP BY c.class_name, c.class_numeric
           ORDER BY c.class_numeric""",
        (session_id, tid),
    )


# ── Exam toppers ──
def _toppers(tid, exam_id, limit=10):
    return fetchall(
        """SELECT s.name, c.class_name, e.roll_no,
                  SUM(sm.total_obtained) AS obtained,
                  SUM(subj.full_marks) AS full_marks,
                  ROUND(SUM(sm.total_obtained)::numeric / NULLIF(SUM(subj.full_marks),0)*100,1) AS pct
           FROM student_marks sm
           JOIN student_enrollments e ON e.id=sm.enrollment_id
           JOIN students s ON s.id=e.student_id
           JOIN subjects subj ON subj.id=sm.subject_id
           JOIN classes c ON c.id=e.class_id
           WHERE sm.tenant_id=%s AND sm.exam_id=%s AND sm.is_absent=FALSE
           GROUP BY s.name, c.class_name, e.roll_no
           ORDER BY pct DESC LIMIT %s""",
        (tid, exam_id, limit),
    )


# ──────────────────────────────────────────────────────────────────────────────
# CSV export helper
# ──────────────────────────────────────────────────────────────────────────────

def _to_csv_bytes(rows: list[dict]) -> bytes:
    import csv, io
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows([
        {k: csv_safe(v) if isinstance(v, str) else v for k, v in row.items()}
        for row in rows
    ])
    return buf.getvalue().encode("utf-8-sig")  # utf-8-sig for Excel


# ──────────────────────────────────────────────────────────────────────────────
# Main render
# ──────────────────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("📈", t("rep.page_title"), t("rep.page_subtitle"))

    sessions = _get_sessions(tid)
    classes  = _get_classes(tid)

    if not sessions:
        alert(t("rep.warn_no_sessions"), "warning")
        return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]: c["id"] for c in classes}

    tab_fee, tab_defaulter, tab_strength, tab_exam, tab_export = st.tabs([
        t("rep.tab_fee"), t("rep.tab_defaulters"), t("rep.tab_strength"),
        t("rep.tab_exam"), t("rep.tab_export"),
    ])

    yr = current_year()

    # ────────────────────────────────────────────────────────────
    # TAB 1: Fee Reports
    # ────────────────────────────────────────────────────────────
    with tab_fee:
        st.markdown(f"#### {t('rep.fee_reports_heading')}")

        fc1, fc2 = st.columns(2)
        rep_year = fc1.number_input(t("rep.label_year"), min_value=2020, max_value=2040,
                                    value=yr, key="rep_yr")
        fund_opts = ["All", "General", "Zakat", "Lillah Boarding"]
        fund_labels = {
            "All": t("rep.fund_all"), "General": t("rep.fund_general"),
            "Zakat": t("rep.fund_zakat"), "Lillah Boarding": t("rep.fund_lillah_boarding"),
        }
        rep_fund = fc2.selectbox(t("rep.label_fund"), fund_opts,
                                 format_func=lambda f: fund_labels.get(f, f), key="rep_fund")

        # Month-wise chart
        mw = _month_wise(tid, int(rep_year), rep_fund)
        if mw:
            import pandas as pd
            months = months_list()
            mw_dict_paid   = {r["month_name"]: float(r["paid"])    for r in mw}
            mw_dict_unpaid = {r["month_name"]: float(r["unpaid"])  for r in mw}
            df = pd.DataFrame({
                t("rep.col_month"):   [m[:3] for m in months],
                t("rep.col_paid"):    [mw_dict_paid.get(m, 0)   for m in months],
                t("rep.col_unpaid"):  [mw_dict_unpaid.get(m, 0) for m in months],
            }).set_index(t("rep.col_month"))
            st.markdown(f"**{t('rep.chart_billing_vs_collection')}**")
            st.bar_chart(df, height=260, color=[PALETTE["success"], PALETTE["danger"]])
        else:
            alert(t("rep.info_no_data_year_fund"), "info")

        divider()

        # Class-wise table
        st.markdown(f"**{t('rep.class_wise_summary_heading')}**")
        cw = _collection_by_class(tid, int(rep_year))
        if cw:
            import pandas as pd
            rows = []
            for r in cw:
                collected    = float(r["collected"] or 0)
                outstanding  = float(r["outstanding"] or 0)
                total_billed = collected + outstanding
                pct = round(collected / total_billed * 100, 1) if total_billed else 0
                rows.append({
                    t("rep.col_class"):       r["class_name"],
                    t("rep.col_students"):    r["students"],
                    t("rep.col_billed"):      f"৳{total_billed:,.0f}",
                    t("rep.col_collected"):   f"৳{collected:,.0f}",
                    t("rep.col_outstanding"): f"৳{outstanding:,.0f}",
                    t("rep.col_rate_pct"):    f"{pct}%",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

            # Grand total
            total_c = sum(float(r["collected"] or 0) for r in cw)
            total_o = sum(float(r["outstanding"] or 0) for r in cw)
            kpi_row([
                {"label": t("rep.kpi_grand_total_collected"), "value": f"৳{total_c:,.0f}", "cls": "success"},
                {"label": t("rep.kpi_grand_outstanding"),     "value": f"৳{total_o:,.0f}", "cls": "danger"},
                {"label": t("rep.kpi_overall_rate"),
                 "value": f"{total_c/(total_c+total_o)*100:.1f}%" if (total_c+total_o) else "N/A",
                 "cls": "accent"},
            ])
        else:
            alert(t("rep.info_no_class_wise_data"), "info")

        divider()

        # Fund-wise breakdown
        st.markdown(f"**{t('rep.fund_wise_breakdown_heading')}**")
        fw = _fund_annual(tid, int(rep_year))
        if fw:
            fund_label = {"general": t("rep.fund_general"), "zakat": t("rep.fund_zakat"),
                          "lillah_boarding": t("rep.fund_lillah_boarding")}
            rows2 = [
                {
                    t("rep.col_fund"):      fund_label.get(r["fund_type"], r["fund_type"]),
                    t("rep.col_vouchers"):  r["vouchers"],
                    t("rep.col_billed"):    f"৳{float(r['billed'] or 0):,.0f}",
                    t("rep.col_paid"):      f"৳{float(r['paid']   or 0):,.0f}",
                    t("rep.col_unpaid"):    f"৳{float(r['unpaid'] or 0):,.0f}",
                }
                for r in fw
            ]
            st.dataframe(rows2, use_container_width=True, hide_index=True)

    # ────────────────────────────────────────────────────────────
    # TAB 2: Defaulter List
    # ────────────────────────────────────────────────────────────
    with tab_defaulter:
        st.markdown(f"#### {t('rep.defaulter_list_heading')}")
        dc1, dc2 = st.columns(2)
        all_label = t("rep.all_option")
        d_sess  = dc1.selectbox(t("rep.label_session"), [all_label] + list(sess_map.keys()), key="def_sess")
        d_class = dc2.selectbox(t("rep.label_class"),   [all_label] + list(class_map.keys()), key="def_class")

        sess_id_f  = sess_map.get(d_sess)   if d_sess != all_label else None
        class_id_f = class_map.get(d_class) if d_class != all_label else None

        defaulters = _defaulter_list(tid, sess_id_f, class_id_f)

        if not defaulters:
            alert(t("rep.success_no_defaulters"), "success")
        else:
            total_outstanding = sum(float(d["total_due"] or 0) for d in defaulters)
            kpi_row([
                {"label": t("rep.kpi_total_defaulters"),   "value": len(defaulters),          "cls": "danger"},
                {"label": t("rep.kpi_total_outstanding"),  "value": f"৳{total_outstanding:,.0f}", "cls": "danger"},
                {"label": t("rep.kpi_avg_per_student"),
                 "value": f"৳{total_outstanding/len(defaulters):,.0f}",
                 "cls": "warning"},
            ])

            rows = []
            for d in defaulters:
                rows.append({
                    t("rep.col_roll"):         d["roll_no"] or "—",
                    t("rep.col_name"):         d["name"],
                    t("rep.col_class"):        d["class_name"],
                    t("rep.col_mobile"):       d["mobile_no"] or "—",
                    t("rep.col_unpaid_vouchers"): d["unpaid_count"],
                    t("rep.col_total_due"):    f"৳{float(d['total_due'] or 0):,.0f}",
                    t("rep.col_monthly_fee"):  f"৳{float(d['monthly_fee'] or 0):,.0f}",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

            # Download button
            csv = _to_csv_bytes(rows)
            st.download_button(
                t("rep.btn_download_defaulters"),
                data=csv,
                file_name=f"defaulters_{date.today()}.csv",
                mime="text/csv",
                type="primary",
            )

    # ────────────────────────────────────────────────────────────
    # TAB 3: Student Strength Report
    # ────────────────────────────────────────────────────────────
    with tab_strength:
        st.markdown(f"#### {t('rep.strength_report_heading')}")
        str_sess = st.selectbox(t("rep.label_session"), list(sess_map.keys()), key="str_sess")

        strength = _strength_report(tid, sess_map[str_sess])
        if not strength:
            alert(t("rep.info_no_enrollment_data"), "info")
        else:
            import pandas as pd
            total_m = sum(int(r["male"]   or 0) for r in strength)
            total_f = sum(int(r["female"] or 0) for r in strength)
            total   = sum(int(r["total"]  or 0) for r in strength)

            kpi_row([
                {"label": t("rep.kpi_total_students"), "value": total,   "cls": ""},
                {"label": t("rep.col_male"),           "value": total_m, "cls": ""},
                {"label": t("rep.col_female"),         "value": total_f, "cls": "accent"},
                {"label": t("rep.kpi_classes"),        "value": len(strength), "cls": ""},
            ])

            rows = [
                {
                    t("rep.col_class"):  r["class_name"],
                    t("rep.col_male"):   int(r["male"]   or 0),
                    t("rep.col_female"): int(r["female"] or 0),
                    t("rep.col_total"):  int(r["total"]  or 0),
                }
                for r in strength
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

            df = pd.DataFrame(rows)
            if not df.empty:
                st.markdown(f"**{t('rep.class_wise_enrollment_heading')}**")
                st.bar_chart(
                    df.set_index(t("rep.col_class"))[[t("rep.col_male"), t("rep.col_female")]],
                    color=[PALETTE["primary"], PALETTE["accent"]],
                    height=260,
                )

    # ────────────────────────────────────────────────────────────
    # TAB 4: Exam Analytics
    # ────────────────────────────────────────────────────────────
    with tab_exam:
        st.markdown(f"#### {t('rep.exam_analytics_heading')}")
        ea1, ea2 = st.columns(2)
        ea_sess  = ea1.selectbox(t("rep.label_session"), list(sess_map.keys()), key="ea_sess")

        exams = fetchall(
            "SELECT id, exam_name FROM exams WHERE tenant_id=%s AND session_id=%s ORDER BY exam_date",
            (tid, sess_map[ea_sess]),
        )
        if not exams:
            alert(t("rep.info_no_exams_session"), "info")
        else:
            exam_map = {e["exam_name"]: e["id"] for e in exams}
            ea_exam  = ea2.selectbox(t("rep.label_exam"), list(exam_map.keys()), key="ea_exam")

            toppers = _toppers(tid, exam_map[ea_exam], limit=10)
            if not toppers:
                alert(t("rep.info_no_marks_exam"), "info")
            else:
                st.markdown(f"**{t('rep.top_10_students_heading')}**")
                trows = []
                for i, top in enumerate(toppers, 1):
                    grade, gpa = get_grade(float(top["pct"] or 0))
                    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")
                    trows.append({
                        t("rep.col_rank"):     medal,
                        t("rep.col_name"):     top["name"],
                        t("rep.col_class"):    top["class_name"],
                        t("rep.col_roll"):     top["roll_no"] or "—",
                        t("rep.col_obtained"): f"{top['obtained']}/{top['full_marks']}",
                        t("rep.col_pct"):      f"{top['pct']}%",
                        t("rep.col_grade"):    grade,
                        t("rep.col_gpa"):      f"{gpa:.2f}",
                    })
                st.dataframe(trows, use_container_width=True, hide_index=True)

                # Subject-wise class average
                divider()
                st.markdown(f"**{t('rep.subject_wise_avg_heading')}**")
                ea_class = st.selectbox(t("rep.label_class"), list(class_map.keys()), key="ea_class")
                subj_avg = fetchall(
                    """SELECT subj.subject_name,
                              ROUND(AVG(sm.total_obtained)::numeric,1) AS avg,
                              MAX(sm.total_obtained) AS highest,
                              MIN(sm.total_obtained) AS lowest,
                              subj.full_marks
                       FROM student_marks sm
                       JOIN subjects subj ON subj.id=sm.subject_id
                       JOIN student_enrollments e ON e.id=sm.enrollment_id
                       WHERE sm.tenant_id=%s AND sm.exam_id=%s
                         AND e.class_id=%s AND sm.is_absent=FALSE
                       GROUP BY subj.subject_name, subj.full_marks
                       ORDER BY subj.id""",
                    (tid, exam_map[ea_exam], class_map[ea_class]),
                )
                if subj_avg:
                    import pandas as pd
                    sa_rows = [
                        {
                            t("rep.col_subject"): r["subject_name"],
                            t("rep.col_avg"):     float(r["avg"] or 0),
                            t("rep.col_highest"): int(r["highest"] or 0),
                            t("rep.col_lowest"):  int(r["lowest"] or 0),
                            t("rep.col_full"):    int(r["full_marks"]),
                            t("rep.col_avg_pct"): round(float(r["avg"] or 0) / int(r["full_marks"]) * 100, 1),
                        }
                        for r in subj_avg
                    ]
                    st.dataframe(sa_rows, use_container_width=True, hide_index=True)
                    sa_df = pd.DataFrame(sa_rows)
                    st.bar_chart(sa_df.set_index(t("rep.col_subject"))[[t("rep.col_avg_pct")]],
                                 color=PALETTE["accent"], height=240)
                else:
                    alert(t("rep.info_no_marks_class_exam"), "info")

    # ────────────────────────────────────────────────────────────
    # TAB 5: Export
    # ────────────────────────────────────────────────────────────
    with tab_export:
        st.markdown(f"#### {t('rep.export_center_heading')}")
        alert(t("rep.info_export_format"), "info")

        exp_all_label = t("rep.all_option")
        exp_sess  = st.selectbox(t("rep.label_session"), list(sess_map.keys()), key="exp_sess")
        exp_class = st.selectbox(t("rep.label_class_for_students"),
                                  [exp_all_label] + list(class_map.keys()), key="exp_class")
        exp_year  = st.number_input(t("rep.label_year_for_fees"), min_value=2020,
                                     max_value=2040, value=yr, key="exp_yr")

        divider()
        c1, c2, c3 = st.columns(3)

        # Export: Active students
        with c1:
            st.markdown(f"**{t('rep.export_active_students')}**")
            if st.button(t("rep.btn_generate_csv"), key="exp_students", use_container_width=True):
                q_params = [tid, sess_map[exp_sess]]
                # SQL Injection fix (v9.0): cls_clause শুধু hardcoded placeholder
                cls_clause = ""
                if exp_class != exp_all_label:
                    cls_clause = "AND e.class_id=%s"
                    q_params.append(class_map[exp_class])
                rows = fetchall(
                    "SELECT s.name, s.father_name, s.mobile_no, s.gender,"
                    "       e.roll_no, c.class_name, sess.session_name,"
                    "       e.monthly_fee, e.enrollment_status"
                    " FROM students s"
                    " JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id"
                    " JOIN classes c ON c.id=e.class_id"
                    " JOIN academic_sessions sess ON sess.id=e.session_id"
                    " WHERE s.tenant_id=%s AND e.session_id=%s"
                    "   AND s.status='active'"
                    f"  {cls_clause}"
                    " ORDER BY c.class_numeric, e.roll_no",
                    tuple(q_params),
                )
                if rows:
                    csv = _to_csv_bytes([dict(r) for r in rows])
                    st.download_button(t("rep.btn_download"), csv,
                                       f"students_{date.today()}.csv", "text/csv")
                    st.caption(t("rep.caption_records", n=len(rows)))
                else:
                    alert(t("rep.info_no_data"), "info")

        # Export: Fee vouchers
        with c2:
            st.markdown(f"**{t('rep.export_fee_vouchers')}**")
            if st.button(t("rep.btn_generate_csv"), key="exp_vouchers", use_container_width=True):
                rows = fetchall(
                    """SELECT v.voucher_no, s.name, c.class_name,
                              v.month_name, v.year, v.amount, v.fund_type,
                              v.status, v.issue_date, v.due_date
                       FROM fee_vouchers v
                       JOIN students s ON s.id=v.student_id
                       JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
                       JOIN classes c ON c.id=e.class_id
                       WHERE v.tenant_id=%s AND v.year=%s
                       ORDER BY v.issue_date DESC""",
                    (tid, int(exp_year)),
                )
                if rows:
                    csv = _to_csv_bytes([dict(r) for r in rows])
                    st.download_button(t("rep.btn_download"), csv,
                                       f"vouchers_{exp_year}.csv", "text/csv")
                    st.caption(t("rep.caption_records", n=len(rows)))
                else:
                    alert(t("rep.info_no_data"), "info")

        # Export: Defaulters
        with c3:
            st.markdown(f"**{t('rep.export_defaulters')}**")
            if st.button(t("rep.btn_generate_csv"), key="exp_defaulters", use_container_width=True):
                defaulters = _defaulter_list(tid, sess_map.get(exp_sess))
                if defaulters:
                    export_rows = [
                        {
                            "Name":          d["name"],
                            "Father":        d.get("father_name", ""),
                            "Mobile":        d["mobile_no"] or "",
                            "Class":         d["class_name"],
                            "Roll":          d["roll_no"] or "",
                            "Unpaid_Vouchers": d["unpaid_count"],
                            "Total_Due_BDT": float(d["total_due"] or 0),
                        }
                        for d in defaulters
                    ]
                    csv = _to_csv_bytes(export_rows)
                    st.download_button(t("rep.btn_download"), csv,
                                       f"defaulters_{date.today()}.csv", "text/csv")
                    st.caption(t("rep.caption_defaulters", n=len(export_rows)))
                else:
                    alert(t("rep.success_no_defaulters_export"), "success")

        divider()
        c4, c5 = st.columns(2)

        # Export: Attendance
        with c4:
            st.markdown(f"**{t('rep.export_attendance_log')}**")
            att_month_opts = list(range(1, 13))
            att_m = st.selectbox(t("rep.label_month"), att_month_opts,
                                  format_func=lambda m: date(2000, m, 1).strftime("%B"),
                                  key="exp_att_month")
            if st.button(t("rep.btn_generate_csv"), key="exp_att", use_container_width=True):
                q_params = [tid, sess_map[exp_sess], int(exp_year), att_m]
                # SQL Injection fix (v9.0): cls_clause শুধু hardcoded placeholder
                cls_clause = ""
                if exp_class != exp_all_label:
                    cls_clause = "AND e.class_id=%s"
                    q_params.append(class_map[exp_class])
                rows = fetchall(
                    "SELECT s.name, e.roll_no, c.class_name,"
                    "       a.date, a.status"
                    " FROM attendance a"
                    " JOIN student_enrollments e ON e.id=a.enrollment_id"
                    " JOIN students s ON s.id=e.student_id"
                    " JOIN classes c ON c.id=e.class_id"
                    " WHERE a.tenant_id=%s AND e.session_id=%s"
                    "   AND EXTRACT(YEAR FROM a.date)=%s"
                    "   AND EXTRACT(MONTH FROM a.date)=%s"
                    f"  {cls_clause}"
                    " ORDER BY a.date, e.roll_no",
                    tuple(q_params),
                )
                if rows:
                    csv = _to_csv_bytes([dict(r) for r in rows])
                    st.download_button(t("rep.btn_download"), csv,
                                       f"attendance_{exp_year}_{att_m:02d}.csv", "text/csv")
                    st.caption(t("rep.caption_records", n=len(rows)))
                else:
                    alert(t("rep.info_no_attendance_data"), "info")

        # Export: Exam marks
        with c5:
            st.markdown(f"**{t('rep.export_exam_marks')}**")
            exams_all = fetchall(
                "SELECT id, exam_name FROM exams WHERE tenant_id=%s AND session_id=%s ORDER BY exam_date",
                (tid, sess_map[exp_sess]),
            )
            if exams_all:
                em_map = {e["exam_name"]: e["id"] for e in exams_all}
                sel_em = st.selectbox(t("rep.label_exam"), list(em_map.keys()), key="exp_exam")
                if st.button(t("rep.btn_generate_csv"), key="exp_marks", use_container_width=True):
                    rows = fetchall(
                        """SELECT s.name, e.roll_no, c.class_name,
                                  subj.subject_name, sm.written_obtained,
                                  sm.mcq_obtained, sm.practical_obtained,
                                  sm.total_obtained, sm.is_absent
                           FROM student_marks sm
                           JOIN student_enrollments e ON e.id=sm.enrollment_id
                           JOIN students s ON s.id=e.student_id
                           JOIN classes c ON c.id=e.class_id
                           JOIN subjects subj ON subj.id=sm.subject_id
                           WHERE sm.tenant_id=%s AND sm.exam_id=%s
                           ORDER BY c.class_numeric, e.roll_no, subj.id""",
                        (tid, em_map[sel_em]),
                    )
                    if rows:
                        csv = _to_csv_bytes([dict(r) for r in rows])
                        st.download_button(t("rep.btn_download"), csv,
                                           f"marks_{sel_em}.csv", "text/csv")
                        st.caption(t("rep.caption_records", n=len(rows)))
                    else:
                        alert(t("rep.info_no_marks_data"), "info")
            else:
                st.caption(t("rep.caption_no_exams_session"))
