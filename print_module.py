"""
print_module.py — প্রিন্টযোগ্য ডকুমেন্ট সেন্টার
১. ফি ভাউচার (৩ কপি — অফিস / অভিভাবক / ব্যাংক)
২. টাকা জমার রিসিট (Money Receipt)
৩. শিক্ষক বেতন স্লিপ (Salary Slip)
৪. মার্কশিট (HTML → Print/PDF)

সব ডকুমেন্ট browser print / Ctrl+P দিয়ে PDF সেভ করা যাবে।
"""

import streamlit as st
from datetime import date
from db import fetchall, fetchone
from utils import page_header, alert, divider, get_tenant_id, get_grade, flatten_html
from utils import print_button as _shared_print_button
from i18n import t

_METHOD_LABEL_MAP_KEYS = {
    "cash": "fin.method_cash", "bkash": "fin.method_bkash",
    "nagad": "fin.method_nagad", "bank transfer": "fin.method_bank",
}

# ──────────────────────────────────────────────────────────────────────────────
# Shared print CSS (injected once per document)
# ──────────────────────────────────────────────────────────────────────────────

PRINT_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Amiri:wght@400;700&family=Noto+Sans+Bengali:wght@400;500;600;700&display=swap');
.print-wrap { font-family:'Inter',sans-serif; color:#1A2332; }
.print-wrap table { border-collapse:collapse; width:100%; }
.print-wrap td, .print-wrap th {
    border:1px solid #ccc; padding:5px 8px; font-size:12px;
}
.print-wrap th { background:#0F4C5C; color:white; font-weight:600; }
.print-wrap .no-border td { border:none; padding:3px 6px; }
@media print {
    .stApp > header, section[data-testid="stSidebar"],
    .stButton, .stSelectbox, .stTextInput,
    [data-testid="stToolbar"], footer { display:none !important; }
    .print-wrap { margin:0; padding:0; }
}
</style>
"""

def _inject_print_css():
    st.markdown(PRINT_CSS, unsafe_allow_html=True)

def _print_button(doc_id: str):
    """
    Print trigger for a specific div. Delegates to utils.print_button(),
    which uses a real iframe (st.components.v1.html) instead of
    st.markdown(unsafe_allow_html=True) — a raw onclick="..." attribute
    rendered via st.markdown crashes on click with a React error
    ("Expected onClick listener to be a function, instead got a value of
    `string` type"), and a <script> tag inserted that way never executes at
    all. Neither limitation applies inside a genuine iframe.
    """
    _shared_print_button(t("print.btn_print"), doc_id=doc_id)

# ──────────────────────────────────────────────────────────────────────────────
# Helper — tenant info
# ──────────────────────────────────────────────────────────────────────────────

def _tenant_info(tid):
    """tenants + tenant_branding merge — printable document-এর branding source।"""
    import branding as _br
    from db import get_connection, release_connection
    conn = get_connection()
    if not conn:
        return {"madrasa_name": "Smart Madrasa", "address": "", "phone": "",
                "email": "", **{k: v for k, v in _br._DEFAULTS.items()}}
    try:
        with conn.cursor() as cur:
            return _br.get_branding(cur, tid)
    finally:
        release_connection(conn)


def _brand_name_block(tenant, font_size=17):
    """হেডারে লোগো (থাকলে) + নাম — সব template-এর কমন block।"""
    logo = ""
    if tenant.get("show_logo") and tenant.get("logo_base64"):
        logo = (f'<img src="data:{tenant.get("logo_mime") or "image/png"};'
                f'base64,{tenant["logo_base64"]}" '
                f'style="height:44px;vertical-align:middle;margin-right:8px;'
                f'object-fit:contain">')
    else:
        logo = "🕌 "
    p = tenant.get("primary_color") or "#0F4C5C"
    arabic = (f'<div class="brand-calligraphy" style="font-size:12px;color:{p}" dir="rtl">'
              f'{tenant["name_arabic"]}</div>') if tenant.get("name_arabic") else ""
    return (f'{arabic}<div class="brand-calligraphy" style="font-size:{font_size}px;font-weight:700;'
            f'color:{p}">{logo}{tenant["madrasa_name"]}</div>')

# ──────────────────────────────────────────────────────────────────────────────
# ১. ফি ভাউচার — ৩ কপি
# ──────────────────────────────────────────────────────────────────────────────

def _fee_voucher_html(tenant, student, voucher, copy_label, bg_color):
    fund_labels = {
        "general": t("fin.fund_general"),
        "zakat": t("fin.fund_zakat"),
        "lillah_boarding": t("fin.fund_lillah_boarding"),
    }
    fund = fund_labels.get(voucher.get("fund_type", "general"), t("fin.fund_general"))
    status_color = "#2E7D32" if voucher["status"] == "paid" else "#C62828"
    status_text  = t("print.status_paid") if voucher["status"] == "paid" else t("print.status_unpaid")

    return flatten_html(f"""
    <div style="border:2px solid #0F4C5C;border-radius:10px;padding:16px;
                background:{bg_color};margin-bottom:8px;page-break-inside:avoid">

      <!-- হেডার -->
      <table class="no-border" style="margin-bottom:8px">
        <tr>
          <td style="width:70%">
            {_brand_name_block(tenant, font_size=17)}
            <div style="font-size:11px;color:#555">
              {tenant.get('address','') or ''} | ☎ {tenant.get('phone','') or ''}
            </div>
          </td>
          <td style="text-align:right;vertical-align:top">
            <div style="background:#0F4C5C;color:white;padding:4px 10px;
                        border-radius:6px;font-size:11px;font-weight:600">
              {copy_label}
            </div>
            <div style="font-size:10px;color:#777;margin-top:4px">
              {t('print.voucher_no_label')}: <strong>{voucher['voucher_no']}</strong>
            </div>
          </td>
        </tr>
      </table>

      <div style="border-top:2px dashed #0F4C5C;margin:6px 0;padding-top:8px">
        <div style="text-align:center;font-size:13px;font-weight:700;
                    color:#0F4C5C;margin-bottom:8px">
          {t('print.voucher_title')}
        </div>
      </div>

      <!-- ছাত্রের তথ্য -->
      <table class="no-border" style="margin-bottom:8px;font-size:12px">
        <tr>
          <td style="width:50%"><strong>{t('print.student_name_label')}:</strong> {student['name']}</td>
          <td><strong>{t('print.father_name_label')}:</strong> {student.get('father_name') or '—'}</td>
        </tr>
        <tr>
          <td><strong>{t('print.class_label')}:</strong> {student.get('class_name','')}</td>
          <td><strong>{t('print.roll_no_label')}:</strong> {student.get('roll_no') or '—'}</td>
        </tr>
        <tr>
          <td><strong>{t('print.session_label')}:</strong> {student.get('session_name','')}</td>
          <td><strong>{t('print.mobile_label')}:</strong> {student.get('mobile_no') or '—'}</td>
        </tr>
      </table>

      <!-- ভাউচার বিবরণ -->
      <table style="margin-bottom:8px;font-size:12px">
        <tr>
          <th>{t('print.col_description')}</th><th>{t('print.col_month')}</th><th>{t('print.col_fund')}</th>
          <th>{t('print.col_amount')}</th><th>{t('print.col_due_date')}</th><th>{t('print.col_status')}</th>
        </tr>
        <tr>
          <td>{t('print.row_monthly_fee')}</td>
          <td>{voucher.get('month_name','')} {voucher.get('year','')}</td>
          <td>{fund}</td>
          <td style="font-weight:700">৳ {float(voucher['amount']):,.0f}</td>
          <td>{str(voucher.get('due_date','')) or '—'}</td>
          <td style="color:{status_color};font-weight:700">{status_text}</td>
        </tr>
      </table>

      <!-- মোট -->
      <table class="no-border" style="font-size:13px">
        <tr>
          <td style="width:60%">
            {f"<span style='color:#2E7D32;font-size:11px'>{t('print.paid_date_note', date=str(voucher.get('paid_at',''))[:10])}</span>"
             if voucher['status']=='paid' else
             f"<span style='color:#C62828;font-size:11px'>{t('print.due_warning')}</span>"}
          </td>
          <td style="text-align:right">
            <strong style="font-size:15px;color:#0F4C5C">
              {t('print.total_label', amount=f"{float(voucher['amount']):,.0f}")}
            </strong>
          </td>
        </tr>
      </table>

      <!-- স্বাক্ষর -->
      <table class="no-border" style="margin-top:16px;font-size:11px">
        <tr>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:30px;padding-top:4px">
              {t('print.sig_guardian')}
            </div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:30px;padding-top:4px">
              {t('print.sig_cashier')}
            </div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:30px;padding-top:4px">
              {t('print.sig_headteacher')}
            </div>
          </td>
        </tr>
      </table>

      <div style="text-align:center;font-size:10px;color:#888;margin-top:6px">
        {t('print.issue_date_footer', date=str(voucher.get('issue_date', date.today())))}
      </div>
    </div>""")


def _render_fee_voucher(tid):
    st.markdown(f"#### {t('print.fv_header')}")

    students = fetchall(
        """SELECT s.id, s.name, s.father_name, s.mobile_no,
                  e.id AS enrollment_id, e.roll_no,
                  c.class_name, sess.session_name
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s AND s.status='active' AND e.enrollment_status='active'
           ORDER BY c.class_numeric, e.roll_no""",
        (tid,),
    )
    if not students:
        alert(t("print.warn_no_active_students"), "warning")
        return

    stu_map = {f"{s['name']} — {s['class_name']} (Roll {s['roll_no'] or '—'})": s for s in students}
    sel = st.selectbox(t("print.select_student"), list(stu_map.keys()), key="fv_stu")
    stu = stu_map[sel]

    vouchers = fetchall(
        """SELECT * FROM fee_vouchers
           WHERE tenant_id=%s AND student_id=%s
           ORDER BY year DESC, id DESC LIMIT 20""",
        (tid, stu["id"]),
    )
    if not vouchers:
        alert(t("print.info_no_vouchers"), "info")
        return

    vch_map = {
        f"{v['voucher_no']} | {v['month_name']} {v['year']} | ৳{float(v['amount']):,.0f} [{v['status'].upper()}]": v
        for v in vouchers
    }
    sel_vch = st.selectbox(t("print.select_voucher"), list(vch_map.keys()), key="fv_vch")
    voucher = vch_map[sel_vch]
    tenant  = _tenant_info(tid)

    _print_button("fee_voucher_print")
    cut = t("print.cut_here")
    st.markdown(
        f"""<div id="fee_voucher_print" class="print-wrap">
          <div style="text-align:center;font-size:11px;color:#888;margin-bottom:10px">
            {cut} {cut}
          </div>
          {_fee_voucher_html(tenant, stu, voucher, t("print.copy_office"), "#EAF4F8")}
          <div style="text-align:center;font-size:11px;color:#888;margin:6px 0">
            {cut}
          </div>
          {_fee_voucher_html(tenant, stu, voucher, t("print.copy_guardian"), "#FFF8E1")}
          <div style="text-align:center;font-size:11px;color:#888;margin:6px 0">
            {cut}
          </div>
          {_fee_voucher_html(tenant, stu, voucher, t("print.copy_bank"), "#F3F9F3")}
        </div>""",
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# ২. মানি রিসিট (টাকা জমার রিসিট)
# ──────────────────────────────────────────────────────────────────────────────

def _money_receipt_html(tenant, student, payment, voucher):
    method_key = (payment.get("payment_method") or "cash").lower()
    method_label = t(_METHOD_LABEL_MAP_KEYS.get(method_key, "fin.method_cash"))
    return flatten_html(f"""
    <div style="max-width:560px;margin:0 auto;border:3px double #0F4C5C;
                border-radius:12px;padding:20px;font-family:Inter,sans-serif;
                background:white">

      <!-- হেডার -->
      <div style="text-align:center;border-bottom:2px solid #0F4C5C;padding-bottom:12px;margin-bottom:12px">
        {_brand_name_block(tenant, font_size=22)}
        <div style="font-size:11px;color:#666;margin-top:3px">
          {tenant.get('address','') or ''} | ☎ {tenant.get('phone','') or ''}
        </div>
        <div style="margin-top:10px;font-size:16px;font-weight:700;
                    background:#0F4C5C;color:white;padding:5px 20px;
                    border-radius:20px;display:inline-block;letter-spacing:1px">
          {t('print.receipt_title')}
        </div>
      </div>

      <!-- রিসিট নং ও তারিখ -->
      <table class="no-border" style="font-size:12px;margin-bottom:12px">
        <tr>
          <td><strong>{t('print.receipt_no_label')}</strong>
            <span style="color:#0F4C5C;font-weight:700"> {payment['receipt_no']}</span>
          </td>
          <td style="text-align:right"><strong>{t('print.date_label')}</strong> {str(payment['payment_date'])}</td>
        </tr>
      </table>

      <!-- ছাত্রের তথ্য বক্স -->
      <div style="background:#F7F9FA;border:1px solid #DDE3E7;border-radius:8px;
                  padding:10px 14px;margin-bottom:12px;font-size:12px">
        <table class="no-border">
          <tr>
            <td style="width:50%"><strong>{t('print.student_name_label')}:</strong> {student['name']}</td>
            <td><strong>{t('print.father_name_label')}:</strong> {student.get('father_name') or '—'}</td>
          </tr>
          <tr>
            <td><strong>{t('print.class_label')}:</strong> {student.get('class_name','')}</td>
            <td><strong>{t('print.roll_no_label')}:</strong> {student.get('roll_no') or '—'}</td>
          </tr>
          <tr>
            <td><strong>{t('print.mobile_label')}:</strong> {student.get('mobile_no') or '—'}</td>
            <td><strong>{t('print.session_label')}:</strong> {student.get('session_name','')}</td>
          </tr>
        </table>
      </div>

      <!-- পেমেন্ট বিবরণ -->
      <table style="font-size:12px;margin-bottom:12px">
        <tr>
          <th>{t('print.col_description')}</th><th>{t('print.col_month_year')}</th><th>{t('print.col_voucher_no')}</th>
          <th>{t('print.col_payment_method')}</th><th style="text-align:right">{t('print.col_amount')}</th>
        </tr>
        <tr>
          <td>{t('print.row_monthly_fee2')}</td>
          <td>{voucher.get('month_name','')} {voucher.get('year','')}</td>
          <td>{voucher.get('voucher_no','')}</td>
          <td>{method_label}</td>
          <td style="text-align:right;font-weight:700">
            ৳ {float(payment['amount_paid']):,.0f}
          </td>
        </tr>
      </table>

      <!-- মোট টাকার বক্স -->
      <div style="background:#0F4C5C;color:white;border-radius:8px;
                  padding:12px 16px;margin-bottom:16px;text-align:center">
        <div style="font-size:11px;opacity:0.8;margin-bottom:4px">{t('print.total_received_label')}</div>
        <div style="font-size:24px;font-weight:700">
          ৳ {float(payment['amount_paid']):,.2f} {t('print.amount_words_suffix')}
        </div>
        <div style="font-size:11px;opacity:0.8;margin-top:4px">
          ({_amount_in_words(int(payment['amount_paid']))} {t('print.amount_words_suffix')})
        </div>
      </div>

      <!-- স্বাক্ষর -->
      <table class="no-border" style="font-size:11px;margin-top:8px">
        <tr>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">
              {t('print.sig_customer')}
            </div>
          </td>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">
              {t('print.sig_cashier_approver')}
            </div>
          </td>
        </tr>
      </table>

      <div style="text-align:center;font-size:10px;color:#999;margin-top:12px;
                  border-top:1px dashed #ccc;padding-top:8px">
        {t('print.receipt_footer', madrasa=tenant['madrasa_name'])}
      </div>
    </div>""")


def _amount_in_words(amount: int) -> str:
    """Simple Bangla amount-in-words for common amounts."""
    ones = ["", "এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "নয়",
            "দশ", "এগারো", "বারো", "তেরো", "চৌদ্দ", "পনেরো", "ষোল", "সতেরো",
            "আঠারো", "উনিশ"]
    tens  = ["", "", "বিশ", "ত্রিশ", "চল্লিশ", "পঞ্চাশ", "ষাট", "সত্তর",
             "আশি", "নব্বই"]

    if amount == 0:
        return "শূন্য"
    if amount < 0:
        return "ঋণাত্মক " + _amount_in_words(-amount)

    result = ""
    if amount >= 1000:
        result += _amount_in_words(amount // 1000) + " হাজার "
        amount %= 1000
    if amount >= 100:
        result += ones[amount // 100] + " শত "
        amount %= 100
    if amount >= 20:
        result += tens[amount // 10] + " "
        amount %= 10
    if amount > 0:
        result += ones[amount] + " "

    return result.strip()


def _render_money_receipt(tid):
    st.markdown(f"#### {t('print.mr_header')}")

    students = fetchall(
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
    if not students:
        alert(t("print.warn_no_students"), "warning")
        return

    stu_map = {f"{s['name']} — {s['class_name']} (Roll {s['roll_no'] or '—'})": s for s in students}
    sel = st.selectbox(t("print.select_student"), list(stu_map.keys()), key="mr_stu")
    stu = stu_map[sel]

    payments = fetchall(
        """SELECT p.*, v.voucher_no, v.month_name, v.year, v.fund_type
           FROM fee_payments p
           JOIN fee_vouchers v ON v.id=p.voucher_id
           WHERE p.tenant_id=%s AND v.student_id=%s
           ORDER BY p.created_at DESC LIMIT 20""",
        (tid, stu["id"]),
    )
    if not payments:
        alert(t("print.info_no_payments"), "info")
        return

    pay_map = {
        f"{p['receipt_no']} | {p['month_name']} {p['year']} | ৳{float(p['amount_paid']):,.0f} | {str(p['payment_date'])}": p
        for p in payments
    }
    sel_pay = st.selectbox(t("print.select_payment"), list(pay_map.keys()), key="mr_pay")
    payment = pay_map[sel_pay]

    voucher = fetchone(
        "SELECT * FROM fee_vouchers WHERE voucher_no=%s AND tenant_id=%s",
        (payment["voucher_no"], tid),
    ) or {}
    tenant = _tenant_info(tid)

    _print_button("money_receipt_print")
    st.markdown(
        f'<div id="money_receipt_print" class="print-wrap">'
        f'{_money_receipt_html(tenant, stu, payment, voucher)}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# ৩. বেতন স্লিপ (Salary Slip)
# ──────────────────────────────────────────────────────────────────────────────

def _salary_slip_html(tenant, teacher, salary):
    net = float(salary.get("net_salary") or
                (float(salary["basic_salary"]) + float(salary["bonus"]) - float(salary["deduction"])))
    method_key = (salary.get("payment_method") or "cash").lower()
    method_label = t(_METHOD_LABEL_MAP_KEYS.get(method_key, "fin.method_cash"))
    return flatten_html(f"""
    <div style="max-width:580px;margin:0 auto;border:2px solid #0F4C5C;
                border-radius:12px;padding:20px;font-family:Inter,sans-serif;
                background:white">

      <!-- হেডার -->
      <div style="text-align:center;border-bottom:2px solid #0F4C5C;
                  padding-bottom:12px;margin-bottom:14px">
        {_brand_name_block(tenant, font_size=20)}
        <div style="font-size:11px;color:#666">
          {tenant.get('address','') or ''} | ☎ {tenant.get('phone','') or ''}
        </div>
        <div style="margin-top:10px;font-size:15px;font-weight:700;
                    background:#0F4C5C;color:white;padding:4px 20px;
                    border-radius:20px;display:inline-block">
          {t('print.slip_title', month=salary['month_name'], year=salary['year'])}
        </div>
      </div>

      <!-- শিক্ষকের তথ্য -->
      <div style="background:#F7F9FA;border:1px solid #DDE3E7;border-radius:8px;
                  padding:10px 14px;margin-bottom:14px;font-size:12px">
        <table class="no-border">
          <tr>
            <td style="width:50%"><strong>{t('print.name_label')}</strong> {teacher['name']}</td>
            <td><strong>{t('print.designation_label')}</strong> {teacher.get('designation','Teacher')}</td>
          </tr>
          <tr>
            <td><strong>{t('print.joining_date_label')}</strong> {str(teacher.get('joining_date','')) or '—'}</td>
            <td><strong>{t('print.mobile_label')}:</strong> {teacher.get('mobile_no','') or '—'}</td>
          </tr>
          <tr>
            <td><strong>{t('print.qualification_label')}</strong> {teacher.get('qualification','') or '—'}</td>
            <td><strong>{t('print.nid_label')}</strong> {teacher.get('nid_no','') or '—'}</td>
          </tr>
        </table>
      </div>

      <!-- বেতনের হিসাব -->
      <table style="font-size:13px;margin-bottom:14px">
        <tr><th colspan="2" style="text-align:left">{t('print.earnings_header')}</th></tr>
        <tr>
          <td>{t('print.basic_salary_label')}</td>
          <td style="text-align:right;font-weight:600">৳ {float(salary['basic_salary']):,.2f}</td>
        </tr>
        <tr>
          <td>{t('print.bonus_label')}</td>
          <td style="text-align:right;color:#2E7D32">৳ {float(salary['bonus'] or 0):,.2f}</td>
        </tr>
        <tr style="background:#FFF8E1">
          <td style="font-weight:700">{t('print.total_earnings_label')}</td>
          <td style="text-align:right;font-weight:700">
            ৳ {float(salary['basic_salary'])+float(salary['bonus'] or 0):,.2f}
          </td>
        </tr>
        <tr><th colspan="2" style="text-align:left">{t('print.deductions_header')}</th></tr>
        <tr>
          <td>{t('print.total_deduction_label')}</td>
          <td style="text-align:right;color:#C62828">৳ {float(salary['deduction'] or 0):,.2f}</td>
        </tr>
      </table>

      <!-- নেট বেতন বক্স -->
      <div style="background:#0F4C5C;color:white;border-radius:8px;
                  padding:12px 16px;text-align:center;margin-bottom:16px">
        <div style="font-size:11px;opacity:0.8;margin-bottom:4px">{t('print.net_salary_label')}</div>
        <div style="font-size:26px;font-weight:700">৳ {net:,.2f} {t('print.amount_words_suffix')}</div>
        <div style="font-size:11px;opacity:0.8;margin-top:4px">
          ({_amount_in_words(int(net))} {t('print.net_salary_suffix')})
        </div>
      </div>

      <!-- পেমেন্ট তথ্য -->
      <table class="no-border" style="font-size:12px;margin-bottom:16px">
        <tr>
          <td><strong>{t('print.payment_method_label')}</strong>
            {method_label}</td>
          <td style="text-align:right"><strong>{t('print.payment_date_label')}</strong>
            {str(salary.get('payment_date','')) or '—'}</td>
        </tr>
        {"<tr><td colspan='2'><strong>" + t('print.remarks_label') + "</strong> " + (salary.get('remarks','') or '') + "</td></tr>"
         if salary.get('remarks') else ""}
      </table>

      <!-- স্বাক্ষর -->
      <table class="no-border" style="font-size:11px">
        <tr>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">
              {t('print.sig_teacher')}
            </div>
          </td>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">
              {t('print.sig_authority')}
            </div>
          </td>
        </tr>
      </table>

      <div style="text-align:center;font-size:10px;color:#999;
                  margin-top:12px;border-top:1px dashed #ccc;padding-top:8px">
        {t('print.slip_footer', madrasa=tenant['madrasa_name'])}
      </div>
    </div>""")


def _render_salary_slip(tid):
    st.markdown(f"#### {t('print.ss_header')}")

    # Check teacher table exists
    teachers = fetchall(
        "SELECT * FROM teachers WHERE tenant_id=%s AND status='active' ORDER BY name",
        (tid,),
    )
    if not teachers:
        alert(t("print.warn_no_teachers"), "warning")
        return

    tch_map = {tc["name"]: tc for tc in teachers}
    sel_tch = st.selectbox(t("print.select_teacher"), list(tch_map.keys()), key="ss_tch")
    tch = tch_map[sel_tch]

    salaries = fetchall(
        """SELECT * FROM teacher_salary
           WHERE tenant_id=%s AND teacher_id=%s AND status='paid'
           ORDER BY year DESC, id DESC LIMIT 24""",
        (tid, tch["id"]),
    )
    if not salaries:
        alert(t("print.info_no_salary_records"), "info")
        return

    sal_map = {
        f"{s['month_name']} {s['year']} — ৳{float(s['net_salary'] or 0):,.0f}": s
        for s in salaries
    }
    sel_sal = st.selectbox(t("print.select_salary_month"), list(sal_map.keys()), key="ss_sal")
    salary  = sal_map[sel_sal]
    tenant  = _tenant_info(tid)

    _print_button("salary_slip_print")
    st.markdown(
        f'<div id="salary_slip_print" class="print-wrap">'
        f'{_salary_slip_html(tenant, tch, salary)}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# ৪. মার্কশিট প্রিন্ট
# ──────────────────────────────────────────────────────────────────────────────

def _marksheet_print_html(tenant, student, exam_name, session_name,
                           marks_list, total_obt, total_full, pct, grade, gpa, rank):
    grade_color = "#2E7D32" if grade not in ("D","F") else "#C62828"
    status_pass, status_fail, status_abs = t("print.status_pass"), t("print.status_fail"), t("print.status_abs")
    rows = ""
    for m in marks_list:
        obt = int(m.get("total_obtained") or 0)
        pm  = int(m.get("pass_marks") or 33)
        is_abs = m.get("is_absent", False)
        res_color = "#C62828" if (is_abs or obt < pm) else "#2E7D32"
        result    = status_abs if is_abs else (status_pass if obt >= pm else status_fail)
        rows += f"""<tr>
          <td>{m['subject_name']}</td>
          <td style="text-align:center">{m['full_marks']}</td>
          <td style="text-align:center">{m['pass_marks']}</td>
          <td style="text-align:center">{status_abs if is_abs else m.get('written_obtained',0)}</td>
          <td style="text-align:center">{'—' if is_abs else m.get('mcq_obtained',0)}</td>
          <td style="text-align:center">{'—' if is_abs else m.get('practical_obtained',0)}</td>
          <td style="text-align:center;font-weight:700">{status_abs if is_abs else obt}</td>
          <td style="text-align:center;color:{res_color};font-weight:700">{result}</td>
        </tr>"""

    return flatten_html(f"""
    <div style="max-width:680px;margin:0 auto;border:2px solid #0F4C5C;
                border-radius:12px;padding:20px;font-family:Inter,sans-serif;background:white">

      <!-- হেডার -->
      <div style="text-align:center;border-bottom:2px solid #0F4C5C;
                  padding-bottom:12px;margin-bottom:14px">
        {_brand_name_block(tenant, font_size=20)}
        <div style="font-size:11px;color:#666">{tenant.get('address','') or ''}</div>
        <div style="margin-top:8px;font-size:15px;font-weight:700;
                    background:#0F4C5C;color:white;padding:4px 20px;
                    border-radius:20px;display:inline-block">
          {t('print.marksheet_title', exam=exam_name)}
        </div>
        <div style="font-size:12px;color:#555;margin-top:4px">{t('print.session_prefix_label', session=session_name)}</div>
      </div>

      <!-- ছাত্রের তথ্য -->
      <div style="background:#F7F9FA;border:1px solid #DDE3E7;border-radius:8px;
                  padding:10px 14px;margin-bottom:14px;font-size:12px">
        <table class="no-border">
          <tr>
            <td style="width:50%"><strong>{t('print.student_name_label')}:</strong> {student['name']}</td>
            <td><strong>{t('print.father_name_label')}:</strong> {student.get('father_name') or '—'}</td>
          </tr>
          <tr>
            <td><strong>{t('print.class_label')}:</strong> {student.get('class_name','')}</td>
            <td><strong>{t('print.roll_no_label')}:</strong> {student.get('roll_no') or '—'}</td>
          </tr>
          <tr>
            <td><strong>{t('print.session_label')}:</strong> {session_name}</td>
            <td><strong>{t('print.rank_label')}</strong> {rank}</td>
          </tr>
        </table>
      </div>

      <!-- মার্কস টেবিল -->
      <table style="font-size:12px;margin-bottom:14px">
        <tr>
          <th style="text-align:left">{t('print.col_subject')}</th>
          <th>{t('print.col_full_marks')}</th><th>{t('print.col_pass_marks')}</th>
          <th>{t('print.col_written')}</th><th>{t('print.col_mcq')}</th><th>{t('print.col_practical')}</th>
          <th>{t('print.col_total')}</th><th>{t('print.col_result')}</th>
        </tr>
        {rows}
        <tr style="background:#EAF4F8;font-weight:700">
          <td>{t('print.grand_total_label')}</td>
          <td style="text-align:center">{total_full}</td>
          <td colspan="4"></td>
          <td style="text-align:center">{total_obt}</td>
          <td></td>
        </tr>
      </table>

      <!-- ফলাফল সারাংশ -->
      <div style="display:flex;gap:16px;margin-bottom:16px">
        <div style="flex:1;background:#F7F9FA;border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:11px;color:#666">{t('print.total_obtained_label')}</div>
          <div style="font-size:20px;font-weight:700;color:#0F4C5C">{total_obt}/{total_full}</div>
        </div>
        <div style="flex:1;background:#F7F9FA;border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:11px;color:#666">{t('print.percentage_label')}</div>
          <div style="font-size:20px;font-weight:700;color:#0F4C5C">{pct:.1f}%</div>
        </div>
        <div style="flex:1;background:{grade_color};border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:11px;color:rgba(255,255,255,0.8)">{t('print.grade_label')}</div>
          <div style="font-size:20px;font-weight:700;color:white">{grade}</div>
        </div>
        <div style="flex:1;background:#F7F9FA;border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:11px;color:#666">{t('print.gpa_label')}</div>
          <div style="font-size:20px;font-weight:700;color:#0F4C5C">{gpa:.2f}</div>
        </div>
        <div style="flex:1;background:#F7F9FA;border-radius:8px;padding:10px;text-align:center">
          <div style="font-size:11px;color:#666">{t('print.rank_label2')}</div>
          <div style="font-size:20px;font-weight:700;color:#E8A838">#{rank}</div>
        </div>
      </div>

      <!-- স্বাক্ষর -->
      <table class="no-border" style="font-size:11px">
        <tr>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">
              {t('print.sig_class_teacher')}
            </div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">
              {t('print.sig_exam_controller')}
            </div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">
              {t('print.sig_headteacher2')}
            </div>
          </td>
        </tr>
      </table>

      <div style="text-align:center;font-size:10px;color:#999;
                  margin-top:12px;border-top:1px dashed #ccc;padding-top:8px">
        {t('print.ms_footer', date=str(date.today()), madrasa=tenant['madrasa_name'])}
      </div>
    </div>""")


def _render_marksheet(tid):
    st.markdown(f"#### {t('print.ms_header')}")

    sessions = fetchall(
        "SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC", (tid,)
    )
    classes = fetchall(
        "SELECT id, class_name, class_numeric FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,)
    )
    if not sessions or not classes:
        alert(t("print.warn_no_session_class"), "warning")
        return

    sess_map  = {s["session_name"]: s["id"] for s in sessions}
    class_map = {c["class_name"]:   c["id"] for c in classes}

    c1, c2 = st.columns(2)
    sel_sess  = c1.selectbox(t("print.select_session"), list(sess_map.keys()),  key="ms2_sess")
    sel_class = c2.selectbox(t("print.select_class"), list(class_map.keys()), key="ms2_class")

    exams = fetchall(
        "SELECT id, exam_name FROM exams WHERE tenant_id=%s AND session_id=%s ORDER BY exam_date",
        (tid, sess_map[sel_sess]),
    )
    if not exams:
        alert(t("print.info_no_exams"), "info")
        return

    exam_map = {e["exam_name"]: e["id"] for e in exams}
    sel_exam = st.selectbox(t("print.select_exam"), list(exam_map.keys()), key="ms2_exam")

    students = fetchall(
        """SELECT s.id AS student_id, s.name, s.father_name,
                  e.roll_no, e.id AS enrollment_id, c.class_name, sess.session_name
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s AND e.session_id=%s AND e.class_id=%s
             AND e.enrollment_status='active'
           ORDER BY e.roll_no""",
        (tid, sess_map[sel_sess], class_map[sel_class]),
    )
    if not students:
        alert(t("print.info_no_students"), "info")
        return

    stu_map = {f"Roll {s['roll_no'] or '?'} — {s['name']}": s for s in students}
    sel_stu = st.selectbox(t("print.select_student2"), list(stu_map.keys()), key="ms2_stu")
    stu = stu_map[sel_stu]

    if st.button(t("print.btn_generate_marksheet"), type="primary", key="gen_ms2"):
        marks = fetchall(
            """SELECT sm.*, subj.subject_name, subj.full_marks, subj.pass_marks
               FROM student_marks sm
               JOIN subjects subj ON subj.id=sm.subject_id
               WHERE sm.tenant_id=%s AND sm.enrollment_id=%s AND sm.exam_id=%s
               ORDER BY subj.id""",
            (tid, stu["enrollment_id"], exam_map[sel_exam]),
        )
        if not marks:
            alert(t("print.warn_no_marks"), "warning")
            return

        # Calculate rank
        all_results = fetchall(
            """SELECT e.id AS enrollment_id, SUM(sm.total_obtained) AS total
               FROM student_enrollments e
               JOIN student_marks sm ON sm.enrollment_id=e.id
               WHERE e.tenant_id=%s AND e.session_id=%s AND e.class_id=%s AND sm.exam_id=%s
               GROUP BY e.id ORDER BY total DESC""",
            (tid, sess_map[sel_sess], class_map[sel_class], exam_map[sel_exam]),
        )
        rank = next(
            (i+1 for i, r in enumerate(all_results) if r["enrollment_id"] == stu["enrollment_id"]),
            "—"
        )

        total_full = sum(int(m["full_marks"]) for m in marks)
        total_obt  = sum(int(m["total_obtained"] or 0) for m in marks)
        pct        = round(total_obt / total_full * 100, 2) if total_full else 0
        grade, gpa = get_grade(pct)
        tenant     = _tenant_info(tid)

        _print_button("marksheet_print")
        st.markdown(
            f'<div id="marksheet_print" class="print-wrap">'
            f'{_marksheet_print_html(tenant, stu, sel_exam, sel_sess, marks, total_obt, total_full, pct, grade, gpa, rank)}'
            f'</div>',
            unsafe_allow_html=True,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Main Render
# ──────────────────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    _inject_print_css()
    page_header("🖨️", t("print.page_title"), t("print.page_subtitle"))

    st.markdown(
        f"""<div class="alert alert-info" style="margin-bottom:1rem">
        {t('print.instructions_html')}
        </div>""",
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3, tab4 = st.tabs([
        t("print.tab_fee_voucher"),
        t("print.tab_money_receipt"),
        t("print.tab_salary_slip"),
        t("print.tab_marksheet"),
    ])

    with tab1:
        _render_fee_voucher(tid)

    with tab2:
        _render_money_receipt(tid)

    with tab3:
        _render_salary_slip(tid)

    with tab4:
        _render_marksheet(tid)
