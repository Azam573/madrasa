"""
parent_portal.py — অভিভাবক মোবাইল পোর্টাল (PWA-Ready)
লগইন ছাড়া মোবাইল নম্বর + OTP দিয়ে অ্যাক্সেস।
ছাত্রের সব তথ্য মোবাইল-ফ্রেন্ডলি ভিউতে।
"""

import streamlit as st
import random
from datetime import date
from db import fetchall, fetchone, get_connection
from utils import get_tenant_id, alert, PALETTE, flatten_html
from i18n import t


# ─────────────────────────────────────────────
# OTP System (Demo — production-এ SMS দিয়ে পাঠান)
# ─────────────────────────────────────────────

def _generate_otp():
    return str(random.randint(100000, 999999))

def _find_student_by_mobile(tid, mobile):
    return fetchall(
        """SELECT s.id, s.name, s.father_name, s.status,
                  e.roll_no, e.monthly_fee, e.id AS enrollment_id,
                  c.class_name, sess.session_name
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s AND s.mobile_no=%s AND s.status='active'""",
        (tid, mobile.strip()),
    )


# ─────────────────────────────────────────────
# Mobile-optimized card components
# ─────────────────────────────────────────────

def _mobile_card(icon, title, value, color="#0F4C5C", bg="#EAF4F8"):
    st.markdown(
        flatten_html(f"""<div style="background:{bg};border-radius:12px;padding:12px 16px;
                        margin-bottom:8px;display:flex;align-items:center;gap:12px">
          <div style="font-size:1.8rem">{icon}</div>
          <div>
            <div style="font-size:11px;color:#6B7A8D;font-weight:600">{title}</div>
            <div style="font-size:16px;font-weight:700;color:{color}">{value}</div>
          </div>
        </div>"""),
        unsafe_allow_html=True,
    )

def _due_alert_card(count, amount):
    if count > 0:
        st.markdown(
            flatten_html(f"""<div style="background:#FFEBEE;border:2px solid #C62828;border-radius:12px;
                            padding:14px 16px;margin-bottom:12px;text-align:center">
              <div style="font-size:1.5rem">⚠️</div>
              <div style="font-size:14px;font-weight:700;color:#C62828;margin-top:4px">
                {t("parent.due_fee_count", count=count)}
              </div>
              <div style="font-size:20px;font-weight:700;color:#C62828">
                ৳{amount:,.0f}
              </div>
              <div style="font-size:11px;color:#666;margin-top:4px">
                {t("parent.pay_quickly")}
              </div>
            </div>"""),
            unsafe_allow_html=True,
        )


def render():
    """অভিভাবক মোবাইল পোর্টাল — মোবাইল OTP লগইন।"""
    st.markdown(
        flatten_html(f"""<style>
        .main .block-container {{ padding: 1rem 1rem 4rem !important; max-width: 480px !important; }}
        </style>
        <div style="text-align:center;padding:1rem 0 1.5rem">
          <div style="font-size:2.5rem">🕌</div>
          <div style="font-size:1.1rem;font-weight:700;color:#0F4C5C;margin-top:6px">
            {t("parent.portal_title")}
          </div>
          <div style="font-size:0.75rem;color:#6B7A8D">{t("parent.portal_subtitle")}</div>
        </div>"""),
        unsafe_allow_html=True,
    )

    tid = get_tenant_id()

    # ── Step 1: Mobile input ──
    if "parent_verified" not in st.session_state:
        st.session_state.parent_verified = False
    if "parent_otp" not in st.session_state:
        st.session_state.parent_otp = None
    if "parent_mobile" not in st.session_state:
        st.session_state.parent_mobile = None

    if not st.session_state.parent_verified:
        if st.session_state.parent_otp is None:
            # Enter mobile
            with st.form("parent_login"):
                mobile = st.text_input(
                    t("parent.mobile_label"),
                    placeholder="01XXXXXXXXX",
                    help=t("parent.mobile_help"),
                )
                if st.form_submit_button(t("parent.send_otp_btn"), type="primary", use_container_width=True):
                    students = _find_student_by_mobile(tid, mobile)
                    if not students:
                        st.error(t("parent.no_student_found"))
                    else:
                        otp = _generate_otp()
                        st.session_state.parent_otp    = otp
                        st.session_state.parent_mobile = mobile.strip()
                        # Production-এ এখানে SMS পাঠান
                        st.info(t("parent.otp_sent", otp=otp))
                        st.rerun()
        else:
            # OTP verify
            with st.form("otp_form"):
                otp_input = st.text_input(t("parent.otp_code_label"), placeholder=t("parent.otp_placeholder"))
                c1, c2 = st.columns(2)
                verify = c1.form_submit_button(t("parent.verify_btn"), type="primary", use_container_width=True)
                resend = c2.form_submit_button(t("parent.resend_btn"), use_container_width=True)

                if verify:
                    if otp_input.strip() == st.session_state.parent_otp:
                        st.session_state.parent_verified = True
                        st.rerun()
                    else:
                        st.error(t("parent.otp_incorrect"))
                if resend:
                    st.session_state.parent_otp = None
                    st.rerun()
        return

    # ── Verified — show portal ──
    mobile   = st.session_state.parent_mobile
    students = _find_student_by_mobile(tid, mobile)

    if not students:
        alert(t("parent.data_not_found"),"danger"); return

    # Multiple children
    if len(students) > 1:
        stu_map = {s["name"]: s for s in students}
        sel     = st.selectbox(t("parent.select_child_label"), list(stu_map.keys()))
        stu     = stu_map[sel]
    else:
        stu = students[0]

    # Profile card
    st.markdown(
        flatten_html(f"""<div style="background:linear-gradient(135deg,#0F4C5C,#1a7a96);
                        border-radius:14px;padding:16px;color:white;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:12px">
            <div style="width:50px;height:50px;background:rgba(255,255,255,0.2);
                        border-radius:50%;display:flex;align-items:center;
                        justify-content:center;font-size:1.4rem">👤</div>
            <div>
              <div style="font-size:1rem;font-weight:700">{stu['name']}</div>
              <div style="font-size:0.75rem;opacity:0.8">
                {stu['class_name']} · Roll {stu['roll_no'] or '—'}
              </div>
              <div style="font-size:0.72rem;opacity:0.7">{stu['session_name']}</div>
            </div>
          </div>
        </div>"""),
        unsafe_allow_html=True,
    )

    # Due alerts
    dues = fetchall(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS total FROM fee_vouchers WHERE tenant_id=%s AND student_id=%s AND status='unpaid'",
        (tid, stu["id"]),
    )
    if dues:
        _due_alert_card(int(dues[0]["n"]), float(dues[0]["total"]))

    tab_fee, tab_result, tab_att = st.tabs([t("parent.tab_fee"), t("parent.tab_result"), t("parent.tab_att")])

    with tab_fee:
        # Fee summary
        paid_total = fetchone(
            "SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE tenant_id=%s AND student_id=%s AND status='paid'",
            (tid, stu["id"]),
        )
        _mobile_card("✅", t("parent.total_paid"),
                     f"৳{float(paid_total['n']):,.0f}" if paid_total else t("parent.zero_amount"),
                     "#2E7D32","#E8F5E9")
        _mobile_card("💰", t("parent.monthly_fee"),
                     f"৳{float(stu['monthly_fee']):,.0f}",
                     "#0F4C5C","#EAF4F8")

        # Voucher list
        vouchers = fetchall(
            "SELECT voucher_no, month_name, year, amount, status, due_date FROM fee_vouchers WHERE tenant_id=%s AND student_id=%s ORDER BY year DESC, id DESC LIMIT 12",
            (tid, stu["id"]),
        )
        for v in vouchers:
            s_color = "#2E7D32" if v["status"]=="paid" else "#C62828"
            s_text  = t("parent.status_paid") if v["status"]=="paid" else t("parent.status_due")
            st.markdown(
                flatten_html(f"""<div style="background:white;border:1px solid #DDE3E7;border-radius:8px;
                                padding:10px 14px;margin-bottom:6px;
                                display:flex;justify-content:space-between;align-items:center">
                  <div>
                    <div style="font-size:12px;font-weight:600">
                      {v['month_name']} {v['year']}
                    </div>
                    <div style="font-size:10px;color:#6B7A8D">{v['voucher_no']}</div>
                  </div>
                  <div style="text-align:right">
                    <div style="font-weight:700;font-size:13px">৳{float(v['amount']):,.0f}</div>
                    <div style="font-size:10px;color:{s_color};font-weight:600">{s_text}</div>
                  </div>
                </div>"""),
                unsafe_allow_html=True,
            )

    with tab_result:
        exams = fetchall(
            """SELECT ex.id, ex.exam_name,
                      SUM(sm.total_obtained) AS obtained,
                      SUM(subj.full_marks) AS full_marks
               FROM student_marks sm
               JOIN exams ex ON ex.id=sm.exam_id
               JOIN subjects subj ON subj.id=sm.subject_id
               JOIN student_enrollments e ON e.id=sm.enrollment_id
               WHERE sm.tenant_id=%s AND e.student_id=%s
               GROUP BY ex.id, ex.exam_name ORDER BY ex.id DESC""",
            (tid, stu["id"]),
        )
        if not exams:
            alert(t("parent.no_results"),"info")
        else:
            for ex in exams:
                fm  = float(ex["full_marks"] or 1)
                obt = float(ex["obtained"] or 0)
                pct = round(obt/fm*100, 1)
                # Fix (F821): get_grade আগে import করা হয়নি — রেজাল্ট পেজ
                # খুললেই NameError হতো। utils থেকে আনা হলো।
                from utils import get_grade
                grade, gpa = get_grade(pct)
                g_color = "#2E7D32" if grade not in ("D","F") else "#C62828"
                st.markdown(
                    flatten_html(f"""<div style="background:white;border:1px solid #DDE3E7;
                                    border-radius:10px;padding:14px;margin-bottom:8px">
                      <div style="font-size:13px;font-weight:700;color:#0F4C5C">
                        {ex['exam_name']}
                      </div>
                      <div style="display:flex;gap:12px;margin-top:8px">
                        <div style="text-align:center;flex:1">
                          <div style="font-size:18px;font-weight:700;color:#0F4C5C">{pct}%</div>
                          <div style="font-size:10px;color:#6B7A8D">{t("parent.pct_label")}</div>
                        </div>
                        <div style="text-align:center;flex:1">
                          <div style="font-size:18px;font-weight:700;color:{g_color}">{grade}</div>
                          <div style="font-size:10px;color:#6B7A8D">{t("parent.grade_label")}</div>
                        </div>
                        <div style="text-align:center;flex:1">
                          <div style="font-size:18px;font-weight:700;color:#0F4C5C">{obt:.0f}/{fm:.0f}</div>
                          <div style="font-size:10px;color:#6B7A8D">{t("parent.marks_label")}</div>
                        </div>
                      </div>
                    </div>"""),
                    unsafe_allow_html=True,
                )

    with tab_att:
        att_summary = fetchone(
            """SELECT COUNT(*) AS total,
                      COUNT(CASE WHEN status='present' THEN 1 END) AS present,
                      COUNT(CASE WHEN status='absent' THEN 1 END) AS absent
               FROM attendance a
               JOIN student_enrollments e ON e.id=a.enrollment_id
               WHERE a.tenant_id=%s AND e.student_id=%s""",
            (tid, stu["id"]),
        )
        if att_summary and int(att_summary["total"]) > 0:
            tot = int(att_summary["total"])
            pre = int(att_summary["present"])
            abs = int(att_summary["absent"])
            pct = round(pre/tot*100, 1)
            att_color = "#2E7D32" if pct >= 75 else "#C62828"

            # Circle progress
            st.markdown(
                flatten_html(f"""<div style="background:white;border:1px solid #DDE3E7;border-radius:12px;
                                padding:20px;text-align:center;margin-bottom:12px">
                  <div style="font-size:3rem;font-weight:700;color:{att_color}">{pct}%</div>
                  <div style="font-size:12px;color:#6B7A8D;margin-top:4px">{t("parent.attendance_rate_label")}</div>
                  <div style="background:#EEE;border-radius:20px;height:10px;
                              margin:10px 0;overflow:hidden">
                    <div style="background:{att_color};height:100%;width:{pct}%;
                                border-radius:20px"></div>
                  </div>
                  <div style="display:flex;justify-content:space-around;font-size:12px;margin-top:8px">
                    <span>{t("parent.present_label")} <b>{pre}</b></span>
                    <span>{t("parent.absent_label")} <b>{abs}</b></span>
                    <span>{t("parent.total_label")} <b>{tot}</b></span>
                  </div>
                </div>"""),
                unsafe_allow_html=True,
            )
            if pct < 75:
                st.markdown(
                    f'<div style="background:#FFEBEE;border-radius:8px;padding:10px;'
                    f'text-align:center;font-size:12px;color:#C62828;font-weight:600">'
                    f'{t("parent.low_attendance_warning")}</div>',
                    unsafe_allow_html=True,
                )
        else:
            alert(t("parent.no_attendance_data"),"info")

    # Logout
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button(t("parent.logout_btn"), use_container_width=True):
        for k in ["parent_verified","parent_otp","parent_mobile"]:
            st.session_state.pop(k, None)
        st.rerun()
