"""
donor_module.py — মাসিক চাঁদা / নিয়মিত দাতা ব্যবস্থাপনা
নিয়মিত মাসিক চাঁদাদাতাদের তথ্য সংরক্ষণ, প্রতি মাসের চাঁদা আদায়,
রসিদ প্রিন্ট, এবং বকেয়া/ইতিহাস রিপোর্ট।
"""

import streamlit as st
from datetime import date
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id, months_list, current_year, flatten_html
from utils import print_button as _shared_print_button
import audit_module
from error_handler import safe_db_error
from i18n import t
import bulk_import
from sanitize import csv_safe

# ─────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────

def _get_donors(tid, status=None):
    if status and status != "all":
        return fetchall(
            "SELECT * FROM monthly_donors WHERE tenant_id=%s AND status=%s ORDER BY name",
            (tid, status),
        )
    return fetchall("SELECT * FROM monthly_donors WHERE tenant_id=%s ORDER BY name", (tid,))


def _get_donor(tid, donor_id):
    return fetchone("SELECT * FROM monthly_donors WHERE tenant_id=%s AND id=%s", (tid, donor_id))


def _create_donor(tid, name, mobile, address, amount, start_dt, notes):
    conn = get_connection()
    if not conn:
        return False, t("donor.err_db_generic")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO monthly_donors
                   (tenant_id, name, mobile_no, address, monthly_amount, start_date, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, name, mobile, address, amount, start_dt, notes),
            )
            did = cur.fetchone()["id"]
        conn.commit()
        return True, did
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _update_donor(tid, donor_id, name, mobile, address, amount, notes):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE monthly_donors
                   SET name=%s, mobile_no=%s, address=%s, monthly_amount=%s, notes=%s
                   WHERE tenant_id=%s AND id=%s""",
                (name, mobile, address, amount, notes, tid, donor_id),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _toggle_donor_status(tid, donor_id, new_status):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE monthly_donors SET status=%s WHERE tenant_id=%s AND id=%s",
                (new_status, tid, donor_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _record_payment(tid, donor_id, month_name, year, amount, pdate, method, collected_by, notes):
    existing = fetchone(
        "SELECT id FROM donor_payments WHERE tenant_id=%s AND donor_id=%s AND month_name=%s AND year=%s",
        (tid, donor_id, month_name, year),
    )
    if existing:
        return False, t("donor.err_duplicate_payment")

    seq = fetchone(
        "SELECT COUNT(*)+1 AS n FROM donor_payments WHERE tenant_id=%s AND year=%s",
        (tid, year),
    )
    receipt_no = f"DON-{tid:03d}-{year}-{seq['n']:04d}"

    conn = get_connection()
    if not conn:
        return False, t("donor.err_db_generic")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO donor_payments
                   (tenant_id, donor_id, month_name, year, amount_paid,
                    payment_date, payment_method, receipt_no, collected_by, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (tid, donor_id, month_name, year, amount, pdate, method, receipt_no, collected_by, notes),
            )
        conn.commit()
        return True, receipt_no
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _get_donor_payments(tid, donor_id=None, limit=100):
    if donor_id:
        return fetchall(
            """SELECT p.*, d.name AS donor_name FROM donor_payments p
               JOIN monthly_donors d ON d.id=p.donor_id
               WHERE p.tenant_id=%s AND p.donor_id=%s
               ORDER BY p.created_at DESC LIMIT %s""",
            (tid, donor_id, limit),
        )
    return fetchall(
        """SELECT p.*, d.name AS donor_name FROM donor_payments p
           JOIN monthly_donors d ON d.id=p.donor_id
           WHERE p.tenant_id=%s
           ORDER BY p.created_at DESC LIMIT %s""",
        (tid, limit),
    )


def _donor_summary(tid, month_name, year):
    active = fetchone("SELECT COUNT(*) AS n FROM monthly_donors WHERE tenant_id=%s AND status='active'", (tid,))
    commitment = fetchone(
        "SELECT COALESCE(SUM(monthly_amount),0) AS n FROM monthly_donors WHERE tenant_id=%s AND status='active'",
        (tid,),
    )
    collected = fetchone(
        "SELECT COALESCE(SUM(amount_paid),0) AS n FROM donor_payments WHERE tenant_id=%s AND month_name=%s AND year=%s",
        (tid, month_name, year),
    )
    unpaid = _unpaid_this_month(tid, month_name, year)
    return {
        "active_donors": int(active["n"]) if active else 0,
        "commitment":    float(commitment["n"]) if commitment else 0,
        "collected":     float(collected["n"]) if collected else 0,
        "unpaid_count":  len(unpaid),
    }


def _unpaid_this_month(tid, month_name, year):
    return fetchall(
        """SELECT d.* FROM monthly_donors d
           WHERE d.tenant_id=%s AND d.status='active'
             AND NOT EXISTS (
                SELECT 1 FROM donor_payments p
                WHERE p.donor_id=d.id AND p.month_name=%s AND p.year=%s
             )
           ORDER BY d.name""",
        (tid, month_name, year),
    )


def _paid_month_set(tid, donor_ids, month_name, year):
    """donor_id set that already has a payment row for this month/year."""
    if not donor_ids:
        return set()
    rows = fetchall(
        """SELECT donor_id FROM donor_payments
           WHERE tenant_id=%s AND month_name=%s AND year=%s AND donor_id = ANY(%s)""",
        (tid, month_name, year, donor_ids),
    )
    return {r["donor_id"] for r in rows}


# ─────────────────────────────────────────────
# Tenant branding (local, self-contained — mirrors the pattern already
# used independently by print_module.py / zakat_module.py rather than
# importing their underscore-prefixed helpers across modules)
# ─────────────────────────────────────────────

def _tenant_info(tid):
    import branding as _br
    conn = get_connection()
    if not conn:
        return {"madrasa_name": "Smart Madrasa", "address": "", "phone": "", **_br._DEFAULTS}
    try:
        with conn.cursor() as cur:
            return _br.get_branding(cur, tid)
    finally:
        release_connection(conn)


def _receipt_html(tenant, donor, payment):
    p = tenant.get("primary_color") or "#0F4C5C"
    logo = ""
    if tenant.get("show_logo") and tenant.get("logo_base64"):
        logo = (f'<img src="data:{tenant.get("logo_mime") or "image/png"};'
                f'base64,{tenant["logo_base64"]}" '
                f'style="height:44px;vertical-align:middle;margin-right:8px;object-fit:contain">')
    else:
        logo = "🕌 "
    return flatten_html(f"""
    <div style="max-width:520px;margin:0 auto;border:3px double {p};
                border-radius:12px;padding:20px;font-family:Inter,sans-serif;background:white">
      <div style="text-align:center;border-bottom:2px solid {p};padding-bottom:12px;margin-bottom:12px">
        <div class="brand-calligraphy" style="font-size:20px;font-weight:700;color:{p}">{logo}{tenant.get('madrasa_name','Smart Madrasa')}</div>
        <div style="font-size:11px;color:#666;margin-top:3px">
          {tenant.get('address','') or ''} | ☎ {tenant.get('phone','') or ''}
        </div>
        <div style="margin-top:10px;font-size:16px;font-weight:700;
                    background:{p};color:white;padding:5px 20px;
                    border-radius:20px;display:inline-block;letter-spacing:1px">
          মাসিক চাঁদার রসিদ
        </div>
      </div>

      <table class="no-border" style="width:100%;font-size:12px;margin-bottom:10px">
        <tr>
          <td style="width:50%"><strong>রসিদ নং:</strong> {payment['receipt_no']}</td>
          <td style="text-align:right"><strong>তারিখ:</strong> {str(payment['payment_date'])}</td>
        </tr>
      </table>

      <table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:14px;border:1px solid #DDD">
        <tr style="background:#F7F9FA">
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD;width:40%">দাতার নাম</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{donor['name']}</td>
        </tr>
        <tr>
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">মোবাইল</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{donor.get('mobile_no') or '—'}</td>
        </tr>
        <tr style="background:#F7F9FA">
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">যে মাসের চাঁদা</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{payment['month_name']} {payment['year']}</td>
        </tr>
        <tr>
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">পেমেন্ট পদ্ধতি</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{payment['payment_method'].title()}</td>
        </tr>
        <tr style="background:#F7F9FA">
          <td style="padding:7px 10px;font-weight:600;border:1px solid #DDD">আদায়কারী</td>
          <td style="padding:7px 10px;border:1px solid #DDD">{payment.get('collected_by') or '—'}</td>
        </tr>
      </table>

      <div style="text-align:center;background:{p};color:white;border-radius:10px;
                  padding:14px;margin-bottom:14px">
        <div style="font-size:11px;opacity:0.85">পরিশোধিত পরিমাণ</div>
        <div style="font-size:26px;font-weight:700">৳{float(payment['amount_paid']):,.0f}</div>
      </div>

      <table class="no-border" style="width:100%;font-size:11px;margin-top:30px">
        <tr>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">আদায়কারীর স্বাক্ষর</div>
          </td>
          <td style="width:50%;text-align:center">
            <div style="border-top:1px solid #555;margin-top:35px;padding-top:4px">দাতার স্বাক্ষর</div>
          </td>
        </tr>
      </table>

      <div style="text-align:center;font-size:10px;color:#999;
                  margin-top:12px;border-top:1px dashed #ccc;padding-top:8px">
        আপনার নিয়মিত সহযোগিতার জন্য ধন্যবাদ। এই রসিদটি সংগ্রহে রাখুন — {tenant.get('madrasa_name','')}
      </div>
    </div>""")


def _print_button(doc_id: str):
    """
    Delegates to utils.print_button() (real iframe via st.components.v1.html)
    — a raw onclick="..." rendered via st.markdown crashes on click with a
    React error ("Expected onClick listener to be a function, instead got a
    value of `string` type"), which a plain st.markdown fix can't avoid.
    """
    _shared_print_button(f"🖨️ {t('donor.print_receipt_btn')}", doc_id=doc_id)


# ─────────────────────────────────────────────
# Main render
# ─────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("❤️", t("donor.page_title"), t("donor.page_subtitle"))

    today = date.today()
    cur_month = months_list()[today.month - 1]
    cur_year = current_year()
    summary = _donor_summary(tid, cur_month, cur_year)

    kpi_row([
        {"label": t("donor.kpi_active_donors"), "value": summary["active_donors"], "cls": ""},
        {"label": t("donor.kpi_monthly_commitment"), "value": f"৳{summary['commitment']:,.0f}", "cls": "accent"},
        {"label": t("donor.kpi_collected_month", month=cur_month), "value": f"৳{summary['collected']:,.0f}", "cls": "success"},
        {"label": t("donor.kpi_outstanding_month", month=cur_month), "value": summary["unpaid_count"],
         "cls": "danger" if summary["unpaid_count"] > 0 else "success"},
    ])

    tab_list, tab_add, tab_collect, tab_history, tab_bulk = st.tabs([
        t("donor.tab_list"), t("donor.tab_add"), t("donor.tab_collect"), t("donor.tab_history"),
        t("bulk.tab_import"),
    ])

    # ── Donor List ──
    with tab_list:
        st.markdown(f"#### {t('donor.list_heading')}")
        c1, c2 = st.columns([1, 2])
        status_opts = {
            t("donor.status_all"): "all",
            t("donor.status_active"): "active",
            t("donor.status_inactive"): "inactive",
        }
        sel_status_label = c1.selectbox(t("donor.filter_status"), list(status_opts.keys()))
        search_q = c2.text_input(t("donor.search_label"), key="donor_search")

        donors = _get_donors(tid, status_opts[sel_status_label])
        if search_q.strip():
            q = search_q.strip().lower()
            donors = [d for d in donors if q in (d["name"] or "").lower() or q in (d.get("mobile_no") or "")]

        if not donors:
            alert(t("donor.no_donors"), "info")
        else:
            paid_set = _paid_month_set(tid, [d["id"] for d in donors], cur_month, cur_year)
            for d in donors:
                paid = d["id"] in paid_set
                c1, c2, c3, c4, c5 = st.columns([2.2, 1.5, 1.3, 1.3, 1.7])
                c1.markdown(f"**{d['name']}**  \n`{d.get('mobile_no') or '—'}`")
                c2.markdown(f"৳{float(d['monthly_amount']):,.0f}")
                c3.markdown(t("donor.paid_yes") if paid else t("donor.paid_no"))
                c4.markdown(t("donor.status_active") if d["status"] == "active" else t("donor.status_inactive"))
                toggle_label = t("donor.btn_deactivate") if d["status"] == "active" else t("donor.btn_activate")
                if c5.button(toggle_label, key=f"donor_tog_{d['id']}", use_container_width=True):
                    new_status = "inactive" if d["status"] == "active" else "active"
                    _toggle_donor_status(tid, d["id"], new_status)
                    audit_module.log("UPDATE", "Donors", f"{d['name']} → {new_status}")
                    st.rerun()

                with st.expander(t("donor.edit_expander", name=d["name"])):
                    with st.form(f"donor_edit_{d['id']}"):
                        e1, e2 = st.columns(2)
                        e_name = e1.text_input(t("donor.label_name"), value=d["name"])
                        e_mobile = e2.text_input(t("donor.label_mobile"), value=d.get("mobile_no") or "")
                        e3, e4 = st.columns(2)
                        e_amount = e3.number_input(t("donor.label_monthly_amount"), min_value=0.0,
                                                    value=float(d["monthly_amount"]), step=50.0)
                        e_address = e4.text_input(t("donor.label_address"), value=d.get("address") or "")
                        e_notes = st.text_input(t("donor.label_notes"), value=d.get("notes") or "")
                        if st.form_submit_button(t("donor.btn_save_changes"), type="primary"):
                            if _update_donor(tid, d["id"], e_name.strip(), e_mobile.strip(),
                                              e_address.strip(), e_amount, e_notes.strip()):
                                audit_module.log("UPDATE", "Donors", f"তথ্য হালনাগাদ: {e_name}")
                                st.success(t("donor.updated_success"))
                                st.rerun()
                divider()

    # ── Add Donor ──
    with tab_add:
        st.markdown(f"#### {t('donor.add_heading')}")
        with st.form("donor_add_form"):
            c1, c2 = st.columns(2)
            name = c1.text_input(t("donor.label_name"), placeholder=t("donor.placeholder_name"))
            mobile = c2.text_input(t("donor.label_mobile"), placeholder="01XXXXXXXXX")
            c3, c4 = st.columns(2)
            amount = c3.number_input(t("donor.label_monthly_amount"), min_value=0.0, step=50.0)
            start_dt = c4.date_input(t("donor.label_start_date"), value=date.today())
            address = st.text_input(t("donor.label_address"))
            notes = st.text_input(t("donor.label_notes"))
            confirm_dup = st.checkbox(t("donor.confirm_duplicate_checkbox"))
            if st.form_submit_button(t("donor.btn_add_donor"), type="primary"):
                if not name.strip():
                    st.error(t("donor.err_name_required"))
                elif amount <= 0:
                    st.error(t("donor.err_amount_required"))
                else:
                    dups = bulk_import.find_duplicate_donor_mobiles(tid, mobile.strip()) if mobile.strip() else []
                    if dups and not confirm_dup:
                        names = ", ".join(f"{d['name']} ({d['status']})" for d in dups)
                        st.warning(t("donor.duplicate_mobile_warning", names=names))
                    else:
                        ok, result = _create_donor(tid, name.strip(), mobile.strip(), address.strip(),
                                                    amount, start_dt, notes.strip())
                        if ok:
                            audit_module.log("CREATE", "Donors", t("donor.audit_new_donor", name=name, amount=f"{amount:,.0f}"))
                            st.success(t("donor.success_added", name=name))
                            st.rerun()
                        else:
                            st.error(result)

    # ── Collect Payment ──
    with tab_collect:
        st.markdown(f"#### {t('donor.collect_heading')}")
        active_donors = _get_donors(tid, "active")
        if not active_donors:
            alert(t("donor.no_active_donors"), "info")
        else:
            donor_map = {f"{d['name']} ({d.get('mobile_no') or '—'})": d for d in active_donors}
            sel_label = st.selectbox(t("donor.select_donor"), list(donor_map.keys()), key="collect_donor_sel")
            sel_donor = donor_map[sel_label]

            with st.form("donor_collect_form"):
                c1, c2 = st.columns(2)
                months = months_list()
                sel_month = c1.selectbox(t("donor.label_month"), months, index=today.month - 1)
                sel_year = c2.number_input(t("donor.label_year"), min_value=2020, max_value=2040, value=cur_year)
                c3, c4 = st.columns(2)
                pay_amount = c3.number_input(t("donor.label_amount"), min_value=0.0,
                                              value=float(sel_donor["monthly_amount"]), step=50.0)
                pay_method = c4.selectbox(t("donor.label_payment_method"),
                                           ["cash", "bkash", "bank", "other"],
                                           format_func=lambda m: {
                                               "cash": t("donor.method_cash"), "bkash": t("donor.method_bkash"),
                                               "bank": t("donor.method_bank"), "other": t("donor.method_other"),
                                           }[m],
                                           key="donor_pay_method")
                collected_by = st.text_input(t("donor.label_collected_by"),
                                              value=st.session_state.get("user_name", "Admin"))
                pay_notes = st.text_input(t("donor.label_notes"), key="collect_notes")

                if st.form_submit_button(t("donor.btn_collect"), type="primary"):
                    ok, result = _record_payment(tid, sel_donor["id"], sel_month, int(sel_year),
                                                  pay_amount, date.today(), pay_method,
                                                  collected_by.strip(), pay_notes.strip())
                    if ok:
                        audit_module.log("CREATE", "Donors",
                                          t("donor.audit_payment_collected", name=sel_donor["name"],
                                            month=sel_month, year=sel_year, amount=f"{pay_amount:,.0f}"))
                        st.success(t("donor.success_collected", receipt=result))
                        st.session_state["_last_receipt_id"] = result
                        st.rerun()
                    else:
                        st.error(result)

            last_receipt = st.session_state.get("_last_receipt_id")
            if last_receipt:
                payment = fetchone(
                    "SELECT * FROM donor_payments WHERE tenant_id=%s AND receipt_no=%s",
                    (tid, last_receipt),
                )
                if payment:
                    donor_rec = _get_donor(tid, payment["donor_id"])
                    tenant = _tenant_info(tid)
                    _print_button("donor_receipt_print")
                    st.markdown(f'<div id="donor_receipt_print">{_receipt_html(tenant, donor_rec, payment)}</div>',
                                unsafe_allow_html=True)

    # ── History & Reports ──
    with tab_history:
        st.markdown(f"#### {t('donor.unpaid_heading', month=cur_month, year=cur_year)}")
        unpaid = _unpaid_this_month(tid, cur_month, cur_year)
        if not unpaid:
            alert(t("donor.all_paid_msg"), "success")
        else:
            rows = [{
                t("donor.col_name"): d["name"],
                t("donor.col_mobile"): d.get("mobile_no") or "—",
                t("donor.col_monthly_amount"): f"৳{float(d['monthly_amount']):,.0f}",
            } for d in unpaid]
            st.dataframe(rows, use_container_width=True, hide_index=True)

        divider()
        st.markdown(f"#### {t('donor.history_heading')}")
        payments = _get_donor_payments(tid, limit=100)
        if not payments:
            alert(t("donor.no_payments"), "info")
        else:
            rows = [{
                t("donor.col_date"): str(p["payment_date"]),
                t("donor.col_donor"): p["donor_name"],
                t("donor.col_month_year"): f"{p['month_name']} {p['year']}",
                t("donor.col_amount"): f"৳{float(p['amount_paid']):,.0f}",
                t("donor.col_receipt_no"): p["receipt_no"] or "—",
                t("donor.col_method"): p["payment_method"].title(),
            } for p in payments]
            st.dataframe(rows, use_container_width=True, hide_index=True)

            reprint_map = {f"{p['donor_name']} — {p['month_name']} {p['year']} ({p['receipt_no']})": p for p in payments}
            sel_reprint_label = st.selectbox(t("donor.reprint_select_label"), list(reprint_map.keys()))
            if st.button(t("donor.btn_reprint")):
                payment = reprint_map[sel_reprint_label]
                donor_rec = _get_donor(tid, payment["donor_id"])
                tenant = _tenant_info(tid)
                _print_button("donor_reprint")
                st.markdown(f'<div id="donor_reprint">{_receipt_html(tenant, donor_rec, payment)}</div>',
                            unsafe_allow_html=True)

            import io, csv
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([t("donor.col_date"), t("donor.col_donor"), t("donor.col_month_year"),
                              t("donor.col_amount"), t("donor.col_receipt_no"), t("donor.col_method")])
            for p in payments:
                writer.writerow([str(p["payment_date"]), csv_safe(p["donor_name"]), f"{p['month_name']} {p['year']}",
                                  float(p["amount_paid"]), p["receipt_no"] or "", p["payment_method"]])
            st.download_button(
                t("donor.csv_download_btn"),
                buf.getvalue().encode("utf-8-sig"),
                f"donor_payments_{cur_year}.csv",
                "text/csv",
            )

    with tab_bulk:
        st.markdown(f"#### {t('bulk.heading')}")
        st.caption(t("bulk.intro_donors"))
        st.download_button(
            t("bulk.download_template"),
            bulk_import.donors_template_csv(),
            "donors_template.csv",
            "text/csv",
        )

        uploaded = st.file_uploader(t("bulk.upload_label"), type=["csv", "xlsx"], key="donor_bulk_upload")
        if uploaded:
            df, err = bulk_import.parse_upload(uploaded)
            if err:
                st.error(err)
            else:
                validated = bulk_import.validate_donors(tid, df)
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
                elif st.button(t("bulk.btn_commit", count=len(valid_rows)), type="primary", key="donor_bulk_commit"):
                    created, failures = bulk_import.commit_donors(tid, valid_rows)
                    if created:
                        audit_module.log("IMPORT", "Donors", f"বাল্ক ইম্পোর্ট — {created} donors")
                        st.success(t("bulk.import_success", count=created))
                    if failures:
                        details = "; ".join(f"row {rn}: {msg}" for rn, msg in failures)
                        st.warning(t("bulk.import_partial_fail", count=len(failures), details=details))
                    if created:
                        st.rerun()
