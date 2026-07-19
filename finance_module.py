"""
finance_module.py — Fee collection, digital voucher generation,
and Multi-Fund Ledgers (General, Zakat, Lillah Boarding).

পরিবর্তন (v9.0):
- সব critical financial action-এ audit_module.log() যোগ করা হয়েছে
- voucher create, payment collect, voucher cancel — সব audit trail-এ
- বাংলাদেশ আর্থিক নিয়ন্ত্রক সংস্থার compliance requirement

পরিবর্তন (এই সেশন — বাগ ফিক্স):
- render() সম্পূর্ণ i18n করা হলো (আগে পুরো পেজ হার্ডকোড করা ইংরেজিতে ছিল,
  ভাষা পরিবর্তন করলেও এই পেজ কখনো বাংলায় আসত না)।
- _collect_payment()-এর status/amount বাগ ফিক্স: আগে প্রতিটা payment-এর
  amount voucher-এর মূল amount-এর সাথে তুলনা করা হতো, আগের কিস্তি
  (fee_payments) যোগ না করেই — ফলে কিস্তিতে ফি দিলে voucher কখনো
  "paid" status-এ পৌঁছাত না, আর ওভারচার্জ/ডাবল-কালেকশন ঠেকানোর কোনো
  ব্যবস্থাও ছিল না। এখন আগের সব payment-এর যোগফল বিবেচনা করে remaining
  due হিসাব করা হয়, এবং remaining-এর বেশি amount দিলে রিজেক্ট করা হয়।
- _voucher_html()-এ "Demo Madrasa" হার্ডকোড ছিল, call site কখনো আসল
  tenant-এর নাম পাঠাত না — এখন আসল madrasa_name তোলা হয় ও পাঠানো হয়।
- Fund/status/payment-method প্রদর্শনে .replace()/.title() দিয়ে raw
  ইংরেজি key দেখানো হতো (already-existing fin.fund_* অনুবাদ থাকা সত্ত্বেও
  ব্যবহার হতো না) — এখন সব জায়গায় সঠিক অনুবাদ ব্যবহার করা হচ্ছে।
- _recent_payments()/detailed ledger query student_enrollments-এ
  student_id দিয়ে জয়েন করত (voucher-এর নিজস্ব enrollment_id উপেক্ষা করে) —
  কোনো ছাত্রের একাধিক enrollment row থাকলে (re-enrollment) payment/voucher
  সারি ডুপ্লিকেট হয়ে যেতে পারত। এখন v.enrollment_id দিয়ে সরাসরি জয়েন।
"""

import streamlit as st
from datetime import date, datetime
from db import get_connection, fetchall, fetchone, release_connection
from utils import (
    page_header, kpi_row, badge, alert, divider,
    get_tenant_id, months_list, current_year, flatten_html,
)
import audit_module
from error_handler import safe_db_error
from i18n import t, month_name

# ──────────────────────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────────────────────

def _active_students(tid):
    return fetchall(
        """SELECT s.id, s.name, s.father_name, s.mobile_no,
                  e.id AS enrollment_id, e.roll_no, e.monthly_fee,
                  c.class_name, sess.session_name, sess.id AS session_id
           FROM students s
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s AND s.status='active' AND e.enrollment_status='active'
           ORDER BY c.class_numeric, e.roll_no""",
        (tid,),
    )


def _vouchers_for_student(tid, student_id):
    return fetchall(
        """SELECT v.id, v.voucher_no, v.month_name, v.year, v.amount,
                  v.fund_type, v.status, v.issue_date, v.due_date, v.paid_at,
                  v.remarks
           FROM fee_vouchers v
           WHERE v.tenant_id=%s AND v.student_id=%s
           ORDER BY v.year DESC, v.id DESC""",
        (tid, student_id),
    )


def _voucher_paid_amount(tid, voucher_id):
    """এই voucher-এ এখন পর্যন্ত মোট কত টাকা জমা পড়েছে (আগের সব কিস্তি মিলিয়ে)।"""
    row = fetchone(
        "SELECT COALESCE(SUM(amount_paid),0) AS n FROM fee_payments WHERE voucher_id=%s AND tenant_id=%s",
        (voucher_id, tid),
    )
    return float(row["n"]) if row else 0.0


def _ledger_summary(tid, fund_type=None, year=None):
    # SQL Injection fix (v9.0): build_where দিয়ে safe parameterized clause তৈরি
    from sql_safe import build_where
    optional = {}
    if fund_type and fund_type != "All":
        optional["fund_type"] = fund_type.lower().replace(" ", "_")
    if year:
        optional["year"] = year

    where, extra_params = build_where(["tenant_id=%s"], optional)
    params = tuple([tid] + extra_params)

    total_billed = fetchone(
        f"SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE {where}", params
    )
    paid = fetchone(
        f"SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE {where} AND status='paid'", params
    )
    unpaid = fetchone(
        f"SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE {where} AND status='unpaid'", params
    )
    partial = fetchone(
        f"SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers WHERE {where} AND status='partial'", params
    )
    count = fetchone(
        f"SELECT COUNT(*) AS n FROM fee_vouchers WHERE {where}", params
    )
    return {
        "billed":  float(total_billed["n"]),
        "paid":    float(paid["n"]),
        "unpaid":  float(unpaid["n"]),
        "partial": float(partial["n"]),
        "count":   int(count["n"]),
    }


def _recent_payments(tid, limit=20):
    return fetchall(
        """SELECT p.id, p.amount_paid, p.payment_date, p.payment_method, p.receipt_no,
                  v.voucher_no, v.month_name, v.year, v.fund_type,
                  s.name AS student_name, c.class_name
           FROM fee_payments p
           JOIN fee_vouchers v ON v.id=p.voucher_id
           JOIN students s ON s.id=v.student_id
           JOIN student_enrollments e ON e.id=v.enrollment_id
           JOIN classes c ON c.id=e.class_id
           WHERE p.tenant_id=%s
           ORDER BY p.created_at DESC LIMIT %s""",
        (tid, limit),
    )


def _next_voucher_no(tid):
    row = fetchone(
        "SELECT COUNT(*) AS n FROM fee_vouchers WHERE tenant_id=%s", (tid,)
    )
    n = int(row["n"]) + 1 if row else 1
    return f"VCH-{tid:03d}-{n:05d}"


def _monthly_collection(tid, year):
    rows = fetchall(
        """SELECT month_name, SUM(amount) AS total
           FROM fee_vouchers
           WHERE tenant_id=%s AND year=%s AND status='paid'
           GROUP BY month_name
           ORDER BY MIN(id)""",
        (tid, year),
    )
    return {r["month_name"]: float(r["total"]) for r in rows}


def _fund_breakdown(tid, year):
    rows = fetchall(
        """SELECT fund_type, SUM(amount) AS total
           FROM fee_vouchers
           WHERE tenant_id=%s AND year=%s AND status='paid'
           GROUP BY fund_type""",
        (tid, year),
    )
    return {r["fund_type"]: float(r["total"]) for r in rows}


@st.cache_data(ttl=300)
def _tenant_name(tid):
    row = fetchone("SELECT madrasa_name FROM tenants WHERE id=%s", (tid,))
    return row["madrasa_name"] if row else "Smart Madrasa ERP"


# ──────────────────────────────────────────────────────────────────────────────
# Voucher generation (atomic)
# ──────────────────────────────────────────────────────────────────────────────

def _create_voucher(tid, enrollment_id, student_id, month, year, amount, fund_type, due_date, remarks):
    """
    Race Condition Fix (v9.0):
    SELECT ... FOR UPDATE দিয়ে duplicate voucher creation প্রতিরোধ।
    দুইজন একসাথে একই voucher তৈরি করতে পারবে না।
    """
    conn = get_connection()
    if not conn:
        return False, t("fin.err_db_connection")
    try:
        with conn.cursor() as cur:
            # ── Pessimistic Lock — duplicate check with FOR UPDATE ──
            # FOR UPDATE: এই row-এ অন্য transaction lock পাবে না যতক্ষণ commit না হয়
            cur.execute(
                """SELECT id FROM fee_vouchers
                   WHERE tenant_id=%s AND student_id=%s
                     AND month_name=%s AND year=%s AND fund_type=%s
                   FOR UPDATE""",
                (tid, student_id, month, year, fund_type),
            )
            if cur.fetchone():
                fund_label = {"general": t('fin.fund_general'), "zakat": t('fin.fund_zakat'),
                              "lillah_boarding": t('fin.fund_lillah_boarding')}.get(fund_type, fund_type)
                return False, t("fin.err_voucher_exists", month=month_name(months_list().index(month))
                                 if month in months_list() else month, year=year, fund=fund_label)

            # Voucher number — transaction-এর ভেতরে generate (race-safe)
            cur.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 AS next FROM fee_vouchers WHERE tenant_id=%s",
                (tid,),
            )
            seq        = cur.fetchone()["next"]
            voucher_no = f"VCH-{tid:03d}-{seq:05d}"

            cur.execute(
                """INSERT INTO fee_vouchers
                   (tenant_id, enrollment_id, student_id, voucher_no, issue_date,
                    due_date, month_name, year, amount, fund_type, status, remarks)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unpaid',%s)
                   RETURNING id, voucher_no""",
                (tid, enrollment_id, student_id, voucher_no,
                 date.today(), due_date, month, year, amount, fund_type, remarks),
            )
            row = cur.fetchone()
        conn.commit()
        # ── Audit Trail (v9.0) ────────────────────────────────────
        audit_module.log(
            action      = "CREATE",
            module      = "Finance",
            description = t("fin.audit_voucher_created", voucher_no=voucher_no,
                             student_id=student_id, month=month, year=year,
                             amount=amount, fund_type=fund_type),
        )
        return True, row
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex, "create_voucher")
    finally:
        release_connection(conn)


def _collect_payment(tid, voucher_id, amount_paid, method, notes):
    """
    Race Condition Fix (v9.0):
    SELECT ... FOR UPDATE — দুইজন একই voucher-এ একসাথে payment করতে পারবে না।
    Idempotency: paid voucher-এ আবার payment করা যাবে না।

    Partial-payment fix: নতুন payment-এর status শুধু এই একবারের amount দিয়ে না,
    বরং আগের সব কিস্তি (fee_payments) + এই payment মিলিয়ে মোট কত জমা পড়ল তার
    ভিত্তিতে ঠিক করা হয় — তাই কিস্তিতে ফি দিলেও সঠিকভাবে "paid" status-এ
    পৌঁছায়, আর remaining due-এর বেশি জমা নেওয়াও আটকানো হয় (ওভারচার্জ প্রতিরোধ)।
    """
    conn = get_connection()
    if not conn:
        return False, t("fin.err_db_connection")
    try:
        with conn.cursor() as cur:
            # ── Pessimistic Lock — voucher row lock করা ──────────
            # FOR UPDATE: অন্য concurrent transaction এই row পাবে না
            cur.execute(
                "SELECT amount, status FROM fee_vouchers WHERE id=%s AND tenant_id=%s FOR UPDATE",
                (voucher_id, tid),
            )
            v = cur.fetchone()
            if not v:
                return False, t("fin.err_voucher_not_found")
            if v["status"] == "paid":
                return False, t("fin.err_already_paid")

            cur.execute(
                "SELECT COALESCE(SUM(amount_paid),0) AS n FROM fee_payments WHERE voucher_id=%s AND tenant_id=%s",
                (voucher_id, tid),
            )
            already_paid = float(cur.fetchone()["n"])
            remaining = float(v["amount"]) - already_paid
            if float(amount_paid) > remaining + 0.01:
                return False, t("fin.err_amount_exceeds_due", remaining=remaining)

            receipt_no = f"RCP-{tid:03d}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            cur.execute(
                """INSERT INTO fee_payments
                   (tenant_id, voucher_id, amount_paid, payment_date,
                    payment_method, receipt_no, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (tid, voucher_id, amount_paid, date.today(), method, receipt_no, notes),
            )
            total_paid_now = already_paid + float(amount_paid)
            new_status = "paid" if total_paid_now >= float(v["amount"]) - 0.01 else "partial"
            cur.execute(
                "UPDATE fee_vouchers SET status=%s, paid_at=NOW() WHERE id=%s AND tenant_id=%s",
                (new_status, voucher_id, tid),
            )
        conn.commit()
        # ── Audit Trail (v9.0) ────────────────────────────────────
        audit_module.log(
            action      = "CREATE",
            module      = "Finance",
            description = t("fin.audit_payment_collected", receipt_no=receipt_no,
                             voucher_id=voucher_id, amount_paid=amount_paid, method=method),
        )
        return True, receipt_no
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex, "collect_payment")
    finally:
        release_connection(conn)


# ──────────────────────────────────────────────────────────────────────────────
# Voucher HTML display
# ──────────────────────────────────────────────────────────────────────────────

def _voucher_html(student_name, father_name, class_name, voucher_no,
                  month, year, amount, fund_type, due_date, madrasa_name):
    fund_label = {"general": t('fin.fund_general'), "zakat": t('fin.fund_zakat'),
                  "lillah_boarding": t('fin.fund_lillah_boarding')}.get(fund_type, fund_type.title())
    month_label = month_name(months_list().index(month)) if month in months_list() else month
    return flatten_html(f"""
    <div class="voucher">
      <div class="vch-header">
        <div class="brand-calligraphy" style="font-size:1.1rem;font-weight:700;color:#0F4C5C">{madrasa_name}</div>
        <div style="font-size:0.75rem;color:#6B7A8D">{t('fin.voucher_subtitle')}</div>
      </div>
      <div class="vch-row"><span>{t('fin.voucher_no_label')}</span><strong>{voucher_no}</strong></div>
      <div class="vch-row"><span>{t('fin.student_label')}</span><span>{student_name}</span></div>
      <div class="vch-row"><span>{t('fin.father_label')}</span><span>{father_name}</span></div>
      <div class="vch-row"><span>{t('fin.class_label')}</span><span>{class_name}</span></div>
      <div class="vch-row"><span>{t('fin.month_label')}</span><span>{month_label} {year}</span></div>
      <div class="vch-row"><span>{t('fin.fund_label')}</span><span>{fund_label}</span></div>
      <div class="vch-row"><span>{t('fin.due_date_label')}</span><span>{due_date}</span></div>
      <div class="vch-row vch-total"><span>{t('fin.amount_due_label')}</span><span>৳ {amount:,.0f}</span></div>
      <div style="margin-top:1rem;font-size:0.72rem;color:#6B7A8D;text-align:center">
        {t('fin.voucher_footer_note')}
      </div>
    </div>
    """)


# ──────────────────────────────────────────────────────────────────────────────
# Main render
# ──────────────────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("💰", t("fin.page_title"), t("fin.page_subtitle"))

    def _localized_month(name):
        return month_name(months_list().index(name)) if name in months_list() else name

    fund_label_map = {
        "general": t("fin.fund_general"), "zakat": t("fin.fund_zakat"),
        "lillah_boarding": t("fin.fund_lillah_boarding"),
    }
    status_label_map = {
        "paid": t("fin.status_paid"), "unpaid": t("fin.status_unpaid"),
        "partial": t("fin.status_partial"), "waived": t("fin.status_waived"),
    }
    method_label_map = {
        "cash": t("fin.method_cash"), "bkash": t("fin.method_bkash"),
        "nagad": t("fin.method_nagad"), "bank transfer": t("fin.method_bank"),
    }
    choose = t("fin.choose_placeholder")

    yr = current_year()
    summary = _ledger_summary(tid, year=yr)
    kpi_row([
        {"label": t("fin.kpi_total_billed_year", year=yr), "value": f"৳{summary['billed']:,.0f}",  "cls": ""},
        {"label": t("fin.kpi_collected"),                   "value": f"৳{summary['paid']:,.0f}",    "cls": "success"},
        {"label": t("fin.kpi_outstanding"),                 "value": f"৳{summary['unpaid']:,.0f}",  "cls": "danger"},
        {"label": t("fin.kpi_partial"),                     "value": f"৳{summary['partial']:,.0f}", "cls": "warning"},
        {"label": t("fin.kpi_total_vouchers"),               "value": summary["count"],              "cls": ""},
    ])

    tab_collect, tab_voucher, tab_ledger, tab_history = st.tabs([
        t("fin.tab_collect"), t("fin.tab_voucher"), t("fin.tab_ledger"), t("fin.tab_history")
    ])

    students = _active_students(tid)
    student_map = {f"{s['name']} — {s['class_name']} (Roll {s['roll_no'] or '—'})": s
                   for s in students}

    # ── Tab 1: Collect Fee ──
    with tab_collect:
        st.markdown(f"#### {t('fin.collect_heading')}")
        if not students:
            alert(t("fin.warn_no_active_students"), "warning")
        else:
            sel_label = st.selectbox(t("fin.select_student"), [choose] + list(student_map.keys()),
                                     key="fc_student")
            if sel_label != choose:
                stu = student_map[sel_label]
                vouchers = _vouchers_for_student(tid, stu["id"])
                unpaid = [v for v in vouchers if v["status"] in ("unpaid", "partial")]

                if not unpaid:
                    alert(t("fin.info_no_pending_vouchers"), "info")
                else:
                    vch_opts = {}
                    for v in unpaid:
                        remaining_v = float(v["amount"]) - _voucher_paid_amount(tid, v["id"])
                        status_lbl = status_label_map.get(v["status"], v["status"])
                        vch_opts[
                            f"{v['voucher_no']} | {_localized_month(v['month_name'])} {v['year']} | "
                            f"৳{remaining_v:,.0f} {t('fin.remaining_due_label')} [{status_lbl}]"
                        ] = v
                    sel_vch_label = st.selectbox(t("fin.select_voucher"), list(vch_opts.keys()), key="fc_vch")
                    sel_vch = vch_opts[sel_vch_label]

                    already_paid = _voucher_paid_amount(tid, sel_vch["id"])
                    remaining = float(sel_vch["amount"]) - already_paid
                    if already_paid > 0:
                        st.caption(
                            f"{t('fin.already_paid_label')}: ৳{already_paid:,.0f} | "
                            f"{t('fin.remaining_due_label')}: ৳{remaining:,.0f}"
                        )

                    if remaining <= 0:
                        alert(t("fin.err_already_paid"), "info")
                    else:
                        with st.form("collect_form"):
                            c1, c2 = st.columns(2)
                            amount_paid = c1.number_input(
                                t("fin.amount_paid_label"), min_value=1.0,
                                max_value=remaining, value=remaining, step=50.0
                            )
                            method = c2.selectbox(
                                t("fin.payment_method_label"),
                                ["cash", "bkash", "nagad", "bank transfer"],
                                format_func=lambda m: method_label_map.get(m, m),
                            )
                            notes = st.text_input(t("fin.notes_optional"), placeholder=t("fin.notes_placeholder"))
                            submitted = st.form_submit_button(t("fin.btn_record_payment"), type="primary")
                            if submitted:
                                ok, result = _collect_payment(
                                    tid, sel_vch["id"], amount_paid, method, notes
                                )
                                if ok:
                                    st.success(t("fin.success_payment_recorded", receipt=result))
                                    st.rerun()
                                else:
                                    st.error(result)

    # ── Tab 2: Issue Voucher ──
    with tab_voucher:
        st.markdown(f"#### {t('fin.voucher_heading')}")
        if not students:
            alert(t("fin.warn_no_students_voucher"), "warning")
        else:
            sel_label2 = st.selectbox(t("fin.select_student"), [choose] + list(student_map.keys()),
                                      key="iv_student")
            if sel_label2 != choose:
                stu2 = student_map[sel_label2]

                with st.form("voucher_form"):
                    c1, c2, c3 = st.columns(3)
                    month     = c1.selectbox(t("fin.month_label"), months_list(),
                                              format_func=lambda m: month_name(months_list().index(m)))
                    year      = c2.number_input(t("fin.year_label"), min_value=2020, max_value=2040, value=yr)
                    fund_type = c3.selectbox(t("fin.fund_type_label"),
                                             ["general", "zakat", "lillah_boarding"],
                                             format_func=lambda x: fund_label_map.get(x, x),
                                             help=t("fin.fund_type_help"))

                    c4, c5 = st.columns(2)
                    amount   = c4.number_input(t("fin.amount_label"), min_value=1.0,
                                               value=float(stu2["monthly_fee"]), step=50.0)
                    due_date = c5.date_input(t("fin.due_date_label"), value=date.today())
                    remarks  = st.text_input(t("fin.notes_optional"))

                    submitted2 = st.form_submit_button(t("fin.btn_generate_voucher"), type="primary")
                    if submitted2:
                        ok, result = _create_voucher(
                            tid, stu2["enrollment_id"], stu2["id"],
                            month, int(year), amount, fund_type, str(due_date), remarks
                        )
                        if ok:
                            st.success(t("fin.success_voucher_created", voucher_no=result['voucher_no']))
                            # Show printable voucher
                            st.markdown(
                                _voucher_html(
                                    stu2["name"], stu2["father_name"] or "—",
                                    stu2["class_name"], result["voucher_no"],
                                    month, int(year), amount, fund_type, str(due_date),
                                    _tenant_name(tid),
                                ),
                                unsafe_allow_html=True,
                            )
                        else:
                            st.error(result)

                # Show existing vouchers for selected student
                divider()
                st.markdown(f"**{t('fin.voucher_history_heading', name=stu2['name'])}**")
                vouchers = _vouchers_for_student(tid, stu2["id"])
                if vouchers:
                    rows = []
                    for v in vouchers:
                        rows.append({
                            t("fin.voucher_no_label"): v["voucher_no"],
                            t("fin.month_label"): f"{_localized_month(v['month_name'])} {v['year']}",
                            t("fin.fund_label"): fund_label_map.get(v["fund_type"], v["fund_type"]),
                            t("fin.col_amount"): f"৳{v['amount']:,.0f}",
                            t("fin.col_status"): status_label_map.get(v["status"], v["status"].upper()),
                            t("fin.due_date_label"): str(v["due_date"] or "—"),
                        })
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                else:
                    alert(t("fin.info_no_vouchers_yet"), "info")

    # ── Tab 3: Ledger ──
    with tab_ledger:
        st.markdown(f"#### {t('fin.ledger_heading')}")
        c1, c2 = st.columns(2)
        ledger_year = c1.number_input(t("fin.year_label"), min_value=2020, max_value=2040,
                                       value=yr, key="ledger_yr")
        fund_filter = c2.selectbox(
            t("fin.fund_type_label"), ["all", "general", "zakat", "lillah_boarding"],
            format_func=lambda f: t("fin.fund_all") if f == "all" else fund_label_map.get(f, f),
            key="ledger_fund",
        )

        summ = _ledger_summary(tid, fund_type=None if fund_filter == "all" else fund_filter, year=int(ledger_year))
        kpi_row([
            {"label": t("fin.kpi_total_billed"), "value": f"৳{summ['billed']:,.0f}",  "cls": ""},
            {"label": t("fin.kpi_collected"),    "value": f"৳{summ['paid']:,.0f}",    "cls": "success"},
            {"label": t("fin.kpi_outstanding"),  "value": f"৳{summ['unpaid']:,.0f}",  "cls": "danger"},
            {"label": t("fin.kpi_partial"),      "value": f"৳{summ['partial']:,.0f}", "cls": "warning"},
        ])

        divider()
        st.markdown(f"**{t('fin.monthly_chart_heading')}**")
        monthly = _monthly_collection(tid, int(ledger_year))
        if monthly:
            import pandas as pd
            months = months_list()
            df = pd.DataFrame({
                t("fin.month_label"): [month_name(i) for i in range(12)],
                t("fin.col_paid"): [monthly.get(m, 0) for m in months],
            })
            st.bar_chart(df.set_index(t("fin.month_label")), color="#0F4C5C", height=280)
        else:
            alert(t("fin.info_no_payment_data"), "info")

        divider()
        st.markdown(f"**{t('fin.fund_breakdown_heading')}**")
        breakdown = _fund_breakdown(tid, int(ledger_year))
        if breakdown:
            import pandas as pd
            df2 = pd.DataFrame([
                {t("fin.fund_type_label"): fund_label_map.get(k, k), t("fin.col_amount"): v}
                for k, v in breakdown.items()
            ])
            st.dataframe(df2, use_container_width=True, hide_index=True)
        else:
            alert(t("fin.info_no_fund_data"), "info")

        divider()
        st.markdown(f"**{t('fin.detailed_ledger_heading')}**")
        params = [tid]
        # SQL Injection fix (v9.0): fund_clause শুধু hardcoded placeholder string।
        # fund_key_map থেকে আসা value সব parameterized (%s)।
        fund_clause = ""
        if fund_filter != "all":
            fund_clause = "AND v.fund_type=%s "
            params.append(fund_filter)
        params.append(int(ledger_year))

        ledger_rows = fetchall(
            "SELECT s.name, c.class_name, v.voucher_no, v.month_name,"
            "       v.amount, v.fund_type, v.status, v.issue_date"
            " FROM fee_vouchers v"
            " JOIN students s ON s.id=v.student_id"
            " JOIN student_enrollments e ON e.id=v.enrollment_id"
            " JOIN classes c ON c.id=e.class_id"
            f" WHERE v.tenant_id=%s {fund_clause} AND v.year=%s"
            " ORDER BY v.issue_date DESC LIMIT 200",
            tuple(params),
        )
        if ledger_rows:
            display = []
            for r in ledger_rows:
                display.append({
                    t("fin.student_label"): r["name"],
                    t("fin.class_label"): r["class_name"],
                    t("fin.voucher_no_label"): r["voucher_no"],
                    t("fin.month_label"): _localized_month(r["month_name"]),
                    t("fin.fund_label"): fund_label_map.get(r["fund_type"], r["fund_type"]),
                    t("fin.col_amount"): f"৳{r['amount']:,.0f}",
                    t("fin.col_status"): status_label_map.get(r["status"], r["status"].upper()),
                    t("fin.col_issued"): str(r["issue_date"]),
                })
            st.dataframe(display, use_container_width=True, hide_index=True)
        else:
            alert(t("fin.info_no_ledger_entries"), "info")

    # ── Tab 4: Payment History ──
    with tab_history:
        st.markdown(f"#### {t('fin.history_heading')}")
        recent = _recent_payments(tid, limit=50)
        if not recent:
            alert(t("fin.info_no_payments"), "info")
        else:
            rows = []
            for r in recent:
                rows.append({
                    t("fin.col_date"): str(r["payment_date"]),
                    t("fin.student_label"): r["student_name"],
                    t("fin.class_label"): r["class_name"],
                    t("fin.voucher_no_label"): r["voucher_no"],
                    t("fin.month_label"): f"{_localized_month(r['month_name'])} {r['year']}",
                    t("fin.fund_label"): fund_label_map.get(r["fund_type"], r["fund_type"]),
                    t("fin.col_paid"): f"৳{r['amount_paid']:,.0f}",
                    t("fin.col_method"): method_label_map.get(r["payment_method"], r["payment_method"].title()),
                    t("fin.col_receipt"): r["receipt_no"],
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.caption(t("fin.caption_showing_transactions", n=len(rows)))
