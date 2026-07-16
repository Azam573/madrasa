"""
online_admission.py — পাবলিক অনলাইন ভর্তি ফর্ম
অভিভাবক যেকোনো ডিভাইস থেকে ভর্তির আবেদন করতে পারবেন।
লগইন ছাড়াই অ্যাক্সেসযোগ্য।
"""

import streamlit as st
import time
from datetime import date
from db import get_connection, release_connection, fetchall, fetchone
from utils import PALETTE, flatten_html
import audit_module
import monitoring
from error_handler import safe_db_error, safe_error, log_warning
from ip_throttle import check_ip_rate_limit
from pii_crypto import encrypt_nid
from i18n import t

MIN_SUBMIT_SECONDS = 3  # কোনো মানুষ ৩-ধাপের ফর্ম এর চেয়ে দ্রুত পূরণ করতে পারবে না


# ─────────────────────────────────────────────
# Public render — লগইন ছাড়া
# ─────────────────────────────────────────────

def render_public(tenant_id: int = 1):
    """
    app.py থেকে ?page=admission URL parameter দিয়ে call করুন।
    লগইন ছাড়াই এই ফর্ম দেখা যাবে।
    """
    tenant = fetchone("SELECT * FROM tenants WHERE id=%s", (tenant_id,))
    madrasa = tenant["madrasa_name"] if tenant else "Smart Madrasa"

    st.markdown(
        flatten_html(f"""
        <style>
        .adm-header {{
            background: linear-gradient(135deg, #0F4C5C, #1a7a96);
            border-radius: 14px;
            padding: 2rem;
            text-align: center;
            color: white;
            margin-bottom: 1.5rem;
        }}
        .adm-header h1 {{ color: white !important; font-size: 1.6rem !important; margin: 0 !important; font-family: 'Amiri', 'Noto Sans Bengali', 'Inter', serif !important; }}
        .adm-section {{
            background: white;
            border: 1px solid #DDE3E7;
            border-radius: 10px;
            padding: 1.25rem 1.5rem;
            margin-bottom: 1rem;
        }}
        .adm-section h3 {{
            color: #0F4C5C;
            font-size: 1rem;
            font-weight: 700;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 2px solid #EAF4F8;
        }}
        .success-box {{
            background: #E8F5E9;
            border: 2px solid #2E7D32;
            border-radius: 12px;
            padding: 2rem;
            text-align: center;
        }}
        </style>

        <div class="adm-header">
            <div style="font-size:2.5rem;margin-bottom:0.5rem">🕌</div>
            <h1>{madrasa}</h1>
            <div style="opacity:0.85;margin-top:0.4rem;font-size:0.9rem">
                {t('oadm.header_subtitle')}
            </div>
            <div style="opacity:0.7;font-size:0.78rem;margin-top:0.25rem">
                {t('oadm.session_label', start=date.today().year, end=date.today().year+1)}
            </div>
        </div>
        """),
        unsafe_allow_html=True,
    )

    # Success state
    if st.session_state.get("admission_submitted"):
        app_no = st.session_state.get("admission_app_no", "")
        st.markdown(
            flatten_html(f"""<div class="success-box">
              <div style="font-size:3rem">✅</div>
              <h2 style="color:#2E7D32;margin:0.5rem 0">{t('oadm.success_title')}</h2>
              <p>{t('oadm.success_app_no_label')}</p>
              <div style="font-size:1.8rem;font-weight:700;color:#0F4C5C;
                          background:#F7F9FA;border-radius:8px;padding:0.75rem 1.5rem;
                          display:inline-block;letter-spacing:2px;margin:0.5rem 0">
                {app_no}
              </div>
              <p style="color:#555;font-size:0.85rem;margin-top:0.75rem">
                {t('oadm.success_note')}
              </p>
            </div>"""),
            unsafe_allow_html=True,
        )
        for w in st.session_state.get("admission_upload_warnings", []):
            st.warning(w)
        if st.button(t("oadm.new_application_button"), type="primary"):
            st.session_state.pop("admission_submitted", None)
            st.session_state.pop("admission_app_no", None)
            st.session_state.pop("admission_upload_warnings", None)
            st.rerun()
        return

    # Get available classes & active session
    classes  = fetchall(
        "SELECT id, class_name, class_numeric FROM classes WHERE tenant_id=%s ORDER BY class_numeric",
        (tenant_id,),
    )
    sessions = fetchall(
        "SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s AND is_active=TRUE LIMIT 1",
        (tenant_id,),
    )

    if not classes or not sessions:
        st.error(t("oadm.form_unavailable"))
        return

    sess_id  = sessions[0]["id"]
    class_map = {c["class_name"]: c["id"] for c in classes}

    # ── Step tracker ──
    if "adm_pub_step" not in st.session_state:
        st.session_state.adm_pub_step = 1
        st.session_state.adm_pub_form_started_at = time.time()

    step = st.session_state.adm_pub_step

    # Progress bar
    progress_pct = (step - 1) / 3 * 100
    st.markdown(
        flatten_html(f"""<div style="background:#EEE;border-radius:20px;height:8px;
                        margin-bottom:1.5rem;overflow:hidden">
          <div style="background:#0F4C5C;height:100%;width:{progress_pct:.0f}%;
                      border-radius:20px;transition:width 0.4s"></div>
        </div>
        <div style="display:flex;justify-content:space-between;
                    font-size:0.75rem;color:#6B7A8D;margin-bottom:1.5rem">
          <span style="{'font-weight:700;color:#0F4C5C' if step>=1 else ''}">{t('oadm.step1_label')}</span>
          <span style="{'font-weight:700;color:#0F4C5C' if step>=2 else ''}">{t('oadm.step2_label')}</span>
          <span style="{'font-weight:700;color:#0F4C5C' if step>=3 else ''}">{t('oadm.step3_label')}</span>
        </div>"""),
        unsafe_allow_html=True,
    )

    # ── Step 1: ছাত্রের তথ্য ──
    if step == 1:
        st.markdown(f'<div class="adm-section"><h3>👤 {t("oadm.student_personal_info_heading")}</h3>', unsafe_allow_html=True)

        with st.form("pub_step1"):
            c1, c2 = st.columns(2)
            name     = c1.text_input(t("oadm.student_full_name"), placeholder=t("oadm.placeholder_student_name"))
            bangla_name = c2.text_input(t("oadm.name_in_bangla"), placeholder=t("oadm.placeholder_student_name"))

            c3, c4 = st.columns(2)
            dob    = c3.date_input(t("oadm.dob_label"), min_value=date(2000,1,1),
                                    max_value=date.today(), value=date(2012,1,1))
            gender = c4.selectbox(t("oadm.gender_label"), ["Male","Female"])

            c5, c6 = st.columns(2)
            blood  = c5.selectbox(t("oadm.blood_group_label"), ["—","A+","A−","B+","B−","AB+","AB−","O+","O−"])
            nid    = c6.text_input(t("oadm.birth_reg_number"))

            address = st.text_area(t("oadm.current_address"), height=70,
                                    placeholder=t("oadm.placeholder_address"))
            perm_address = st.text_area(t("oadm.permanent_address"), height=70)

            submitted = st.form_submit_button(t("oadm.next_step_button"), type="primary",
                                               use_container_width=True)
            if submitted:
                if not name.strip() or not address.strip():
                    st.error(t("oadm.err_name_address_required"))
                else:
                    st.session_state.pub_s1 = {
                        "name": name.strip(),
                        "bangla_name": bangla_name.strip(),
                        "dob": str(dob),
                        "gender": gender,
                        "blood": None if blood == "—" else blood,
                        "nid": nid.strip(),
                        "address": address.strip(),
                        "perm_address": perm_address.strip(),
                    }
                    st.session_state.adm_pub_step = 2
                    st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Step 2: পারিবারিক তথ্য ──
    elif step == 2:
        st.markdown(f'<div class="adm-section"><h3>👨‍👩‍👦 {t("oadm.family_info_heading")}</h3>', unsafe_allow_html=True)

        with st.form("pub_step2"):
            c1, c2 = st.columns(2)
            father_name = c1.text_input(t("oadm.father_name"), placeholder=t("oadm.placeholder_father_name"))
            father_occ  = c2.text_input(t("oadm.father_occupation"), placeholder=t("oadm.placeholder_occupation"))

            c3, c4 = st.columns(2)
            father_mobile = c3.text_input(t("oadm.father_mobile"), placeholder="01XXXXXXXXX")
            father_nid    = c4.text_input(t("oadm.father_nid"))

            c5, c6 = st.columns(2)
            mother_name   = c5.text_input(t("oadm.mother_name"))
            mother_mobile = c6.text_input(t("oadm.mother_mobile"))

            guardian_same = st.checkbox(t("oadm.guardian_different"), value=False)
            guardian_name = guardian_mobile = ""
            if guardian_same:
                cg1, cg2 = st.columns(2)
                guardian_name   = cg1.text_input(t("oadm.guardian_name"))
                guardian_mobile = cg2.text_input(t("oadm.guardian_mobile"))

            monthly_income = st.selectbox(t("oadm.monthly_income_label"),
                                           [t("oadm.income_below_5k"), t("oadm.income_5k_10k"),
                                            t("oadm.income_10k_20k"), t("oadm.income_above_20k")])

            col_back, col_next = st.columns(2)
            back = col_back.form_submit_button(t("oadm.prev_step_button"), use_container_width=True)
            nxt  = col_next.form_submit_button(t("oadm.next_step_button"), type="primary",
                                                use_container_width=True)

            if back:
                st.session_state.adm_pub_step = 1; st.rerun()
            if nxt:
                if not father_name.strip() or not father_mobile.strip() or not mother_name.strip():
                    st.error(t("oadm.err_father_mother_required"))
                else:
                    st.session_state.pub_s2 = {
                        "father_name": father_name.strip(),
                        "father_occ":  father_occ.strip(),
                        "father_mobile": father_mobile.strip(),
                        "father_nid":  father_nid.strip(),
                        "mother_name": mother_name.strip(),
                        "mother_mobile": mother_mobile.strip(),
                        "guardian_name": guardian_name.strip(),
                        "guardian_mobile": guardian_mobile.strip(),
                        "monthly_income": monthly_income,
                    }
                    st.session_state.adm_pub_step = 3; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Step 3: ভর্তি তথ্য ──
    elif step == 3:
        s1 = st.session_state.get("pub_s1", {})
        s2 = st.session_state.get("pub_s2", {})

        st.markdown('<div class="adm-section"><h3>📚 ভর্তির তথ্য ও পর্যালোচনা</h3>',
                    unsafe_allow_html=True)

        with st.form("pub_step3"):
            c1, c2 = st.columns(2)
            sel_class     = c1.selectbox("ভর্তি ইচ্ছুক শ্রেণী *", list(class_map.keys()))
            prev_school   = c2.text_input("পূর্ববর্তী মাদ্রাসা/স্কুল")

            c3, c4 = st.columns(2)
            prev_class    = c3.text_input("পূর্ববর্তী শ্রেণী", placeholder="যেমন: ৩য়")
            hafiz_status  = c4.selectbox("হিফয অবস্থা",
                                          ["হাফেয নন","হাফেয (সম্পূর্ণ)","হাফেয (অসম্পূর্ণ)"])

            has_disability = st.checkbox("বিশেষ চাহিদাসম্পন্ন")
            remarks        = st.text_area("অতিরিক্ত তথ্য / বিশেষ অনুরোধ", height=70)

            # Review summary
            st.markdown(
                f"""---
                **পর্যালোচনা:**
                - ছাত্রের নাম: **{s1.get('name','')}**
                - জন্ম তারিখ: {s1.get('dob','')} | লিঙ্গ: {s1.get('gender','')}
                - পিতা: **{s2.get('father_name','')}** | মোবাইল: {s2.get('father_mobile','')}
                - মাতা: {s2.get('mother_name','')}
                - ঠিকানা: {s1.get('address','')}
                """,
            )

            c_photo, c_docs = st.columns(2)
            photo_upload = c_photo.file_uploader(
                "ছাত্রের ছবি (ঐচ্ছিক)",
                type=["jpg", "jpeg", "png", "webp"],
                key="pub_photo_upload",
                help="JPG/PNG/WebP, সর্বোচ্চ 5MB",
            )
            doc_uploads = c_docs.file_uploader(
                "জন্ম নিবন্ধন সনদ ও অন্যান্য কাগজপত্র (ঐচ্ছিক)",
                type=["pdf", "jpg", "jpeg", "png"],
                accept_multiple_files=True,
                key="pub_doc_upload",
                help="PDF/JPG/PNG, একাধিক ফাইল যোগ করা যাবে, প্রতিটি সর্বোচ্চ 10MB",
            ) or []

            agree = st.checkbox(
                "আমি নিশ্চিত করছি যে উপরের তথ্য সঠিক এবং মাদ্রাসার নিয়মকানুন মেনে চলব। *"
            )

            # honeypot — আসল ব্যবহারকারী কখনো দেখে না (utils.py-এর CSS দিয়ে
            # লুকানো), bot auto-fill heuristic এই লেবেলে ধরা পড়ে
            hp_website = st.text_input("Website", key="pub_hp_website")

            col_back2, col_submit = st.columns(2)
            back2  = col_back2.form_submit_button("← পূর্ববর্তী", use_container_width=True)
            submit = col_submit.form_submit_button("✅ আবেদন জমা করুন", type="primary",
                                                    use_container_width=True)

            if back2:
                st.session_state.adm_pub_step = 2; st.rerun()

            if submit:
                elapsed = time.time() - st.session_state.get("adm_pub_form_started_at", 0)
                if hp_website.strip():
                    # honeypot ধরা পড়েছে — bot-কে বোঝানো হয় না, চুপচাপ বাতিল
                    monitoring.capture_message(
                        "Public admission form honeypot triggered", level="warning",
                        context={"tenant_id": tenant_id},
                    )
                    st.session_state.admission_submitted = True
                    st.session_state.admission_app_no    = "APP-000000"
                    st.session_state.admission_upload_warnings = []
                    st.session_state.adm_pub_step        = 1
                    st.rerun()
                elif elapsed < MIN_SUBMIT_SECONDS:
                    st.error(t("oadm.err_try_again"))
                elif not check_ip_rate_limit("admission_submit", max_requests=5, window_seconds=3600):
                    st.error(t("oadm.err_too_many_submissions"))
                elif not agree:
                    st.error("অনুগ্রহ করে নিশ্চিতকরণ চেকবক্সে টিক দিন।")
                else:
                    # Save to DB
                    ok, app_no, upload_warnings = _save_online_application(
                        tenant_id, sess_id,
                        class_map[sel_class],
                        s1, s2,
                        {
                            "class_name": sel_class,
                            "prev_school": prev_school,
                            "prev_class": prev_class,
                            "hafiz_status": hafiz_status,
                            "has_disability": has_disability,
                            "remarks": remarks,
                            "photo_bytes": photo_upload.getvalue() if photo_upload else None,
                            "photo_filename": photo_upload.name if photo_upload else None,
                            "documents": doc_uploads,
                        }
                    )
                    if ok:
                        st.session_state.admission_submitted = True
                        st.session_state.admission_app_no   = app_no
                        st.session_state.admission_upload_warnings = upload_warnings
                        st.session_state.adm_pub_step       = 1
                        st.rerun()
                    else:
                        st.error(f"আবেদন জমা ব্যর্থ: {app_no}")
        st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Optional file uploads (photo + documents) — best-effort, never blocks submission
# ─────────────────────────────────────────────

def _upload_online_application_files(tid, student_id, photo_bytes, photo_filename, uploaded_docs):
    """
    পাবলিক ফর্মের ঐচ্ছিক ছবি/ডকুমেন্ট আপলোড — সম্পূর্ণ best-effort, কখনো আবেদন
    ব্যর্থ করে না। অননুমোদিত/anonymous ব্যবহারকারী বলে raw exception কখনো
    দেখানো হয় না — শুধু safe_error()-এর generic বার্তা।
    """
    import storage
    warnings = []

    if photo_bytes and photo_filename:
        try:
            ok, err = storage.upload_student_photo(tid, student_id, photo_bytes, photo_filename)
            if not ok:
                log_warning(err, "public_admission_photo_upload")
                warnings.append(f"ছবি আপলোড করা যায়নি: {err}")
        except Exception as ex:
            warnings.append(safe_error(ex, "upload", "public_admission_photo_upload"))

    for f in (uploaded_docs or []):
        try:
            ok, err = storage.upload_document(tid, student_id, "admission_document", f.getvalue(), f.name)
            if not ok:
                log_warning(err, "public_admission_document_upload")
                warnings.append(f"'{f.name}' আপলোড করা যায়নি: {err}")
        except Exception as ex:
            warnings.append(safe_error(ex, "upload", "public_admission_document_upload"))
    return warnings


# ─────────────────────────────────────────────
# DB Save
# ─────────────────────────────────────────────

def _save_online_application(tid, sess_id, class_id, s1, s2, s3):
    import random, string
    app_no = "APP-" + "".join(random.choices(string.digits, k=6))

    conn = get_connection()
    if not conn:
        return False, "DB error", []
    try:
        with conn.cursor() as cur:
            # Insert student
            cur.execute(
                """INSERT INTO students
                   (tenant_id, name, father_name, mother_name, mobile_no,
                    date_of_birth, gender, blood_group, present_address, status,
                    prev_school, prev_class, hafiz_status, has_disability, admission_remarks,
                    nid_no_encrypted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, s1["name"], s2["father_name"], s2["mother_name"],
                 s2["father_mobile"], s1["dob"], s1["gender"],
                 s1.get("blood"), s1["address"],
                 s3.get("prev_school") or None, s3.get("prev_class") or None,
                 s3.get("hafiz_status") or None, bool(s3.get("has_disability")),
                 s3.get("remarks") or None, encrypt_nid(s1.get("nid"))),
            )
            stu_id = cur.fetchone()["id"]

            # Insert enrollment
            cur.execute(
                """INSERT INTO student_enrollments
                   (tenant_id, student_id, session_id, class_id,
                    monthly_fee, enrollment_status)
                   VALUES (%s,%s,%s,%s,0,'pending')""",
                (tid, stu_id, sess_id, class_id),
            )

        conn.commit()
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex), []
    finally:
        release_connection(conn)

    upload_warnings = _upload_online_application_files(
        tid, stu_id, s3.get("photo_bytes"), s3.get("photo_filename"), s3.get("documents")
    )
    return True, app_no, upload_warnings


# ─────────────────────────────────────────────
# Admin Panel — আবেদন দেখুন ও অনুমোদন করুন
# ─────────────────────────────────────────────

def render_admin():
    """Admin লগইনের ভেতরে এই ফাংশন call করুন।"""
    from utils import page_header, kpi_row, alert, divider, get_tenant_id
    tid = get_tenant_id()

    page_header("🌐", "অনলাইন ভর্তি আবেদন", "অভিভাবকদের অনলাইন আবেদন পর্যালোচনা ও অনুমোদন")

    # Shareable link
    try:
        app_url = st.secrets.get("APP_URL", "https://your-app.streamlit.app")
    except Exception:
        app_url = "https://your-app.streamlit.app"

    st.markdown(
        f"""<div style="background:linear-gradient(135deg,#0F4C5C,#1a7a96);
                        border-radius:10px;padding:1rem 1.25rem;color:white;
                        margin-bottom:1rem">
          <div style="font-size:0.85rem;opacity:0.8;margin-bottom:4px">
            📎 অভিভাবকদের সাথে এই লিংক শেয়ার করুন:
          </div>
          <div style="font-family:monospace;font-size:0.9rem;font-weight:600;
                      background:rgba(255,255,255,0.15);border-radius:6px;
                      padding:6px 12px;display:inline-block">
            {app_url}/?page=admission&tenant={tid}
          </div>
          <div style="font-size:0.72rem;opacity:0.7;margin-top:4px">
            লিংকে ক্লিক করলেই অভিভাবকরা লগইন ছাড়াই ভর্তির আবেদন করতে পারবেন।
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # Pending applications
    pending = fetchall(
        """SELECT s.id AS student_id, s.name, s.father_name, s.mobile_no,
                  s.present_address, s.created_at, s.gender,
                  s.prev_school, s.prev_class, s.hafiz_status,
                  s.has_disability, s.admission_remarks,
                  e.id AS enrollment_id, c.class_name, sess.session_name
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s AND s.status='pending'
           ORDER BY s.created_at DESC""",
        (tid,),
    )

    kpi_row([
        {"label": "মোট আবেদন",   "value": len(pending), "cls": ""},
        {"label": "অপেক্ষমান",   "value": len(pending), "cls": "warning"},
    ])

    if not pending:
        alert("কোনো অপেক্ষমান আবেদন নেই।", "success")
        return

    for app in pending:
        with st.expander(
            f"**{app['name']}** — {app['class_name']} | "
            f"পিতা: {app['father_name'] or '—'} | "
            f"আবেদন: {str(app['created_at'])[:10]}"
        ):
            c1, c2 = st.columns([2, 1])
            with c1:
                st.markdown(flatten_html(f"""
                | তথ্য | মান |
                |---|---|
                | নাম | {app['name']} |
                | পিতা | {app['father_name'] or '—'} |
                | মোবাইল | {app['mobile_no'] or '—'} |
                | লিঙ্গ | {app['gender'] or '—'} |
                | ঠিকানা | {app['present_address'] or '—'} |
                | শ্রেণী | {app['class_name']} |
                | সেশন | {app['session_name']} |
                | পূর্ববর্তী মাদ্রাসা/স্কুল | {app['prev_school'] or '—'} |
                | পূর্ববর্তী শ্রেণী | {app['prev_class'] or '—'} |
                | হিফয অবস্থা | {app['hafiz_status'] or '—'} |
                | বিশেষ চাহিদাসম্পন্ন | {'হ্যাঁ' if app['has_disability'] else 'না'} |
                """))
                if app['admission_remarks']:
                    st.markdown(f"**অতিরিক্ত তথ্য / বিশেষ অনুরোধ:** {app['admission_remarks']}")
            with c2:
                roll_no = st.number_input("রোল নম্বর দিন",
                                           min_value=1, value=1,
                                           key=f"roll_{app['enrollment_id']}")
                fee = st.number_input("মাসিক ফি (৳)", min_value=0,
                                       value=500, step=50,
                                       key=f"fee_{app['enrollment_id']}")

                if st.button("✅ অনুমোদন করুন",
                              key=f"approve_{app['enrollment_id']}",
                              type="primary", use_container_width=True):
                    _approve_application(tid, app["student_id"],
                                          app["enrollment_id"], roll_no, fee)
                    audit_module.log("APPROVE","OnlineAdmission",
                                     f"অনলাইন আবেদন অনুমোদন: {app['name']}",
                                     "student", app["student_id"])
                    st.success(f"✅ {app['name']} অনুমোদিত!")
                    st.rerun()

                if st.button("❌ বাতিল করুন",
                              key=f"reject_{app['enrollment_id']}",
                              use_container_width=True):
                    _reject_application(tid, app["student_id"], app["enrollment_id"])
                    st.warning("আবেদন বাতিল করা হয়েছে।")
                    st.rerun()


def _approve_application(tid, student_id, enrollment_id, roll_no, fee):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE students SET status='active' WHERE id=%s AND tenant_id=%s",
                (student_id, tid),
            )
            cur.execute(
                """UPDATE student_enrollments
                   SET enrollment_status='active', roll_no=%s, monthly_fee=%s
                   WHERE id=%s AND tenant_id=%s""",
                (roll_no, fee, enrollment_id, tid),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _reject_application(tid, student_id, enrollment_id):
    conn = get_connection()
    if not conn:
        return
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
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)
