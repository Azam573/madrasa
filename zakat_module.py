"""
zakat_module.py — যাকাত ফান্ড ট্র্যাকার
আলাদা যাকাত সংগ্রহ, বিতরণ ও বার্ষিক রিপোর্ট।
বাংলাদেশ সরকারের ফরম্যাটে রিপোর্ট জেনারেটর।
"""

import streamlit as st
from datetime import date
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id, months_list, current_year, flatten_html, print_button
import audit_module
from error_handler import safe_db_error
from i18n import t
from sanitize import csv_safe

def _add_collection(tid, donor, mobile, amount, cdate, ztype, notes):
    yr = cdate.year if hasattr(cdate,'year') else current_year()
    receipt_no = f"ZKT-{tid:03d}-{yr}-{fetchone('SELECT COUNT(*)+1 AS n FROM zakat_collections WHERE tenant_id=%s',(tid,))['n']:04d}"
    conn = get_connection()
    if not conn: return False, "DB error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO zakat_collections
                   (tenant_id, donor_name, donor_mobile, amount, collection_date,
                    collection_year, zakat_type, receipt_no, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, donor, mobile, amount, str(cdate), yr, ztype, receipt_no, notes),
            )
            rid = cur.fetchone()["id"]
        conn.commit()
        return True, receipt_no
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)

def _add_distribution(tid, stu_id, recipient, amount, ddate, purpose, approved_by):
    yr = ddate.year if hasattr(ddate,'year') else current_year()
    conn = get_connection()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO zakat_distributions
                   (tenant_id, student_id, recipient_name, amount, distribution_date,
                    distribution_year, purpose, approved_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (tid, stu_id, recipient, amount, str(ddate), yr, purpose, approved_by),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _zakat_summary(tid, year):
    collected   = fetchone("SELECT COALESCE(SUM(amount),0) AS n FROM zakat_collections WHERE tenant_id=%s AND collection_year=%s", (tid, year))
    distributed = fetchone("SELECT COALESCE(SUM(amount),0) AS n FROM zakat_distributions WHERE tenant_id=%s AND distribution_year=%s", (tid, year))
    donors      = fetchone("SELECT COUNT(DISTINCT donor_name) AS n FROM zakat_collections WHERE tenant_id=%s AND collection_year=%s", (tid, year))
    recipients  = fetchone("SELECT COUNT(*) AS n FROM zakat_distributions WHERE tenant_id=%s AND distribution_year=%s", (tid, year))
    return {
        "collected":   float(collected["n"]) if collected else 0,
        "distributed": float(distributed["n"]) if distributed else 0,
        "donors":      int(donors["n"]) if donors else 0,
        "recipients":  int(recipients["n"]) if recipients else 0,
    }

# ── Government Report HTML ──
# NOTE: This report mimics the official Bangladesh Zakat Board format —
# its content is intentionally kept in Bangla regardless of UI language,
# since it follows a government-mandated template.
def _govt_report_html(tenant, year, summary, collections, distributions):
    col_rows = "".join(
        f"""<tr>
          <td style="border:1px solid #ccc;padding:5px 8px">{i+1}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{r['donor_name'] or 'অজ্ঞাত'}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{r['donor_mobile'] or '—'}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:right">৳{float(r['amount']):,.0f}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{str(r['collection_date'])}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{r['receipt_no'] or '—'}</td>
        </tr>"""
        for i, r in enumerate(collections)
    )
    dist_rows = "".join(
        f"""<tr>
          <td style="border:1px solid #ccc;padding:5px 8px">{i+1}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{r['recipient_name'] or '—'}</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:right">৳{float(r['amount']):,.0f}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{str(r['distribution_date'])}</td>
          <td style="border:1px solid #ccc;padding:5px 8px">{r['purpose'] or '—'}</td>
        </tr>"""
        for i, r in enumerate(distributions)
    )
    return flatten_html(f"""
    <div style="font-family:Arial,sans-serif;max-width:750px;margin:0 auto;
                font-size:12px;color:#000;background:white;padding:20px">

      <div style="text-align:center;border-bottom:2px solid #000;padding-bottom:12px;margin-bottom:14px">
        <div style="font-size:16px;font-weight:700">{tenant.get('madrasa_name','')}</div>
        <div style="font-size:11px">{tenant.get('address','')}</div>
        <div style="font-size:14px;font-weight:700;margin-top:8px">
          যাকাত তহবিলের বার্ষিক বিবরণী — {year}
        </div>
        <div style="font-size:11px">বাংলাদেশ যাকাত বোর্ড নির্ধারিত ফরম্যাট অনুযায়ী</div>
      </div>

      <!-- Summary Box -->
      <table style="width:100%;border-collapse:collapse;margin-bottom:14px;border:1px solid #000">
        <tr style="background:#F0F0F0">
          <td style="border:1px solid #000;padding:6px 10px;font-weight:700">মোট সংগ্রহ</td>
          <td style="border:1px solid #000;padding:6px 10px;font-weight:700">
            ৳{summary['collected']:,.2f}
          </td>
          <td style="border:1px solid #000;padding:6px 10px;font-weight:700">মোট বিতরণ</td>
          <td style="border:1px solid #000;padding:6px 10px;font-weight:700">
            ৳{summary['distributed']:,.2f}
          </td>
        </tr>
        <tr>
          <td style="border:1px solid #000;padding:6px 10px">দাতার সংখ্যা</td>
          <td style="border:1px solid #000;padding:6px 10px">{summary['donors']} জন</td>
          <td style="border:1px solid #000;padding:6px 10px">উপকারভোগী</td>
          <td style="border:1px solid #000;padding:6px 10px">{summary['recipients']} জন</td>
        </tr>
        <tr style="background:#F0F0F0">
          <td style="border:1px solid #000;padding:6px 10px;font-weight:700">অবশিষ্ট তহবিল</td>
          <td colspan="3" style="border:1px solid #000;padding:6px 10px;font-weight:700;color:{'green' if summary['collected']>=summary['distributed'] else 'red'}">
            ৳{summary['collected']-summary['distributed']:,.2f}
          </td>
        </tr>
      </table>

      <!-- Collections -->
      <div style="font-weight:700;margin-bottom:6px;font-size:13px">
        (ক) যাকাত সংগ্রহের তালিকা
      </div>
      <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
        <tr style="background:#E0E0E0">
          <th style="border:1px solid #ccc;padding:5px 8px">ক্রঃ</th>
          <th style="border:1px solid #ccc;padding:5px 8px;text-align:left">দাতার নাম</th>
          <th style="border:1px solid #ccc;padding:5px 8px;text-align:left">মোবাইল</th>
          <th style="border:1px solid #ccc;padding:5px 8px">পরিমাণ</th>
          <th style="border:1px solid #ccc;padding:5px 8px">তারিখ</th>
          <th style="border:1px solid #ccc;padding:5px 8px">রিসিট নং</th>
        </tr>
        {col_rows if col_rows else '<tr><td colspan="6" style="text-align:center;border:1px solid #ccc;padding:8px">কোনো তথ্য নেই</td></tr>'}
        <tr style="background:#F0F0F0;font-weight:700">
          <td colspan="3" style="border:1px solid #ccc;padding:5px 8px;text-align:right">মোট</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:right">৳{summary['collected']:,.2f}</td>
          <td colspan="2" style="border:1px solid #ccc"></td>
        </tr>
      </table>

      <!-- Distributions -->
      <div style="font-weight:700;margin-bottom:6px;font-size:13px">
        (খ) যাকাত বিতরণের তালিকা
      </div>
      <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
        <tr style="background:#E0E0E0">
          <th style="border:1px solid #ccc;padding:5px 8px">ক্রঃ</th>
          <th style="border:1px solid #ccc;padding:5px 8px;text-align:left">উপকারভোগীর নাম</th>
          <th style="border:1px solid #ccc;padding:5px 8px">পরিমাণ</th>
          <th style="border:1px solid #ccc;padding:5px 8px">তারিখ</th>
          <th style="border:1px solid #ccc;padding:5px 8px;text-align:left">উদ্দেশ্য</th>
        </tr>
        {dist_rows if dist_rows else '<tr><td colspan="5" style="text-align:center;border:1px solid #ccc;padding:8px">কোনো তথ্য নেই</td></tr>'}
        <tr style="background:#F0F0F0;font-weight:700">
          <td colspan="2" style="border:1px solid #ccc;padding:5px 8px;text-align:right">মোট</td>
          <td style="border:1px solid #ccc;padding:5px 8px;text-align:right">৳{summary['distributed']:,.2f}</td>
          <td colspan="2" style="border:1px solid #ccc"></td>
        </tr>
      </table>

      <!-- Signature -->
      <table style="width:100%;border-collapse:collapse;margin-top:24px">
        <tr>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">
              হিসাব রক্ষকের স্বাক্ষর
            </div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">
              তারিখ: ___________
            </div>
          </td>
          <td style="width:33%;text-align:center">
            <div style="border-top:1px solid #000;margin-top:40px;padding-top:4px">
              পরিচালক/মুহতামিমের সিল
            </div>
          </td>
        </tr>
      </table>
    </div>""")


def render():
    tid = get_tenant_id()
    page_header("🌙", t("zakat.page_title"),
                t("zakat.page_subtitle"))

    yr = current_year()
    summary = _zakat_summary(tid, yr)
    kpi_row([
        {"label": t("zakat.kpi_collection_year", year=yr), "value": f"৳{summary['collected']:,.0f}",   "cls": "success"},
        {"label": t("zakat.kpi_distribution"),      "value": f"৳{summary['distributed']:,.0f}", "cls": "accent"},
        {"label": t("zakat.kpi_remaining_fund"),  "value": f"৳{summary['collected']-summary['distributed']:,.0f}",
         "cls": "success" if summary["collected"] >= summary["distributed"] else "danger"},
        {"label": t("zakat.kpi_donor_count"),   "value": summary["donors"],   "cls": ""},
        {"label": t("zakat.kpi_recipients"),      "value": summary["recipients"],"cls": ""},
    ])

    tab_collect, tab_dist, tab_report = st.tabs([
        t("zakat.tab_collection_entry"), t("zakat.tab_distribution_entry"), t("zakat.tab_govt_report")
    ])

    with tab_collect:
        st.markdown(f"#### {t('zakat.collection_entry_header')}")
        with st.form("zakat_collect_form"):
            c1, c2 = st.columns(2)
            donor    = c1.text_input(t("zakat.donor_name"), placeholder=t("zakat.donor_name_placeholder"))
            mobile   = c2.text_input(t("zakat.mobile"), placeholder="01XXXXXXXXX")
            c3, c4, c5 = st.columns(3)
            amount   = c3.number_input(t("zakat.amount_label"), min_value=1.0, step=100.0)
            cdate    = c4.date_input(t("zakat.date_label"), value=date.today())
            ztype    = c5.selectbox(t("zakat.type_label"), ["zakat","sadaqah","fitrana","other"])
            notes    = st.text_input(t("zakat.notes_label"))
            if st.form_submit_button(t("zakat.save_button"), type="primary"):
                ok, result = _add_collection(tid, donor.strip(), mobile.strip(),
                                              amount, cdate, ztype, notes.strip())
                if ok:
                    audit_module.log("CREATE","Zakat",f"সংগ্রহ: ৳{amount:,.0f} ({donor})")
                    st.success(t("zakat.receipt_success", receipt=result))
                    st.rerun()
                else:
                    st.error(result)

        # List
        divider()
        collections = fetchall(
            "SELECT * FROM zakat_collections WHERE tenant_id=%s AND collection_year=%s ORDER BY collection_date DESC LIMIT 30",
            (tid, yr),
        )
        if collections:
            rows = [{
                t("zakat.col_date"):    str(r["collection_date"]),
                t("zakat.col_donor"):     r["donor_name"] or t("zakat.unknown_donor"),
                t("zakat.col_type"):      r["zakat_type"].title(),
                t("zakat.col_amount"):  f"৳{float(r['amount']):,.0f}",
                t("zakat.col_receipt"):   r["receipt_no"] or "—",
            } for r in collections]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    with tab_dist:
        st.markdown(f"#### {t('zakat.distribution_entry_header')}")
        students = fetchall(
            "SELECT s.id, s.name FROM students s WHERE s.tenant_id=%s AND s.status='active' ORDER BY s.name",
            (tid,),
        )
        stu_opts = {t("zakat.direct_person_option"): None}
        stu_opts.update({s["name"]: s["id"] for s in students})

        with st.form("zakat_dist_form"):
            c1, c2 = st.columns(2)
            sel_stu   = c1.selectbox(t("zakat.student_or_recipient"), list(stu_opts.keys()))
            recipient = c2.text_input(t("zakat.recipient_name"), placeholder=t("zakat.recipient_name_placeholder"))
            c3, c4 = st.columns(2)
            dist_amt  = c3.number_input(t("zakat.amount_label"), min_value=1.0, step=50.0)
            ddate     = c4.date_input(t("zakat.date_label"), value=date.today())
            purpose   = st.text_input(t("zakat.purpose_label"), placeholder=t("zakat.purpose_placeholder"))
            approved  = st.text_input(t("zakat.approved_by_label"), value=st.session_state.get("user_name","Admin"))

            if st.form_submit_button(t("zakat.record_distribution_button"), type="primary"):
                stu_id    = stu_opts.get(sel_stu)
                rec_name  = students[[s["id"] for s in students].index(stu_id)]["name"] if stu_id else recipient.strip()
                ok = _add_distribution(tid, stu_id, rec_name, dist_amt, ddate, purpose.strip(), approved.strip())
                if ok:
                    audit_module.log("CREATE","Zakat",f"বিতরণ: ৳{dist_amt:,.0f} → {rec_name}")
                    st.success(t("zakat.distribution_recorded_success"))
                    st.rerun()
                else:
                    st.error(t("zakat.save_failed"))

        divider()
        dists = fetchall(
            "SELECT * FROM zakat_distributions WHERE tenant_id=%s AND distribution_year=%s ORDER BY distribution_date DESC LIMIT 30",
            (tid, yr),
        )
        if dists:
            rows = [{
                t("zakat.col_date"):     str(r["distribution_date"]),
                t("zakat.col_recipient"): r["recipient_name"] or "—",
                t("zakat.col_amount"):   f"৳{float(r['amount']):,.0f}",
                t("zakat.col_purpose"):  r["purpose"] or "—",
            } for r in dists]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    with tab_report:
        st.markdown(f"#### {t('zakat.annual_report_header')}")
        rep_yr = st.number_input(t("zakat.year_label"), min_value=2020, max_value=2040, value=yr, key="zk_yr")
        summary2 = _zakat_summary(tid, int(rep_yr))

        if st.button(t("zakat.generate_report_button"), type="primary"):
            collections2 = fetchall(
                "SELECT * FROM zakat_collections WHERE tenant_id=%s AND collection_year=%s ORDER BY collection_date",
                (tid, int(rep_yr)),
            )
            dists2 = fetchall(
                "SELECT * FROM zakat_distributions WHERE tenant_id=%s AND distribution_year=%s ORDER BY distribution_date",
                (tid, int(rep_yr)),
            )
            tenant = fetchone("SELECT * FROM tenants WHERE id=%s", (tid,)) or {}

            # raw onclick="..." markdown বাটন ক্লিকে React error #231 ছোঁড়ে —
            # তাই real <iframe> ভিত্তিক print_button() (utils.py) ব্যবহার করা হয়েছে।
            print_button(f"🖨️ {t('zakat.print_button')}")
            st.markdown(
                _govt_report_html(tenant, int(rep_yr), summary2, collections2, dists2),
                unsafe_allow_html=True,
            )

            # CSV
            import io, csv
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([t("zakat.csv_type"), t("zakat.csv_name"), t("zakat.csv_amount"),
                              t("zakat.csv_date"), t("zakat.csv_details")])
            for r in collections2:
                writer.writerow([t("zakat.csv_type_collection"), csv_safe(r["donor_name"] or ""), float(r["amount"]),
                                  str(r["collection_date"]), r["notes"] or ""])
            for r in dists2:
                writer.writerow([t("zakat.csv_type_distribution"), csv_safe(r["recipient_name"] or ""), float(r["amount"]),
                                  str(r["distribution_date"]), r["purpose"] or ""])
            st.download_button(
                t("zakat.csv_download_button"),
                buf.getvalue().encode("utf-8-sig"),
                f"zakat_report_{rep_yr}.csv",
                "text/csv",
            )
