"""
govt_report.py — বাংলাদেশ সরকারি ফরম্যাটে রিপোর্ট
মাদ্রাসা বোর্ড, BMEB, জেলা শিক্ষা অফিসের নির্ধারিত ফরম্যাট।
"""

import streamlit as st
from datetime import date
from db import fetchall, fetchone
from utils import page_header, alert, divider, get_tenant_id, months_list, current_year, flatten_html, print_button
import audit_module
from i18n import t


def _tenant(tid):
    return fetchone("SELECT * FROM tenants WHERE id=%s", (tid,)) or {}


# ── মাসিক উপস্থিতি প্রতিবেদন ──
# NOTE: This mimics the official government-format monthly attendance report
# (madrasa board / district education office template) — content stays in
# Bangla regardless of UI language, per government-mandated wording rules.
def _monthly_att_report_html(tenant, class_name, month, year, summary):
    rows = ""
    for i, r in enumerate(summary, 1):
        tot  = int(r["total_marked"] or 0)
        pre  = int(r["present"] or 0)
        pct  = round(pre / tot * 100, 1) if tot else 0
        rows += f"""<tr>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{i}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{r['name']}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{r['roll_no'] or '—'}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{tot}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{pre}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{int(r['absent'] or 0)}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{int(r['late'] or 0)}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{pct}%</td>
        </tr>"""
    return flatten_html(f"""
    <div style="font-family:Arial,sans-serif;max-width:780px;margin:0 auto;font-size:12px">
      <div style="text-align:center;border-bottom:2px solid #000;padding-bottom:10px;margin-bottom:12px">
        <div style="font-size:16px;font-weight:700">গণপ্রজাতন্ত্রী বাংলাদেশ সরকার</div>
        <div style="font-size:14px;font-weight:700;margin-top:3px">{tenant.get('madrasa_name','')}</div>
        <div style="font-size:11px">{tenant.get('address','')}</div>
        <div style="font-size:13px;font-weight:700;margin-top:8px">
          মাসিক উপস্থিতি প্রতিবেদন — {month} {year}
        </div>
        <div style="font-size:11px">শ্রেণী: {class_name}</div>
      </div>
      <table style="width:100%;border-collapse:collapse">
        <tr style="background:#E0E0E0">
          <th style="border:1px solid #ccc;padding:6px 8px">ক্রঃ</th>
          <th style="border:1px solid #ccc;padding:6px 8px;text-align:left">ছাত্রের নাম</th>
          <th style="border:1px solid #ccc;padding:6px 8px">রোল</th>
          <th style="border:1px solid #ccc;padding:6px 8px">মোট দিন</th>
          <th style="border:1px solid #ccc;padding:6px 8px">উপস্থিত</th>
          <th style="border:1px solid #ccc;padding:6px 8px">অনুপস্থিত</th>
          <th style="border:1px solid #ccc;padding:6px 8px">দেরি</th>
          <th style="border:1px solid #ccc;padding:6px 8px">হার %</th>
        </tr>
        {rows}
      </table>
      <table style="width:100%;border-collapse:collapse;margin-top:30px;font-size:11px">
        <tr>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">শ্রেণী শিক্ষকের স্বাক্ষর</div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">তারিখ: {date.today()}</div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">মুহতামিমের সিল ও স্বাক্ষর</div>
          </td>
        </tr>
      </table>
    </div>""")


# ── বার্ষিক পরীক্ষা ফলাফল প্রতিবেদন ──
# NOTE: Official government-format exam result report — content stays in
# Bangla regardless of UI language, per government-mandated wording rules.
def _annual_result_html(tenant, class_name, session_name, exam_name, results):
    rows = ""
    for r in results:
        grade_color = "#2E7D32" if r["grade"] not in ("D","F") else "#C62828"
        rows += f"""<tr>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{r['rank']}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{r['roll_no'] or '—'}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{r['name']}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{r['total_obtained']}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{r['total_full']}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{r['percentage']:.1f}%</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center;
                     color:{grade_color};font-weight:700">{r['grade']}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{r['gpa']:.2f}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center;
                     color:{'#2E7D32' if r['failed']==0 else '#C62828'}">
            {'উত্তীর্ণ' if r['failed']==0 else 'অনুত্তীর্ণ'}
          </td>
        </tr>"""

    total_students = len(results)
    passed = sum(1 for r in results if r["failed"] == 0)
    avg_pct = sum(r["percentage"] for r in results) / total_students if total_students else 0

    return flatten_html(f"""
    <div style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;font-size:11px">
      <div style="text-align:center;border-bottom:2px solid #000;padding-bottom:10px;margin-bottom:12px">
        <div style="font-size:15px;font-weight:700">গণপ্রজাতন্ত্রী বাংলাদেশ সরকার</div>
        <div style="font-size:13px;font-weight:700;margin-top:3px">{tenant.get('madrasa_name','')}</div>
        <div style="font-size:10px">{tenant.get('address','')}</div>
        <div style="font-size:12px;font-weight:700;margin-top:8px">
          পরীক্ষার ফলাফল প্রতিবেদন — {exam_name}
        </div>
        <div style="font-size:10px">শ্রেণী: {class_name} | সেশন: {session_name}</div>
      </div>
      <table style="width:100%;border-collapse:collapse;margin-bottom:12px;font-size:10px;border:1px solid #000">
        <tr>
          <td style="border:1px solid #000;padding:4px 8px">মোট ছাত্র</td>
          <td style="border:1px solid #000;padding:4px 8px;font-weight:700">{total_students} জন</td>
          <td style="border:1px solid #000;padding:4px 8px">উত্তীর্ণ</td>
          <td style="border:1px solid #000;padding:4px 8px;font-weight:700;color:green">{passed} জন</td>
          <td style="border:1px solid #000;padding:4px 8px">অনুত্তীর্ণ</td>
          <td style="border:1px solid #000;padding:4px 8px;font-weight:700;color:red">{total_students-passed} জন</td>
          <td style="border:1px solid #000;padding:4px 8px">পাসের হার</td>
          <td style="border:1px solid #000;padding:4px 8px;font-weight:700">{passed/total_students*100:.1f}%</td>
          <td style="border:1px solid #000;padding:4px 8px">গড় নম্বর</td>
          <td style="border:1px solid #000;padding:4px 8px;font-weight:700">{avg_pct:.1f}%</td>
        </tr>
      </table>
      <table style="width:100%;border-collapse:collapse">
        <tr style="background:#D0D0D0">
          <th style="border:1px solid #ccc;padding:5px">মেধা</th>
          <th style="border:1px solid #ccc;padding:5px">রোল</th>
          <th style="border:1px solid #ccc;padding:5px;text-align:left">নাম</th>
          <th style="border:1px solid #ccc;padding:5px">প্রাপ্ত</th>
          <th style="border:1px solid #ccc;padding:5px">পূর্ণমান</th>
          <th style="border:1px solid #ccc;padding:5px">%</th>
          <th style="border:1px solid #ccc;padding:5px">গ্রেড</th>
          <th style="border:1px solid #ccc;padding:5px">GPA</th>
          <th style="border:1px solid #ccc;padding:5px">ফলাফল</th>
        </tr>
        {rows}
      </table>
      <table style="width:100%;border-collapse:collapse;margin-top:30px;font-size:11px">
        <tr>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">পরীক্ষা নিয়ন্ত্রকের স্বাক্ষর</div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">তারিখ: {date.today()}</div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">মুহতামিমের সিল ও স্বাক্ষর</div>
          </td>
        </tr>
      </table>
    </div>""")


# ── ছাত্র সংখ্যা প্রতিবেদন (সরকারি ফরম) ──
# NOTE: Official government student-strength form — content stays in Bangla
# regardless of UI language, per government-mandated wording rules.
def _strength_html(tenant, session_name, strength_data):
    rows = ""
    total_male = total_female = total_all = 0
    for i, r in enumerate(strength_data, 1):
        m = int(r["male"] or 0)
        f = int(r["female"] or 0)
        t = int(r["total"] or 0)
        total_male += m; total_female += f; total_all += t
        rows += f"""<tr>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{i}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{r['class_name']}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{m}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{f}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:center;font-weight:700">{t}</td>
        </tr>"""
    rows += f"""<tr style="background:#F0F0F0;font-weight:700">
      <td colspan="2" style="border:1px solid #ccc;padding:5px 8px;text-align:right">সর্বমোট</td>
      <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{total_male}</td>
      <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{total_female}</td>
      <td style="border:1px solid #ccc;padding:5px 8px;text-align:center">{total_all}</td>
    </tr>"""

    return flatten_html(f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;font-size:12px">
      <div style="text-align:center;border:2px solid #000;padding:12px;margin-bottom:14px">
        <div style="font-size:14px;font-weight:700">গণপ্রজাতন্ত্রী বাংলাদেশ সরকার</div>
        <div style="font-size:13px;font-weight:700;margin-top:4px">{tenant.get('madrasa_name','')}</div>
        <div style="font-size:10px">{tenant.get('address','')}</div>
        <div style="font-size:13px;font-weight:700;margin-top:8px">
          শিক্ষার্থী সংখ্যা বিবরণী
        </div>
        <div style="font-size:11px">সেশন: {session_name} | তারিখ: {date.today()}</div>
      </div>
      <table style="width:100%;border-collapse:collapse">
        <tr style="background:#D0D0D0">
          <th style="border:1px solid #ccc;padding:6px 8px">ক্রঃ</th>
          <th style="border:1px solid #ccc;padding:6px 8px;text-align:left">শ্রেণী</th>
          <th style="border:1px solid #ccc;padding:6px 8px">ছাত্র</th>
          <th style="border:1px solid #ccc;padding:6px 8px">ছাত্রী</th>
          <th style="border:1px solid #ccc;padding:6px 8px">মোট</th>
        </tr>
        {rows}
      </table>
      <table style="width:100%;border-collapse:collapse;margin-top:30px;font-size:11px">
        <tr>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">প্রধান শিক্ষকের স্বাক্ষর ও সিল</div>
          </td>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">তারিখ: ___________</div>
          </td>
        </tr>
      </table>
    </div>""")


def render():
    tid = get_tenant_id()
    page_header("🏛️", t("govt.page_title"),
                t("govt.page_subtitle"))

    sessions = fetchall("SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC", (tid,))
    classes  = fetchall("SELECT id, class_name FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,))
    if not sessions or not classes:
        alert(t("govt.need_session_class"),"warning"); return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]:   c["id"] for c in classes}
    tenant    = _tenant(tid)

    def _print_btn():
        # raw onclick="..." markdown বাটন ক্লিকে React error #231 ছোঁড়ে —
        # তাই real <iframe> ভিত্তিক print_button() (utils.py) ব্যবহার করা হয়েছে।
        print_button(f"🖨️ {t('govt.print_button')}")

    tab_att, tab_result, tab_strength = st.tabs([
        t("govt.tab_attendance"),
        t("govt.tab_result"),
        t("govt.tab_strength"),
    ])

    with tab_att:
        st.markdown(f"#### {t('govt.monthly_attendance_header')}")
        c1, c2, c3, c4 = st.columns(4)
        sel_sess  = c1.selectbox(t("govt.session_label"),  list(sess_map.keys()), key="ga_sess")
        sel_class = c2.selectbox(t("govt.class_label"), list(class_map.keys()), key="ga_class")
        sel_month = c3.selectbox(t("govt.month_label"), months_list(), key="ga_month")
        sel_year  = c4.number_input(t("govt.year_label"), min_value=2020, max_value=2040,
                                     value=current_year(), key="ga_year")

        if st.button(t("govt.generate_report_button"), type="primary", key="gen_ga"):
            summary = fetchall(
                """SELECT s.name, e.roll_no,
                          COUNT(CASE WHEN a.status='present' THEN 1 END) AS present,
                          COUNT(CASE WHEN a.status='absent'  THEN 1 END) AS absent,
                          COUNT(CASE WHEN a.status='late'    THEN 1 END) AS late,
                          COUNT(a.id) AS total_marked
                   FROM student_enrollments e
                   JOIN students s ON s.id=e.student_id
                   LEFT JOIN attendance a ON a.enrollment_id=e.id
                     AND a.tenant_id=e.tenant_id
                     AND a.date BETWEEN
                       DATE_TRUNC('month', TO_DATE(%s || ' ' || %s || ' 1', 'Month YYYY D'))
                       AND (DATE_TRUNC('month', TO_DATE(%s || ' ' || %s || ' 1', 'Month YYYY D'))
                            + INTERVAL '1 month' - INTERVAL '1 day')
                   WHERE e.tenant_id=%s AND e.class_id=%s AND e.session_id=%s
                     AND e.enrollment_status='active' AND s.status='active'
                   GROUP BY s.name, e.roll_no ORDER BY e.roll_no NULLS LAST, s.name""",
                (sel_month, str(int(sel_year)),
                 sel_month, str(int(sel_year)),
                 tid, class_map[sel_class], sess_map[sel_sess]),
            )
            if not summary:
                alert(t("govt.no_data_found"),"warning")
            else:
                audit_module.log("EXPORT","GovtReport",f"উপস্থিতি রিপোর্ট: {sel_class} {sel_month} {sel_year}")
                _print_btn()
                st.markdown(
                    _monthly_att_report_html(tenant, sel_class, sel_month, int(sel_year), summary),
                    unsafe_allow_html=True,
                )

    with tab_result:
        st.markdown(f"#### {t('govt.exam_result_report_header')}")
        c1, c2 = st.columns(2)
        sel_sess2  = c1.selectbox(t("govt.session_label"),  list(sess_map.keys()), key="gr_sess")
        sel_class2 = c2.selectbox(t("govt.class_label"), list(class_map.keys()), key="gr_class")
        exams = fetchall(
            "SELECT id, exam_name FROM exams WHERE tenant_id=%s AND session_id=%s ORDER BY exam_date",
            (tid, sess_map[sel_sess2]),
        )
        if not exams:
            alert(t("govt.no_exams_in_session"),"info")
        else:
            exam_map = {e["exam_name"]: e["id"] for e in exams}
            sel_exam = st.selectbox(t("govt.exam_label"), list(exam_map.keys()), key="gr_exam")
            if st.button(t("govt.generate_report_button"), type="primary", key="gen_gr"):
                from academic_module import _class_result_summary
                results = _class_result_summary(
                    tid, exam_map[sel_exam], class_map[sel_class2], sess_map[sel_sess2]
                )
                if not results:
                    alert(t("govt.no_marks_data"),"warning")
                else:
                    audit_module.log("EXPORT","GovtReport",f"ফলাফল রিপোর্ট: {sel_class2} {sel_exam}")
                    _print_btn()
                    st.markdown(
                        _annual_result_html(tenant, sel_class2, sel_sess2, sel_exam, results),
                        unsafe_allow_html=True,
                    )

    with tab_strength:
        st.markdown(f"#### {t('govt.student_strength_header')}")
        sel_sess3 = st.selectbox(t("govt.session_label"), list(sess_map.keys()), key="gs_sess")
        if st.button(t("govt.generate_report_button"), type="primary", key="gen_gs"):
            strength = fetchall(
                """SELECT c.class_name,
                          COUNT(CASE WHEN s.gender='Male'   THEN 1 END) AS male,
                          COUNT(CASE WHEN s.gender='Female' THEN 1 END) AS female,
                          COUNT(s.id) AS total
                   FROM classes c
                   LEFT JOIN student_enrollments e ON e.class_id=c.id AND e.tenant_id=c.tenant_id
                     AND e.session_id=%s AND e.enrollment_status='active'
                   LEFT JOIN students s ON s.id=e.student_id AND s.status='active'
                   WHERE c.tenant_id=%s
                   GROUP BY c.class_name, c.class_numeric ORDER BY c.class_numeric""",
                (sess_map[sel_sess3], tid),
            )
            audit_module.log("EXPORT","GovtReport",f"ছাত্র সংখ্যা বিবরণী: {sel_sess3}")
            _print_btn()
            st.markdown(
                _strength_html(tenant, sel_sess3, strength),
                unsafe_allow_html=True,
            )
