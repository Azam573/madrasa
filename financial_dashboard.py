"""
financial_dashboard.py — Consolidated Financial Overview
Fee, Zakat, Donation, Expense — এতদিন সব আলাদা মডিউলে ছিল; এই পেজে সবকটা
ফান্ডের এই মাস/বছরের ইন-আউট এক জায়গায় দেখা যায়। প্রতিটা সংখ্যা প্রতিটা
নিজস্ব মডিউলের already-existing summary function থেকে আসে — নতুন করে
কোনো query লেখা হয়নি, শুধু একত্রিত করা হয়েছে।
"""
import streamlit as st
import pandas as pd

from utils import page_header, hero_kpi_row, divider, get_tenant_id, months_list, current_year
from i18n import t

import finance_module
import zakat_module
import donor_module
import enterprise_modules


def render():
    tid = get_tenant_id()
    page_header("💹", t("findash.page_title"), t("findash.page_subtitle"))

    yr = current_year()
    cur_month = months_list()[__import__("datetime").date.today().month - 1]

    fee    = finance_module._ledger_summary(tid, year=yr)
    zakat  = zakat_module._zakat_summary(tid, yr)
    donor  = donor_module._donor_summary(tid, cur_month, yr)
    expense_rows  = enterprise_modules._expense_summary(tid, yr)
    expense_total = sum(float(r["total"] or 0) for r in expense_rows)

    total_in  = fee["paid"] + zakat["collected"] + donor["collected"]
    total_out = expense_total + zakat["distributed"]
    net       = total_in - total_out

    hero_kpi_row([
        {"icon": "💰", "label": t("findash.kpi_total_in", year=yr),  "value": f"৳{total_in:,.0f}"},
        {"icon": "💸", "label": t("findash.kpi_total_out", year=yr), "value": f"৳{total_out:,.0f}"},
        {"icon": "📊", "label": t("findash.kpi_net", year=yr),       "value": f"৳{net:,.0f}"},
        {"icon": "⚠️", "label": t("findash.kpi_fee_outstanding"),    "value": f"৳{fee['unpaid']:,.0f}"},
    ])

    divider()
    st.markdown(f"#### {t('findash.breakdown_heading', year=yr)}")

    col_in, col_out = t("findash.col_in"), t("findash.col_out")
    col_fund = t("findash.col_fund")
    rows = [
        {col_fund: t("findash.fund_fee"),      col_in: fee["paid"],       col_out: 0},
        {col_fund: t("findash.fund_zakat"),    col_in: zakat["collected"],col_out: zakat["distributed"]},
        {col_fund: t("findash.fund_donation"), col_in: donor["collected"],col_out: 0},
        {col_fund: t("findash.fund_expense"),  col_in: 0,                 col_out: expense_total},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**{t('findash.fee_status_heading')}**")
        st.caption(t("findash.fee_status_caption", billed=fee["billed"], paid=fee["paid"],
                      unpaid=fee["unpaid"], partial=fee["partial"]))
    with c2:
        st.markdown(f"**{t('findash.donor_status_heading')}**")
        st.caption(t("findash.donor_status_caption", active=donor["active_donors"],
                      unpaid_count=donor["unpaid_count"], month=cur_month))

    if expense_rows:
        divider()
        st.markdown(f"#### {t('findash.expense_by_category')}")
        cat_col, amt_col = t("findash.col_category"), t("findash.col_amount")
        df = pd.DataFrame([
            {cat_col: r["category"] or t("findash.uncategorized"), amt_col: float(r["total"] or 0)}
            for r in expense_rows
        ])
        st.bar_chart(df.set_index(cat_col), height=220)
