"""
branch_module.py — Multi-Branch Support
একটি অ্যাকাউন্টে একাধিক শাখা (ঢাকা, চট্টগ্রাম, সিলেট…)
প্রতিটি শাখা আলাদা tenant হিসেবে কাজ করে।
"""

import secrets
import string
import streamlit as st
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id
import audit_module
from error_handler import safe_db_error
from i18n import t


def _get_all_tenants():
    return fetchall("SELECT id, madrasa_name, address, phone FROM tenants ORDER BY id")


def _generate_temp_password(length: int = 12) -> str:
    """
    র‍্যান্ডম, একবার-ব্যবহারযোগ্য পাসওয়ার্ড তৈরি করে (auth.py::
    validate_password_strength-এর নিয়ম মেনে — অন্তত একটি অক্ষর ও একটি সংখ্যা
    guarantee করা হয় নির্মাণের সময়েই, শুধু probability-র উপর ভরসা না করে)।
    """
    rng = secrets.SystemRandom()
    chars = [rng.choice(string.ascii_letters), rng.choice(string.digits)]
    chars += [rng.choice(string.ascii_letters + string.digits) for _ in range(length - 2)]
    rng.shuffle(chars)
    return "".join(chars)


def _create_branch_tenant(name, address, phone, email):
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        temp_password = _generate_temp_password()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tenants (madrasa_name, address, phone, email, slug)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (name, address, phone, email,
                 name.lower().replace(" ", "_")[:30]),
            )
            new_tid = cur.fetchone()["id"]
            # Seed default session + classes
            cur.execute(
                """INSERT INTO academic_sessions (tenant_id, session_name, is_active)
                   VALUES (%s,'2024-2025',TRUE)""",
                (new_tid,),
            )
            for cls_name, cls_num in [
                ("Hifz-1",1),("Hifz-2",2),("Nazera-1",3),("Ibtedaee",4)
            ]:
                cur.execute(
                    "INSERT INTO classes (tenant_id, class_name, class_numeric) VALUES (%s,%s,%s)",
                    (new_tid, cls_name, cls_num),
                )
            # Seed admin user
            # Fix (v9.0): হার্ডকোড "admin123"-এর বদলে প্রতিটি নতুন branch-এর
            # জন্য একবার-ব্যবহারযোগ্য random password তৈরি হয় (নাহলে যেকেউ
            # অনুমান করে যেকোনো নতুন branch-এ ঢুকতে পারত), এবং
            # must_change_password=TRUE — auth.py::check_auth() প্রথম
            # লগইনেই পাসওয়ার্ড বদলাতে বাধ্য করবে।
            from auth import hash_password
            pwd_hash = hash_password(temp_password)
            cur.execute(
                """INSERT INTO app_users
                   (tenant_id, username, password_hash, role, full_name, must_change_password)
                   VALUES (%s,'admin',%s,'admin','Branch Admin',TRUE)""",
                (new_tid, pwd_hash),
            )
        conn.commit()
        return True, {"tenant_id": new_tid, "password": temp_password}
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _branch_stats(tenant_id):
    students = fetchone(
        "SELECT COUNT(*) AS n FROM students WHERE tenant_id=%s AND status='active'",
        (tenant_id,),
    )
    collected = fetchone(
        """SELECT COALESCE(SUM(amount),0) AS n FROM fee_vouchers
           WHERE tenant_id=%s AND status='paid'
             AND EXTRACT(YEAR FROM issue_date)=EXTRACT(YEAR FROM CURRENT_DATE)""",
        (tenant_id,),
    )
    teachers = fetchone(
        "SELECT COUNT(*) AS n FROM teachers WHERE tenant_id=%s AND status='active'",
        (tenant_id,),
    )
    return {
        "students":  int(students["n"]) if students else 0,
        "collected": float(collected["n"]) if collected else 0.0,
        "teachers":  int(teachers["n"]) if teachers else 0,
    }


def _branch_stats_bulk(tenant_ids: list) -> dict:
    """
    #5 fix — N+1 query সমাধান:
    আগে: N টা tenant-এর জন্য render()-এ loop করে _branch_stats() কল হতো,
         প্রতিটা কল ৩টা query চালাতো → মোট 3×N query (N=১০ হলে ৩০টা,
         ২টা tab খুললে ৬০টা)।
    এখন: সব tenant-এর জন্য একবারে ৩টা GROUP BY query — tenant সংখ্যা
         যতই হোক, সবসময় constant (৩টা) query।

    Returns: {tenant_id: {"students":.., "collected":.., "teachers":..}, ...}
    """
    if not tenant_ids:
        return {}

    result = {tid: {"students": 0, "collected": 0.0, "teachers": 0} for tid in tenant_ids}

    student_rows = fetchall(
        """SELECT tenant_id, COUNT(*) AS n FROM students
           WHERE tenant_id = ANY(%s) AND status='active'
           GROUP BY tenant_id""",
        (tenant_ids,),
    )
    for row in student_rows:
        result[row["tenant_id"]]["students"] = int(row["n"])

    collected_rows = fetchall(
        """SELECT tenant_id, COALESCE(SUM(amount),0) AS n FROM fee_vouchers
           WHERE tenant_id = ANY(%s) AND status='paid'
             AND EXTRACT(YEAR FROM issue_date)=EXTRACT(YEAR FROM CURRENT_DATE)
           GROUP BY tenant_id""",
        (tenant_ids,),
    )
    for row in collected_rows:
        result[row["tenant_id"]]["collected"] = float(row["n"])

    teacher_rows = fetchall(
        """SELECT tenant_id, COUNT(*) AS n FROM teachers
           WHERE tenant_id = ANY(%s) AND status='active'
           GROUP BY tenant_id""",
        (tenant_ids,),
    )
    for row in teacher_rows:
        result[row["tenant_id"]]["teachers"] = int(row["n"])

    return result


def render():
    tid  = get_tenant_id()
    role = st.session_state.get("user_role", "staff")

    page_header("🏢", t("branch.page_title"), t("branch.page_subtitle"))

    if role != "admin":
        alert(t("branch.admin_required"), "danger"); return

    tenants = _get_all_tenants()

    # #5 fix: সব tenant-এর stats একবারে আনি (3টা query), loop-এর ভেতরে নয়
    all_tenant_ids = [t["id"] for t in tenants]
    stats_by_tenant = _branch_stats_bulk(all_tenant_ids)

    tab_overview, tab_create, tab_compare = st.tabs([
        t("branch.tab_overview"), t("branch.tab_create"), t("branch.tab_compare")
    ])

    # ── সারসংক্ষেপ ──
    with tab_overview:
        st.markdown(f"#### {t('branch.total_active', n=len(tenants))}")
        for br in tenants:
            stats = stats_by_tenant[br["id"]]
            is_current = br["id"] == tid

            with st.expander(
                f"{'🏠 ' if is_current else '🏢 '}"
                f"**{br['madrasa_name']}**"
                f"{('  ' + t('branch.current_branch_suffix')) if is_current else ''}"
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric(t("branch.metric_students"),  stats["students"])
                c2.metric(t("branch.metric_teachers"), stats["teachers"])
                c3.metric(t("branch.metric_collected"), f"৳{stats['collected']:,.0f}")
                c4.metric("Tenant ID", f"#{br['id']}")
                st.caption(f"📍 {br.get('address','') or '—'}  |  ☎ {br.get('phone','') or '—'}")

                if not is_current:
                    if st.button(t("branch.switch_btn"), key=f"sw_{br['id']}"):
                        st.session_state["tenant_id"] = br["id"]
                        st.session_state["schema_ready"] = False
                        audit_module.log("UPDATE","Branch",t("branch.audit_switch_branch", name=br['madrasa_name']))
                        st.success(t("branch.switch_success", name=br['madrasa_name']))
                        st.rerun()

    # ── নতুন শাখা ──
    with tab_create:
        st.markdown(f"#### {t('branch.create_heading')}")
        alert(t("branch.create_info"), "info")

        with st.form("new_branch_form"):
            b_name    = st.text_input(t("branch.name_label"), placeholder=t("branch.name_placeholder"))
            b_address = st.text_area(t("branch.address_label"), height=60)
            c1, c2   = st.columns(2)
            b_phone   = c1.text_input(t("branch.phone_label"))
            b_email   = c2.text_input(t("branch.email_label"))

            if st.form_submit_button(t("branch.create_btn"), type="primary"):
                if not b_name.strip():
                    st.error(t("branch.err_name_required"))
                else:
                    ok, result = _create_branch_tenant(
                        b_name.strip(), b_address.strip(), b_phone.strip(), b_email.strip()
                    )
                    if ok:
                        audit_module.log("CREATE","Branch",t("branch.audit_new_branch", name=b_name),
                                          "tenant",result["tenant_id"])
                        st.success(t("branch.create_success", id=result["tenant_id"]))
                        st.warning(t("branch.login_info", password=result["password"]))
                    else:
                        st.error(result)

    # ── তুলনামূলক বিশ্লেষণ ──
    with tab_compare:
        st.markdown(f"#### {t('branch.compare_heading')}")

        col_branch   = t("branch.col_branch")
        col_students = t("branch.col_students")

        rows = []
        for br in tenants:
            s = stats_by_tenant[br["id"]]  # #5 fix: bulk-fetched, কোনো নতুন query নয়
            rows.append({
                col_branch:        br["madrasa_name"],
                col_students:       s["students"],
                t("branch.col_teachers"):      s["teachers"],
                t("branch.col_collected"): f"৳{s['collected']:,.0f}",
                t("branch.col_status"):      t("branch.status_active"),
            })

        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
            import pandas as pd
            df = pd.DataFrame(rows)
            df[col_students] = df[col_students].astype(int)
            st.bar_chart(df.set_index(col_branch)[[col_students]], height=240)
