"""
teacher_module.py — Teacher Management
Profiles, class/subject assignments, salary ledger, and performance overview.
"""

import streamlit as st
from datetime import date
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id
import audit_module
from error_handler import safe_db_error
from i18n import t


# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_teachers(tid):
    return fetchall(
        "SELECT * FROM teachers WHERE tenant_id=%s ORDER BY name", (tid,)
    )


def _teacher_kpis(tid):
    total  = fetchone("SELECT COUNT(*) AS n FROM teachers WHERE tenant_id=%s", (tid,))
    active = fetchone("SELECT COUNT(*) AS n FROM teachers WHERE tenant_id=%s AND status='active'", (tid,))
    sal    = fetchone(
        "SELECT COALESCE(SUM(monthly_salary),0) AS n FROM teachers WHERE tenant_id=%s AND status='active'",
        (tid,),
    )
    paid_this_month = fetchone(
        """SELECT COALESCE(SUM(net_salary),0) AS n FROM teacher_salary
           WHERE tenant_id=%s AND status='paid'
             AND EXTRACT(MONTH FROM payment_date)=EXTRACT(MONTH FROM CURRENT_DATE)
             AND EXTRACT(YEAR FROM payment_date)=EXTRACT(YEAR FROM CURRENT_DATE)""",
        (tid,),
    )
    return (
        int(total["n"]) if total else 0,
        int(active["n"]) if active else 0,
        float(sal["n"]) if sal else 0.0,
        float(paid_this_month["n"]) if paid_this_month else 0.0,
    )


def _create_teacher(tid, data):
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO teachers
                   (tenant_id, name, father_name, mobile_no, email, nid_no,
                    designation, joining_date, monthly_salary, qualification,
                    present_address, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')
                   RETURNING id""",
                (tid, data["name"], data["father_name"], data["mobile"],
                 data["email"], data["nid"], data["designation"],
                 data["joining_date"], data["salary"],
                 data["qualification"], data["address"]),
            )
            tid_result = cur.fetchone()["id"]
        conn.commit()
        return True, tid_result
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _update_teacher_status(tid, teacher_id, status):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE teachers SET status=%s WHERE id=%s AND tenant_id=%s",
                (status, teacher_id, tid),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _assign_class(tid, teacher_id, class_id, subject_id, session_id, is_class_teacher):
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO teacher_assignments
                   (tenant_id, teacher_id, class_id, subject_id, session_id, is_class_teacher)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, teacher_id, class_id, subject_id, session_id, is_class_teacher),
            )
        conn.commit()
        return True, None
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _get_assignments(tid, teacher_id):
    return fetchall(
        """SELECT ta.id, c.class_name, subj.subject_name,
                  sess.session_name, ta.is_class_teacher
           FROM teacher_assignments ta
           LEFT JOIN classes c ON c.id=ta.class_id
           LEFT JOIN subjects subj ON subj.id=ta.subject_id
           LEFT JOIN academic_sessions sess ON sess.id=ta.session_id
           WHERE ta.tenant_id=%s AND ta.teacher_id=%s
           ORDER BY ta.id DESC""",
        (tid, teacher_id),
    )


def _pay_salary(tid, teacher_id, month, year, basic, bonus, deduction, method, remarks):
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO teacher_salary
                   (tenant_id, teacher_id, month_name, year, basic_salary,
                    bonus, deduction, payment_date, payment_method, status, remarks)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'paid',%s)
                   ON CONFLICT (tenant_id, teacher_id, month_name, year)
                   DO UPDATE SET basic_salary=%s, bonus=%s, deduction=%s,
                     payment_date=%s, payment_method=%s, status='paid', remarks=%s
                   RETURNING id""",
                (tid, teacher_id, month, year, basic, bonus, deduction,
                 date.today(), method, remarks,
                 basic, bonus, deduction, date.today(), method, remarks),
            )
        conn.commit()
        return True, None
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _salary_history(tid, teacher_id):
    return fetchall(
        """SELECT month_name, year, basic_salary, bonus, deduction,
                  net_salary, payment_date, status, payment_method
           FROM teacher_salary
           WHERE tenant_id=%s AND teacher_id=%s
           ORDER BY year DESC, id DESC""",
        (tid, teacher_id),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Render
# ──────────────────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("👨‍🏫", t("teacher.page_title"), t("teacher.page_subtitle"))

    total, active, monthly_bill, paid_this_month = _teacher_kpis(tid)
    kpi_row([
        {"label": t("teacher.kpi_total"),       "value": total,               "cls": ""},
        {"label": t("teacher.kpi_active"),           "value": active,              "cls": "success"},
        {"label": t("teacher.kpi_monthly_bill"),   "value": f"৳{monthly_bill:,.0f}", "cls": "accent"},
        {"label": t("teacher.kpi_paid_this_month"),   "value": f"৳{paid_this_month:,.0f}", "cls": "success"},
    ])

    tab_list, tab_add, tab_assign, tab_salary = st.tabs([
        t("teacher.tab_list"), t("teacher.tab_add"), t("teacher.tab_assign"), t("teacher.tab_salary")
    ])

    teachers = _get_teachers(tid)
    teacher_map = {t["name"]: t for t in teachers}

    # ── তালিকা ──
    with tab_list:
        if not teachers:
            alert(t("teacher.no_teachers"), "info")
        else:
            search = st.text_input(t("teacher.search_label"), key="tch_search")
            filtered = [
                tch for tch in teachers
                if not search or search.lower() in (tch["name"] or "").lower()
                or search in (tch["mobile_no"] or "")
            ]

            for tch in filtered:
                status_icon = "🟢" if tch["status"] == "active" else "🔴"
                with st.expander(f"{status_icon} **{tch['name']}** — {tch['designation']} | ৳{float(tch['monthly_salary'] or 0):,.0f}{t('teacher.per_month_suffix')}"):
                    c1, c2, c3 = st.columns([2, 2, 1])
                    with c1:
                        st.markdown(f"""
                        - **{t('teacher.father_name_label')}:** {tch['father_name'] or '—'}
                        - **{t('teacher.mobile_label')}:** {tch['mobile_no'] or '—'}
                        - **{t('teacher.email_label')}:** {tch['email'] or '—'}
                        - **NID:** {tch['nid_no'] or '—'}
                        """)
                    with c2:
                        st.markdown(f"""
                        - **{t('teacher.joining_label')}:** {str(tch['joining_date']) if tch['joining_date'] else '—'}
                        - **{t('teacher.qualification_label')}:** {tch['qualification'] or '—'}
                        - **{t('teacher.address_label')}:** {tch['present_address'] or '—'}
                        """)
                    with c3:
                        if tch["status"] == "active":
                            if st.button(t("teacher.deactivate_btn"), key=f"deact_{tch['id']}"):
                                _update_teacher_status(tid, tch["id"], "inactive")
                                st.rerun()
                        else:
                            if st.button(t("teacher.activate_btn"), key=f"act_{tch['id']}"):
                                _update_teacher_status(tid, tch["id"], "active")
                                st.rerun()

    # ── নতুন শিক্ষক ──
    with tab_add:
        st.markdown(f"#### {t('teacher.add_heading')}")
        with st.form("teacher_form"):
            c1, c2 = st.columns(2)
            name        = c1.text_input(t("teacher.name_label"), placeholder=t("teacher.name_placeholder"))
            father_name = c2.text_input(t("teacher.father_name_label"))
            c3, c4 = st.columns(2)
            mobile = c3.text_input(t("teacher.mobile_label"), placeholder="01XXXXXXXXX")
            email  = c4.text_input(t("teacher.email_label"))
            c5, c6 = st.columns(2)
            designation = c5.selectbox(t("teacher.designation_label"), ["Teacher", "Head Teacher", "Assistant Teacher",
                                                  "Hafiz", "Qari", "Mufti", "Other"])
            salary = c6.number_input(t("teacher.salary_label"), min_value=0, value=8000, step=500)
            c7, c8 = st.columns(2)
            joining_date = c7.date_input(t("teacher.joining_date_label"), value=date.today())
            nid          = c8.text_input(t("teacher.nid_label"))
            qualification = st.text_input(t("teacher.qualification_input_label"), placeholder=t("teacher.qualification_placeholder"))
            address = st.text_area(t("teacher.present_address_label"), height=60)

            if st.form_submit_button(t("teacher.add_submit_btn"), type="primary"):
                if not name.strip():
                    st.error(t("teacher.err_name_required"))
                else:
                    ok, result = _create_teacher(tid, {
                        "name": name.strip(), "father_name": father_name.strip(),
                        "mobile": mobile.strip(), "email": email.strip(),
                        "nid": nid.strip(), "designation": designation,
                        "joining_date": str(joining_date), "salary": salary,
                        "qualification": qualification.strip(),
                        "address": address.strip(),
                    })
                    if ok:
                        st.success(t("teacher.success_added", id=result))
                        st.rerun()
                    else:
                        st.error(result)

    # ── ক্লাস অ্যাসাইনমেন্ট ──
    with tab_assign:
        st.markdown(f"#### {t('teacher.assign_heading')}")
        if not teachers:
            alert(t("teacher.add_teacher_first"), "warning")
        else:
            sel_tch = st.selectbox(t("teacher.select_teacher_label"), list(teacher_map.keys()), key="asgn_tch")
            tch = teacher_map[sel_tch]

            sessions = fetchall(
                "SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC", (tid,)
            )
            classes  = fetchall(
                "SELECT id, class_name FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,)
            )
            subjects = fetchall(
                "SELECT id, subject_name FROM subjects WHERE tenant_id=%s ORDER BY id", (tid,)
            )

            if sessions and classes:
                with st.form("assign_form"):
                    sess_map = {s["session_name"]: s["id"] for s in sessions}
                    cls_map  = {c["class_name"]: c["id"] for c in classes}
                    subj_map = {s["subject_name"]: s["id"] for s in subjects} if subjects else {}

                    c1, c2 = st.columns(2)
                    sel_sess = c1.selectbox(t("teacher.session_label"), list(sess_map.keys()))
                    sel_cls  = c2.selectbox(t("teacher.class_label"), list(cls_map.keys()))
                    NO_SUBJECT = t("teacher.no_subject_selected")
                    sel_subj = st.selectbox(t("teacher.subject_label"), [NO_SUBJECT] + list(subj_map.keys()))
                    is_ct    = st.checkbox(t("teacher.class_teacher_checkbox"))

                    if st.form_submit_button(t("teacher.assign_btn"), type="primary"):
                        subj_id = subj_map.get(sel_subj) if sel_subj != NO_SUBJECT else None
                        ok, err = _assign_class(
                            tid, tch["id"], cls_map[sel_cls], subj_id,
                            sess_map[sel_sess], is_ct
                        )
                        if ok:
                            st.success(t("teacher.assign_success"))
                            st.rerun()
                        else:
                            st.error(err)

            divider()
            st.markdown(f"**{t('teacher.current_assignments_heading', name=sel_tch)}**")
            assignments = _get_assignments(tid, tch["id"])
            if assignments:
                rows = [
                    {
                        t("teacher.col_class"):    a["class_name"] or "—",
                        t("teacher.col_subject"):    a["subject_name"] or "—",
                        t("teacher.col_session"):    a["session_name"] or "—",
                        t("teacher.col_class_teacher"): "✅" if a["is_class_teacher"] else "—",
                    }
                    for a in assignments
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                alert(t("teacher.no_assignments"), "info")

    # ── বেতন ──
    with tab_salary:
        st.markdown(f"#### {t('teacher.salary_heading')}")
        if not teachers:
            alert(t("teacher.add_teacher_first"), "warning")
        else:
            active_teachers = [tch for tch in teachers if tch["status"] == "active"]
            if not active_teachers:
                alert(t("teacher.no_active_teachers"), "warning")
            else:
                sal_map = {tch["name"]: tch for tch in active_teachers}
                sel_sal = st.selectbox(t("teacher.teacher_label"), list(sal_map.keys()), key="sal_tch")
                tch2    = sal_map[sel_sal]

                with st.form("salary_form"):
                    from utils import months_list, current_year
                    c1, c2 = st.columns(2)
                    month = c1.selectbox(t("teacher.month_label"), months_list())
                    year  = c2.number_input(t("teacher.year_label"), min_value=2020, max_value=2040,
                                             value=current_year())
                    c3, c4, c5 = st.columns(3)
                    basic     = c3.number_input(t("teacher.basic_salary_label"), min_value=0,
                                                 value=int(tch2["monthly_salary"] or 0), step=500)
                    bonus     = c4.number_input(t("teacher.bonus_label"), min_value=0, value=0, step=100)
                    deduction = c5.number_input(t("teacher.deduction_label"), min_value=0, value=0, step=100)

                    net = basic + bonus - deduction
                    st.markdown(f"**{t('teacher.net_salary_label', amount=f'{net:,.0f}')}**")

                    method  = st.selectbox(t("teacher.payment_method_label"), ["Cash", "bKash", "Nagad", "Bank"])
                    remarks = st.text_input(t("teacher.remarks_label"))

                    if st.form_submit_button(t("teacher.pay_salary_btn"), type="primary"):
                        ok, err = _pay_salary(
                            tid, tch2["id"], month, int(year),
                            basic, bonus, deduction, method.lower(), remarks
                        )
                        if ok:
                            st.success(t("teacher.salary_paid_success", name=sel_sal, month=month, year=int(year)))
                            st.rerun()
                        else:
                            st.error(err)

                divider()
                st.markdown(f"**{t('teacher.salary_history_heading', name=sel_sal)}**")
                hist = _salary_history(tid, tch2["id"])
                if hist:
                    rows = [
                        {
                            t("teacher.col_month"):      f"{r['month_name']} {r['year']}",
                            t("teacher.col_basic"):      f"৳{float(r['basic_salary'] or 0):,.0f}",
                            t("teacher.col_bonus"):   f"৳{float(r['bonus'] or 0):,.0f}",
                            t("teacher.col_deduction"):   f"৳{float(r['deduction'] or 0):,.0f}",
                            t("teacher.col_net"):     f"৳{float(r['net_salary'] or 0):,.0f}",
                            t("teacher.col_paid_date"):  str(r["payment_date"]) if r["payment_date"] else "—",
                            t("teacher.col_status"):  t("teacher.status_paid") if r["status"] == "paid" else t("teacher.status_due"),
                        }
                        for r in hist
                    ]
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                    total_paid = sum(float(r["net_salary"] or 0) for r in hist if r["status"] == "paid")
                    st.caption(t("teacher.total_paid_caption", amount=f"{total_paid:,.0f}"))
                else:
                    alert(t("teacher.no_salary_records"), "info")
