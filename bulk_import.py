"""
bulk_import.py — Shared CSV/Excel bulk-import engine (students & donors)

পুরনো register/Excel থেকে বাল্ক ডেটা আনার জন্য। backup_module.py-এর
"untrusted file input" safety প্যাটার্ন অনুসরণ করে: preview-before-commit,
প্রতিটি row-এর error/warning আলাদা করে দেখানো, একটাই transaction, এবং
ব্যর্থ row বাদ দিয়ে বাকি valid row-গুলো commit করা (একটা ভুল row-এর
জন্য পুরো ফাইল আটকে যায় না)।
"""
import io
import pandas as pd
from datetime import date

from db import get_connection, release_connection, fetchall
from error_handler import safe_db_error
from pii_crypto import encrypt_nid
from i18n import t

MAX_BULK_IMPORT_BYTES = 5 * 1024 * 1024  # 5 MB — row-oriented student/donor CSV/Excel, generous cap


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------

def parse_upload(uploaded_file):
    """CSV বা Excel ফাইল পার্স করে DataFrame রিটার্ন করে। ব্যর্থ হলে (None, error)।"""
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > MAX_BULK_IMPORT_BYTES:
        mb = size / 1024 / 1024
        return None, t("bulk.err_file_too_large", mb=mb, max_mb=MAX_BULK_IMPORT_BYTES // 1024 // 1024)

    name = (uploaded_file.name or "").lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(uploaded_file, dtype=str, keep_default_na=False)
        elif name.endswith(".xlsx") or name.endswith(".xls"):
            df = pd.read_excel(uploaded_file, dtype=str)
        else:
            return None, t("bulk.err_unsupported_format")
    except Exception as ex:
        return None, t("bulk.err_parse_failed", error=str(ex))

    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.fillna("")
    if df.empty:
        return None, t("bulk.err_empty_file")
    return df, None


def _cell(row, key):
    val = row.get(key, "")
    if val is None:
        return ""
    val = str(val).strip()
    return "" if val.lower() == "nan" else val


# ---------------------------------------------------------------------------
# Shared duplicate-mobile lookup (donor_module.py reuses this for the
# single-add form too, to keep one source of truth).
# ---------------------------------------------------------------------------

def find_duplicate_donor_mobiles(tid: int, mobile_no: str) -> list:
    if not mobile_no:
        return []
    return fetchall(
        """SELECT id, name, status FROM monthly_donors
           WHERE tenant_id=%s AND mobile_no=%s""",
        (tid, mobile_no),
    )


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------

STUDENT_TEMPLATE_COLUMNS = [
    "name", "father_name", "mother_name", "mobile_no", "date_of_birth",
    "gender", "present_address", "permanent_address", "nid_no", "blood_group",
    "class_name", "session_name", "roll_no", "monthly_fee",
]


def students_template_csv() -> bytes:
    example = {
        "name": "Muhammad Abdullah", "father_name": "Abdul Karim", "mother_name": "",
        "mobile_no": "01700000000", "date_of_birth": "2012-05-10", "gender": "Male",
        "present_address": "Dhaka", "permanent_address": "", "nid_no": "",
        "blood_group": "O+", "class_name": "Hifz-1", "session_name": "2026",
        "roll_no": "1", "monthly_fee": "500",
    }
    buf = io.StringIO()
    pd.DataFrame([example])[STUDENT_TEMPLATE_COLUMNS].to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


def validate_students(tid: int, df: pd.DataFrame) -> list:
    """প্রতিটি row-এর জন্য {row_no, data, class_id, session_id, errors, warnings} রিটার্ন করে।"""
    classes = {
        c["class_name"].strip().lower(): c["id"]
        for c in fetchall("SELECT id, class_name FROM classes WHERE tenant_id=%s", (tid,))
    }
    sessions = {
        s["session_name"].strip().lower(): s["id"]
        for s in fetchall("SELECT id, session_name FROM academic_sessions WHERE tenant_id=%s", (tid,))
    }
    existing_mobiles = {
        r["mobile_no"] for r in fetchall(
            "SELECT mobile_no FROM students WHERE tenant_id=%s AND mobile_no IS NOT NULL AND mobile_no != ''",
            (tid,),
        )
    }

    rows = []
    seen_in_file = set()
    for i, raw in df.iterrows():
        row_no = i + 2  # header = row 1
        name = _cell(raw, "name")
        errors, warnings = [], []
        if not name:
            errors.append(t("bulk.err_missing_field", field="name"))

        class_id = None
        class_name = _cell(raw, "class_name")
        if class_name:
            class_id = classes.get(class_name.lower())
            if class_id is None:
                errors.append(t("bulk.err_unknown_class", value=class_name))

        session_id = None
        session_name = _cell(raw, "session_name")
        if session_name:
            session_id = sessions.get(session_name.lower())
            if session_id is None:
                errors.append(t("bulk.err_unknown_session", value=session_name))

        mobile = _cell(raw, "mobile_no")
        if mobile:
            if mobile in existing_mobiles or mobile in seen_in_file:
                warnings.append(t("bulk.warn_duplicate_mobile", value=mobile))
            seen_in_file.add(mobile)

        monthly_fee = _cell(raw, "monthly_fee")
        try:
            monthly_fee = float(monthly_fee) if monthly_fee else 0
        except ValueError:
            errors.append(t("bulk.err_invalid_number", field="monthly_fee", value=monthly_fee))
            monthly_fee = 0

        rows.append({
            "row_no": row_no,
            "data": {
                "name": name,
                "father_name": _cell(raw, "father_name") or None,
                "mother_name": _cell(raw, "mother_name") or None,
                "mobile_no": mobile or None,
                "date_of_birth": _cell(raw, "date_of_birth") or None,
                "gender": _cell(raw, "gender") or None,
                "present_address": _cell(raw, "present_address") or None,
                "permanent_address": _cell(raw, "permanent_address") or None,
                "nid_no": _cell(raw, "nid_no") or None,
                "blood_group": _cell(raw, "blood_group") or None,
                "roll_no": _cell(raw, "roll_no") or None,
                "monthly_fee": monthly_fee,
            },
            "class_id": class_id,
            "session_id": session_id,
            "errors": errors,
            "warnings": warnings,
        })
    return rows


def commit_students(tid: int, validated_rows: list):
    """শুধু error-free row commit হয়। Returns (created_count, per_row_failures)."""
    conn = get_connection()
    if not conn:
        return 0, [t("bulk.err_db_generic")]
    created = 0
    failures = []
    try:
        with conn.cursor() as cur:
            for r in validated_rows:
                if r["errors"]:
                    continue
                d = r["data"]
                try:
                    cur.execute(
                        """INSERT INTO students
                           (tenant_id, name, father_name, mother_name, mobile_no,
                            date_of_birth, gender, present_address, permanent_address,
                            nid_no_encrypted, blood_group, status)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           RETURNING id""",
                        (
                            tid, d["name"], d["father_name"], d["mother_name"], d["mobile_no"],
                            d["date_of_birth"], d["gender"], d["present_address"],
                            d["permanent_address"], encrypt_nid(d["nid_no"]), d["blood_group"],
                            "active" if (r["class_id"] and r["session_id"]) else "pending",
                        ),
                    )
                    student_id = cur.fetchone()["id"]
                    if r["class_id"] and r["session_id"]:
                        cur.execute(
                            """INSERT INTO student_enrollments
                               (tenant_id, student_id, session_id, class_id, roll_no,
                                monthly_fee, enrollment_status)
                               VALUES (%s,%s,%s,%s,%s,%s,'active')""",
                            (tid, student_id, r["session_id"], r["class_id"],
                             d["roll_no"], d["monthly_fee"]),
                        )
                    created += 1
                except Exception as ex:
                    failures.append((r["row_no"], safe_db_error(ex)))
        conn.commit()
        return created, failures
    except Exception as ex:
        conn.rollback()
        return 0, [(0, safe_db_error(ex))]
    finally:
        release_connection(conn)


# ---------------------------------------------------------------------------
# Donors
# ---------------------------------------------------------------------------

DONOR_TEMPLATE_COLUMNS = ["name", "mobile_no", "address", "monthly_amount", "start_date", "notes"]


def donors_template_csv() -> bytes:
    example = {
        "name": "Abdul Karim", "mobile_no": "01700000000", "address": "Dhaka",
        "monthly_amount": "500", "start_date": str(date.today()), "notes": "",
    }
    buf = io.StringIO()
    pd.DataFrame([example])[DONOR_TEMPLATE_COLUMNS].to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


def validate_donors(tid: int, df: pd.DataFrame) -> list:
    rows = []
    seen_in_file = set()
    for i, raw in df.iterrows():
        row_no = i + 2
        name = _cell(raw, "name")
        errors, warnings = [], []
        if not name:
            errors.append(t("bulk.err_missing_field", field="name"))

        amount_raw = _cell(raw, "monthly_amount")
        try:
            monthly_amount = float(amount_raw) if amount_raw else None
            if monthly_amount is None:
                errors.append(t("bulk.err_missing_field", field="monthly_amount"))
                monthly_amount = 0
        except ValueError:
            errors.append(t("bulk.err_invalid_number", field="monthly_amount", value=amount_raw))
            monthly_amount = 0

        mobile = _cell(raw, "mobile_no")
        if mobile:
            dups = find_duplicate_donor_mobiles(tid, mobile)
            if dups or mobile in seen_in_file:
                warnings.append(t("bulk.warn_duplicate_mobile", value=mobile))
            seen_in_file.add(mobile)

        rows.append({
            "row_no": row_no,
            "data": {
                "name": name,
                "mobile_no": mobile or None,
                "address": _cell(raw, "address") or None,
                "monthly_amount": monthly_amount,
                "start_date": _cell(raw, "start_date") or None,
                "notes": _cell(raw, "notes") or None,
            },
            "errors": errors,
            "warnings": warnings,
        })
    return rows


def commit_donors(tid: int, validated_rows: list):
    conn = get_connection()
    if not conn:
        return 0, [t("bulk.err_db_generic")]
    created = 0
    failures = []
    try:
        with conn.cursor() as cur:
            for r in validated_rows:
                if r["errors"]:
                    continue
                d = r["data"]
                try:
                    cur.execute(
                        """INSERT INTO monthly_donors
                           (tenant_id, name, mobile_no, address, monthly_amount, start_date, notes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (tid, d["name"], d["mobile_no"], d["address"],
                         d["monthly_amount"], d["start_date"], d["notes"]),
                    )
                    created += 1
                except Exception as ex:
                    failures.append((r["row_no"], safe_db_error(ex)))
        conn.commit()
        return created, failures
    except Exception as ex:
        conn.rollback()
        return 0, [(0, safe_db_error(ex))]
    finally:
        release_connection(conn)


# ---------------------------------------------------------------------------
# Question bank (online_exam.py)
# ---------------------------------------------------------------------------

QUESTION_BANK_TEMPLATE_COLUMNS = [
    "class_name", "subject_name", "topic", "difficulty",
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_option", "marks", "explanation",
]


def question_bank_template_csv() -> bytes:
    example = {
        "class_name": "Hifz-1", "subject_name": "আল-কুরআন",
        "topic": "সূরা আল-বাকারা ১-২০", "difficulty": "medium",
        "question_text": "সূরা বাকারার প্রথম আয়াতে কী আছে?",
        "option_a": "বিকল্প A", "option_b": "বিকল্প B",
        "option_c": "বিকল্প C", "option_d": "বিকল্প D",
        "correct_option": "A", "marks": "2", "explanation": "",
    }
    buf = io.StringIO()
    pd.DataFrame([example])[QUESTION_BANK_TEMPLATE_COLUMNS].to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


def validate_question_bank(tid: int, df: pd.DataFrame) -> list:
    """প্রতিটি row-এর জন্য {row_no, data, class_id, subject_id, errors, warnings} রিটার্ন করে।"""
    classes = {
        c["class_name"].strip().lower(): c["id"]
        for c in fetchall("SELECT id, class_name FROM classes WHERE tenant_id=%s", (tid,))
    }
    subjects = {
        s["subject_name"].strip().lower(): s["id"]
        for s in fetchall("SELECT id, subject_name FROM subjects WHERE tenant_id=%s", (tid,))
    }

    rows = []
    for i, raw in df.iterrows():
        row_no = i + 2  # header = row 1
        errors, warnings = [], []

        class_id = None
        class_name = _cell(raw, "class_name")
        if not class_name:
            errors.append(t("bulk.err_missing_field", field="class_name"))
        else:
            class_id = classes.get(class_name.lower())
            if class_id is None:
                errors.append(t("bulk.err_unknown_class", value=class_name))

        subject_id = None
        subject_name = _cell(raw, "subject_name")
        if not subject_name:
            errors.append(t("bulk.err_missing_field", field="subject_name"))
        else:
            subject_id = subjects.get(subject_name.lower())
            if subject_id is None:
                errors.append(t("bulk.err_unknown_subject", value=subject_name))

        question_text = _cell(raw, "question_text")
        if not question_text:
            errors.append(t("bulk.err_missing_field", field="question_text"))
        opt_a = _cell(raw, "option_a")
        if not opt_a:
            errors.append(t("bulk.err_missing_field", field="option_a"))
        opt_b = _cell(raw, "option_b")
        if not opt_b:
            errors.append(t("bulk.err_missing_field", field="option_b"))

        correct = _cell(raw, "correct_option").upper()
        if correct not in ("A", "B", "C", "D"):
            errors.append(t("bulk.err_invalid_choice", field="correct_option", value=correct))

        difficulty = _cell(raw, "difficulty").lower() or "medium"
        if difficulty not in ("easy", "medium", "hard"):
            errors.append(t("bulk.err_invalid_choice", field="difficulty", value=difficulty))

        marks_raw = _cell(raw, "marks")
        try:
            marks = int(float(marks_raw)) if marks_raw else 1
        except ValueError:
            errors.append(t("bulk.err_invalid_number", field="marks", value=marks_raw))
            marks = 1

        rows.append({
            "row_no": row_no,
            "data": {
                "topic": _cell(raw, "topic") or None,
                "difficulty": difficulty,
                "question_text": question_text,
                "option_a": opt_a,
                "option_b": opt_b,
                "option_c": _cell(raw, "option_c") or None,
                "option_d": _cell(raw, "option_d") or None,
                "correct_option": correct,
                "marks": marks,
                "explanation": _cell(raw, "explanation") or None,
            },
            "class_id": class_id,
            "subject_id": subject_id,
            "errors": errors,
            "warnings": warnings,
        })
    return rows


def commit_question_bank(tid: int, validated_rows: list, created_by: str):
    """শুধু error-free row commit হয়। Returns (created_count, per_row_failures)।
    created_by explicit param — bulk_import.py-তে UI framework নির্ভরতা (st.session_state)
    এড়াতে caller নিজেই username পাঠায়।"""
    conn = get_connection()
    if not conn:
        return 0, [t("bulk.err_db_generic")]
    created = 0
    failures = []
    try:
        with conn.cursor() as cur:
            for r in validated_rows:
                if r["errors"]:
                    continue
                d = r["data"]
                try:
                    cur.execute(
                        """INSERT INTO question_bank
                           (tenant_id, subject_id, class_id, topic, difficulty,
                            question_text, option_a, option_b, option_c, option_d,
                            correct_option, marks, explanation, created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            tid, r["subject_id"], r["class_id"], d["topic"], d["difficulty"],
                            d["question_text"], d["option_a"], d["option_b"],
                            d["option_c"], d["option_d"], d["correct_option"],
                            d["marks"], d["explanation"], created_by,
                        ),
                    )
                    created += 1
                except Exception as ex:
                    failures.append((r["row_no"], safe_db_error(ex)))
        conn.commit()
        return created, failures
    except Exception as ex:
        conn.rollback()
        return 0, [(0, safe_db_error(ex))]
    finally:
        release_connection(conn)


# ---------------------------------------------------------------------------
# Preview table (shared rendering helper)
# ---------------------------------------------------------------------------

def preview_dataframe(validated_rows: list) -> pd.DataFrame:
    out = []
    for r in validated_rows:
        if r["errors"]:
            status = "❌ " + t("bulk.status_error")
            issues = "; ".join(r["errors"])
        elif r["warnings"]:
            status = "⚠️ " + t("bulk.status_warning")
            issues = "; ".join(r["warnings"])
        else:
            status = "✅ " + t("bulk.status_ok")
            issues = ""
        row_out = {t("bulk.col_row"): r["row_no"], t("bulk.col_status"): status}
        row_out.update({k: v for k, v in r["data"].items() if k != "notes"})
        row_out[t("bulk.col_issues")] = issues
        out.append(row_out)
    return pd.DataFrame(out)
