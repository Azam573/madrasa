"""
expense_module.py  — Expense Ledger & Budget Tracker
hostel_module.py   — Boarding/Hostel Management
library_module.py  — Library Book Issue/Return

সব একই ফাইলে, আলাদা render() ফাংশন সহ।
"""

import streamlit as st
from datetime import date
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id, months_list, current_year
import audit_module
from error_handler import safe_db_error
from i18n import t


def _seed_default_expense_categories():
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            defaults = [
                "বেতন ও ভাতা", "বিদ্যুৎ ও পানি", "খাদ্য ও রেশন",
                "মেরামত ও সংস্কার", "শিক্ষা উপকরণ", "অফিস সামগ্রী",
                "যানবাহন", "বিবিধ"
            ]
            for cat in defaults:
                cur.execute(
                    """INSERT INTO expense_categories (tenant_id, name)
                       VALUES (%s,%s) ON CONFLICT (tenant_id, name) DO NOTHING""",
                    (st.session_state.get("tenant_id",1), cat),
                )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


# ════════════════════════════════════════════════════════════════
# EXPENSE MODULE
# ════════════════════════════════════════════════════════════════

def _get_categories(tid):
    return fetchall(
        "SELECT * FROM expense_categories WHERE tenant_id=%s ORDER BY name", (tid,)
    )

def _add_expense(tid, cat_id, amount, desc, exp_date, method, month, year):
    conn = get_connection()
    if not conn:
        return False, "ডাটাবেজ সংযোগ ব্যর্থ হয়েছে। আবার চেষ্টা করুন।"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO expenses
                   (tenant_id, category_id, amount, description, expense_date,
                    payment_method, month_name, year, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, cat_id, amount, desc, exp_date, method, month, year,
                 st.session_state.get("username","admin")),
            )
            eid = cur.fetchone()["id"]
        conn.commit()
        return True, eid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)

def _expense_summary(tid, year, month=None):
    # SQL Injection fix (v9.0): m_clause শুধু hardcoded placeholder।
    # month value সম্পূর্ণ parameterized।
    params = [tid, year]
    m_clause = ""
    if month:
        m_clause = "AND month_name=%s"
        params.append(month)
    return fetchall(
        "SELECT ec.name AS category,"
        "       SUM(e.amount) AS total,"
        "       COUNT(e.id) AS count"
        " FROM expenses e"
        " LEFT JOIN expense_categories ec ON ec.id=e.category_id"
        f" WHERE e.tenant_id=%s AND e.year=%s {m_clause}"
        " GROUP BY ec.name ORDER BY total DESC",
        tuple(params),
    )

def _budget_vs_actual(tid, year, month):
    return fetchall(
        """SELECT ec.name AS category,
                  COALESCE(b.amount,0) AS budget,
                  COALESCE(SUM(e.amount),0) AS actual
           FROM expense_categories ec
           LEFT JOIN budgets b ON b.category_id=ec.id
             AND b.tenant_id=ec.tenant_id
             AND b.month_name=%s AND b.year=%s
           LEFT JOIN expenses e ON e.category_id=ec.id
             AND e.tenant_id=ec.tenant_id
             AND e.month_name=%s AND e.year=%s
           WHERE ec.tenant_id=%s
           GROUP BY ec.name, b.amount
           ORDER BY budget DESC""",
        (month, year, month, year, tid),
    )

def _set_budget(tid, cat_id, month, year, amount):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO budgets (tenant_id, category_id, month_name, year, amount)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, category_id, month_name, year)
                   DO UPDATE SET amount=%s""",
                (tid, cat_id, month, year, amount, amount),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def render_expense():
    tid = get_tenant_id()
    _seed_default_expense_categories()
    page_header("💸", t("expense.page_title"), t("expense.page_subtitle"))

    yr = current_year()
    categories = _get_categories(tid)
    cat_map = {c["name"]: c["id"] for c in categories}

    tab_add, tab_report, tab_budget = st.tabs([
        "➕ খরচ এন্ট্রি", "📊 রিপোর্ট", "📋 বাজেট"
    ])

    with tab_add:
        st.markdown("#### ➕ নতুন খরচ এন্ট্রি")
        with st.form("expense_form"):
            c1, c2 = st.columns(2)
            sel_cat = c1.selectbox("ক্যাটাগরি *", list(cat_map.keys()))
            amount  = c2.number_input("পরিমাণ (৳) *", min_value=1.0, step=50.0)
            desc    = st.text_input("বিবরণ", placeholder="যেমন: বিদ্যুৎ বিল অক্টোবর")
            c3, c4, c5 = st.columns(3)
            exp_date = c3.date_input("তারিখ", value=date.today())
            method   = c4.selectbox("পেমেন্ট পদ্ধতি", ["Cash","bKash","Nagad","Bank"])
            month    = c5.selectbox("মাস", months_list(), index=date.today().month - 1)
            year     = st.number_input("বছর", min_value=2020, max_value=2040, value=yr)

            if st.form_submit_button("✅ সেভ করুন", type="primary"):
                ok, result = _add_expense(
                    tid, cat_map[sel_cat], amount, desc.strip(),
                    str(exp_date), method.lower(), month, int(year)
                )
                if ok:
                    audit_module.log("CREATE","Expense",f"খরচ: ৳{amount:,.0f} — {desc}","expense",result)
                    st.success(f"✅ খরচ এন্ট্রি সেভ হয়েছে! ID: {result}")
                    st.rerun()
                else:
                    st.error(result)

    with tab_report:
        st.markdown("#### 📊 খরচের রিপোর্ট")
        rc1, rc2 = st.columns(2)
        rep_yr  = rc1.number_input("বছর", min_value=2020, max_value=2040, value=yr, key="exp_yr")
        rep_mon = rc2.selectbox("মাস (খালি = সারা বছর)", ["সারা বছর"] + months_list(), key="exp_mon")
        mon_filter = None if rep_mon == "সারা বছর" else rep_mon

        summary = _expense_summary(tid, int(rep_yr), mon_filter)
        if not summary:
            alert("এই সময়ে কোনো খরচ রেকর্ড নেই।", "info")
        else:
            total_exp = sum(float(r["total"] or 0) for r in summary)
            kpi_row([
                {"label": "মোট খরচ",    "value": f"৳{total_exp:,.0f}", "cls": "danger"},
                {"label": "খরচ এন্ট্রি", "value": sum(int(r["count"]) for r in summary), "cls": ""},
            ])
            rows = [{
                "ক্যাটাগরি":  r["category"] or "অন্যান্য",
                "মোট (৳)":   f"৳{float(r['total'] or 0):,.0f}",
                "এন্ট্রি":    int(r["count"]),
                "শেয়ার":     f"{float(r['total'] or 0)/total_exp*100:.1f}%" if total_exp else "—",
            } for r in summary]
            st.dataframe(rows, use_container_width=True, hide_index=True)
            import pandas as pd
            df = pd.DataFrame(rows)
            df["মোট"] = df["মোট (৳)"].str.replace("[৳,]","",regex=True).astype(float)
            st.bar_chart(df.set_index("ক্যাটাগরি")[["মোট"]], color="#C62828", height=240)

            # Detail
            divider()
            det_params = [tid, int(rep_yr)]
            det_m_clause = ""
            if mon_filter:
                det_m_clause = "AND e.month_name=%s"
                det_params.append(mon_filter)
            detail = fetchall(
                f"""SELECT e.expense_date, ec.name AS cat, e.amount,
                          e.description, e.payment_method
                   FROM expenses e
                   LEFT JOIN expense_categories ec ON ec.id=e.category_id
                   WHERE e.tenant_id=%s AND e.year=%s {det_m_clause}
                   ORDER BY e.expense_date DESC LIMIT 100""",
                tuple(det_params),
            )
            method_label_map = {"cash": "Cash", "bkash": "bKash", "nagad": "Nagad", "bank": "Bank"}
            if detail:
                drows = [{
                    "তারিখ":   str(r["expense_date"]),
                    "ক্যাটাগরি": r["cat"] or "—",
                    "বিবরণ":   r["description"] or "—",
                    "পরিমাণ":  f"৳{float(r['amount']):,.0f}",
                    "পদ্ধতি":  method_label_map.get(r["payment_method"], r["payment_method"].title()),
                } for r in detail]
                st.dataframe(drows, use_container_width=True, hide_index=True)

    with tab_budget:
        st.markdown("#### 📋 মাসিক বাজেট নির্ধারণ ও ট্র্যাকিং")
        bc1, bc2 = st.columns(2)
        bud_yr  = bc1.number_input("বছর", min_value=2020, max_value=2040, value=yr, key="bud_yr")
        bud_mon = bc2.selectbox("মাস", months_list(), index=date.today().month - 1, key="bud_mon")

        existing_budgets = fetchall(
            "SELECT category_id, amount FROM budgets WHERE tenant_id=%s AND month_name=%s AND year=%s",
            (tid, bud_mon, int(bud_yr)),
        )
        existing_map = {b["category_id"]: float(b["amount"]) for b in existing_budgets}

        with st.form("budget_form"):
            st.markdown("**প্রতিটি ক্যাটাগরির বাজেট নির্ধারণ করুন:**")
            for cat in categories:
                st.number_input(
                    cat["name"], min_value=0.0, step=500.0,
                    value=existing_map.get(cat["id"], 0.0),
                    key=f"bud_{cat['id']}",
                )
            if st.form_submit_button("💾 বাজেট সেভ করুন", type="primary"):
                for cat in categories:
                    val = st.session_state.get(f"bud_{cat['id']}", 0)
                    _set_budget(tid, cat["id"], bud_mon, int(bud_yr), val)
                st.success("✅ বাজেট সেভ হয়েছে!")
                st.rerun()

        # Budget vs Actual
        divider()
        bva = _budget_vs_actual(tid, int(bud_yr), bud_mon)
        if bva:
            st.markdown("**বাজেট বনাম প্রকৃত খরচ:**")
            rows = []
            for r in bva:
                budget = float(r["budget"] or 0)
                actual = float(r["actual"] or 0)
                variance = budget - actual
                rows.append({
                    "ক্যাটাগরি": r["category"],
                    "বাজেট":    f"৳{budget:,.0f}",
                    "প্রকৃত":   f"৳{actual:,.0f}",
                    "পার্থক্য": f"{'✅' if variance >= 0 else '❌'} ৳{abs(variance):,.0f}",
                    "অবস্থা":   "সীমার মধ্যে" if actual <= budget else "⚠️ অতিরিক্ত",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════
# HOSTEL MODULE
# ════════════════════════════════════════════════════════════════

def _get_rooms(tid):
    return fetchall(
        """SELECT r.*, COUNT(a.id) AS occupied
           FROM hostel_rooms r
           LEFT JOIN hostel_allocations a ON a.room_id=r.id
             AND a.tenant_id=r.tenant_id AND a.status='active'
           WHERE r.tenant_id=%s
           GROUP BY r.id ORDER BY r.room_no""",
        (tid,),
    )

def _create_room(tid, room_no, capacity, floor, room_type):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO hostel_rooms (tenant_id, room_no, capacity, floor, type)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (tid, room_no, capacity, floor, room_type),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)

def _allocate_room(tid, student_id, room_id, monthly_charge):
    conn = get_connection()
    if not conn:
        return False, t("hostel.err_db_connection")
    try:
        with conn.cursor() as cur:
            # Check capacity
            room = fetchone("SELECT capacity FROM hostel_rooms WHERE id=%s", (room_id,))
            occupied = fetchone(
                "SELECT COUNT(*) AS n FROM hostel_allocations WHERE room_id=%s AND status='active'",
                (room_id,),
            )
            if int(occupied["n"]) >= int(room["capacity"]):
                return False, t("hostel.err_no_vacancy")
            cur.execute(
                """INSERT INTO hostel_allocations
                   (tenant_id, student_id, room_id, monthly_charge, status)
                   VALUES (%s,%s,%s,%s,'active')
                   ON CONFLICT (tenant_id, student_id, room_id) DO UPDATE
                     SET status='active', monthly_charge=%s""",
                (tid, student_id, room_id, monthly_charge, monthly_charge),
            )
        conn.commit()
        return True, None
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def render_hostel():
    tid = get_tenant_id()
    page_header("🏨", t("hostel.page_title"), t("hostel.page_subtitle"))

    room_type_label = {
        "general": t("hostel.type_general"), "vip": t("hostel.type_vip"), "teacher": t("hostel.type_teacher"),
    }

    tab_rooms, tab_alloc, tab_list = st.tabs([
        t("hostel.tab_rooms"), t("hostel.tab_allocate"), t("hostel.tab_boarders")
    ])

    with tab_rooms:
        rooms = _get_rooms(tid)
        kpi_row([
            {"label": t("hostel.kpi_total_rooms"),      "value": len(rooms), "cls": ""},
            {"label": t("hostel.kpi_total_capacity"),   "value": sum(int(r["capacity"]) for r in rooms), "cls": ""},
            {"label": t("hostel.kpi_total_boarders"),   "value": sum(int(r["occupied"]) for r in rooms), "cls": "success"},
            {"label": t("hostel.kpi_available_space"),
             "value": sum(int(r["capacity"]) - int(r["occupied"]) for r in rooms),
             "cls": "accent"},
        ])
        if rooms:
            rows = [{
                t("hostel.col_room_no"):  r["room_no"],
                t("hostel.col_capacity"): r["capacity"],
                t("hostel.col_current"):  r["occupied"],
                t("hostel.col_empty"):    int(r["capacity"]) - int(r["occupied"]),
                t("hostel.col_floor"):    r.get("floor") or "—",
                t("hostel.col_type"):     room_type_label.get(r.get("type","general"), r.get("type","general")),
                t("hostel.col_status"):   t("hostel.status_available") if int(r["occupied"]) < int(r["capacity"]) else t("hostel.status_full"),
            } for r in rooms]
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            alert(t("hostel.info_no_rooms"), "info")

        divider()
        st.markdown(f"**{t('hostel.add_room_heading')}**")
        with st.form("room_form"):
            c1, c2, c3, c4 = st.columns(4)
            r_no   = c1.text_input(t("hostel.room_no_label"), placeholder="101")
            r_cap  = c2.number_input(t("hostel.capacity_label"), min_value=1, value=4)
            r_floor= c3.text_input(t("hostel.floor_label"), placeholder=t("hostel.floor_placeholder"))
            r_type = c4.selectbox(t("hostel.type_label"), ["general","vip","teacher"],
                                   format_func=lambda x: room_type_label.get(x, x))
            if st.form_submit_button(t("hostel.btn_add"), type="primary"):
                if not r_no.strip():
                    st.error(t("hostel.err_room_no_required"))
                elif _create_room(tid, r_no.strip(), int(r_cap), r_floor.strip(), r_type):
                    st.success(t("hostel.success_room_added", room=r_no))
                    st.rerun()

    with tab_alloc:
        st.markdown(f"#### {t('hostel.allocate_heading')}")
        students = fetchall(
            "SELECT s.id, s.name, e.roll_no, c.class_name FROM students s "
            "JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id "
            "JOIN classes c ON c.id=e.class_id "
            "WHERE s.tenant_id=%s AND s.status='active' ORDER BY s.name",
            (tid,),
        )
        rooms = _get_rooms(tid)
        available_rooms = [r for r in rooms if int(r["occupied"]) < int(r["capacity"])]

        if not students or not available_rooms:
            alert(t("hostel.warn_no_student_or_room"), "warning")
        else:
            stu_map = {f"{s['name']} (Roll {s['roll_no'] or '—'})": s for s in students}
            room_map = {f"{t('hostel.room_label')} {r['room_no']} — {t('hostel.col_empty')} {int(r['capacity'])-int(r['occupied'])}": r
                        for r in available_rooms}
            with st.form("alloc_form"):
                sel_stu  = st.selectbox(t("hostel.student_label"), list(stu_map.keys()))
                sel_room = st.selectbox(t("hostel.room_label"), list(room_map.keys()))
                charge   = st.number_input(t("hostel.monthly_charge_label"), min_value=0, value=1000, step=100)
                if st.form_submit_button(t("hostel.btn_allocate"), type="primary"):
                    stu_d  = stu_map[sel_stu]
                    room_d = room_map[sel_room]
                    ok, err = _allocate_room(tid, stu_d["id"], room_d["id"], charge)
                    if ok:
                        audit_module.log("CREATE","Hostel",f"রুম বরাদ্দ: {stu_d['name']} → রুম {room_d['room_no']}")
                        st.success(t("hostel.success_allocated", name=stu_d['name'], room=room_d['room_no']))
                        st.rerun()
                    else:
                        st.error(err)

    with tab_list:
        st.markdown(f"#### {t('hostel.boarders_heading')}")
        boarders = fetchall(
            """SELECT s.name, e.roll_no, c.class_name,
                      r.room_no, ha.monthly_charge, ha.alloc_date
               FROM hostel_allocations ha
               JOIN students s ON s.id=ha.student_id
               JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
               JOIN classes c ON c.id=e.class_id
               JOIN hostel_rooms r ON r.id=ha.room_id
               WHERE ha.tenant_id=%s AND ha.status='active'
               ORDER BY r.room_no, s.name""",
            (tid,),
        )
        if not boarders:
            alert(t("hostel.info_no_boarders"), "info")
        else:
            total_charge = sum(float(b["monthly_charge"]) for b in boarders)
            kpi_row([
                {"label": t("hostel.kpi_total_boarders"), "value": len(boarders), "cls": ""},
                {"label": t("hostel.kpi_monthly_income"), "value": f"৳{total_charge:,.0f}", "cls": "success"},
            ])
            rows = [{
                t("hostel.col_name"):           b["name"],
                t("hostel.col_roll"):           b["roll_no"] or "—",
                t("hostel.col_class"):          b["class_name"],
                t("hostel.room_label"):         b["room_no"],
                t("hostel.col_monthly_charge"): f"৳{float(b['monthly_charge']):,.0f}",
                t("hostel.col_admission_date"): str(b["alloc_date"]),
            } for b in boarders]
            st.dataframe(rows, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════
# LIBRARY MODULE
# ════════════════════════════════════════════════════════════════

def _add_book(tid, title, author, isbn, category, copies, shelf):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO library_books
                   (tenant_id, title, author, isbn, category, total_copies, available, shelf_no)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (tid, title, author, isbn, category, copies, copies, shelf),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)

def _issue_book(tid, book_id, student_id, due_date):
    conn = get_connection()
    if not conn:
        return False, t("library.err_db_connection")
    try:
        with conn.cursor() as cur:
            avail = fetchone("SELECT available FROM library_books WHERE id=%s", (book_id,))
            if not avail or int(avail["available"]) < 1:
                return False, t("library.err_book_unavailable")
            cur.execute(
                """INSERT INTO book_issues (tenant_id, book_id, student_id, due_date,
                   status, issued_by)
                   VALUES (%s,%s,%s,%s,'issued',%s)""",
                (tid, book_id, student_id, due_date,
                 st.session_state.get("username","admin")),
            )
            cur.execute(
                "UPDATE library_books SET available=available-1 WHERE id=%s", (book_id,)
            )
        conn.commit()
        return True, None
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)

def _return_book(tid, issue_id, book_id, fine):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE book_issues SET return_date=CURRENT_DATE,
                   status='returned', fine_amount=%s WHERE id=%s AND tenant_id=%s""",
                (fine, issue_id, tid),
            )
            cur.execute(
                "UPDATE library_books SET available=available+1 WHERE id=%s AND tenant_id=%s",
                (book_id, tid),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def render_library():
    tid = get_tenant_id()
    page_header("📚", t("library.page_title"), t("library.page_subtitle"))

    books = fetchall(
        "SELECT * FROM library_books WHERE tenant_id=%s ORDER BY title", (tid,)
    )
    total_books = sum(int(b["total_copies"]) for b in books)
    avail_books = sum(int(b["available"]) for b in books)

    kpi_row([
        {"label": t("library.kpi_total_books"), "value": total_books,              "cls": ""},
        {"label": t("library.kpi_available"),   "value": avail_books,              "cls": "success"},
        {"label": t("library.kpi_issued"),      "value": total_books - avail_books,"cls": "warning"},
        {"label": t("library.kpi_titles"),      "value": len(books),               "cls": ""},
    ])

    tab_catalog, tab_issue, tab_return, tab_add = st.tabs([
        t("library.tab_catalog"), t("library.tab_issue"), t("library.tab_return"), t("library.tab_add")
    ])

    with tab_catalog:
        search = st.text_input(t("library.search_placeholder"), key="lib_search")
        filtered = [b for b in books if not search or
                    search.lower() in (b["title"] or "").lower() or
                    search.lower() in (b["author"] or "").lower() or
                    search in (b["isbn"] or "")]
        rows = [{
            t("library.col_title"):     b["title"],
            t("library.col_author"):    b["author"] or "—",
            t("library.col_category"):  b["category"],
            t("library.col_shelf"):     b["shelf_no"] or "—",
            t("library.col_total_copies"): b["total_copies"],
            t("library.col_available"): b["available"],
            t("library.col_status"):    t("library.status_available") if int(b["available"]) > 0 else t("library.status_unavailable"),
        } for b in filtered]
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            alert(t("library.info_no_books_found"), "info")

    with tab_issue:
        st.markdown(f"#### {t('library.issue_heading')}")
        available_books = [b for b in books if int(b["available"]) > 0]
        students = fetchall(
            "SELECT s.id, s.name, e.roll_no FROM students s "
            "JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id "
            "WHERE s.tenant_id=%s AND s.status='active' ORDER BY s.name",
            (tid,),
        )
        if not available_books or not students:
            alert(t("library.warn_no_book_or_student"), "warning")
        else:
            book_map = {t("library.copies_available_suffix", title=b['title'], n=b['available']): b for b in available_books}
            stu_map  = {f"{s['name']} (Roll {s['roll_no'] or '—'})": s for s in students}
            with st.form("issue_form"):
                sel_b  = st.selectbox(t("library.select_book"), list(book_map.keys()))
                sel_s  = st.selectbox(t("library.select_student"), list(stu_map.keys()))
                due    = st.date_input(t("library.due_date_label"))
                if st.form_submit_button(t("library.btn_issue"), type="primary"):
                    bk = book_map[sel_b]
                    # Fix (F823): আগে local variable-এর নাম ছিল `st` — যেটা
                    # streamlit module-কে shadow করত। Python-এর scoping rule
                    # অনুযায়ী পুরো ফাংশনে st তখন local হয়ে যেত, ফলে উপরের
                    # st.tabs() call-এ UnboundLocalError-এ পেজটাই খুলত না।
                    stu = stu_map[sel_s]
                    ok, err = _issue_book(tid, bk["id"], stu["id"], str(due))
                    if ok:
                        audit_module.log("CREATE","Library",f"বই ইস্যু: {bk['title']} → {stu['name']}")
                        st.success(t("library.success_issued", title=bk['title']))
                        st.rerun()
                    else:
                        st.error(err)

    with tab_return:
        st.markdown(f"#### {t('library.return_heading')}")
        issued = fetchall(
            """SELECT bi.id AS issue_id, bi.book_id, b.title, s.name AS student_name,
                      bi.issue_date, bi.due_date,
                      GREATEST(0, CURRENT_DATE - bi.due_date) AS overdue_days
               FROM book_issues bi
               JOIN library_books b ON b.id=bi.book_id
               JOIN students s ON s.id=bi.student_id
               WHERE bi.tenant_id=%s AND bi.status='issued'
               ORDER BY bi.due_date""",
            (tid,),
        )
        if not issued:
            alert(t("library.success_no_issued_books"), "success")
        else:
            for b in issued:
                overdue = int(b["overdue_days"] or 0)
                fine    = overdue * 5  # ৳5 per day
                color   = "#C62828" if overdue > 0 else "#2E7D32"
                c1, c2, c3 = st.columns([3, 2, 1])
                c1.markdown(
                    f"**{b['title']}**  \n"
                    f"{b['student_name']} | {t('library.issued_label')} {str(b['issue_date'])} | "
                    f"<span style='color:{color}'>{t('library.return_label')} {str(b['due_date'])}"
                    f"{'  🔴 ' + str(overdue) + ' ' + t('library.overdue_days_suffix') if overdue > 0 else ''}</span>",
                    unsafe_allow_html=True,
                )
                c2.markdown(
                    t("library.fine_label", amount=fine) if fine > 0 else t("library.no_fine")
                )
                if c3.button(t("library.btn_return"), key=f"ret_{b['issue_id']}"):
                    if _return_book(tid, b["issue_id"], b["book_id"], fine):
                        audit_module.log("UPDATE","Library",f"বই রিটার্ন: {b['title']}")
                        st.rerun()

    with tab_add:
        st.markdown(f"#### {t('library.add_book_heading')}")
        with st.form("add_book_form"):
            title  = st.text_input(t("library.title_label"))
            author = st.text_input(t("library.author_label"))
            c1, c2, c3, c4 = st.columns(4)
            isbn     = c1.text_input(t("library.isbn_label"))
            category = c2.selectbox(t("library.category_label"),
                                     ["Islamic","Quran","Hadith","Fiqh","Arabic",
                                      "Bengali","Science","History","Other"])
            copies   = c3.number_input(t("library.copies_label"), min_value=1, value=1)
            shelf    = c4.text_input(t("library.shelf_no_label"))
            if st.form_submit_button(t("library.btn_add_book"), type="primary"):
                if not title.strip():
                    st.error(t("library.err_title_required"))
                elif _add_book(tid, title.strip(), author.strip(), isbn.strip(),
                               category, int(copies), shelf.strip()):
                    audit_module.log("CREATE","Library",f"নতুন বই: {title}")
                    st.success(t("library.success_book_added", title=title))
                    st.rerun()
                else:
                    st.error(t("library.err_add_failed"))
