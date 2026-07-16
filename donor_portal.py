"""
donor_portal.py — দাতা মোবাইল পোর্টাল (parent_portal.py-এর প্যাটার্ন অনুসরণ করে)
লগইন ছাড়া মোবাইল নম্বর + OTP দিয়ে অ্যাক্সেস — নিজের চাঁদার ইতিহাস দেখা
ও রসিদ রিপ্রিন্ট করা যায়।
"""

import streamlit as st
import random
from db import fetchall
from utils import get_tenant_id, alert, flatten_html
from i18n import t

import donor_module


def _generate_otp():
    return str(random.randint(100000, 999999))


def _find_donor_by_mobile(tid, mobile):
    return fetchall(
        """SELECT id, name, mobile_no, address, monthly_amount, status, start_date
           FROM monthly_donors
           WHERE tenant_id=%s AND mobile_no=%s AND status='active'""",
        (tid, mobile.strip()),
    )


def render(tid: int = None):
    st.markdown(
        flatten_html(f"""<style>
        .main .block-container {{ padding: 1rem 1rem 4rem !important; max-width: 480px !important; }}
        </style>
        <div style="text-align:center;padding:1rem 0 1.5rem">
          <div style="font-size:2.5rem">❤️</div>
          <div style="font-size:1.1rem;font-weight:700;color:#0F4C5C;margin-top:6px">
            {t("donorportal.title")}
          </div>
          <div style="font-size:0.75rem;color:#6B7A8D">{t("donorportal.subtitle")}</div>
        </div>"""),
        unsafe_allow_html=True,
    )

    tid = tid or get_tenant_id()

    if "donor_verified" not in st.session_state:
        st.session_state.donor_verified = False
    if "donor_otp" not in st.session_state:
        st.session_state.donor_otp = None
    if "donor_mobile" not in st.session_state:
        st.session_state.donor_mobile = None

    if not st.session_state.donor_verified:
        if st.session_state.donor_otp is None:
            with st.form("donor_portal_login"):
                mobile = st.text_input(
                    t("donorportal.mobile_label"),
                    placeholder="01XXXXXXXXX",
                    help=t("donorportal.mobile_help"),
                )
                if st.form_submit_button(t("donorportal.send_otp_btn"), type="primary", use_container_width=True):
                    donors = _find_donor_by_mobile(tid, mobile)
                    if not donors:
                        st.error(t("donorportal.no_donor_found"))
                    else:
                        otp = _generate_otp()
                        st.session_state.donor_otp    = otp
                        st.session_state.donor_mobile = mobile.strip()
                        # Production-এ এখানে SMS/WhatsApp পাঠান
                        st.info(t("donorportal.otp_sent", otp=otp))
                        st.rerun()
        else:
            with st.form("donor_otp_form"):
                otp_input = st.text_input(t("donorportal.otp_code_label"), placeholder=t("donorportal.otp_placeholder"))
                c1, c2 = st.columns(2)
                verify = c1.form_submit_button(t("donorportal.verify_btn"), type="primary", use_container_width=True)
                resend = c2.form_submit_button(t("donorportal.resend_btn"), use_container_width=True)

                if verify:
                    if otp_input.strip() == st.session_state.donor_otp:
                        st.session_state.donor_verified = True
                        st.rerun()
                    else:
                        st.error(t("donorportal.otp_incorrect"))
                if resend:
                    st.session_state.donor_otp = None
                    st.rerun()
        return

    # ── Verified — show portal ──
    mobile = st.session_state.donor_mobile
    donors = _find_donor_by_mobile(tid, mobile)
    if not donors:
        alert(t("donorportal.data_not_found"), "danger")
        return

    if len(donors) > 1:
        donor_map = {d["name"]: d for d in donors}
        sel = st.selectbox(t("donorportal.select_donor_label"), list(donor_map.keys()))
        donor = donor_map[sel]
    else:
        donor = donors[0]

    st.markdown(
        flatten_html(f"""<div style="background:linear-gradient(135deg,#0F4C5C,#1a7a96);
                        border-radius:14px;padding:16px;color:white;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:12px">
            <div style="width:50px;height:50px;background:rgba(255,255,255,0.2);
                        border-radius:50%;display:flex;align-items:center;
                        justify-content:center;font-size:1.4rem">❤️</div>
            <div>
              <div style="font-size:1rem;font-weight:700">{donor['name']}</div>
              <div style="font-size:0.75rem;opacity:0.8">
                {t("donorportal.monthly_commitment")}: ৳{float(donor['monthly_amount']):,.0f}
              </div>
            </div>
          </div>
        </div>"""),
        unsafe_allow_html=True,
    )

    payments = donor_module._get_donor_payments(tid, donor["id"], limit=24)

    st.markdown(f"#### {t('donorportal.payment_history')}")
    if not payments:
        alert(t("donorportal.no_payments_yet"), "info")
    else:
        for p in payments:
            st.markdown(
                flatten_html(f"""<div style="background:white;border:1px solid #DDE3E7;border-radius:8px;
                                padding:10px 14px;margin-bottom:6px;
                                display:flex;justify-content:space-between;align-items:center">
                  <div>
                    <div style="font-size:12px;font-weight:600">{p['month_name']} {p['year']}</div>
                    <div style="font-size:10px;color:#6B7A8D">{p['receipt_no'] or '—'}</div>
                  </div>
                  <div style="text-align:right">
                    <div style="font-weight:700;font-size:13px">৳{float(p['amount_paid']):,.0f}</div>
                    <div style="font-size:10px;color:#2E7D32;font-weight:600">✅ {t('donorportal.paid')}</div>
                  </div>
                </div>"""),
                unsafe_allow_html=True,
            )

        reprint_map = {f"{p['month_name']} {p['year']} — ৳{float(p['amount_paid']):,.0f}": p for p in payments}
        sel_label = st.selectbox(t("donorportal.reprint_select_label"), list(reprint_map.keys()))
        if st.button(t("donorportal.btn_reprint"), use_container_width=True):
            payment = reprint_map[sel_label]
            tenant = donor_module._tenant_info(tid)
            donor_module._print_button("donor_portal_receipt")
            st.markdown(
                f'<div id="donor_portal_receipt">{donor_module._receipt_html(tenant, donor, payment)}</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button(t("donorportal.logout_btn"), use_container_width=True):
        for k in ["donor_verified", "donor_otp", "donor_mobile"]:
            st.session_state.pop(k, None)
        st.rerun()
