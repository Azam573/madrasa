"""
notice_module.py — Notice Board & Notification Center
Publish notices, due-fee SMS alerts, exam schedule announcements.
Integrates with local SMS gateway (configurable) or stores as in-app notifications.
"""

import streamlit as st
from datetime import date, datetime
from db import get_connection, release_connection, fetchall, fetchone
from utils import page_header, kpi_row, alert, divider, get_tenant_id, flatten_html
from sanitize import esc, sanitize_notice_body, sanitize_name
from error_handler import safe_db_error
from i18n import t


# ──────────────────────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_notices(tid, category=None):
    # SQL Injection fix (v9.0): cat_clause শুধু hardcoded placeholder।
    # category value পুরোটাই parameterized।
    params = [tid, date.today()]
    cat_clause = ""
    if category and category != "সব":
        cat_clause = "AND n.category=%s"
        params.append(category.lower())
    return fetchall(
        "SELECT n.*, c.class_name FROM notices n"
        " LEFT JOIN classes c ON c.id=n.target_class"
        " WHERE n.tenant_id=%s"
        "   AND (n.expiry_date IS NULL OR n.expiry_date >= %s)"
        f"  {cat_clause}"
        " ORDER BY n.is_pinned DESC, n.created_at DESC"
        " LIMIT 50",
        tuple(params),
    )


def _create_notice(tid, title, body, category, target_class_id, is_pinned, expiry):
    conn = get_connection()
    if not conn:
        return False, "DB error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO notices
                   (tenant_id, title, body, category, target_class, is_pinned, expiry_date)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, title, body, category, target_class_id, is_pinned,
                 str(expiry) if expiry else None),
            )
            nid = cur.fetchone()["id"]
        conn.commit()
        return True, nid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _delete_notice(tid, notice_id):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM notices WHERE id=%s AND tenant_id=%s", (notice_id, tid))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _generate_due_notifications(tid):
    """Auto-generate in-app notifications for students with unpaid fees."""
    due_students = fetchall(
        """SELECT DISTINCT s.id AS student_id, s.name, s.mobile_no,
                  v.month_name, v.year, v.amount, v.fund_type
           FROM fee_vouchers v
           JOIN students s ON s.id=v.student_id
           WHERE v.tenant_id=%s AND v.status='unpaid'
             AND (v.due_date IS NULL OR v.due_date <= CURRENT_DATE + INTERVAL '3 days')""",
        (tid,),
    )
    conn = get_connection()
    if not conn:
        return 0
    count = 0
    try:
        with conn.cursor() as cur:
            for stu in due_students:
                msg = t(
                    "notice.due_fee_msg",
                    name=stu['name'], month=stu['month_name'], year=stu['year'],
                    amount=f"{float(stu['amount']):,.0f}",
                )
                cur.execute(
                    """INSERT INTO notifications (tenant_id, student_id, message, type)
                       VALUES (%s,%s,%s,'warning')
                       ON CONFLICT DO NOTHING""",
                    (tid, stu["student_id"], msg),
                )
                count += 1
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        return 0
    finally:
        release_connection(conn)


def _get_notifications(tid, limit=30):
    return fetchall(
        """SELECT n.id, n.message, n.type, n.is_read, n.sent_via_sms,
                  n.created_at, s.name AS student_name, s.mobile_no
           FROM notifications n
           LEFT JOIN students s ON s.id=n.student_id
           WHERE n.tenant_id=%s
           ORDER BY n.created_at DESC LIMIT %s""",
        (tid, limit),
    )


def _mark_all_read(tid):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE notifications SET is_read=TRUE WHERE tenant_id=%s", (tid,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _bulk_sms_preview(tid):
    """Returns list of students with pending dues for SMS preview."""
    return fetchall(
        """SELECT s.name, s.mobile_no,
                  COUNT(v.id) AS due_count,
                  SUM(v.amount) AS total_due
           FROM students s
           JOIN fee_vouchers v ON v.student_id=s.id AND v.tenant_id=s.tenant_id
           WHERE s.tenant_id=%s AND v.status='unpaid' AND s.mobile_no IS NOT NULL
           GROUP BY s.id, s.name, s.mobile_no
           ORDER BY total_due DESC""",
        (tid,),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Notice card HTML
# ──────────────────────────────────────────────────────────────────────────────

CATEGORY_COLORS = {
    "general":  ("#E3F2FD", "#1565C0", "📢"),
    "exam":     ("#FFF8E1", "#9a6800", "📝"),
    "fee":      ("#FFEBEE", "#C62828", "💰"),
    "holiday":  ("#E8F5E9", "#2E7D32", "🌿"),
    "result":   ("#F3E5F5", "#6A1B9A", "🏆"),
    "event":    ("#FBE9E7", "#BF360C", "🎉"),
}

def _notice_card_html(n):
    bg, color, icon = CATEGORY_COLORS.get(n["category"], ("#F7F9FA", "#1A2332", "📌"))
    pinned = "📌 " if n["is_pinned"] else ""
    target = f" · {n['class_name']}" if n.get("class_name") else ""
    return flatten_html(f"""
    <div style="background:{bg};border-left:4px solid {color};border-radius:8px;
                padding:1rem 1.25rem;margin-bottom:0.75rem">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <span style="font-size:1.1rem">{icon}</span>
          <strong style="color:{color};margin-left:0.4rem">{pinned}{esc(str(n['title']))}</strong>
          <span style="font-size:0.72rem;color:#6B7A8D;margin-left:0.5rem">
            {n['category'].title()}{target} · {str(n['publish_date'])}
          </span>
        </div>
      </div>
      <div style="margin-top:0.5rem;font-size:0.88rem;color:#1A2332;white-space:pre-wrap">{n['body']}</div>
    </div>""")


# ──────────────────────────────────────────────────────────────────────────────
# Render
# ──────────────────────────────────────────────────────────────────────────────

def render():
    tid = get_tenant_id()
    page_header("📢", t("notice.page_title"), t("notice.page_subtitle"))

    # Unread count
    unread = fetchone(
        "SELECT COUNT(*) AS n FROM notifications WHERE tenant_id=%s AND is_read=FALSE", (tid,)
    )
    unread_count = int(unread["n"]) if unread else 0
    notices_total = fetchone("SELECT COUNT(*) AS n FROM notices WHERE tenant_id=%s", (tid,))

    kpi_row([
        {"label": t("notice.kpi_total"),  "value": int(notices_total["n"]) if notices_total else 0, "cls": ""},
        {"label": t("notice.kpi_unread"), "value": unread_count,   "cls": "warning" if unread_count else ""},
    ])

    tab_board, tab_create, tab_notif, tab_sms = st.tabs([
        t("notice.tab_board"), t("notice.tab_create"), t("notice.tab_notif", n=unread_count), t("notice.tab_sms")
    ])

    # ── নোটিশ বোর্ড ──
    with tab_board:
        ALL_LABEL = t("notice.all")
        CATEGORIES = [ALL_LABEL, "General", "Exam", "Fee", "Holiday", "Result", "Event"]
        cat_filter = st.radio(t("notice.category_label"), CATEGORIES, horizontal=True, key="notice_cat")
        notices = _get_notices(tid, None if cat_filter == ALL_LABEL else cat_filter)

        if not notices:
            alert(t("notice.no_notices"), "info")
        else:
            for n in notices:
                col_notice, col_del = st.columns([10, 1])
                with col_notice:
                    st.markdown(_notice_card_html(n), unsafe_allow_html=True)
                with col_del:
                    st.markdown("<div style='margin-top:0.75rem'>", unsafe_allow_html=True)
                    if st.button("🗑", key=f"del_notice_{n['id']}", help=t("notice.delete_help")):
                        _delete_notice(tid, n["id"])
                        st.rerun()
                    st.markdown("</div>", unsafe_allow_html=True)

    # ── নোটিশ তৈরি ──
    with tab_create:
        st.markdown(f"#### {t('notice.create_heading')}")
        classes = fetchall(
            "SELECT id, class_name FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,)
        )
        class_opts = {t("notice.all_classes"): None}
        class_opts.update({c["class_name"]: c["id"] for c in classes})

        with st.form("notice_form"):
            title    = st.text_input(t("notice.title_label"), placeholder=t("notice.title_placeholder"))
            body     = st.text_area(t("notice.body_label"), height=140,
                                    placeholder=t("notice.body_placeholder"))
            c1, c2, c3 = st.columns(3)
            category = c1.selectbox(t("notice.category_label"),
                                    ["general", "exam", "fee", "holiday", "result", "event"])
            target   = c2.selectbox(t("notice.target_label"), list(class_opts.keys()))
            expiry   = c3.date_input(t("notice.expiry_label"), value=None)
            is_pinned = st.checkbox(t("notice.pin_label"))

            if st.form_submit_button(t("notice.publish_btn"), type="primary"):
                if not title.strip() or not body.strip():
                    st.error(t("notice.err_required"))
                else:
                    ok, result = _create_notice(
                        tid, sanitize_name(title.strip()), sanitize_notice_body(body.strip()), category,
                        class_opts[target], is_pinned, expiry
                    )
                    if ok:
                        st.success(t("notice.success_published", id=result))
                        st.rerun()
                    else:
                        st.error(result)

    # ── নোটিফিকেশন ──
    with tab_notif:
        col_gen, col_mark = st.columns([3, 1])
        with col_gen:
            if st.button(t("notice.gen_alerts_btn"), type="primary"):
                count = _generate_due_notifications(tid)
                st.success(t("notice.success_generated", count=count))
                st.rerun()
        with col_mark:
            if st.button(t("notice.mark_all_read_btn"), key="mark_all_read"):
                _mark_all_read(tid)
                # Cache invalidate (v9.0) — app.py-এর notification cache reset
                st.session_state["_notif_cached_at"] = 0
                st.session_state["_notif_unread"]    = 0
                st.rerun()

        notifs = _get_notifications(tid)
        if not notifs:
            alert(t("notice.no_notifications"), "info")
        else:
            type_colors = {"warning": "🟡", "danger": "🔴", "info": "🔵", "success": "🟢"}
            for n in notifs:
                read_style = "" if n["is_read"] else "font-weight:600;"
                icon = type_colors.get(n["type"], "⚪")
                st.markdown(
                    f"""<div style="padding:0.6rem 0.75rem;border-bottom:1px solid #DDE3E7;
                                    {read_style}">
                      {icon} {n['message']}
                      <span style="float:right;font-size:0.72rem;color:#6B7A8D">
                        {n['student_name'] or ''} · {str(n['created_at'])[:16]}
                      </span>
                    </div>""",
                    unsafe_allow_html=True,
                )

    # ── SMS অ্যালার্ট ──
    with tab_sms:
        st.markdown(f"#### {t('notice.sms_preview_heading')}")
        alert(t("notice.sms_gateway_info"), "info")

        sms_list = _bulk_sms_preview(tid)
        if not sms_list:
            alert(t("notice.no_dues_sms"), "success")
        else:
            st.markdown(f"**{t('notice.sms_will_send', n=len(sms_list))}**")
            rows = []
            for s in sms_list:
                msg = t(
                    "notice.sms_due_msg",
                    name=s['name'], due_count=s['due_count'],
                    total=f"{float(s['total_due']):,.0f}",
                )
                rows.append({
                    t("notice.col_name"):    s["name"],
                    t("notice.col_mobile"): s["mobile_no"],
                    t("notice.col_due"): s["due_count"],
                    t("notice.col_total"): f"৳{float(s['total_due']):,.0f}",
                    t("notice.col_sms_msg"): msg[:60] + "...",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

            # Custom SMS composer
            divider()
            st.markdown(f"**{t('notice.custom_composer_heading')}**")
            with st.form("sms_compose"):
                custom_msg = st.text_area(
                    t("notice.msg_label"),
                    value=t("notice.default_sms_value"),
                    height=100
                )
                char_count = len(custom_msg)
                sms_count  = (char_count // 160) + 1
                st.caption(t("notice.char_count_caption", chars=char_count, sms_count=sms_count, total_sms=sms_count * len(sms_list)))
                if st.form_submit_button(t("notice.send_sms_btn"), type="primary"):
                    alert(t("notice.sms_not_configured"), "warning")
