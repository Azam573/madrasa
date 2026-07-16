"""
whatsapp_module.py — WhatsApp & SMS Auto-Alert System
বকেয়া ফি, পরীক্ষার ফলাফল, অনুপস্থিতি — স্বয়ংক্রিয় বার্তা।
Twilio / UltraMsg / Green Web SMS gateway integration।
"""

import streamlit as st
import urllib.request, urllib.parse, json
from datetime import date
from db import fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id
import audit_module
from error_handler import safe_db_error
from i18n import t


def _get_wa_config():
    """Streamlit secrets থেকে WhatsApp config পড়ুন।"""
    try:
        return {
            "provider":        st.secrets.get("WA_PROVIDER","ultramsg"),
            "instance_id":     st.secrets.get("WA_INSTANCE_ID",""),
            "token":           st.secrets.get("WA_TOKEN",""),
            "sms_api_key":     st.secrets.get("SMS_API_KEY",""),
            "sms_sender":      st.secrets.get("SMS_SENDER_ID","SmartMadrasa"),
        }
    except Exception:
        return {}


def _send_ultramsg(instance_id, token, phone, message):
    """UltraMsg দিয়ে WhatsApp পাঠান।"""
    url = f"https://api.ultramsg.com/{instance_id}/messages/chat"
    payload = urllib.parse.urlencode({
        "token":   token,
        "to":      phone,
        "body":    message,
        "priority":"1",
    }).encode()
    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as ex:
        return {"error": safe_db_error(ex)}


def _send_greenweb_sms(api_key, sender, phone, message):
    """Green Web Bangladesh SMS। sms_gateway.py-এর shared HTTPS POST transport
    ব্যবহার করে (আগে http:// GET, token query-string-এ — এখন নয়)।"""
    from sms_gateway import send_sms_raw
    try:
        return send_sms_raw(phone, message, api_key=api_key, sender=sender)
    except Exception as ex:
        return safe_db_error(ex, "greenweb_sms")


def _due_fee_message(student_name, amount, month, madrasa_name):
    return t(
        "wa.msg_due_fee",
        madrasa=madrasa_name, student=student_name, month=month, amount=amount,
    )


def _absent_message(student_name, att_date, madrasa_name):
    return t(
        "wa.msg_absent",
        madrasa=madrasa_name, student=student_name, att_date=att_date,
    )


def _result_message(student_name, exam_name, grade, gpa, madrasa_name):
    return t(
        "wa.msg_result",
        madrasa=madrasa_name, exam=exam_name, student=student_name, grade=grade, gpa=gpa,
    )


def render():
    tid  = get_tenant_id()
    page_header("📱", "WhatsApp & SMS Alert", t("wa.page_subtitle"))

    tenant  = fetchone("SELECT * FROM tenants WHERE id=%s", (tid,)) or {}
    madrasa = tenant.get("madrasa_name","Smart Madrasa")
    config  = _get_wa_config()

    # Config status
    is_configured = bool(config.get("token") or config.get("sms_api_key"))
    if not is_configured:
        alert(
            f"📋 <strong>{t('wa.setup_title')}</strong> "
            f"{t('wa.setup_instructions')}<br>"
            "<code>WA_PROVIDER = \"ultramsg\"</code><br>"
            "<code>WA_INSTANCE_ID = \"your-instance\"</code><br>"
            "<code>WA_TOKEN = \"your-token\"</code><br>"
            f"{t('wa.setup_or_sms')} <code>SMS_API_KEY = \"your-greenweb-key\"</code>",
            "warning",
        )

    tab_due, tab_absent, tab_result, tab_custom, tab_log = st.tabs([
        t("wa.tab_due"),
        t("wa.tab_absent"),
        t("wa.tab_result"),
        t("wa.tab_custom"),
        t("wa.tab_log"),
    ])

    # ── বকেয়া ফি Alert ──
    with tab_due:
        st.markdown(f"#### 💰 {t('wa.due_fee_heading')}")

        due_students = fetchall(
            """SELECT s.name, s.mobile_no,
                      COUNT(v.id) AS due_count,
                      SUM(v.amount) AS total_due,
                      STRING_AGG(v.month_name || ' ' || v.year::text, ', ') AS months
               FROM fee_vouchers v
               JOIN students s ON s.id=v.student_id
               WHERE v.tenant_id=%s AND v.status IN ('unpaid','partial')
                 AND s.mobile_no IS NOT NULL
               GROUP BY s.id, s.name, s.mobile_no
               ORDER BY total_due DESC""",
            (tid,),
        )

        if not due_students:
            alert(t("wa.no_due_fee"), "success")
        else:
            total_due_sum = sum(float(d["total_due"] or 0) for d in due_students)
            kpi_row([
                {"label": t("wa.kpi_due_students"),    "value": len(due_students),       "cls": "danger"},
                {"label": t("wa.kpi_total_due"),       "value": f"৳{total_due_sum:,.0f}", "cls": "danger"},
            ])

            # Preview
            st.markdown(f"**{t('wa.message_preview')}**")
            sample = due_students[0]
            preview_msg = _due_fee_message(
                sample["name"], float(sample["total_due"]), sample["months"], madrasa
            )
            st.code(preview_msg, language=None)

            col_send, col_test = st.columns(2)

            if col_test.button(t("wa.send_test_button"), key="test_due"):
                if not is_configured:
                    alert(t("wa.configure_gateway"), "danger")
                else:
                    msg = _due_fee_message(
                        sample["name"], float(sample["total_due"]), sample["months"], madrasa
                    )
                    phone = sample["mobile_no"]
                    if config.get("token"):
                        result = _send_ultramsg(
                            config["instance_id"], config["token"], phone, msg
                        )
                        st.json(result)
                    else:
                        r = _send_greenweb_sms(config["sms_api_key"], config["sms_sender"], phone, msg)
                        st.write(f"SMS Response: {r}")
                    audit_module.log("EXPORT","WhatsApp","টেস্ট বকেয়া ফি বার্তা পাঠানো হয়েছে")

            if col_send.button(t("wa.send_all_button", n=len(due_students)), type="primary", key="send_all_due"):
                if not is_configured:
                    alert(t("wa.configure_gateway"), "danger")
                else:
                    sent = 0
                    failed = 0
                    progress = st.progress(0)
                    for i, stu in enumerate(due_students):
                        msg   = _due_fee_message(stu["name"], float(stu["total_due"]), stu["months"], madrasa)
                        phone = stu["mobile_no"]
                        try:
                            if config.get("token"):
                                res = _send_ultramsg(config["instance_id"], config["token"], phone, msg)
                                if "sent" in str(res).lower():
                                    sent += 1
                                else:
                                    failed += 1
                            else:
                                _send_greenweb_sms(config["sms_api_key"], config["sms_sender"], phone, msg)
                                sent += 1
                        except Exception:
                            failed += 1
                        progress.progress((i+1)/len(due_students))

                    audit_module.log("EXPORT","WhatsApp",f"বকেয়া ফি বার্তা পাঠানো: {sent} সফল, {failed} ব্যর্থ")
                    st.success(t("wa.send_summary", sent=sent, failed=failed))

    # ── অনুপস্থিতি Alert ──
    with tab_absent:
        st.markdown(f"#### 📅 {t('wa.absent_heading')}")

        absent_today = fetchall(
            """SELECT s.name, s.mobile_no, c.class_name
               FROM attendance a
               JOIN student_enrollments e ON e.id=a.enrollment_id
               JOIN students s ON s.id=e.student_id
               JOIN classes c ON c.id=e.class_id
               WHERE a.tenant_id=%s AND a.date=CURRENT_DATE
                 AND a.status='absent' AND s.mobile_no IS NOT NULL
               ORDER BY c.class_numeric, s.name""",
            (tid,),
        )

        if not absent_today:
            alert(t("wa.no_absent_today"), "info")
        else:
            kpi_row([{"label": t("wa.kpi_absent_today"), "value": len(absent_today), "cls": "danger"}])

            today_str = date.today().strftime("%d %B %Y")
            sample_msg = _absent_message(absent_today[0]["name"], today_str, madrasa)
            st.code(sample_msg, language=None)

            if st.button(t("wa.send_absent_alert_button", n=len(absent_today)), type="primary", key="send_absent"):
                if not is_configured:
                    alert(t("wa.configure_gateway"), "danger")
                else:
                    sent = 0
                    for stu in absent_today:
                        msg = _absent_message(stu["name"], today_str, madrasa)
                        try:
                            if config.get("token"):
                                _send_ultramsg(config["instance_id"], config["token"], stu["mobile_no"], msg)
                            else:
                                _send_greenweb_sms(config["sms_api_key"], config["sms_sender"], stu["mobile_no"], msg)
                            sent += 1
                        except Exception:
                            pass
                    audit_module.log("EXPORT","WhatsApp",f"অনুপস্থিতি alert: {sent} জনকে পাঠানো হয়েছে")
                    st.success(t("wa.absent_alert_sent", n=sent))

    # ── ফলাফল Alert ──
    with tab_result:
        st.markdown(f"#### 🏆 {t('wa.result_heading')}")
        alert(t("wa.result_info"),"info")

        exams = fetchall(
            "SELECT id, exam_name FROM exams WHERE tenant_id=%s ORDER BY id DESC LIMIT 10",
            (tid,),
        )
        if exams:
            exam_map = {e["exam_name"]: e["id"] for e in exams}
            sel_exam = st.selectbox(t("wa.exam_label"), list(exam_map.keys()))
            if st.button(t("wa.send_result_button"), type="primary"):
                results = fetchall(
                    """SELECT s.name, s.mobile_no,
                              SUM(sm.total_obtained) AS obtained,
                              SUM(subj.full_marks) AS full_marks
                       FROM student_marks sm
                       JOIN student_enrollments e ON e.id=sm.enrollment_id
                       JOIN students s ON s.id=e.student_id
                       JOIN subjects subj ON subj.id=sm.subject_id
                       WHERE sm.tenant_id=%s AND sm.exam_id=%s AND s.mobile_no IS NOT NULL
                       GROUP BY s.id, s.name, s.mobile_no""",
                    (tid, exam_map[sel_exam]),
                )
                if not results:
                    alert(t("wa.no_marks_data"), "warning")
                elif not is_configured:
                    alert(t("wa.configure_gateway"), "danger")
                else:
                    from utils import get_grade
                    sent = 0
                    for r in results:
                        fm = float(r["full_marks"] or 1)
                        pct = float(r["obtained"] or 0) / fm * 100
                        grade, gpa = get_grade(pct)
                        msg = _result_message(r["name"], sel_exam, grade, f"{gpa:.2f}", madrasa)
                        try:
                            if config.get("token"):
                                _send_ultramsg(config["instance_id"], config["token"], r["mobile_no"], msg)
                            else:
                                _send_greenweb_sms(config["sms_api_key"], config["sms_sender"], r["mobile_no"], msg)
                            sent += 1
                        except Exception:
                            pass
                    audit_module.log("EXPORT","WhatsApp",f"ফলাফল বার্তা: {sent} জনকে পাঠানো হয়েছে")
                    st.success(t("wa.result_alert_sent", n=sent))

    # ── কাস্টম বার্তা ──
    with tab_custom:
        st.markdown(f"#### ✉️ {t('wa.custom_message_heading')}")
        with st.form("custom_msg_form"):
            target = st.radio(t("wa.recipient_label"), [t("wa.recipient_specific"), t("wa.recipient_all_active")], horizontal=True)
            if target == t("wa.recipient_specific"):
                phone_input = st.text_input(t("wa.mobile_number_label"), placeholder="01XXXXXXXXX")
            custom_msg = st.text_area(
                t("wa.write_message_label"), height=120,
                value=t("wa.custom_message_template", madrasa=madrasa),
            )
            char_count = len(custom_msg)
            st.caption(t("wa.char_sms_count", chars=char_count, sms=(char_count//160)+1))

            if st.form_submit_button(t("wa.send_button"), type="primary"):
                if not is_configured:
                    alert(t("wa.configure_gateway"), "danger")
                elif target == t("wa.recipient_specific"):
                    if not phone_input.strip():
                        st.error(t("wa.enter_number"))
                    else:
                        if config.get("token"):
                            res = _send_ultramsg(config["instance_id"], config["token"],
                                                  phone_input.strip(), custom_msg)
                            st.json(res)
                        else:
                            r = _send_greenweb_sms(config["sms_api_key"], config["sms_sender"],
                                                    phone_input.strip(), custom_msg)
                            st.write(f"SMS: {r}")
                        audit_module.log("EXPORT","WhatsApp",f"কাস্টম বার্তা → {phone_input.strip()}")
                else:
                    all_parents = fetchall(
                        "SELECT DISTINCT mobile_no FROM students WHERE tenant_id=%s AND mobile_no IS NOT NULL AND status='active'",
                        (tid,),
                    )
                    sent = 0
                    for p in all_parents:
                        try:
                            if config.get("token"):
                                _send_ultramsg(config["instance_id"], config["token"],
                                               p["mobile_no"], custom_msg)
                            else:
                                _send_greenweb_sms(config["sms_api_key"], config["sms_sender"],
                                                   p["mobile_no"], custom_msg)
                            sent += 1
                        except Exception:
                            pass
                    audit_module.log("EXPORT","WhatsApp",f"সব অভিভাবক: {sent} জনকে কাস্টম বার্তা")
                    st.success(t("wa.custom_sent", n=sent))

    # ── লগ ──
    with tab_log:
        st.markdown(f"#### 📜 {t('wa.log_heading')}")
        logs = fetchall(
            """SELECT username, description, created_at
               FROM audit_logs
               WHERE tenant_id=%s AND module='WhatsApp'
               ORDER BY created_at DESC LIMIT 50""",
            (tid,),
        )
        if not logs:
            alert(t("wa.no_messages_sent"), "info")
        else:
            for l in logs:
                st.markdown(
                    f'<div style="padding:6px 12px;border-bottom:1px solid #EEE;font-size:12px">'
                    f'📱 {l["description"]} — '
                    f'<span style="color:#6B7A8D">{l["username"]} | {str(l["created_at"])[:16]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
