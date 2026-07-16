"""
settings_module.py — System configuration: tenant profile,
academic sessions, classes, and user management.
"""

import streamlit as st
from db import get_connection, release_connection, fetchall, fetchone, execute
from utils import page_header, alert, divider, get_tenant_id
from error_handler import safe_db_error
from i18n import t


def _get_tenant(tid):
    return fetchone("SELECT * FROM tenants WHERE id=%s", (tid,))


def _update_tenant(tid, name, address, phone, email):
    execute(
        "UPDATE tenants SET madrasa_name=%s, address=%s, phone=%s, email=%s WHERE id=%s",
        (name, address, phone, email, tid),
    )


def _get_sessions(tid):
    return fetchall(
        "SELECT * FROM academic_sessions WHERE tenant_id=%s ORDER BY id DESC", (tid,)
    )


def _create_session(tid, name, start, end):
    conn = get_connection()
    if not conn:
        return False, t("set.err_db_generic")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO academic_sessions (tenant_id, session_name, start_date, end_date, is_active)
                   VALUES (%s,%s,%s,%s,FALSE) RETURNING id""",
                (tid, name, start, end),
            )
            sid = cur.fetchone()["id"]
        conn.commit()
        return True, sid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def _set_active_session(tid, session_id):
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE academic_sessions SET is_active=FALSE WHERE tenant_id=%s", (tid,))
            cur.execute(
                "UPDATE academic_sessions SET is_active=TRUE WHERE id=%s AND tenant_id=%s",
                (session_id, tid),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def _get_classes(tid):
    return fetchall(
        "SELECT * FROM classes WHERE tenant_id=%s ORDER BY class_numeric", (tid,)
    )


def _create_class(tid, name, numeric, section):
    conn = get_connection()
    if not conn:
        return False, t("set.err_db_generic")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO classes (tenant_id, class_name, class_numeric, section)
                   VALUES (%s,%s,%s,%s) RETURNING id""",
                (tid, name, numeric, section),
            )
            cid = cur.fetchone()["id"]
        conn.commit()
        return True, cid
    except Exception as ex:
        conn.rollback()
        return False, safe_db_error(ex)
    finally:
        release_connection(conn)


def render():
    tid = get_tenant_id()
    page_header("⚙️", t("set.page_title"), t("set.page_subtitle"))

    tab_profile, tab_branding, tab_sessions, tab_classes = st.tabs(
        [t("set.tab_profile"), t("set.tab_branding"), t("set.tab_sessions"), t("set.tab_classes")]
    )

    # ── Madrasa Profile ──
    with tab_profile:
        tenant = _get_tenant(tid)
        if tenant:
            with st.form("profile_form"):
                st.markdown(f"##### {t('set.madrasa_info_heading')}")
                name    = st.text_input(t("set.label_madrasa_name"), value=tenant["madrasa_name"] or "")
                address = st.text_area(t("set.label_address"), value=tenant["address"] or "", height=68)
                c1, c2 = st.columns(2)
                phone   = c1.text_input(t("set.label_phone"), value=tenant["phone"] or "")
                email   = c2.text_input(t("set.label_email"), value=tenant["email"] or "")
                if st.form_submit_button(t("set.btn_save_profile"), type="primary"):
                    if not name.strip():
                        st.error(t("set.err_madrasa_name_required"))
                    else:
                        _update_tenant(tid, name.strip(), address.strip(), phone.strip(), email.strip())
                        st.success(t("set.success_profile_updated"))

    # ── Academic Sessions ──
    # ── Branding — লোগো, রঙ, অধ্যক্ষ, রিসিট/TC customization ──
    with tab_branding:
        import base64 as _b64
        import branding as _br

        conn = get_connection()
        if not conn:
            alert(t("set.err_db_connection"), "error")
        else:
            try:
                with conn.cursor() as cur:
                    brand = _br.get_branding(cur, tid)
            finally:
                from db import release_connection as _rel
                _rel(conn)

            st.markdown(f"##### {t('set.branding_heading')}")
            st.caption(t("set.branding_caption"))

            # বর্তমান লোগো প্রিভিউ
            pc1, pc2 = st.columns([1, 3])
            with pc1:
                if brand.get("logo_base64"):
                    st.image(_b64.b64decode(brand["logo_base64"]), width=90)
                else:
                    st.markdown("<div style='font-size:56px'>🕌</div>",
                                unsafe_allow_html=True)
            with pc2:
                logo_file = st.file_uploader(
                    t("set.label_logo_upload"),
                    type=["png", "jpg", "jpeg"], key="brand_logo_up",
                )
                sig_file = st.file_uploader(
                    t("set.label_signature_upload"),
                    type=["png", "jpg", "jpeg"], key="brand_sig_up",
                )

            with st.form("branding_form"):
                b1, b2 = st.columns(2)
                name_ar = b1.text_input(t("set.label_name_arabic"),
                                        value=brand.get("name_arabic") or "")
                name_en = b2.text_input(t("set.label_name_english"),
                                        value=brand.get("name_english") or "")
                tagline = st.text_input(t("set.label_tagline"),
                                        value=brand.get("tagline") or "")
                b3, b4, b5 = st.columns(3)
                est   = b3.text_input(t("set.label_established_year"),
                                      value=str(brand.get("established_year") or ""))
                eiin  = b4.text_input(t("set.label_eiin"), value=brand.get("eiin_no") or "")
                regno = b5.text_input(t("set.label_reg_no"), value=brand.get("reg_no") or "")
                b6, b7 = st.columns(2)
                pname  = b6.text_input(t("set.label_principal_name"),
                                       value=brand.get("principal_name") or "")
                ptitle = b7.text_input(t("set.label_principal_title"),
                                       value=brand.get("principal_title") or t("set.default_principal_title"))
                b8, b9 = st.columns(2)
                pcolor = b8.color_picker(t("set.label_primary_color"),
                                         value=brand.get("primary_color") or "#0F4C5C")
                scolor = b9.color_picker(t("set.label_secondary_color"),
                                         value=brand.get("secondary_color") or "#C9A227")
                rfoot = st.text_input(t("set.label_receipt_footer"),
                                      value=brand.get("receipt_footer") or "")
                tfoot = st.text_input(t("set.label_tc_footer"),
                                      value=brand.get("tc_footer") or "")
                show_logo = st.checkbox(t("set.label_show_logo"),
                                        value=bool(brand.get("show_logo", True)))

                if st.form_submit_button(t("set.btn_save_branding"), type="primary"):
                    conn = get_connection()
                    if not conn:
                        alert(t("set.err_db_connection"), "error")
                    else:
                        try:
                            with conn.cursor() as cur:
                                fields = {
                                    "name_arabic": name_ar.strip() or None,
                                    "name_english": name_en.strip() or None,
                                    "tagline": tagline.strip() or None,
                                    "eiin_no": eiin.strip() or None,
                                    "reg_no": regno.strip() or None,
                                    "principal_name": pname.strip() or None,
                                    "principal_title": ptitle.strip() or None,
                                    "primary_color": pcolor,
                                    "secondary_color": scolor,
                                    "receipt_footer": rfoot.strip() or None,
                                    "tc_footer": tfoot.strip() or None,
                                    "show_logo": show_logo,
                                }
                                if est.strip().isdigit():
                                    fields["established_year"] = int(est.strip())
                                _br.upsert_branding(cur, tid, fields)
                                # আপলোড হওয়া ছবি (validation branding.py-তেই)
                                if logo_file is not None:
                                    _br.set_image(cur, tid, "logo",
                                        _b64.b64encode(logo_file.read()).decode())
                                if sig_file is not None:
                                    _br.set_image(cur, tid, "signature",
                                        _b64.b64encode(sig_file.read()).decode())
                            conn.commit()
                            alert(t("set.success_branding_saved"), "success")
                            st.rerun()
                        except ValueError as ex:
                            conn.rollback()
                            alert(str(ex), "error")
                        except Exception as ex:
                            conn.rollback()
                            alert(safe_db_error(ex), "error")
                        finally:
                            from db import release_connection as _rel
                            _rel(conn)

    with tab_sessions:
        sessions = _get_sessions(tid)
        st.markdown(f"##### {t('set.sessions_heading')}")
        if sessions:
            for s in sessions:
                col_name, col_status, col_btn = st.columns([3, 1, 1])
                col_name.markdown(f"**{s['session_name']}**  \n{str(s['start_date'] or '')} → {str(s['end_date'] or '')}")
                col_status.markdown(
                    t("set.badge_active") if s["is_active"] else t("set.badge_inactive")
                )
                if not s["is_active"]:
                    if col_btn.button(t("set.btn_set_active"), key=f"sess_act_{s['id']}"):
                        if _set_active_session(tid, s["id"]):
                            # Store in session state
                            st.session_state["active_session_id"] = s["id"]
                            st.success(t("set.success_session_active", name=s["session_name"]))
                            st.rerun()
        else:
            alert(t("set.info_no_sessions"), "info")

        divider()
        st.markdown(f"##### {t('set.new_session_heading')}")
        with st.form("session_form"):
            s_name = st.text_input(t("set.label_session_name"), placeholder=t("set.placeholder_session_name"))
            c1, c2 = st.columns(2)
            s_start = c1.date_input(t("set.label_start_date"), value=None)
            s_end   = c2.date_input(t("set.label_end_date"), value=None)
            if st.form_submit_button(t("set.btn_create_session"), type="primary"):
                if not s_name.strip():
                    st.error(t("set.err_session_name_required"))
                else:
                    ok, result = _create_session(tid, s_name.strip(), str(s_start), str(s_end))
                    if ok:
                        st.success(t("set.success_session_created", id=result))
                        st.rerun()
                    else:
                        st.error(result)

    # ── Classes ──
    with tab_classes:
        classes = _get_classes(tid)
        st.markdown(f"##### {t('set.existing_classes_heading')}")
        if classes:
            rows = [
                {t("set.col_id"): c["id"], t("set.col_class_name"): c["class_name"],
                 t("set.col_order"): c["class_numeric"], t("set.col_section"): c.get("section") or "A"}
                for c in classes
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            alert(t("set.info_no_classes"), "info")

        divider()
        st.markdown(f"##### {t('set.add_class_heading')}")
        with st.form("class_form"):
            c1, c2, c3 = st.columns(3)
            c_name    = c1.text_input(t("set.label_class_name"), placeholder=t("set.placeholder_class_name"))
            c_numeric = c2.number_input(t("set.label_order_numeric"), min_value=1, value=1)
            c_section = c3.text_input(t("set.label_section"), value="A")
            if st.form_submit_button(t("set.btn_add_class"), type="primary"):
                if not c_name.strip():
                    st.error(t("set.err_class_name_required"))
                else:
                    ok, result = _create_class(tid, c_name.strip(), int(c_numeric), c_section.strip() or "A")
                    if ok:
                        st.success(t("set.success_class_added", name=c_name))
                        st.rerun()
                    else:
                        st.error(result)
