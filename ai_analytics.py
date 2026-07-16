"""
ai_analytics.py — AI-Powered Result Analysis  v9.0
Claude API ব্যবহার করে ছাত্রদের দুর্বল বিষয় চিহ্নিতকরণ,
ব্যক্তিগতকৃত উন্নতির পরামর্শ, ক্লাসের ট্রেন্ড বিশ্লেষণ।

পরিবর্তন (v9.0):
- x-api-key header যোগ করা হয়েছে (আগে ছিল না — API কাজই করত না)
- anthropic-version header যোগ করা হয়েছে (required)
- ANTHROPIC_API_KEY env/secrets থেকে নেওয়া হচ্ছে
- Missing API key হলে user-friendly error দেখায়
- HTTP error code handle করা হয়েছে (401, 429, 500)
"""

import os
import streamlit as st
import json
from db import fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id, get_grade, PALETTE
from i18n import t


# ─────────────────────────────────────────────
# Claude API Call
# ─────────────────────────────────────────────

def _get_api_key() -> str | None:
    """ANTHROPIC_API_KEY — Streamlit secrets বা env থেকে পড়ে।"""
    try:
        if hasattr(st, "secrets") and "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


async def _call_claude(prompt: str, system: str = "") -> str:
    """
    Claude API-তে কল করে বাংলায় বিশ্লেষণ নিয়ে আসে।

    Required headers (v9.0 fix):
    - x-api-key: ANTHROPIC_API_KEY
    - anthropic-version: 2023-06-01
    """
    api_key = _get_api_key()
    if not api_key:
        return t("ai.no_api_key")

    try:
        import httpx
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            )

        # HTTP error handling
        if resp.status_code == 401:
            return t("ai.err_invalid_key")
        if resp.status_code == 429:
            return t("ai.err_rate_limit")
        if resp.status_code >= 500:
            return t("ai.err_server", status=resp.status_code)
        if resp.status_code != 200:
            return f"API Error: HTTP {resp.status_code} — {resp.text[:200]}"

        data = resp.json()
        return data["content"][0]["text"] if data.get("content") else t("ai.no_analysis_found")

    except Exception as ex:
        return t("ai.connection_error", err=ex)


def _call_claude_sync(prompt: str, system: str = "") -> str:
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(_call_claude(prompt, system))
    finally:
        loop.close()


# ─────────────────────────────────────────────
# Data preparation
# ─────────────────────────────────────────────

def _student_performance_data(tid, enrollment_id):
    """একজন ছাত্রের সব পরীক্ষার বিস্তারিত তথ্য।"""
    marks = fetchall(
        """SELECT ex.exam_name, subj.subject_name,
                  sm.total_obtained, subj.full_marks, subj.pass_marks,
                  sm.is_absent,
                  ROUND(sm.total_obtained::numeric/NULLIF(subj.full_marks,0)*100,1) AS pct
           FROM student_marks sm
           JOIN exams ex ON ex.id=sm.exam_id
           JOIN subjects subj ON subj.id=sm.subject_id
           WHERE sm.tenant_id=%s AND sm.enrollment_id=%s
           ORDER BY ex.exam_name, subj.subject_name""",
        (tid, enrollment_id),
    )
    return marks


def _class_subject_averages(tid, exam_id, class_id):
    return fetchall(
        """SELECT subj.subject_name,
                  ROUND(AVG(sm.total_obtained)::numeric,1) AS avg,
                  subj.full_marks,
                  MIN(sm.total_obtained) AS min_score,
                  MAX(sm.total_obtained) AS max_score,
                  COUNT(CASE WHEN sm.total_obtained < subj.pass_marks THEN 1 END) AS failed_count,
                  COUNT(sm.id) AS total_students
           FROM student_marks sm
           JOIN subjects subj ON subj.id=sm.subject_id
           JOIN student_enrollments e ON e.id=sm.enrollment_id
           WHERE sm.tenant_id=%s AND sm.exam_id=%s
             AND e.class_id=%s AND sm.is_absent=FALSE
           GROUP BY subj.subject_name, subj.full_marks
           ORDER BY avg ASC""",
        (tid, exam_id, class_id),
    )


def _attendance_trend(tid, enrollment_id):
    rows = fetchall(
        """SELECT EXTRACT(MONTH FROM date) AS month,
                  COUNT(CASE WHEN status='present' THEN 1 END) AS present,
                  COUNT(*) AS total
           FROM attendance
           WHERE tenant_id=%s AND enrollment_id=%s
           GROUP BY EXTRACT(MONTH FROM date)
           ORDER BY month""",
        (tid, enrollment_id),
    )
    return rows


# ─────────────────────────────────────────────
# Analysis prompts
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """তুমি একজন অভিজ্ঞ শিক্ষা বিশ্লেষক। বাংলায় উত্তর দাও।
সংক্ষিপ্ত, স্পষ্ট ও কার্যকর পরামর্শ দাও। 
মার্কডাউন ফরম্যাটে উত্তর দাও — বোল্ড, বুলেট পয়েন্ট ব্যবহার করো।
সর্বোচ্চ ৩০০ শব্দ।"""


def _student_analysis_prompt(student_name, marks_data):
    subjects = {}
    for m in marks_data:
        if m["subject_name"] not in subjects:
            subjects[m["subject_name"]] = []
        if not m["is_absent"]:
            subjects[m["subject_name"]].append({
                "পরীক্ষা": m["exam_name"],
                "প্রাপ্ত": int(m["total_obtained"] or 0),
                "পূর্ণ": int(m["full_marks"]),
                "শতকরা": float(m["pct"] or 0),
            })

    return f"""
ছাত্র: {student_name}

বিষয়ভিত্তিক পারফরম্যান্স:
{json.dumps(subjects, ensure_ascii=False, indent=2)}

এই ডেটা বিশ্লেষণ করে:
১. **দুর্বল বিষয়গুলো** চিহ্নিত করো (৫০%-এর নিচে)
২. **শক্তিশালী বিষয়** উল্লেখ করো
৩. **উন্নতির জন্য ৩টি নির্দিষ্ট পরামর্শ** দাও
৪. **মোট মূল্যায়ন** সংক্ষেপে দাও
"""


def _class_analysis_prompt(class_name, avg_data):
    return f"""
শ্রেণী: {class_name}

বিষয়ভিত্তিক গড় নম্বর:
{json.dumps([{
    "বিষয়": r["subject_name"],
    "গড়": float(r["avg"] or 0),
    "পূর্ণমান": int(r["full_marks"]),
    "ফেল সংখ্যা": int(r["failed_count"]),
    "মোট ছাত্র": int(r["total_students"]),
} for r in avg_data], ensure_ascii=False, indent=2)}

এই ক্লাসের জন্য:
১. **সবচেয়ে দুর্বল বিষয়** ও কারণ বিশ্লেষণ করো
২. **শিক্ষকদের জন্য ৩টি পরামর্শ** দাও
৩. **ক্লাসের সামগ্রিক অবস্থা** মূল্যায়ন করো
"""


def _improvement_plan_prompt(student_name, weak_subjects, att_pct):
    return f"""
ছাত্র: {student_name}
দুর্বল বিষয়: {', '.join(weak_subjects)}
উপস্থিতির হার: {att_pct}%

এই ছাত্রের জন্য একটি **৩০-দিনের উন্নতি পরিকল্পনা** তৈরি করো:
- দৈনিক অধ্যয়নের সময়সূচি
- দুর্বল বিষয়ে মনোযোগ দেওয়ার কৌশল
- অভিভাবকের ভূমিকা
- শিক্ষকের পরামর্শ
"""


# ─────────────────────────────────────────────
# Render
# ─────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("🤖", t("ai.header_title"), t("ai.header_subtitle"))

    sessions = fetchall(
        "SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC", (tid,)
    )
    classes = fetchall(
        "SELECT id, class_name FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,)
    )

    if not sessions or not classes:
        alert(t("ai.no_session_class"),"warning"); return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]:   c["id"] for c in classes}

    tab_student, tab_class, tab_plan = st.tabs([
        t("ai.tab_student"),
        t("ai.tab_class"),
        t("ai.tab_plan"),
    ])

    # ── ব্যক্তিগত বিশ্লেষণ ──
    with tab_student:
        st.markdown(f"#### {t('ai.student_perf_header')}")

        c1, c2 = st.columns(2)
        sel_sess  = c1.selectbox(t("ai.session_label"),  list(sess_map.keys()), key="ai_sess")
        sel_class = c2.selectbox(t("ai.class_label"), list(class_map.keys()), key="ai_class")

        students = fetchall(
            """SELECT s.id, s.name, e.id AS enrollment_id, e.roll_no
               FROM students s
               JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
               WHERE s.tenant_id=%s AND e.session_id=%s AND e.class_id=%s
                 AND s.status='active' ORDER BY e.roll_no""",
            (tid, sess_map[sel_sess], class_map[sel_class]),
        )
        if not students:
            alert(t("ai.no_students_in_class"),"info"); return

        stu_map = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students}
        sel_stu = st.selectbox(t("ai.select_student_label"), list(stu_map.keys()), key="ai_stu")
        stu     = stu_map[sel_stu]

        marks = _student_performance_data(tid, stu["enrollment_id"])
        if not marks:
            alert(t("ai.no_marks_data"),"warning"); return

        # Static performance overview
        import pandas as pd
        subjects_perf = {}
        for m in marks:
            if m["subject_name"] not in subjects_perf:
                subjects_perf[m["subject_name"]] = []
            if not m["is_absent"]:
                subjects_perf[m["subject_name"]].append(float(m["pct"] or 0))

        avg_per_subj = {
            s: round(sum(v)/len(v), 1)
            for s, v in subjects_perf.items() if v
        }

        # KPIs
        if avg_per_subj:
            overall_avg = round(sum(avg_per_subj.values()) / len(avg_per_subj), 1)
            weak_subjs  = [s for s, v in avg_per_subj.items() if v < 50]
            good_subjs  = [s for s, v in avg_per_subj.items() if v >= 70]
            grade, gpa  = get_grade(overall_avg)
            kpi_row([
                {"label": t("ai.kpi_avg_pct"),        "value": f"{overall_avg}%", "cls": "success" if overall_avg >= 50 else "danger"},
                {"label": t("ai.kpi_grade"),           "value": grade,            "cls": "accent"},
                {"label": t("ai.kpi_weak_subjects"),   "value": len(weak_subjs),  "cls": "danger" if weak_subjs else "success"},
                {"label": t("ai.kpi_good_subjects"),   "value": len(good_subjs),  "cls": "success"},
            ])

        # Subject performance chart
        if avg_per_subj:
            col_subject, col_avg_pct, col_status = t("ai.col_subject"), t("ai.col_avg_pct"), t("ai.col_status")
            df = pd.DataFrame([
                {col_subject: s, col_avg_pct: v,
                 col_status: t("ai.status_weak") if v < 50 else (t("ai.status_medium") if v < 70 else t("ai.status_good"))}
                for s, v in avg_per_subj.items()
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)
            chart_color = PALETTE["primary"]
            st.bar_chart(df.set_index(col_subject)[[col_avg_pct]], color=chart_color, height=220)

        divider()

        # AI Analysis button
        if st.button(t("ai.analyze_btn"), type="primary", key="ai_analyze_stu"):
            with st.spinner(t("ai.analyzing_spinner")):
                prompt   = _student_analysis_prompt(stu["name"], marks)
                analysis = _call_claude_sync(prompt, SYSTEM_PROMPT)

            st.markdown(
                f"""<div style="background:linear-gradient(135deg,#EAF4F8,#F0F8FF);
                                border:1px solid #0F4C5C30;border-radius:12px;
                                padding:1.25rem 1.5rem;margin-top:1rem">
                  <div style="font-size:0.8rem;color:#6B7A8D;margin-bottom:8px;font-weight:600">
                    {t("ai.analysis_result_title", name=stu['name'])}
                  </div>
                  <div style="font-size:0.9rem;line-height:1.7">{analysis}</div>
                </div>""",
                unsafe_allow_html=True,
            )

    # ── ক্লাস বিশ্লেষণ ──
    with tab_class:
        st.markdown(f"#### {t('ai.class_analysis_header')}")

        c1, c2 = st.columns(2)
        sel_sess2  = c1.selectbox(t("ai.session_label"),  list(sess_map.keys()), key="ai2_sess")
        sel_class2 = c2.selectbox(t("ai.class_label"), list(class_map.keys()), key="ai2_class")

        exams = fetchall(
            "SELECT id, exam_name FROM exams WHERE tenant_id=%s AND session_id=%s ORDER BY exam_date",
            (tid, sess_map[sel_sess2]),
        )
        if not exams:
            alert(t("ai.no_exams"),"info")
        else:
            exam_map  = {e["exam_name"]: e["id"] for e in exams}
            sel_exam2 = st.selectbox(t("ai.exam_label"), list(exam_map.keys()), key="ai2_exam")

            avg_data  = _class_subject_averages(tid, exam_map[sel_exam2], class_map[sel_class2])
            if not avg_data:
                alert(t("ai.no_exam_data"),"warning")
            else:
                # Static chart
                import pandas as pd
                col_subject   = t("ai.col_subject")
                col_avg_marks = t("ai.col_avg_marks")
                col_full_marks= t("ai.col_full_marks")
                col_fail_count= t("ai.col_fail_count")
                col_avg_pct   = t("ai.col_avg_pct")
                df2 = pd.DataFrame([{
                    col_subject:    r["subject_name"],
                    col_avg_marks:  float(r["avg"] or 0),
                    col_full_marks: int(r["full_marks"]),
                    col_fail_count: int(r["failed_count"]),
                    col_avg_pct:    round(float(r["avg"] or 0)/int(r["full_marks"])*100,1),
                } for r in avg_data])
                st.dataframe(df2, use_container_width=True, hide_index=True)
                st.bar_chart(df2.set_index(col_subject)[[col_avg_pct]], color=PALETTE["danger"], height=220)

                if st.button(t("ai.class_analyze_btn"), type="primary", key="ai_class_btn"):
                    with st.spinner(t("ai.analyzing_spinner")):
                        prompt   = _class_analysis_prompt(sel_class2, avg_data)
                        analysis = _call_claude_sync(prompt, SYSTEM_PROMPT)

                    st.markdown(
                        f"""<div style="background:linear-gradient(135deg,#FFF8E1,#FFFDE7);
                                        border:1px solid #F57F1730;border-radius:12px;
                                        padding:1.25rem 1.5rem;margin-top:1rem">
                          <div style="font-size:0.8rem;color:#6B7A8D;margin-bottom:8px;font-weight:600">
                            {t("ai.class_analysis_result_title", class_name=sel_class2)}
                          </div>
                          <div style="font-size:0.9rem;line-height:1.7">{analysis}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )

    # ── উন্নতি পরিকল্পনা ──
    with tab_plan:
        st.markdown(f"#### {t('ai.improvement_plan_header')}")

        c1, c2 = st.columns(2)
        sel_sess3  = c1.selectbox(t("ai.session_label"),  list(sess_map.keys()), key="ai3_sess")
        sel_class3 = c2.selectbox(t("ai.class_label"), list(class_map.keys()), key="ai3_class")

        students3 = fetchall(
            """SELECT s.id, s.name, e.id AS enrollment_id, e.roll_no
               FROM students s
               JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
               WHERE s.tenant_id=%s AND e.session_id=%s AND e.class_id=%s AND s.status='active'
               ORDER BY e.roll_no""",
            (tid, sess_map[sel_sess3], class_map[sel_class3]),
        )
        if not students3:
            alert(t("ai.no_students"),"info")
        else:
            stu_map3  = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students3}
            sel_stu3  = st.selectbox(t("ai.student_label_short"), list(stu_map3.keys()), key="ai3_stu")
            stu3      = stu_map3[sel_stu3]

            # Attendance
            att = fetchone(
                """SELECT ROUND(COUNT(CASE WHEN status='present' THEN 1 END)::numeric/NULLIF(COUNT(*),0)*100,1) AS pct
                   FROM attendance WHERE tenant_id=%s AND enrollment_id=%s""",
                (tid, stu3["enrollment_id"]),
            )
            att_pct = float(att["pct"]) if att and att["pct"] else 0.0

            # Weak subjects
            marks3 = _student_performance_data(tid, stu3["enrollment_id"])
            subj_avg3 = {}
            for m in marks3:
                if not m["is_absent"]:
                    if m["subject_name"] not in subj_avg3:
                        subj_avg3[m["subject_name"]] = []
                    subj_avg3[m["subject_name"]].append(float(m["pct"] or 0))
            avg3      = {s: sum(v)/len(v) for s, v in subj_avg3.items() if v}
            weak3     = [s for s, v in avg3.items() if v < 60]

            if not weak3 and not avg3:
                alert(t("ai.no_marks_data"),"warning")
            else:
                # Show summary
                kpi_row([
                    {"label": t("ai.kpi_attendance"),      "value": f"{att_pct}%",  "cls": "success" if att_pct >= 75 else "danger"},
                    {"label": t("ai.kpi_weak_subjects"),    "value": len(weak3),    "cls": "danger" if weak3 else "success"},
                ])

                if weak3:
                    st.markdown(f"**{t('ai.weak_subjects_label')}**")
                    for w in weak3:
                        v = avg3[w]
                        color = "#C62828" if v < 40 else "#F57F17"
                        st.markdown(
                            f'<span style="background:{color}20;color:{color};'
                            f'border-radius:4px;padding:3px 10px;font-size:12px;'
                            f'font-weight:700;margin-right:6px">{w}: {v:.1f}%</span>',
                            unsafe_allow_html=True,
                        )
                    st.markdown("<br>", unsafe_allow_html=True)

                if st.button(t("ai.generate_plan_btn"), type="primary", key="ai3_plan"):
                    with st.spinner(t("ai.plan_generating_spinner")):
                        prompt   = _improvement_plan_prompt(stu3["name"], weak3 or list(avg3.keys()), att_pct)
                        plan     = _call_claude_sync(prompt, SYSTEM_PROMPT)

                    st.markdown(
                        f"""<div style="background:linear-gradient(135deg,#E8F5E9,#F1F8E9);
                                        border:1px solid #2E7D3230;border-radius:12px;
                                        padding:1.25rem 1.5rem;margin-top:1rem">
                          <div style="font-size:0.8rem;color:#2E7D32;margin-bottom:8px;font-weight:600">
                            {t("ai.plan_title", name=stu3['name'])}
                          </div>
                          <div style="font-size:0.9rem;line-height:1.7">{plan}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )

                    # Download plan
                    st.download_button(
                        t("ai.download_plan_btn"),
                        data=plan.encode("utf-8"),
                        file_name=f"plan_{stu3['name']}.txt",
                        mime="text/plain",
                    )
