"""
document_module.py — Document Management System
TC (Transfer Certificate), প্রশংসাপত্র, ছবি আপলোড,
এবং অফিশিয়াল ডকুমেন্ট জেনারেটর।
"""

import streamlit as st
import base64
import io
from datetime import date
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, alert, divider, get_tenant_id, flatten_html
import audit_module
from error_handler import safe_db_error
from i18n import t


def _get_students(tid):
    return fetchall(
        """SELECT s.id, s.name, s.father_name, s.mobile_no,
                  e.roll_no, c.class_name, sess.session_name
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s AND s.status='active'
           ORDER BY c.class_numeric, e.roll_no""",
        (tid,),
    )


# Fix (brutal review #4): COUNT+1 ছিল race-prone; TC নম্বর এখন
# insert-এর পর id থেকে তৈরি হয় (_issue_tc দেখুন) — এই helper আর নেই।


def _issue_tc(tid, student_id, reason, last_class, last_sess,
              conduct, att_pct, remarks):
    issued_by = st.session_state.get("user_name","Admin")
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tc_records
                   (tenant_id, student_id, reason, last_class,
                    last_session, conduct, attendance_pct, remarks, issued_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, student_id, reason, last_class,
                 last_sess, conduct, att_pct, remarks, issued_by),
            )
            _new_id = cur.fetchone()["id"]
            tc_no = f"TC-{tid:03d}-{_new_id:04d}"
            cur.execute(
                "UPDATE tc_records SET tc_number=%s WHERE id=%s",
                (tc_no, _new_id),
            )
            # Deactivate student
            cur.execute(
                "UPDATE students SET status='inactive' WHERE id=%s AND tenant_id=%s",
                (student_id, tid),
            )
        conn.commit()
        return True, tc_no
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _save_photo(tid, student_id, file_bytes, file_name, mime):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            # Update student photo (base64)
            b64 = base64.b64encode(file_bytes).decode()
            cur.execute(
                "UPDATE students SET photo=%s WHERE id=%s AND tenant_id=%s",
                (f"data:{mime};base64,{b64}", student_id, tid),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _get_tc_records(tid):
    return fetchall(
        """SELECT tc.*, s.name AS student_name, s.father_name
           FROM tc_records tc
           JOIN students s ON s.id=tc.student_id
           WHERE tc.tenant_id=%s ORDER BY tc.created_at DESC""",
        (tid,),
    )


def _tc_html(tenant, student, tc):
    cert_body = t(
        "doc.tc_certify_body",
        name=f"<strong>{student['name']}</strong>",
        father=f"<strong>{student.get('father_name','') or '—'}</strong>",
        conduct=f"<strong>{tc['conduct']}</strong>",
    )
    return flatten_html(f"""
    <div style="max-width:640px;margin:0 auto;border:3px double #0F4C5C;
                border-radius:12px;padding:24px;font-family:Inter,sans-serif;
                background:white">
      <div style="text-align:center;border-bottom:2px solid #0F4C5C;padding-bottom:14px;margin-bottom:14px">
        {("<img src='data:" + (tenant.get("logo_mime") or "image/png")
           + ";base64," + tenant["logo_base64"]
           + "' style='height:56px;object-fit:contain'><br>")
          if tenant.get("show_logo") and tenant.get("logo_base64") else ""}
        {("<div class='brand-calligraphy' style='font-size:14px;color:" + tenant.get("primary_color", "#0F4C5C")
           + "' dir='rtl'>" + tenant["name_arabic"] + "</div>")
          if tenant.get("name_arabic") else ""}
        <div class="brand-calligraphy" style="font-size:22px;font-weight:700;color:{tenant.get('primary_color','#0F4C5C')}">
          {tenant.get('madrasa_name','Smart Madrasa')}
        </div>
        <div style="font-size:11px;color:#666">{tenant.get('address','')}</div>
        <div style="margin-top:10px;font-size:16px;font-weight:700;
                    background:#0F4C5C;color:white;padding:5px 24px;
                    border-radius:20px;display:inline-block;letter-spacing:1px">
          {t('doc.tc_title')}
        </div>
      </div>

      <table style="width:100%;border-collapse:collapse;font-size:12px;
                    margin-bottom:14px;border:1px solid #DDD">
        <tr style="background:#F7F9FA">
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD;width:40%">{t('doc.tc_number_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD;color:#0F4C5C;font-weight:700">
            {tc['tc_number']}
          </td>
        </tr>
        <tr>
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.student_name_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{student['name']}</td>
        </tr>
        <tr style="background:#F7F9FA">
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.father_name_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{student.get('father_name','') or '—'}</td>
        </tr>
        <tr>
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.last_class_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{tc['last_class']}</td>
        </tr>
        <tr style="background:#F7F9FA">
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.last_session_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{tc['last_session']}</td>
        </tr>
        <tr>
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.tc_reason_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{tc['reason']}</td>
        </tr>
        <tr style="background:#F7F9FA">
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.conduct_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD;color:#2E7D32;font-weight:600">
            {tc['conduct']}
          </td>
        </tr>
        <tr>
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.attendance_pct_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD">
            {tc['attendance_pct'] or '—'}%
          </td>
        </tr>
        <tr style="background:#F7F9FA">
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.remarks_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{tc['remarks'] or '—'}</td>
        </tr>
        <tr>
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">{t('doc.issue_date_label')}</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{str(tc['issue_date'])}</td>
        </tr>
      </table>

      <p style="font-size:12px;color:#444;text-align:justify;line-height:1.6">
        {cert_body}
      </p>

      <table style="width:100%;margin-top:30px;font-size:11px;border-collapse:collapse">
        <tr>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:40px;padding-top:5px">
              {t('doc.issuer_signature')}
            </div>
          </td>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:40px;padding-top:5px">
              {t('doc.head_teacher_signature')}
            </div>
          </td>
        </tr>
      </table>
    </div>""")


def _certificate_html(tenant, student, exam_name, grade, gpa, rank, session_name):
    passed_text = t(
        "doc.cert_passed_text",
        exam=f"<strong>{exam_name}</strong>",
        session=f"<strong>{session_name}</strong>",
    )
    return flatten_html(f"""
    <div style="max-width:640px;margin:0 auto;
                border:8px double #E8A838;border-radius:16px;
                padding:28px;font-family:Inter,sans-serif;background:white;
                background-image:linear-gradient(135deg,#FFFDF4 0%,#FFF 100%)">
      <div style="text-align:center;margin-bottom:20px">
        <div style="font-size:28px">🏆</div>
        <div style="font-size:22px;font-weight:700;color:#0F4C5C;margin-top:6px">
          {tenant.get('madrasa_name','Smart Madrasa')}
        </div>
        <div style="font-size:10px;color:#888;letter-spacing:1px;margin-top:2px">
          CERTIFICATE OF ACHIEVEMENT
        </div>
        <div style="width:80px;height:3px;background:#E8A838;margin:10px auto"></div>
      </div>

      <p style="text-align:center;font-size:13px;color:#444;margin-bottom:16px">
        {t('doc.cert_proud_announce')}
      </p>

      <div style="text-align:center;margin:16px 0;padding:16px;
                  background:linear-gradient(135deg,#EAF4F8,#F0F8FF);
                  border-radius:10px;border:1px solid #0F4C5C30">
        <div style="font-size:24px;font-weight:700;color:#0F4C5C">{student['name']}</div>
        <div style="font-size:13px;color:#555;margin-top:4px">
          {t('doc.cert_father_roll', father=student.get('father_name','') or '—', roll=student.get('roll_no','') or '—')}
        </div>
      </div>

      <p style="text-align:center;font-size:13px;color:#444;margin:16px 0">
        {passed_text}
      </p>

      <div style="display:flex;justify-content:center;gap:20px;margin:20px 0">
        <div style="background:#0F4C5C;color:white;border-radius:10px;
                    padding:12px 24px;text-align:center">
          <div style="font-size:24px;font-weight:700">{grade}</div>
          <div style="font-size:10px;opacity:0.8">{t('doc.grade_label')}</div>
        </div>
        <div style="background:#E8A838;color:white;border-radius:10px;
                    padding:12px 24px;text-align:center">
          <div style="font-size:24px;font-weight:700">{gpa:.2f}</div>
          <div style="font-size:10px;opacity:0.8">GPA</div>
        </div>
        <div style="background:#2E7D32;color:white;border-radius:10px;
                    padding:12px 24px;text-align:center">
          <div style="font-size:24px;font-weight:700">#{rank}</div>
          <div style="font-size:10px;opacity:0.8">{t('doc.rank_label')}</div>
        </div>
      </div>

      <div style="text-align:center;font-size:11px;color:#777;margin-top:16px">
        {t('doc.cert_date_line', date=date.today(), madrasa=tenant.get('madrasa_name',''))}
      </div>

      <table style="width:100%;margin-top:30px;font-size:11px;border-collapse:collapse">
        <tr>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:40px;padding-top:5px">
              {t('doc.class_teacher_signature')}
            </div>
          </td>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:40px;padding-top:5px">
              {t('doc.cert_head_teacher_signature')}
            </div>
          </td>
        </tr>
      </table>
    </div>""")


def render():
    tid = get_tenant_id()
    page_header("📄", "Document Management", t("doc.page_subtitle"))

    students = _get_students(tid)
    stu_map  = {f"{s['name']} — {s['class_name']} (Roll {s['roll_no'] or '—'})": s
                for s in students}
    # Branding merge — লোগো/আরবি নাম/রঙ TC ও প্রশংসাপত্রে ব্যবহৃত হবে
    import branding as _br
    from db import get_connection as _gc, release_connection as _rc
    _conn = _gc()
    if _conn:
        try:
            with _conn.cursor() as _cur:
                tenant = _br.get_branding(_cur, tid)
        finally:
            _rc(_conn)
    else:
        tenant = fetchone("SELECT * FROM tenants WHERE id=%s", (tid,)) or {}

    tab_photo, tab_tc, tab_cert, tab_list = st.tabs([
        t("doc.tab_photo"), t("doc.tab_tc"), t("doc.tab_cert"), t("doc.tab_list")
    ])

    # ── ছবি আপলোড ──
    with tab_photo:
        st.markdown(f"#### 📸 {t('doc.photo_upload_heading')}")
        if not students:
            alert(t("doc.no_active_students"), "warning"); return

        sel = st.selectbox(t("doc.select_student"), list(stu_map.keys()), key="ph_stu")
        stu = stu_map[sel]

        # Current photo
        current = fetchone("SELECT photo FROM students WHERE id=%s AND tenant_id=%s",
                           (stu["id"], tid))
        if current and current.get("photo"):
            st.markdown(f"**{t('doc.current_photo')}**")
            st.markdown(
                f'<img src="{current["photo"]}" style="width:120px;height:140px;'
                f'object-fit:cover;border-radius:8px;border:2px solid #0F4C5C">',
                unsafe_allow_html=True,
            )

        uploaded = st.file_uploader(
            t("doc.upload_new_photo"),
            type=["jpg","jpeg","png"],
            key="ph_upload",
        )
        if uploaded:
            if uploaded.size > 2 * 1024 * 1024:
                alert(t("doc.file_too_large"), "danger")
            else:
                img_bytes = uploaded.read()
                st.image(img_bytes, width=120, caption=t("doc.preview_caption"))
                if st.button(t("doc.save_photo"), type="primary"):
                    if _save_photo(tid, stu["id"], img_bytes, uploaded.name, uploaded.type):
                        audit_module.log("UPDATE","Document",f"ছবি আপলোড: {stu['name']}","student",stu["id"])
                        st.success(t("doc.photo_saved"))
                        st.rerun()
                    else:
                        st.error(t("doc.save_failed"))

    # ── TC ──
    with tab_tc:
        st.markdown(f"#### 📋 {t('doc.tc_issue_heading')}")
        if not students:
            alert(t("doc.no_active_students"), "warning"); return

        sel_tc = st.selectbox(t("doc.select_student"), list(stu_map.keys()), key="tc_stu")
        stu_tc = stu_map[sel_tc]

        # Attendance
        att = fetchone(
            """SELECT
               ROUND(COUNT(CASE WHEN status='present' THEN 1 END)::numeric /
                     NULLIF(COUNT(*),0) * 100, 1) AS pct
               FROM attendance a
               JOIN student_enrollments e ON e.id=a.enrollment_id
               WHERE a.tenant_id=%s AND e.student_id=%s""",
            (tid, stu_tc["id"]),
        )
        att_pct = float(att["pct"]) if att and att["pct"] else 0.0

        with st.form("tc_form"):
            c1, c2 = st.columns(2)
            last_class = c1.text_input(t("doc.last_class_label"), value=stu_tc.get("class_name",""))
            last_sess  = c2.text_input(t("doc.last_session_label"), value=stu_tc.get("session_name",""))
            reason     = st.selectbox(t("doc.tc_reason_label"),
                                      [t("doc.reason_guardian_request"), t("doc.reason_transfer"),
                                       t("doc.reason_other_institution"), t("doc.reason_withdrawal"),
                                       t("doc.reason_other")])
            conduct    = st.selectbox(t("doc.conduct_label"),
                                      [t("doc.conduct_good"), t("doc.conduct_very_good"), t("doc.conduct_average")])
            att_input  = st.number_input(t("doc.attendance_pct_input_label"), min_value=0.0,
                                          max_value=100.0, value=att_pct)
            remarks    = st.text_area(t("doc.remarks_label"), height=60)

            if st.form_submit_button(t("doc.issue_tc_button"), type="primary"):
                ok, result = _issue_tc(
                    tid, stu_tc["id"], reason, last_class, last_sess,
                    conduct, att_input, remarks
                )
                if ok:
                    audit_module.log("CREATE","Document",f"TC ইস্যু: {stu_tc['name']} — {result}","tc")
                    st.success(t("doc.tc_issued_success", tc_number=result))
                    # Show printable TC
                    tc_data = fetchone(
                        "SELECT * FROM tc_records WHERE tc_number=%s", (result,)
                    )
                    if tc_data:
                        st.markdown(
                            f'<button onclick="window.print()" '
                            f'style="background:#0F4C5C;color:white;border:none;'
                            f'padding:8px 20px;border-radius:8px;cursor:pointer;'
                            f'margin-bottom:1rem;font-weight:600">🖨️ {t("doc.print_button")}</button>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            _tc_html(tenant, stu_tc, tc_data),
                            unsafe_allow_html=True,
                        )
                    st.rerun()
                else:
                    st.error(result)

    # ── Certificate ──
    with tab_cert:
        st.markdown(f"#### 🏆 {t('doc.cert_generator_heading')}")
        if not students:
            alert(t("doc.no_active_students"), "warning"); return

        sel_ct = st.selectbox(t("doc.select_student"), list(stu_map.keys()), key="ct_stu")
        stu_ct = stu_map[sel_ct]

        exams = fetchall(
            """SELECT e.id, e.exam_name, sess.session_name
               FROM exams e
               JOIN academic_sessions sess ON sess.id=e.session_id
               WHERE e.tenant_id=%s ORDER BY e.id DESC""",
            (tid,),
        )
        if not exams:
            alert(t("doc.no_exams"), "info")
        else:
            exam_map = {f"{e['exam_name']} ({e['session_name']})": e for e in exams}
            sel_ex   = st.selectbox(t("doc.exam_label"), list(exam_map.keys()), key="ct_exam")
            exam     = exam_map[sel_ex]

            # Fetch marks
            result_row = fetchone(
                """SELECT
                   SUM(sm.total_obtained) AS obtained,
                   SUM(subj.full_marks) AS full_marks
                   FROM student_marks sm
                   JOIN student_enrollments e ON e.id=sm.enrollment_id
                   JOIN subjects subj ON subj.id=sm.subject_id
                   WHERE sm.tenant_id=%s AND sm.exam_id=%s AND e.student_id=%s""",
                (tid, exam["id"], stu_ct["id"]),
            )
            if result_row and result_row["full_marks"]:
                from utils import get_grade
                pct   = round(float(result_row["obtained"] or 0) /
                               float(result_row["full_marks"]) * 100, 2)
                grade, gpa = get_grade(pct)

                all_res = fetchall(
                    """SELECT e.student_id, SUM(sm.total_obtained) AS total
                       FROM student_marks sm
                       JOIN student_enrollments e ON e.id=sm.enrollment_id
                       WHERE sm.tenant_id=%s AND sm.exam_id=%s
                       GROUP BY e.student_id ORDER BY total DESC""",
                    (tid, exam["id"]),
                )
                rank = next(
                    (i+1 for i,r in enumerate(all_res) if r["student_id"] == stu_ct["id"]),
                    "—"
                )

                if st.button(t("doc.generate_cert_button"), type="primary"):
                    st.markdown(
                        f'<button onclick="window.print()" '
                        f'style="background:#E8A838;color:white;border:none;'
                        f'padding:8px 20px;border-radius:8px;cursor:pointer;'
                        f'margin-bottom:1rem;font-weight:600">🖨️ {t("doc.print_button")}</button>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        _certificate_html(tenant, stu_ct, exam["exam_name"],
                                          grade, gpa, rank, exam["session_name"]),
                        unsafe_allow_html=True,
                    )
            else:
                alert(t("doc.no_marks_for_exam"), "info")

    # ── TC তালিকা ──
    with tab_list:
        st.markdown(f"#### 📂 {t('doc.all_tc_records_heading')}")
        tc_list = _get_tc_records(tid)
        if not tc_list:
            alert(t("doc.no_tc_issued"), "info")
        else:
            rows = [{
                t("doc.col_tc_no"):        r["tc_number"],
                t("doc.col_name"):          r["student_name"],
                t("doc.col_father"):         r["father_name"] or "—",
                t("doc.col_last_class"): r["last_class"],
                t("doc.col_reason"):         r["reason"],
                t("doc.col_conduct"):       r["conduct"],
                t("doc.col_date"):        str(r["issue_date"]),
                t("doc.col_issuer"):   r["issued_by"] or "—",
            } for r in tc_list]
            st.dataframe(rows, use_container_width=True, hide_index=True)
