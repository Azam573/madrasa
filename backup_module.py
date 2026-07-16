"""
backup_module.py — Backup, Restore & System Health
ডেটা এক্সপোর্ট/ব্যাকআপ, সিস্টেম হেলথ, DB পরিসংখ্যান।
"""
import streamlit as st
import json, io, csv
from datetime import date, datetime
from db import fetchall, fetchone, get_connection, release_connection
from utils import page_header, kpi_row, alert, divider, get_tenant_id
from i18n import t
from error_handler import safe_db_error
import audit_module
from sanitize import csv_safe

# Parent-first order — used for INSERT during restore; reversed for DELETE
# so foreign keys are always satisfied (a child row is never inserted before
# its parent, and never left dangling when its parent is deleted).
RESTORE_TABLE_ORDER = [
    "classes", "academic_sessions", "subjects", "students", "teachers",
    "student_enrollments", "exams", "fee_vouchers", "fee_payments",
    "student_marks", "attendance", "teacher_salary", "notices",
]

def _table_counts(tid):
    tables = [
        ("students",t("backup.tbl_students")), ("student_enrollments",t("backup.tbl_enrollments")),
        ("fee_vouchers",t("backup.tbl_fee_vouchers")), ("fee_payments",t("backup.tbl_fee_payments")),
        ("attendance",t("backup.tbl_attendance")), ("student_marks",t("backup.tbl_marks")),
        ("teachers",t("backup.tbl_teachers")), ("teacher_salary",t("backup.tbl_salary")),
        ("exams",t("backup.tbl_exams")), ("audit_logs",t("backup.tbl_audit_logs")),
    ]
    results = []
    # SQL Injection fix (v9.0): hardcoded parameterized queries — কোনো dynamic table নাম নেই
    HEALTH_SQL = {
        "students":            "SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s",
        "student_enrollments": "SELECT COUNT(*) AS n FROM student_enrollments WHERE tenant_id=%s",
        "fee_vouchers":        "SELECT COUNT(*) AS n FROM fee_vouchers WHERE tenant_id=%s",
        "fee_payments":        "SELECT COUNT(*) AS n FROM fee_payments WHERE tenant_id=%s",
        "attendance":          "SELECT COUNT(*) AS n FROM attendance WHERE tenant_id=%s",
        "student_marks":       "SELECT COUNT(*) AS n FROM student_marks WHERE tenant_id=%s",
        "teachers":            "SELECT COUNT(*) AS n FROM teachers WHERE tenant_id=%s",
        "teacher_salary":      "SELECT COUNT(*) AS n FROM teacher_salary WHERE tenant_id=%s",
        "exams":               "SELECT COUNT(*) AS n FROM exams WHERE tenant_id=%s",
        "audit_logs":          "SELECT COUNT(*) AS n FROM audit_logs WHERE tenant_id=%s",
    }
    for table, label in tables:
        sql = HEALTH_SQL.get(table)
        if not sql:
            continue
        try:
            row = fetchone(sql, (tid,))
            results.append({"table": table, "label": label, "count": int(row["n"]) if row else 0})
        except Exception:
            pass
    return results

def _export_students_json(tid):
    rows = fetchall(
        """SELECT s.*, e.roll_no, e.monthly_fee, e.enrollment_status,
                  c.class_name, sess.session_name
           FROM students s
           LEFT JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           LEFT JOIN classes c ON c.id=e.class_id
           LEFT JOIN academic_sessions sess ON sess.id=e.session_id
           WHERE s.tenant_id=%s ORDER BY s.id""",
        (tid,),
    )
    return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str)

def _export_finance_csv(tid, year):
    rows = fetchall(
        """SELECT v.voucher_no, s.name AS student, c.class_name,
                  v.month_name, v.year, v.amount, v.fund_type,
                  v.status, v.issue_date, v.paid_at
           FROM fee_vouchers v
           JOIN students s ON s.id=v.student_id
           JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
           JOIN classes c ON c.id=e.class_id
           WHERE v.tenant_id=%s AND v.year=%s ORDER BY v.issue_date""",
        (tid, year),
    )
    if not rows: return b""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows([{**dict(r), "student": csv_safe(r["student"])} for r in rows])
    return buf.getvalue().encode("utf-8-sig")

def _export_marks_csv(tid):
    rows = fetchall(
        """SELECT s.name, e.roll_no, c.class_name, ex.exam_name,
                  subj.subject_name, sm.written_obtained, sm.mcq_obtained,
                  sm.practical_obtained, sm.total_obtained, sm.is_absent
           FROM student_marks sm
           JOIN student_enrollments e ON e.id=sm.enrollment_id
           JOIN students s ON s.id=e.student_id
           JOIN classes c ON c.id=e.class_id
           JOIN exams ex ON ex.id=sm.exam_id
           JOIN subjects subj ON subj.id=sm.subject_id
           WHERE sm.tenant_id=%s ORDER BY c.class_numeric, e.roll_no""",
        (tid,),
    )
    if not rows: return b""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows([{**dict(r), "name": csv_safe(r["name"])} for r in rows])
    return buf.getvalue().encode("utf-8-sig")

def _export_attendance_csv(tid):
    rows = fetchall(
        """SELECT s.name, e.roll_no, c.class_name,
                  a.date, a.status
           FROM attendance a
           JOIN student_enrollments e ON e.id=a.enrollment_id
           JOIN students s ON s.id=e.student_id
           JOIN classes c ON c.id=e.class_id
           WHERE a.tenant_id=%s ORDER BY a.date DESC, c.class_numeric LIMIT 10000""",
        (tid,),
    )
    if not rows: return b""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows([{**dict(r), "name": csv_safe(r["name"])} for r in rows])
    return buf.getvalue().encode("utf-8-sig")

def _fetchall_strict(sql, params=()):
    """db.fetchall()-এর মতো, কিন্তু ব্যর্থতা swallow করে না। শুধু
    _full_backup_json()-এর per-table loop ব্যবহার করে — db.fetchall()
    নিজে অপরিবর্তিত (app-wide অন্য callers-এর জন্য raise চালু করা হচ্ছে না)।"""
    conn = get_connection()
    if not conn:
        raise RuntimeError("DB connection unavailable")
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        release_connection(conn)


def _full_backup_json(tid):
    """সব টেবিলের ডেটা JSON-এ। রিটার্ন করে (json_str, failed_tables)।"""
    tables = [
        "students","student_enrollments","fee_vouchers","fee_payments",
        "attendance","student_marks","exams","subjects","classes",
        "academic_sessions","teachers","teacher_salary","notices",
    ]
    backup = {
        "backup_date": str(datetime.now()),
        "tenant_id":   tid,
        "version":     "6.0",
        "data":        {},
    }
    # SQL Injection fix (v9.0): hardcoded parameterized queries
    BACKUP_SQL = {
        "students":            "SELECT * FROM students WHERE tenant_id=%s LIMIT 50000",
        "student_enrollments": "SELECT * FROM student_enrollments WHERE tenant_id=%s LIMIT 50000",
        "fee_vouchers":        "SELECT * FROM fee_vouchers WHERE tenant_id=%s LIMIT 50000",
        "fee_payments":        "SELECT * FROM fee_payments WHERE tenant_id=%s LIMIT 50000",
        "attendance":          "SELECT * FROM attendance WHERE tenant_id=%s LIMIT 50000",
        "student_marks":       "SELECT * FROM student_marks WHERE tenant_id=%s LIMIT 50000",
        "exams":               "SELECT * FROM exams WHERE tenant_id=%s LIMIT 50000",
        "subjects":            "SELECT * FROM subjects WHERE tenant_id=%s LIMIT 50000",
        "classes":             "SELECT * FROM classes WHERE tenant_id=%s LIMIT 50000",
        "academic_sessions":   "SELECT * FROM academic_sessions WHERE tenant_id=%s LIMIT 50000",
        "teachers":            "SELECT * FROM teachers WHERE tenant_id=%s LIMIT 50000",
        "teacher_salary":      "SELECT * FROM teacher_salary WHERE tenant_id=%s LIMIT 50000",
        "notices":             "SELECT * FROM notices WHERE tenant_id=%s LIMIT 50000",
    }
    failed_tables = []
    for table in tables:
        sql = BACKUP_SQL.get(table)
        if not sql:
            backup["data"][table] = []
            continue
        try:
            rows = _fetchall_strict(sql, (tid,))
            backup["data"][table] = [dict(r) for r in rows]
        except Exception as ex:
            backup["data"][table] = []
            failed_tables.append(table)
            safe_db_error(ex, f"_full_backup_json tenant={tid} table={table}")
    return json.dumps(backup, ensure_ascii=False, indent=2, default=str), failed_tables


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def _get_table_columns(table_name: str) -> set:
    """
    Real column names for a table, read live from information_schema —
    not a hardcoded list, so it can't drift from the actual schema.
    table_name always comes from our own RESTORE_TABLE_ORDER (never user
    input), so this query itself is safe; its RESULT is what we use to
    whitelist the untrusted column names coming from an uploaded backup file
    before they ever reach a SQL string.
    """
    rows = fetchall(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table_name,),
    )
    return {r["column_name"] for r in rows}


def _get_insertable_columns(table_name: str) -> set:
    """
    Columns that can be explicitly written via INSERT — excludes GENERATED
    ALWAYS columns (e.g. student_marks.total_obtained, teacher_salary.net_salary),
    which Postgres computes itself and rejects explicit values for.
    """
    rows = fetchall(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name=%s AND is_generated <> 'ALWAYS'",
        (table_name,),
    )
    return {r["column_name"] for r in rows}


def validate_backup(backup_data: dict) -> tuple[bool, str]:
    """Structural + column-whitelist check. Returns (ok, message)."""
    if not isinstance(backup_data, dict) or "data" not in backup_data:
        return False, t("backup.err_invalid_structure")
    data = backup_data["data"]
    if not isinstance(data, dict):
        return False, t("backup.err_invalid_structure")

    for table, rows in data.items():
        if table not in RESTORE_TABLE_ORDER:
            return False, t("backup.err_unknown_table", table=table)
        if not rows:
            continue
        allowed = _get_table_columns(table)
        for row in rows:
            if not isinstance(row, dict):
                return False, t("backup.err_invalid_structure")
            bad_cols = set(row.keys()) - allowed
            if bad_cols:
                return False, t("backup.err_unknown_columns", table=table, cols=", ".join(sorted(bad_cols)))
    return True, ""


def _restore_from_backup(tid: int, backup_data: dict):
    """
    Replaces ALL current data in the tables present in backup_data['data']
    for tenant `tid` with the backed-up rows — in one transaction, so a
    failure partway through leaves the database exactly as it was.
    Caller MUST run validate_backup() first; this function trusts its input.
    """
    data = backup_data.get("data", {})
    conn = get_connection()
    if not conn:
        return False, t("backup.err_db_generic"), {}
    restored_counts = {}
    try:
        with conn.cursor() as cur:
            # Children first, so no FK is ever left dangling mid-restore.
            for table in reversed(RESTORE_TABLE_ORDER):
                if table in data:
                    cur.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (tid,))

            # Parents first, so every FK a child row points to already exists.
            for table in RESTORE_TABLE_ORDER:
                rows = data.get(table)
                if not rows:
                    restored_counts[table] = 0
                    continue
                allowed = _get_insertable_columns(table)
                cols = [c for c in rows[0].keys() if c in allowed]
                col_list = ", ".join(f'"{c}"' for c in cols)
                placeholders = ", ".join(["%s"] * len(cols))
                insert_sql = f'INSERT INTO {table} ({col_list}) VALUES ({placeholders})'
                for row in rows:
                    row = dict(row)
                    if "tenant_id" in row:
                        row["tenant_id"] = tid  # always restore into the current tenant
                    cur.execute(insert_sql, [row.get(c) for c in cols])
                restored_counts[table] = len(rows)

                if "id" in cols:
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table}), 1))",
                        (table,),
                    )
        conn.commit()
        return True, "", restored_counts
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex), {}
    finally:
        release_connection(conn)


def render():
    tid = get_tenant_id()
    page_header("💾", t("backup.page_title"), t("backup.page_subtitle"))

    # System health KPIs
    counts = _table_counts(tid)
    total_records = sum(c["count"] for c in counts)

    kpi_row([
        {"label": t("backup.kpi_total_records"),   "value": f"{total_records:,}", "cls": ""},
        {"label": t("backup.kpi_backup_date"),"value": str(date.today()),    "cls": "accent"},
    ])

    tab_health, tab_backup, tab_restore, tab_export, tab_cleanup = st.tabs([
        t("backup.tab_health"), t("backup.tab_full_backup"), t("backup.tab_restore"),
        t("backup.tab_export"), t("backup.tab_cleanup"),
    ])

    with tab_health:
        st.markdown(f"#### {t('backup.db_stats_heading')}")
        import pandas as pd
        col_table, col_count = t("backup.col_table"), t("backup.col_record_count")
        df = pd.DataFrame([{col_table: c["label"], col_count: c["count"]} for c in counts])
        st.dataframe(df, use_container_width=True, hide_index=True)
        if not df.empty:
            st.bar_chart(df.set_index(col_table), height=240)

        divider()
        st.markdown(f"#### {t('backup.sys_info_heading')}")
        tenant = fetchone("SELECT * FROM tenants WHERE id=%s", (tid,))
        if tenant:
            st.markdown(f"""
            | {t('backup.info_label')} | {t('backup.value_label')} |
            |---|---|
            | {t('backup.info_madrasa')} | {tenant['madrasa_name']} |
            | Tenant ID | #{tid} |
            | {t('backup.info_erp_version')} | v6.0 Enterprise |
            | {t('backup.info_today')} | {date.today()} |
            | DB Engine | PostgreSQL (Supabase) |
            """)

    with tab_backup:
        st.markdown(f"#### {t('backup.full_backup_heading')}")
        alert(
            t("backup.full_backup_notice"),
            "info",
        )

        col1, col2 = st.columns(2)
        if col1.button(t("backup.btn_create_full_backup"), type="primary", use_container_width=True):
            with st.spinner(t("backup.spinner_creating")):
                backup_data, failed_tables = _full_backup_json(tid)
            backup_bytes = backup_data.encode("utf-8")
            st.download_button(
                label=t("backup.btn_download_backup", kb=len(backup_bytes)//1024),
                data=backup_bytes,
                file_name=f"madrasa_backup_{date.today()}.json",
                mime="application/json",
                type="primary",
            )
            if failed_tables:
                alert(f"⚠️ কিছু টেবিলের ডেটা আনা যায়নি: {', '.join(failed_tables)} — ব্যাকআপ অসম্পূর্ণ।", "warning")
                audit_module.log("EXPORT","Backup",
                    f"Full backup তৈরি (PARTIAL — failed: {failed_tables}) — {len(backup_bytes)//1024} KB")
            else:
                audit_module.log("EXPORT","Backup",f"Full backup তৈরি — {len(backup_bytes)//1024} KB")

    with tab_restore:
        st.markdown(f"#### {t('backup.restore_heading')}")
        alert(t("backup.restore_warning"), "danger")
        uploaded = st.file_uploader(t("backup.restore_upload_label"), type=["json"], key="restore_upload")

        if uploaded:
            try:
                backup_data = json.loads(uploaded.getvalue().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                backup_data = None
                st.error(t("backup.err_invalid_json"))

            if backup_data is not None:
                ok, msg = validate_backup(backup_data)
                if not ok:
                    st.error(msg)
                else:
                    data = backup_data.get("data", {})
                    st.success(t("backup.restore_file_valid", date=backup_data.get("backup_date", "?")))
                    st.markdown(f"**{t('backup.restore_preview_heading')}**")
                    preview_rows = [{
                        t("backup.col_table"): tbl,
                        t("backup.col_record_count"): len(recs),
                    } for tbl, recs in data.items()]
                    st.dataframe(preview_rows, use_container_width=True, hide_index=True)
                    alert(t("backup.restore_final_warning"), "warning")

                    confirm_text = st.text_input(t("backup.restore_confirm_label"), key="restore_confirm")
                    restore_clicked = st.button(
                        t("backup.btn_restore"), type="primary",
                        disabled=(confirm_text.strip().upper() != "RESTORE"),
                    )
                    if restore_clicked:
                        with st.spinner(t("backup.restore_in_progress")):
                            ok2, err, counts = _restore_from_backup(tid, backup_data)
                        if ok2:
                            total = sum(counts.values())
                            audit_module.log(
                                "RESTORE", "Backup",
                                f"Restore সম্পন্ন — {total} রেকর্ড ({backup_data.get('backup_date','?')} তারিখের ব্যাকআপ থেকে)",
                            )
                            st.success(t("backup.restore_success", total=total))
                            st.rerun()
                        else:
                            st.error(t("backup.restore_failed", error=err))

    with tab_export:
        st.markdown(f"#### {t('backup.export_heading')}")

        c1, c2, c3, c4 = st.columns(4)

        # Students
        with c1:
            st.markdown(f"**{t('backup.students_list')}**")
            if st.button(t("backup.btn_create_json"), key="exp_stu", use_container_width=True):
                data = _export_students_json(tid)
                st.download_button(t("backup.btn_download"), data.encode("utf-8"),
                                   f"students_{date.today()}.json", "application/json")

        # Finance
        with c2:
            st.markdown(f"**{t('backup.fee_data')}**")
            yr = st.number_input(t("backup.year_label"), min_value=2020, max_value=2040,
                                  value=date.today().year, key="bk_yr")
            if st.button(t("backup.btn_create_csv"), key="exp_fin", use_container_width=True):
                data = _export_finance_csv(tid, int(yr))
                if data:
                    st.download_button(t("backup.btn_download"), data,
                                       f"finance_{yr}.csv", "text/csv")
                else:
                    alert(t("backup.no_data"),"info")

        # Marks
        with c3:
            st.markdown(f"**{t('backup.marks_data')}**")
            if st.button(t("backup.btn_create_csv"), key="exp_marks", use_container_width=True):
                data = _export_marks_csv(tid)
                if data:
                    st.download_button(t("backup.btn_download"), data,
                                       f"marks_{date.today()}.csv", "text/csv")
                else:
                    alert(t("backup.no_data"),"info")

        # Attendance
        with c4:
            st.markdown(f"**{t('backup.attendance_data')}**")
            if st.button(t("backup.btn_create_csv"), key="exp_att", use_container_width=True):
                data = _export_attendance_csv(tid)
                if data:
                    st.download_button(t("backup.btn_download"), data,
                                       f"attendance_{date.today()}.csv", "text/csv")
                else:
                    alert(t("backup.no_data"),"info")

    with tab_cleanup:
        st.markdown(f"#### {t('backup.cleanup_heading')}")
        alert(t("backup.cleanup_warning"), "warning")

        old_logs = fetchone(
            "SELECT COUNT(*) AS n FROM audit_logs WHERE tenant_id=%s AND created_at < NOW() - INTERVAL '90 days'",
            (tid,),
        )
        old_notif = fetchone(
            "SELECT COUNT(*) AS n FROM notifications WHERE tenant_id=%s AND is_read=TRUE AND created_at < NOW() - INTERVAL '30 days'",
            (tid,),
        )
        kpi_row([
            {"label": t("backup.kpi_old_audit_logs"), "value": int(old_logs["n"]) if old_logs else 0, "cls": "warning"},
            {"label": t("backup.kpi_read_notifications"), "value": int(old_notif["n"]) if old_notif else 0, "cls": "warning"},
        ])

        c1, c2 = st.columns(2)
        if c1.button(t("backup.delete_old_logs_button"), key="del_logs"):
            conn = get_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM audit_logs WHERE tenant_id=%s AND created_at < NOW() - INTERVAL '90 days'",
                            (tid,),
                        )
                    conn.commit()
                    audit_module.log("DELETE", "Backup", "৯০ দিনের বেশি পুরনো অডিট লগ মুছে ফেলা হয়েছে")
                    st.success(t("backup.old_logs_deleted"))
                    st.rerun()
                except Exception:
                    conn.rollback()
                finally:
                    release_connection(conn)

        if c2.button(t("backup.delete_read_notif_button"), key="del_notif"):
            conn = get_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM notifications WHERE tenant_id=%s AND is_read=TRUE AND created_at < NOW() - INTERVAL '30 days'",
                            (tid,),
                        )
                    conn.commit()
                    audit_module.log("DELETE", "Backup", "পঠিত পুরনো নোটিফিকেশন মুছে ফেলা হয়েছে")
                    st.success(t("backup.notif_deleted"))
                    st.rerun()
                except Exception:
                    conn.rollback()
                finally:
                    release_connection(conn)
