"""
admission_module.py — Enterprise-grade multi-step admission form
and Admin approval panel for Smart Madrasa ERP.

Steps: 1) Personal Info  2) Enrollment Config  3) Review & Submit
Admin panel: assign roll numbers, activate students.
"""

from error_handler import safe_db_error, safe_error, log_warning
import streamlit as st
from datetime import date
from db import get_connection, release_connection, fetchall, fetchone
from utils import (
    page_header, kpi_row, step_bar, badge, alert, divider,
    get_tenant_id, validate_mobile, validate_required,
)
from i18n import t
import audit_module
import bulk_import


# ──────────────────────────────────────────────────────────────────────────────
# Internal data helpers
# ──────────────────────────────────────────────────────────────────────────────

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


def _get_active_session(tid):
    return fetchone(
        "SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s AND is_active=TRUE LIMIT 1",
        (tid,),
    )


def _pending_enrollments(tid):
    return fetchall(
        """SELECT
            s.id AS student_id, s.name, s.father_name, s.mobile_no,
            s.status, s.created_at,
            e.id AS enrollment_id, e.roll_no, e.enrollment_status, e.monthly_fee,
            c.class_name, sess.session_name
        FROM students s
        JOIN student_enrollments e ON e.student_id = s.id AND e.tenant_id = s.tenant_id
        JOIN classes c ON c.id = e.class_id
        JOIN academic_sessions sess ON sess.id = e.session_id
        WHERE s.tenant_id = %s
          AND (s.status = 'pending' OR e.enrollment_status = 'pending')
        ORDER BY s.created_at DESC""",
        (tid,),
    )


def _active_students(tid):
    return fetchall(
        """SELECT
            s.id, s.name, s.father_name, s.mobile_no, s.status,
            e.roll_no, e.monthly_fee, e.enrollment_status,
            c.class_name, sess.session_name
        FROM students s
        JOIN student_enrollments e ON e.student_id = s.id AND e.tenant_id = s.tenant_id
        JOIN classes c ON c.id = e.class_id
        JOIN academic_sessions sess ON sess.id = e.session_id
        WHERE s.tenant_id = %s AND s.status = 'active'
        ORDER BY c.class_numeric, e.roll_no""",
        (tid,),
    )


def anonymize_student(tid, student_id, requested_by):
    """
    "মুছে ফেলার" (right to be forgotten) অনুরোধে ছাত্রের PII মুছে দেয়
    (নাম/পিতা-মাতা/মোবাইল/NID/ঠিকানা/ছবি/ডকুমেন্ট), কিন্তু academic ও
    financial history (marks, attendance, fee vouchers) অক্ষত রাখে —
    প্রতিষ্ঠানের নিজস্ব রেকর্ড-রক্ষণ প্রয়োজনে। হার্ড DELETE নয়, UPDATE —
    cascade করে child record মুছে যাওয়া এড়াতে। status='anonymized' হওয়ায়
    এই ছাত্র app-জুড়ে সব 'active'-filtered তালিকা থেকে স্বয়ংক্রিয়ভাবে
    বাদ পড়ে যায়, আলাদা কোনো কোড পরিবর্তন ছাড়াই।
    """
    import storage

    row = fetchone("SELECT photo FROM students WHERE id=%s AND tenant_id=%s", (student_id, tid))
    if row and row.get("photo"):
        try:
            storage.delete_file(row["photo"])
        except Exception:
            pass

    docs = fetchall(
        "SELECT file_data FROM student_documents WHERE student_id=%s AND tenant_id=%s",
        (student_id, tid),
    )
    for d in docs:
        if d.get("file_data"):
            try:
                storage.delete_file(d["file_data"])
            except Exception:
                pass

    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE students SET
                       name=%s, father_name=NULL, mother_name=NULL, mobile_no=NULL,
                       nid_no=NULL, nid_no_encrypted=NULL, present_address=NULL,
                       permanent_address=NULL, photo=NULL, status='anonymized'
                   WHERE id=%s AND tenant_id=%s""",
                (f"Deleted Student #{student_id}", student_id, tid),
            )
            cur.execute(
                "DELETE FROM student_documents WHERE student_id=%s AND tenant_id=%s",
                (student_id, tid),
            )
        conn.commit()
        audit_module.log(
            "UPDATE", "Students",
            t("adm.audit_anonymized", id=student_id, by=requested_by),
            "student", student_id,
        )
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _admission_kpis(tid):
    total  = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s", (tid,))
    active = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='active'", (tid,))
    pend   = fetchone("SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='pending'", (tid,))
    return (
        int(total["n"]) if total else 0,
        int(active["n"]) if active else 0,
        int(pend["n"]) if pend else 0,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Step 1 – Personal Information
# ──────────────────────────────────────────────────────────────────────────────

def _step_personal():
    st.markdown(f"#### {t('adm.step_personal_heading')}")
    c1, c2 = st.columns(2)
    name        = c1.text_input(t("adm.label_student_name"), placeholder=t("adm.placeholder_student_name"))
    father_name = c2.text_input(t("adm.label_father_name"), placeholder=t("adm.placeholder_father_name"))

    c3, c4 = st.columns(2)
    mobile_no = c3.text_input(t("adm.label_mobile"), placeholder=t("adm.placeholder_mobile"))
    dob       = c4.date_input(t("adm.label_dob"), value=None,
                               min_value=date(1970, 1, 1), max_value=date.today())

    c5, c6 = st.columns(2)
    gender     = c5.selectbox(t("adm.label_gender"), ["Male", "Female"],
                               format_func=lambda g: t("adm.gender_male") if g == "Male" else t("adm.gender_female"),
                               key="adm_gender")
    blood_grp  = c6.selectbox(t("adm.label_blood_group"), ["—", "A+", "A−", "B+", "B−", "AB+", "AB−", "O+", "O−"])

    address = st.text_area(t("adm.label_address"), height=68, placeholder=t("adm.placeholder_address"))

    photo_file = st.file_uploader(
        t("adm.label_photo_upload"),
        type=["jpg", "jpeg", "png", "webp"],
        key="adm_photo_upload",
        help=t("adm.caption_photo_hint"),
    )
    photo_bytes    = photo_file.getvalue() if photo_file else None
    photo_filename = photo_file.name if photo_file else None

    errors = []
    if not name.strip():   errors.append(t("adm.err_name_required"))
    if not father_name.strip(): errors.append(t("adm.err_father_required"))
    if mobile_no and not validate_mobile(mobile_no):
        errors.append(t("adm.err_mobile_invalid"))

    return {
        "name": name.strip(),
        "father_name": father_name.strip(),
        "mobile_no": mobile_no.strip(),
        "dob": str(dob) if dob else None,
        "gender": gender,
        "blood_group": None if blood_grp == "—" else blood_grp,
        "present_address": address.strip(),
        "photo_bytes": photo_bytes,
        "photo_filename": photo_filename,
    }, errors


# ──────────────────────────────────────────────────────────────────────────────
# Step 2 – Enrollment Configuration
# ──────────────────────────────────────────────────────────────────────────────

def _step_enrollment(tid):
    st.markdown(f"#### {t('adm.step_enrollment_heading')}")
    sessions = _get_sessions(tid)
    classes  = _get_classes(tid)

    if not sessions:
        alert(t("adm.warn_no_session"), "warning")
        return None, [t("adm.err_no_session_available")]
    if not classes:
        alert(t("adm.warn_no_class"), "warning")
        return None, [t("adm.err_no_class_available")]

    sess_opts  = {s["session_name"]: s["id"] for s in sessions}
    class_opts = {c["class_name"]: c["id"] for c in classes}

    active_sess = _get_active_session(tid)
    default_sess = active_sess["session_name"] if active_sess else list(sess_opts.keys())[0]

    c1, c2 = st.columns(2)
    sel_sess  = c1.selectbox(t("adm.label_session"), list(sess_opts.keys()),
                              index=list(sess_opts.keys()).index(default_sess))
    sel_class = c2.selectbox(t("adm.label_class"), list(class_opts.keys()))

    c3, c4 = st.columns(2)
    monthly_fee = c3.number_input(t("adm.label_monthly_fee"), min_value=0, value=500, step=50)
    roll_no_placeholder = c4.text_input(
        t("adm.label_roll_optional"), placeholder=t("adm.placeholder_roll_optional")
    )

    roll_no = None
    if roll_no_placeholder.strip():
        try:
            roll_no = int(roll_no_placeholder)
        except ValueError:
            return None, [t("adm.err_roll_invalid")]

    return {
        "session_id":  sess_opts[sel_sess],
        "class_id":    class_opts[sel_class],
        "monthly_fee": monthly_fee,
        "roll_no":     roll_no,
        "session_name": sel_sess,
        "class_name":   sel_class,
    }, []


# ──────────────────────────────────────────────────────────────────────────────
# Step 3 – Review
# ──────────────────────────────────────────────────────────────────────────────

def _step_review(p_data, e_data):
    st.markdown(f"#### {t('adm.step_review_heading')}")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        **{heading}**
        | {f} | {v} |
        |---|---|
        | {lbl_name} | {name} |
        | {lbl_father} | {father_name} |
        | {lbl_mobile} | {mobile_no} |
        | {lbl_gender} | {gender} |
        | {lbl_blood} | {blood_group} |
        | {lbl_address} | {present_address} |
        """.format(
            heading=t("adm.review_personal_details"),
            f=t("adm.review_field"), v=t("adm.review_value"),
            lbl_name=t("adm.review_name"), lbl_father=t("adm.review_father"),
            lbl_mobile=t("adm.review_mobile"), lbl_gender=t("adm.review_gender"),
            lbl_blood=t("adm.review_blood_group"), lbl_address=t("adm.review_address"),
            **{k: v or "—" for k, v in p_data.items()}))
    with c2:
        st.markdown("""
        **{heading}**
        | {f} | {v} |
        |---|---|
        | {lbl_session} | {session_name} |
        | {lbl_class} | {class_name} |
        | {lbl_fee} | ৳ {monthly_fee:,} |
        | {lbl_roll} | {roll_no} |
        """.format(
            heading=t("adm.review_enrollment_details"),
            f=t("adm.review_field"), v=t("adm.review_value"),
            lbl_session=t("adm.review_session"), lbl_class=t("adm.review_class"),
            lbl_fee=t("adm.review_monthly_fee"), lbl_roll=t("adm.review_roll_no"),
            session_name=e_data["session_name"], class_name=e_data["class_name"],
            monthly_fee=e_data["monthly_fee"],
            roll_no=e_data["roll_no"] or t("adm.review_pending"),
        ))

    uploaded_docs = st.file_uploader(
        t("adm.label_documents_upload"),
        type=["pdf", "jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="adm_doc_upload",
        help=t("adm.caption_documents_hint"),
    ) or []

    alert(t("adm.info_submit_pending"), "info")
    return uploaded_docs


# ──────────────────────────────────────────────────────────────────────────────
# Optional file uploads (photo + documents) — best-effort, never blocks admission
# ──────────────────────────────────────────────────────────────────────────────

def _upload_admission_files(tid, student_id, p_data, uploaded_docs):
    """
    ঐচ্ছিক ছবি ও ডকুমেন্ট আপলোড করে — সম্পূর্ণ best-effort। Student ততক্ষণে
    commit হয়ে গেছে, তাই এখানে কোনো ব্যর্থতা admission-কে প্রভাবিত করে না —
    শুধু ফেরত-দেওয়া warning list-এ যোগ হয়।
    """
    import storage
    warnings = []

    photo_bytes    = p_data.get("photo_bytes")
    photo_filename = p_data.get("photo_filename")
    if photo_bytes and photo_filename:
        try:
            ok, err = storage.upload_student_photo(tid, student_id, photo_bytes, photo_filename)
            if not ok:
                log_warning(err, "admission_photo_upload")
                warnings.append(t("adm.warn_photo_upload_failed", error=err))
        except Exception as ex:
            warnings.append(t("adm.warn_photo_upload_failed",
                               error=safe_error(ex, "upload", "admission_photo_upload")))

    for f in (uploaded_docs or []):
        try:
            ok, err = storage.upload_document(tid, student_id, "admission_document", f.getvalue(), f.name)
            if not ok:
                log_warning(err, "admission_document_upload")
                warnings.append(t("adm.warn_document_upload_failed", name=f.name, error=err))
        except Exception as ex:
            warnings.append(t("adm.warn_document_upload_failed", name=f.name,
                               error=safe_error(ex, "upload", "admission_document_upload")))
    return warnings


# ──────────────────────────────────────────────────────────────────────────────
# DB write – atomic admission
# ──────────────────────────────────────────────────────────────────────────────

def _submit_admission(tid, p_data, e_data, uploaded_docs=None):
    conn = get_connection()
    if not conn:
        return False, t("adm.err_db_connection"), []
    try:
        with conn.cursor() as cur:
            # Insert student
            cur.execute(
                """INSERT INTO students
                   (tenant_id, name, father_name, mobile_no, date_of_birth,
                    gender, blood_group, present_address, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending')
                   RETURNING id""",
                (
                    tid, p_data["name"], p_data["father_name"],
                    p_data["mobile_no"] or None, p_data["dob"],
                    p_data["gender"], p_data["blood_group"],
                    p_data["present_address"],
                ),
            )
            student_id = cur.fetchone()["id"]

            # Insert enrollment
            cur.execute(
                """INSERT INTO student_enrollments
                   (tenant_id, student_id, session_id, class_id, roll_no,
                    monthly_fee, enrollment_status)
                   VALUES (%s,%s,%s,%s,%s,%s,'pending')""",
                (
                    tid, student_id,
                    e_data["session_id"], e_data["class_id"],
                    e_data["roll_no"], e_data["monthly_fee"],
                ),
            )
        conn.commit()
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex), []
    finally:
        release_connection(conn)

    upload_warnings = _upload_admission_files(tid, student_id, p_data, uploaded_docs)
    return True, student_id, upload_warnings


# ──────────────────────────────────────────────────────────────────────────────
# Admin: Approve / Activate
# ──────────────────────────────────────────────────────────────────────────────

def _activate_student(tid, student_id, enrollment_id, roll_no):
    if not roll_no:
        return False, t("adm.err_roll_required")
    conn = get_connection()
    if not conn:
        return False, t("adm.err_db_generic")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE students SET status='active' WHERE id=%s AND tenant_id=%s",
                (student_id, tid),
            )
            cur.execute(
                """UPDATE student_enrollments
                   SET enrollment_status='active', roll_no=%s
                   WHERE id=%s AND tenant_id=%s""",
                (roll_no, enrollment_id, tid),
            )
        conn.commit()
        return True, None
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _reject_student(tid, student_id, enrollment_id):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE students SET status='inactive' WHERE id=%s AND tenant_id=%s",
                (student_id, tid),
            )
            cur.execute(
                "UPDATE student_enrollments SET enrollment_status='dropped' WHERE id=%s AND tenant_id=%s",
                (enrollment_id, tid),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


# ──────────────────────────────────────────────────────────────────────────────
# Main render
# ──────────────────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("🎓", t("adm.page_title"), t("adm.page_subtitle"))

    total, active, pending = _admission_kpis(tid)
    kpi_row([
        {"label": t("adm.kpi_total_students"), "value": total,   "cls": ""},
        {"label": t("adm.kpi_active"),         "value": active,  "cls": "success"},
        {"label": t("adm.kpi_pending_approval"),"value": pending, "cls": "warning"},
        {"label": t("adm.kpi_inactive"),       "value": total - active - pending, "cls": "danger"},
    ])

    tab_new, tab_pending, tab_list, tab_bulk = st.tabs(
        [t("adm.tab_new_admission"), t("adm.tab_pending_approvals"), t("adm.tab_student_list"),
         t("bulk.tab_import")]
    )

    # ── Tab 1: Multi-step new admission ──
    with tab_new:
        # Step state
        if "adm_step" not in st.session_state:
            st.session_state.adm_step = 0
        if "adm_personal" not in st.session_state:
            st.session_state.adm_personal = {}
        if "adm_enroll" not in st.session_state:
            st.session_state.adm_enroll = {}

        step = st.session_state.adm_step
        step_bar([t("adm.step_personal_short"), t("adm.step_enrollment_short"), t("adm.step_review_short")], step)

        with st.form("adm_form", clear_on_submit=False):
            if step == 0:
                p_data, errors = _step_personal()
                submitted = st.form_submit_button(t("adm.btn_next"), type="primary")
                if submitted:
                    if errors:
                        for e in errors:
                            st.error(e)
                    else:
                        st.session_state.adm_personal = p_data
                        st.session_state.adm_step = 1
                        st.rerun()

            elif step == 1:
                e_data, errors = _step_enrollment(tid)
                col_back, col_next = st.columns([1, 4])
                back = col_back.form_submit_button(t("adm.btn_back"))
                nxt  = col_next.form_submit_button(t("adm.btn_next"), type="primary")
                if back:
                    st.session_state.adm_step = 0
                    st.rerun()
                if nxt:
                    if errors:
                        for e in errors:
                            st.error(e)
                    elif e_data:
                        st.session_state.adm_enroll = e_data
                        st.session_state.adm_step = 2
                        st.rerun()

            elif step == 2:
                uploaded_docs = _step_review(st.session_state.adm_personal, st.session_state.adm_enroll)
                col_back, col_submit = st.columns([1, 4])
                back   = col_back.form_submit_button(t("adm.btn_back"))
                submit = col_submit.form_submit_button(t("adm.btn_submit"), type="primary")
                if back:
                    st.session_state.adm_step = 1
                    st.rerun()
                if submit:
                    ok, result, upload_warnings = _submit_admission(
                        tid,
                        st.session_state.adm_personal,
                        st.session_state.adm_enroll,
                        uploaded_docs,
                    )
                    if ok:
                        st.success(t("adm.success_admitted", id=result))
                        if upload_warnings:
                            st.warning("; ".join(upload_warnings))
                        # Fix (F821): `class_id` ছিল undefined — submit-এর সময়
                        # এই path-এ NameError crash হতো। enroll data থেকে নেওয়া হলো।
                        _cid = st.session_state.adm_enroll.get("class_id", "?")
                        audit_module.log("CREATE", "Admissions",
                            t("adm.audit_new_admission", id=result, cid=_cid))
                        st.session_state.adm_step = 0
                        st.session_state.adm_personal = {}
                        st.session_state.adm_enroll = {}
                        st.rerun()
                    else:
                        st.error(t("adm.err_submission_failed", error=result))

    # ── Tab 2: Pending Approval Panel ──
    with tab_pending:
        st.markdown(f"#### {t('adm.pending_heading')}")
        rows = _pending_enrollments(tid)
        if not rows:
            alert(t("adm.success_all_clear"), "success")
        else:
            for row in rows:
                with st.expander(
                    t("adm.pending_expander_label",
                      name=row["name"], class_name=row["class_name"],
                      session_name=row["session_name"],
                      date=str(row["created_at"])[:10])
                ):
                    c1, c2 = st.columns([2, 1])
                    with c1:
                        st.markdown(t(
                            "adm.pending_detail_block",
                            father=row["father_name"] or "—",
                            mobile=row["mobile_no"] or "—",
                            fee=f"{row['monthly_fee']:,.0f}",
                        ))
                    with c2:
                        roll_key = f"roll_{row['enrollment_id']}"
                        new_roll = st.number_input(
                            t("adm.label_assign_roll"), min_value=1, key=roll_key, value=1
                        )
                        col_a, col_r = st.columns(2)
                        if col_a.button(t("adm.btn_activate"), key=f"act_{row['enrollment_id']}",
                                        type="primary"):
                            ok, err = _activate_student(
                                tid, row["student_id"], row["enrollment_id"], new_roll
                            )
                            if ok:
                                st.success(t("adm.success_activated"))
                                st.rerun()
                            else:
                                st.error(err)
                        if col_r.button(t("adm.btn_reject"), key=f"rej_{row['enrollment_id']}"):
                            if _reject_student(tid, row["student_id"], row["enrollment_id"]):
                                st.warning(t("adm.warn_rejected"))
                                st.rerun()

    # ── Tab 3: Active Student List ──
    with tab_list:
        st.markdown(f"#### {t('adm.list_heading')}")
        classes = _get_classes(tid)
        all_classes_label = t("adm.all_classes")
        class_filter_opts = [all_classes_label] + [c["class_name"] for c in classes]
        sel_class = st.selectbox(t("adm.label_filter_class"), class_filter_opts, key="adm_class_filter")

        rows = _active_students(tid)
        if sel_class != all_classes_label:
            rows = [r for r in rows if r["class_name"] == sel_class]

        if not rows:
            alert(t("adm.info_no_students_filter"), "info")
        else:
            # Build display table
            data = []
            for r in rows:
                data.append({
                    t("adm.col_roll"): r["roll_no"] or "—",
                    t("adm.col_name"): r["name"],
                    t("adm.col_father"): r["father_name"] or "—",
                    t("adm.col_class"): r["class_name"],
                    t("adm.col_session"): r["session_name"],
                    t("adm.col_fee"): f"{r['monthly_fee']:,.0f}",
                    t("adm.col_mobile"): r["mobile_no"] or "—",
                })
            st.dataframe(data, use_container_width=True, hide_index=True)
            st.caption(t("adm.caption_showing_students", n=len(data)))

            divider()
            with st.expander(t("adm.anonymize_section_heading")):
                st.warning(t("adm.anonymize_warning"))
                anon_map = {
                    f"{r['name']} — {r['class_name']} (Roll {r['roll_no'] or '—'})": r
                    for r in rows
                }
                sel_anon = st.selectbox(
                    t("adm.select_student_to_anonymize"), list(anon_map.keys()), key="anon_select"
                )
                confirm_anon = st.checkbox(t("adm.anonymize_confirm_checkbox"), key="anon_confirm")
                if st.button(t("adm.btn_anonymize"), disabled=not confirm_anon, key="anon_btn"):
                    stu = anon_map[sel_anon]
                    ok = anonymize_student(tid, stu["id"], st.session_state.get("username", "admin"))
                    if ok:
                        st.success(t("adm.anonymize_success"))
                        st.session_state.pop("anon_confirm", None)
                        st.rerun()
                    else:
                        st.error(t("adm.anonymize_failed"))

    with tab_bulk:
        st.markdown(f"#### {t('bulk.heading')}")
        st.caption(t("bulk.intro_students"))
        st.download_button(
            t("bulk.download_template"),
            bulk_import.students_template_csv(),
            "students_template.csv",
            "text/csv",
        )

        uploaded = st.file_uploader(t("bulk.upload_label"), type=["csv", "xlsx"], key="stu_bulk_upload")
        if uploaded:
            df, err = bulk_import.parse_upload(uploaded)
            if err:
                st.error(err)
            else:
                validated = bulk_import.validate_students(tid, df)
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
                elif st.button(t("bulk.btn_commit", count=len(valid_rows)), type="primary", key="stu_bulk_commit"):
                    created, failures = bulk_import.commit_students(tid, valid_rows)
                    if created:
                        audit_module.log("IMPORT", "Admissions", f"বাল্ক ইম্পোর্ট — {created} students")
                        st.success(t("bulk.import_success", count=created))
                    if failures:
                        details = "; ".join(f"row {rn}: {msg}" for rn, msg in failures)
                        st.warning(t("bulk.import_partial_fail", count=len(failures), details=details))
                    if created:
                        st.rerun()
