"""
online_exam.py — অনলাইন পরীক্ষা সিস্টেম
MCQ পরীক্ষা অনলাইনে নেওয়া, স্বয়ংক্রিয় মূল্যায়ন, রেজাল্ট।
"""

import streamlit as st
import json
import random
from datetime import datetime, date
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id
import audit_module
from error_handler import safe_db_error
from i18n import t
import bulk_import

DIFFICULTIES = ["easy", "medium", "hard"]

def _create_exam(tid, title, class_id, session_id, subject_id, duration, instructions, shuffle):
    conn = get_connection()
    if not conn: return False, "DB error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO online_exams
                   (tenant_id, title, class_id, session_id, subject_id,
                    duration_mins, instructions, shuffle_questions)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, title, class_id, session_id, subject_id,
                 duration, instructions, shuffle),
            )
            eid = cur.fetchone()["id"]
        conn.commit()
        return True, eid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)

def _add_question(tid, exam_id, question, opt_a, opt_b, opt_c, opt_d, correct, marks, explanation):
    conn = get_connection()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO exam_questions
                   (tenant_id, exam_id, question_text, option_a, option_b,
                    option_c, option_d, correct_option, marks, explanation)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (tid, exam_id, question, opt_a, opt_b, opt_c or None,
                 opt_d or None, correct, marks, explanation or None),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)

def _add_bank_question(tid, subject_id, class_id, topic, difficulty, question,
                        opt_a, opt_b, opt_c, opt_d, correct, marks, explanation):
    conn = get_connection()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO question_bank
                   (tenant_id, subject_id, class_id, topic, difficulty,
                    question_text, option_a, option_b, option_c, option_d,
                    correct_option, marks, explanation, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (tid, subject_id, class_id, topic or None, difficulty, question,
                 opt_a, opt_b, opt_c or None, opt_d or None, correct, marks,
                 explanation or None, st.session_state.get("username", "admin")),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _count_bank_questions(tid, subject_id, class_id, topic=None, difficulty=None):
    params = [tid, subject_id, class_id]
    clause = ""
    if topic:
        clause += " AND topic ILIKE %s"
        params.append(f"%{topic}%")
    if difficulty and difficulty != "all":
        clause += " AND difficulty=%s"
        params.append(difficulty)
    row = fetchone(
        f"""SELECT COUNT(*) AS n FROM question_bank
            WHERE tenant_id=%s AND subject_id=%s AND class_id=%s
              AND is_active=TRUE {clause}""",
        tuple(params),
    )
    return int(row["n"]) if row else 0


def _list_bank_questions(tid, subject_id=None, class_id=None):
    params = [tid]
    clause = ""
    if subject_id:
        clause += " AND qb.subject_id=%s"
        params.append(subject_id)
    if class_id:
        clause += " AND qb.class_id=%s"
        params.append(class_id)
    return fetchall(
        f"""SELECT qb.*, subj.subject_name, c.class_name
            FROM question_bank qb
            LEFT JOIN subjects subj ON subj.id=qb.subject_id
            LEFT JOIN classes c ON c.id=qb.class_id
            WHERE qb.tenant_id=%s AND qb.is_active=TRUE {clause}
            ORDER BY subj.subject_name, qb.topic, qb.difficulty""",
        tuple(params),
    )


def _get_recently_used_bank_ids(tid, subject_id, class_id, exclude_exam_id, lookback=3):
    rows = fetchall(
        """SELECT DISTINCT eq.source_bank_id FROM exam_questions eq
           WHERE eq.source_bank_id IS NOT NULL
             AND eq.exam_id IN (
               SELECT id FROM online_exams
               WHERE tenant_id=%s AND subject_id=%s AND class_id=%s AND id != %s
               ORDER BY created_at DESC LIMIT %s
             )""",
        (tid, subject_id, class_id, exclude_exam_id, lookback),
    )
    return [r["source_bank_id"] for r in rows]


def _auto_generate_questions(tid, exam_id, subject_id, class_id, topic, difficulty, count):
    """প্রশ্ন ব্যাংক থেকে এলোমেলোভাবে বেছে exam_questions-এ কপি করে।
    সাম্প্রতিক পরীক্ষাগুলোয় ব্যবহৃত প্রশ্ন যতটা সম্ভব এড়িয়ে চলে (পুনরাবৃত্তি
    কমাতে), কিন্তু ব্যাংক ছোট হলে সেগুলোও আবার ব্যবহার করে। একই exam-এ আগে
    থেকে bank-sourced প্রশ্ন থাকলে সেগুলো মুছে নতুন করে বসায় (regenerate),
    ম্যানুয়ালি যোগ করা প্রশ্ন অক্ষত রাখে। রিটার্ন করে (picked_count, requested_count)।
    """
    params = [tid, subject_id, class_id]
    clause = ""
    if topic:
        clause += " AND topic ILIKE %s"
        params.append(f"%{topic}%")
    if difficulty and difficulty != "all":
        clause += " AND difficulty=%s"
        params.append(difficulty)
    eligible = fetchall(
        f"""SELECT id FROM question_bank
            WHERE tenant_id=%s AND subject_id=%s AND class_id=%s
              AND is_active=TRUE {clause}""",
        tuple(params),
    )
    eligible_ids = [r["id"] for r in eligible]
    requested = int(count)

    recent_ids = set(_get_recently_used_bank_ids(tid, subject_id, class_id, exam_id))
    fresh_ids = [i for i in eligible_ids if i not in recent_ids]
    pool = fresh_ids if len(fresh_ids) >= requested else eligible_ids

    picked_ids = random.sample(pool, min(requested, len(pool)))
    if not picked_ids:
        return 0, requested

    picked_questions = fetchall(
        "SELECT * FROM question_bank WHERE id = ANY(%s)", (picked_ids,)
    )

    conn = get_connection()
    if not conn:
        return 0, requested
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM exam_questions WHERE exam_id=%s AND source_bank_id IS NOT NULL",
                (exam_id,),
            )
            for q in picked_questions:
                cur.execute(
                    """INSERT INTO exam_questions
                       (tenant_id, exam_id, question_text, option_a, option_b,
                        option_c, option_d, correct_option, marks, explanation,
                        source_bank_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tid, exam_id, q["question_text"], q["option_a"], q["option_b"],
                     q["option_c"], q["option_d"], q["correct_option"], q["marks"],
                     q["explanation"], q["id"]),
                )
        conn.commit()
        return len(picked_questions), requested
    except Exception:
        conn.rollback()
        return 0, requested
    finally:
        release_connection(conn)


def _toggle_exam(tid, exam_id, active):
    conn = get_connection()
    if not conn: return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE online_exams SET is_active=%s WHERE id=%s AND tenant_id=%s",
                (active, exam_id, tid),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)

def _submit_exam(tid, exam_id, enrollment_id, student_name, answers: dict):
    questions = fetchall(
        "SELECT id, correct_option, marks FROM exam_questions WHERE tenant_id=%s AND exam_id=%s",
        (tid, exam_id),
    )
    score = sum(
        int(q["marks"]) for q in questions
        if str(answers.get(str(q["id"]))) == q["correct_option"]
    )
    total = sum(int(q["marks"]) for q in questions)
    pct   = round(score / total * 100, 2) if total else 0

    conn = get_connection()
    if not conn: return False, 0, 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO exam_submissions
                   (tenant_id, exam_id, enrollment_id, student_name,
                    answers, score, total_marks, percentage, submitted_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                   ON CONFLICT (exam_id, enrollment_id) DO UPDATE
                     SET answers=%s, score=%s, total_marks=%s, percentage=%s, submitted_at=NOW()""",
                (tid, exam_id, enrollment_id, student_name,
                 json.dumps(answers), score, total, pct,
                 json.dumps(answers), score, total, pct),
            )
        conn.commit()
        return True, score, total
    except Exception:
        conn.rollback()
        return False, 0, 0
    finally:
        release_connection(conn)


# ── Render ──
def render():
    tid = get_tenant_id()
    page_header("💻", t("oexam.page_title"), t("oexam.page_subtitle"))

    sessions = fetchall("SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC", (tid,))
    classes  = fetchall("SELECT id, class_name FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,))
    subjects = fetchall("SELECT id, subject_name FROM subjects WHERE tenant_id=%s ORDER BY subject_name", (tid,))

    if not sessions or not classes:
        alert(t("oexam.need_session_class"),"warning"); return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]:   c["id"] for c in classes}
    subj_map  = {s["subject_name"]: s["id"] for s in subjects}

    tab_create, tab_questions, tab_bank, tab_manage, tab_take, tab_results = st.tabs([
        t("oexam.tab_create"), t("oexam.tab_add_question"), t("oexam.tab_bank"),
        t("oexam.tab_manage"), t("oexam.tab_take_exam"), t("oexam.tab_results")
    ])

    difficulty_label = {
        "easy": t("oexam.difficulty_easy"), "medium": t("oexam.difficulty_medium"),
        "hard": t("oexam.difficulty_hard"),
    }

    with tab_create:
        st.markdown(f"#### {t('oexam.create_new_exam_header')}")
        with st.form("create_exam_form"):
            title    = st.text_input(t("oexam.exam_title_label"), placeholder=t("oexam.exam_title_placeholder"))
            c1, c2, c3 = st.columns(3)
            sel_sess = c1.selectbox(t("oexam.session_label"), list(sess_map.keys()))
            sel_cls  = c2.selectbox(t("oexam.class_label"), list(class_map.keys()))
            sel_subj = c3.selectbox(t("oexam.subject_label"), [t("oexam.all_subjects")] + list(subj_map.keys()))
            c4, c5 = st.columns(2)
            duration = c4.number_input(t("oexam.duration_label"), min_value=5, max_value=180, value=30)
            shuffle  = c5.checkbox(t("oexam.shuffle_questions_label"), value=True)
            instructions = st.text_area(t("oexam.instructions_label"), height=80,
                                         placeholder=t("oexam.instructions_placeholder"))
            if st.form_submit_button(t("oexam.create_exam_button"), type="primary"):
                if not title.strip():
                    st.error(t("oexam.title_required"))
                else:
                    subj_id = subj_map.get(sel_subj)
                    ok, result = _create_exam(
                        tid, title.strip(), class_map[sel_cls], sess_map[sel_sess],
                        subj_id, int(duration), instructions.strip(), shuffle
                    )
                    if ok:
                        audit_module.log("CREATE","OnlineExam",f"পরীক্ষা তৈরি: {title}","online_exam",result)
                        st.success(t("oexam.exam_created_success", id=result))
                        st.rerun()
                    else:
                        st.error(result)

    with tab_questions:
        st.markdown(f"#### {t('oexam.add_question_header')}")
        exams = fetchall("SELECT id, title FROM online_exams WHERE tenant_id=%s ORDER BY id DESC", (tid,))
        if not exams:
            alert(t("oexam.create_exam_first"),"info")
        else:
            exam_map = {e["title"]: e["id"] for e in exams}
            sel_exam = st.selectbox(t("oexam.select_exam"), list(exam_map.keys()), key="q_exam")
            exam_id  = exam_map[sel_exam]

            # Question count
            q_count = fetchone("SELECT COUNT(*) AS n FROM exam_questions WHERE tenant_id=%s AND exam_id=%s", (tid, exam_id))
            st.info(t("oexam.current_question_count", n=int(q_count['n']) if q_count else 0))

            with st.form("add_question_form"):
                question = st.text_area(t("oexam.question_label"), placeholder=t("oexam.question_placeholder"), height=80)
                c1, c2 = st.columns(2)
                opt_a = c1.text_input(t("oexam.option_a_label"))
                opt_b = c2.text_input(t("oexam.option_b_label"))
                c3, c4 = st.columns(2)
                opt_c = c3.text_input(t("oexam.option_c_label"))
                opt_d = c4.text_input(t("oexam.option_d_label"))
                c5, c6 = st.columns(2)
                correct = c5.selectbox(t("oexam.correct_answer_label"), ["A","B","C","D"])
                marks   = c6.number_input(t("oexam.marks_label"), min_value=1, value=1)
                explanation = st.text_input(t("oexam.explanation_label"))

                if st.form_submit_button(t("oexam.add_question_button"), type="primary"):
                    if not question.strip() or not opt_a.strip() or not opt_b.strip():
                        st.error(t("oexam.question_ab_required"))
                    else:
                        ok = _add_question(tid, exam_id, question.strip(),
                                            opt_a.strip(), opt_b.strip(),
                                            opt_c.strip(), opt_d.strip(),
                                            correct, int(marks), explanation.strip())
                        if ok:
                            st.success(t("oexam.question_added_success"))
                            st.rerun()

            # ── অটোমেটিক প্রশ্ন জেনারেটর — প্রশ্ন ব্যাংক থেকে ──
            divider()
            st.markdown(f"#### {t('oexam.autogen_heading')}")
            exam_row = fetchone(
                "SELECT subject_id, class_id FROM online_exams WHERE id=%s AND tenant_id=%s",
                (exam_id, tid),
            )
            ex_subj_id, ex_class_id = exam_row["subject_id"], exam_row["class_id"]
            if not ex_subj_id or not ex_class_id:
                alert(t("oexam.autogen_needs_subject_class"), "info")
            else:
                ac1, ac2, ac3 = st.columns(3)
                gen_topic = ac1.text_input(t("oexam.autogen_topic_label"), key="gen_topic")
                gen_diff  = ac2.selectbox(
                    t("oexam.bank_difficulty_label"), ["all"] + DIFFICULTIES,
                    format_func=lambda d: t("oexam.difficulty_all") if d == "all" else difficulty_label[d],
                    key="gen_diff",
                )
                gen_count = ac3.number_input(t("oexam.autogen_count_label"), min_value=1, max_value=100, value=10, key="gen_count")

                available_n = _count_bank_questions(tid, ex_subj_id, ex_class_id, gen_topic.strip(), gen_diff)
                st.caption(t("oexam.autogen_available_count", n=available_n))

                existing_bank_count = fetchone(
                    "SELECT COUNT(*) AS n FROM exam_questions WHERE exam_id=%s AND source_bank_id IS NOT NULL",
                    (exam_id,),
                )
                already_generated = int(existing_bank_count["n"]) > 0 if existing_bank_count else False
                gen_label = t("oexam.autogen_regenerate_button") if already_generated else t("oexam.autogen_button")

                if st.button(gen_label, key="gen_btn", type="primary"):
                    if available_n == 0:
                        st.warning(t("oexam.autogen_none_available"))
                    else:
                        picked, requested = _auto_generate_questions(
                            tid, exam_id, ex_subj_id, ex_class_id,
                            gen_topic.strip(), gen_diff, int(gen_count),
                        )
                        if picked == requested:
                            st.success(t("oexam.autogen_success", n=picked))
                        else:
                            st.warning(t("oexam.autogen_partial_warning", picked=picked, requested=requested))
                        st.rerun()

            # Show existing questions
            divider()
            questions = fetchall(
                "SELECT * FROM exam_questions WHERE tenant_id=%s AND exam_id=%s ORDER BY id",
                (tid, exam_id),
            )
            if questions:
                for i, q in enumerate(questions, 1):
                    badge = f" {t('oexam.bank_source_badge')}" if q.get("source_bank_id") else ""
                    with st.expander(t("oexam.question_expander_title", n=i, text=q['question_text'][:60]) + badge):
                        st.markdown(f"""
                        - {t('oexam.option_marker_a')} {q['option_a']}
                        - {t('oexam.option_marker_b')} {q['option_b']}
                        - {t('oexam.option_marker_c')} {q['option_c'] or '—'}
                        - {t('oexam.option_marker_d')} {q['option_d'] or '—'}
                        - **{t('oexam.correct_answer_label')}: {q['correct_option']}** | {t('oexam.marks_label')}: {q['marks']}
                        """)

    with tab_bank:
        st.markdown(f"#### {t('oexam.bank_heading')}")
        with st.form("add_bank_question_form"):
            b1, b2 = st.columns(2)
            bsubj = b1.selectbox(t("oexam.subject_label"), list(subj_map.keys()), key="bank_subj")
            bcls  = b2.selectbox(t("oexam.class_label"), list(class_map.keys()), key="bank_cls")
            b3, b4 = st.columns(2)
            btopic = b3.text_input(t("oexam.bank_topic_label"), key="bank_topic")
            bdiff  = b4.selectbox(
                t("oexam.bank_difficulty_label"), DIFFICULTIES,
                format_func=lambda d: difficulty_label[d], index=1, key="bank_diff",
            )
            bquestion = st.text_area(t("oexam.question_label"), placeholder=t("oexam.question_placeholder"), height=80, key="bank_q")
            b5, b6 = st.columns(2)
            bopt_a = b5.text_input(t("oexam.option_a_label"), key="bank_a")
            bopt_b = b6.text_input(t("oexam.option_b_label"), key="bank_b")
            b7, b8 = st.columns(2)
            bopt_c = b7.text_input(t("oexam.option_c_label"), key="bank_c")
            bopt_d = b8.text_input(t("oexam.option_d_label"), key="bank_d")
            b9, b10 = st.columns(2)
            bcorrect = b9.selectbox(t("oexam.correct_answer_label"), ["A","B","C","D"], key="bank_correct")
            bmarks   = b10.number_input(t("oexam.marks_label"), min_value=1, value=1, key="bank_marks")
            bexplanation = st.text_input(t("oexam.explanation_label"), key="bank_expl")

            if st.form_submit_button(t("oexam.bank_add_button"), type="primary"):
                if not bquestion.strip() or not bopt_a.strip() or not bopt_b.strip():
                    st.error(t("oexam.question_ab_required"))
                else:
                    ok = _add_bank_question(
                        tid, subj_map[bsubj], class_map[bcls], btopic.strip(), bdiff,
                        bquestion.strip(), bopt_a.strip(), bopt_b.strip(),
                        bopt_c.strip(), bopt_d.strip(), bcorrect, int(bmarks), bexplanation.strip(),
                    )
                    if ok:
                        st.success(t("oexam.bank_question_added_success"))
                        st.rerun()

        divider()
        st.markdown(f"#### {t('bulk.heading_question_bank')}")
        st.caption(t("bulk.intro_question_bank"))
        st.download_button(
            t("bulk.download_template"),
            bulk_import.question_bank_template_csv(),
            "question_bank_template.csv",
            "text/csv",
        )
        uploaded = st.file_uploader(t("bulk.upload_label"), type=["csv", "xlsx"], key="qb_bulk_upload")
        if uploaded:
            df, err = bulk_import.parse_upload(uploaded)
            if err:
                st.error(err)
            else:
                validated = bulk_import.validate_question_bank(tid, df)
                n_err  = sum(1 for r in validated if r["errors"])
                n_warn = sum(1 for r in validated if not r["errors"] and r["warnings"])
                n_ok   = len(validated) - n_err - n_warn

                st.markdown(f"**{t('bulk.preview_heading', total=len(validated), ok=n_ok, warn=n_warn, err=n_err)}**")
                st.dataframe(bulk_import.preview_dataframe(validated), use_container_width=True, hide_index=True)

                valid_rows = [r for r in validated if not r["errors"]]
                if n_err:
                    alert(t("bulk.err_rows_notice"), "warning")

                if not valid_rows:
                    st.info(t("bulk.no_valid_rows"))
                elif st.button(t("bulk.btn_commit", count=len(valid_rows)), type="primary", key="qb_bulk_commit"):
                    created, failures = bulk_import.commit_question_bank(
                        tid, valid_rows, st.session_state.get("username", "admin")
                    )
                    if created:
                        audit_module.log("IMPORT", "OnlineExam", f"বাল্ক ইম্পোর্ট — {created} bank questions")
                        st.success(t("bulk.import_success", count=created))
                    if failures:
                        details = "; ".join(f"row {rn}: {msg}" for rn, msg in failures)
                        st.warning(t("bulk.import_partial_fail", count=len(failures), details=details))
                    if created:
                        st.rerun()

        divider()
        st.markdown(f"#### {t('oexam.bank_list_heading')}")
        bank_questions = _list_bank_questions(tid)
        if not bank_questions:
            alert(t("oexam.autogen_none_available"), "info")
        else:
            from collections import defaultdict
            grouped = defaultdict(list)
            for q in bank_questions:
                key = (q["subject_name"] or "—", q["topic"] or "—", q["difficulty"])
                grouped[key].append(q)
            for (subj_name, topic_name, diff), qs in sorted(grouped.items()):
                st.markdown(f"**{subj_name} → {topic_name} → {difficulty_label.get(diff, diff)}** — {len(qs)}")

    with tab_manage:
        st.markdown(f"#### {t('oexam.manage_header')}")
        exams2 = fetchall(
            """SELECT oe.*, c.class_name, subj.subject_name,
                      (SELECT COUNT(*) FROM exam_questions WHERE exam_id=oe.id) AS q_count,
                      (SELECT COUNT(*) FROM exam_submissions WHERE exam_id=oe.id) AS sub_count
               FROM online_exams oe
               LEFT JOIN classes c ON c.id=oe.class_id
               LEFT JOIN subjects subj ON subj.id=oe.subject_id
               WHERE oe.tenant_id=%s ORDER BY oe.id DESC""",
            (tid,),
        )
        if not exams2:
            alert(t("oexam.no_exams"),"info")
        else:
            for e in exams2:
                status_color = "#2E7D32" if e["is_active"] else "#C62828"
                status_text  = t("oexam.status_active") if e["is_active"] else t("oexam.status_closed")
                with st.expander(f"**{e['title']}** — {e['class_name'] or '—'} | {status_text}"):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric(t("oexam.metric_questions"), e["q_count"])
                    c2.metric(t("oexam.metric_submissions"), e["sub_count"])
                    c3.metric(t("oexam.metric_duration"), t("oexam.minutes_value", n=e['duration_mins']))
                    c4.metric(t("oexam.metric_subject"), e["subject_name"] or t("oexam.all_short"))

                    col_a, col_d = st.columns(2)
                    if e["is_active"]:
                        if col_a.button(t("oexam.close_exam_button"), key=f"deact_{e['id']}"):
                            _toggle_exam(tid, e["id"], False)
                            st.rerun()
                    else:
                        if col_a.button(t("oexam.activate_exam_button"), key=f"act_{e['id']}", type="primary"):
                            _toggle_exam(tid, e["id"], True)
                            audit_module.log("UPDATE","OnlineExam",f"পরীক্ষা চালু: {e['title']}")
                            st.rerun()

    with tab_take:
        st.markdown(f"#### {t('oexam.take_exam_header')}")
        active_exams = fetchall(
            """SELECT oe.*, c.class_name FROM online_exams oe
               LEFT JOIN classes c ON c.id=oe.class_id
               WHERE oe.tenant_id=%s AND oe.is_active=TRUE ORDER BY oe.id DESC""",
            (tid,),
        )
        if not active_exams:
            alert(t("oexam.no_active_exams"),"info")
        else:
            exam_sel_map = {f"{e['title']} — {e['class_name'] or ''}": e for e in active_exams}
            sel_ae = st.selectbox(t("oexam.select_exam"), list(exam_sel_map.keys()), key="take_exam")
            ae     = exam_sel_map[sel_ae]

            # Student selection
            students = fetchall(
                """SELECT s.id, s.name, e.id AS enrollment_id, e.roll_no
                   FROM students s
                   JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
                   WHERE s.tenant_id=%s AND s.status='active'
                   ORDER BY s.name""",
                (tid,),
            )
            if not students:
                alert(t("oexam.no_active_students"),"warning")
            else:
                stu_map2 = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students}
                sel_stu2 = st.selectbox(t("oexam.select_student"), list(stu_map2.keys()), key="take_stu")
                stu2     = stu_map2[sel_stu2]

                # Already submitted?
                existing = fetchone(
                    "SELECT score, total_marks, percentage FROM exam_submissions WHERE exam_id=%s AND enrollment_id=%s",
                    (ae["id"], stu2["enrollment_id"]),
                )
                if existing:
                    st.success(t("oexam.already_submitted", score=existing['score'], total=existing['total_marks'], pct=existing['percentage']))
                else:
                    questions = fetchall(
                        "SELECT * FROM exam_questions WHERE tenant_id=%s AND exam_id=%s ORDER BY order_no, id",
                        (tid, ae["id"]),
                    )
                    if not questions:
                        alert(t("oexam.no_questions_in_exam"),"warning")
                    else:
                        if ae["shuffle_questions"]:
                            random.shuffle(questions)

                        if ae["instructions"]:
                            st.info(t("oexam.instructions_display", text=ae['instructions']))

                        with st.form("take_exam_form"):
                            st.markdown(
                                t("oexam.exam_meta_line", title=ae['title'], duration=ae['duration_mins'], count=len(questions))
                            )
                            divider()
                            answers = {}
                            for i, q in enumerate(questions, 1):
                                st.markdown(f"**{i}. {q['question_text']}** *({t('oexam.marks_label')}: {q['marks']})*")
                                opts = {"A": q["option_a"], "B": q["option_b"]}
                                if q["option_c"]: opts["C"] = q["option_c"]
                                if q["option_d"]: opts["D"] = q["option_d"]
                                opt_labels = [f"({k}) {v}" for k, v in opts.items()]
                                ans = st.radio(t("oexam.answer_label"), opt_labels, key=f"ans_{q['id']}", horizontal=True, index=None)
                                if ans:
                                    answers[str(q["id"])] = ans[1]  # Extract A/B/C/D
                                st.markdown("---")

                            if st.form_submit_button(t("oexam.submit_exam_button"), type="primary", use_container_width=True):
                                ok, score, total = _submit_exam(
                                    tid, ae["id"], stu2["enrollment_id"], stu2["name"], answers
                                )
                                if ok:
                                    pct = round(score/total*100, 1) if total else 0
                                    st.success(t("oexam.exam_submitted_success", score=score, total=total, pct=pct))
                                    st.rerun()

    with tab_results:
        st.markdown(f"#### {t('oexam.results_header')}")
        exams3 = fetchall("SELECT id, title FROM online_exams WHERE tenant_id=%s ORDER BY id DESC", (tid,))
        if exams3:
            exam_map3 = {e["title"]: e["id"] for e in exams3}
            sel_e3    = st.selectbox(t("oexam.exam_label"), list(exam_map3.keys()), key="res_exam")
            subs = fetchall(
                """SELECT es.student_name, es.score, es.total_marks, es.percentage,
                          es.submitted_at
                   FROM exam_submissions es
                   WHERE es.tenant_id=%s AND es.exam_id=%s
                   ORDER BY es.score DESC""",
                (tid, exam_map3[sel_e3]),
            )
            if not subs:
                alert(t("oexam.no_submissions_yet"),"info")
            else:
                avg_pct = sum(float(s["percentage"]) for s in subs) / len(subs)
                passed  = sum(1 for s in subs if float(s["percentage"]) >= 50)
                kpi_row([
                    {"label": t("oexam.kpi_total_candidates"), "value": len(subs),     "cls": ""},
                    {"label": t("oexam.kpi_passed"),        "value": passed,         "cls": "success"},
                    {"label": t("oexam.kpi_failed"),        "value": len(subs)-passed,"cls": "danger"},
                    {"label": t("oexam.kpi_avg_score"),         "value": f"{avg_pct:.1f}%","cls": "accent"},
                ])
                rows = [{
                    t("oexam.col_rank"):  i+1,
                    t("oexam.col_name"):   s["student_name"],
                    t("oexam.col_score"): f"{s['score']}/{s['total_marks']}",
                    "%":     f"{float(s['percentage']):.1f}%",
                    t("oexam.col_result"): t("oexam.result_pass") if float(s["percentage"]) >= 50 else t("oexam.result_fail"),
                    t("oexam.col_time"):   str(s["submitted_at"])[:16],
                } for i, s in enumerate(subs)]
                st.dataframe(rows, use_container_width=True, hide_index=True)
