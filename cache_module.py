"""
cache_module.py — Cache & Background Jobs Dashboard
Redis cache status, Celery task monitor, manual triggers।
"""

import streamlit as st
from datetime import date
from utils import page_header, kpi_row, alert, divider, get_tenant_id
from i18n import t


def _get_cache():
    try:
        from cache import cache_health, cache_stats, invalidate_tenant, invalidate_namespace
        return True, cache_health, cache_stats, invalidate_tenant, invalidate_namespace
    except ImportError:
        return False, None, None, None, None


def _get_celery():
    try:
        from workers.celery_app import celery_app
        return True, celery_app
    except ImportError:
        return False, None


def render():
    tid = get_tenant_id()
    page_header("⚡", t("cache.page_title"), t("cache.page_subtitle"))

    cache_ok, cache_health_fn, cache_stats_fn, inval_tenant, inval_ns = _get_cache()
    celery_ok, celery_app = _get_celery()

    tab_cache, tab_jobs, tab_triggers, tab_schedule = st.tabs([
        "🔴 Redis Cache", "⚙️ Celery Workers", "🚀 Manual Triggers", "📅 Scheduled Tasks"
    ])

    # ── Redis Cache ──
    with tab_cache:
        st.markdown("#### 🔴 Redis Cache Status")

        if not cache_ok:
            alert(t("cache.err_no_cache_module"), "warning")
        else:
            health = cache_health_fn()
            status_color = "#2E7D32" if health.get("status") == "healthy" else "#C62828"
            status_icon  = "🟢" if health.get("status") == "healthy" else "🔴"

            st.markdown(
                f"""<div style="background:white;border:1px solid #DDE3E7;border-radius:10px;
                                padding:1rem 1.25rem;margin-bottom:1rem">
                  <div style="font-size:1rem;font-weight:700;color:{status_color}">
                    {status_icon} Redis — {health.get('status','unknown').upper()}
                  </div>
                  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:0.75rem;font-size:12px">
                    <div><span style="color:#6B7A8D">URL</span><br><b>{health.get('redis_url','—')}</b></div>
                    <div><span style="color:#6B7A8D">Memory</span><br><b>{health.get('used_memory','—')}</b></div>
                    <div><span style="color:#6B7A8D">Clients</span><br><b>{health.get('connected_clients','—')}</b></div>
                  </div>
                </div>""",
                unsafe_allow_html=True,
            )

            # Cache stats
            stats = cache_stats_fn(tid)
            if stats.get("available"):
                kpi_row([
                    {"label": t("cache.total_cache_keys"), "value": stats.get("total_keys", 0), "cls": ""},
                ])

                if stats.get("namespaces"):
                    st.markdown("**Namespace Breakdown:**")
                    rows = [{"Namespace": k, "Keys": v}
                            for k, v in stats["namespaces"].items()]
                    st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                alert(f"Cache stats unavailable: {stats.get('error','')}", "warning")

            divider()
            st.markdown(f"**{t('cache.clear_heading')}**")
            c1, c2, c3 = st.columns(3)

            if c1.button(t("cache.btn_clear_all"), type="primary", use_container_width=True):
                deleted = inval_tenant(tid)
                st.success(t("cache.msg_cache_cleared", n=deleted))
                st.rerun()

            ns_opts = ["dashboard_kpi","student_list","fee_summary",
                       "attendance_today","exam_results","class_list"]
            sel_ns = c2.selectbox("Namespace", ns_opts, label_visibility="collapsed")
            if c3.button(t("cache.btn_clear_ns", ns=sel_ns), use_container_width=True):
                deleted = inval_ns(tid, sel_ns)
                st.success(t("cache.msg_ns_cleared", ns=sel_ns, n=deleted))

    # ── Celery Workers ──
    with tab_jobs:
        st.markdown("#### ⚙️ Celery Worker Status")

        if not celery_ok:
            alert(t("cache.err_no_celery"), "warning")
        else:
            try:
                inspect = celery_app.control.inspect(timeout=3)
                active  = inspect.active()
                stats   = inspect.stats()
                reserved = inspect.reserved()

                if not active and not stats:
                    alert(t("cache.err_no_worker"), "danger")
                else:
                    worker_count = len(active or {})
                    kpi_row([
                        {"label": "Active Workers", "value": worker_count, "cls": "success"},
                        {"label": "Active Tasks",   "value": sum(len(v) for v in (active or {}).values()), "cls": "accent"},
                        {"label": "Reserved",       "value": sum(len(v) for v in (reserved or {}).values()), "cls": ""},
                    ])

                    if active:
                        for worker_name, tasks in active.items():
                            st.markdown(f"**Worker: `{worker_name}`**")
                            if tasks:
                                for task in tasks:
                                    st.markdown(
                                        f'<div style="background:#E8F5E9;border-radius:6px;'
                                        f'padding:6px 12px;margin-bottom:4px;font-size:12px">'
                                        f'▶️ `{task.get("name","unknown")}` — {task.get("id","")[:8]}…</div>',
                                        unsafe_allow_html=True,
                                    )
                            else:
                                st.caption(t("cache.no_active_task"))

                    if stats:
                        divider()
                        st.markdown("**Worker Statistics:**")
                        for worker, stat in stats.items():
                            pool = stat.get("pool", {})
                            st.markdown(
                                f'<div style="background:#F7F9FA;border-radius:6px;'
                                f'padding:8px 12px;margin-bottom:4px;font-size:12px">'
                                f'🔧 `{worker}` — '
                                f'Pool: {pool.get("implementation","unknown")} | '
                                f'Processes: {pool.get("processes",[])} | '
                                f'Completed: {stat.get("total",{})}</div>',
                                unsafe_allow_html=True,
                            )
            except Exception as ex:
                alert(f"Worker connection error: {ex}", "danger")

    # ── Manual Triggers ──
    with tab_triggers:
        st.markdown("#### 🚀 Background Task Manual Trigger")
        alert(t("cache.trigger_notice"), "info")

        c1, c2 = st.columns(2)

        with c1:
            st.markdown(f"**{t('cache.fee_related')}**")

            if st.button(t("cache.btn_bulk_voucher"), use_container_width=True, type="primary"):
                if celery_ok:
                    from utils import months_list, current_year
                    month = date.today().strftime("%B")
                    yr    = current_year()
                    from workers.tasks import generate_fee_vouchers_bulk
                    task = generate_fee_vouchers_bulk.delay(tid, month, yr)
                    st.success(f"✅ Task queued! ID: `{task.id}`")
                else:
                    alert(t("cache.celery_not_running"),"danger")

            if st.button(t("cache.btn_fee_reminder"), use_container_width=True):
                if celery_ok:
                    from workers.tasks import monthly_fee_reminder
                    task = monthly_fee_reminder.delay()
                    st.success(f"✅ Fee reminder queued! ID: `{task.id}`")
                else:
                    alert(t("cache.celery_not_running"),"danger")

            if st.button(t("cache.btn_due_alert"), use_container_width=True):
                if celery_ok:
                    from workers.tasks import weekly_due_alert
                    task = weekly_due_alert.delay()
                    st.success(f"✅ Due alert queued! ID: `{task.id}`")
                else:
                    alert(t("cache.celery_not_running"),"danger")

        with c2:
            st.markdown(f"**{t('cache.reports_cache')}**")

            if st.button(t("cache.btn_cache_warmup"), use_container_width=True, type="primary"):
                if celery_ok:
                    from workers.tasks import warmup_dashboard_cache
                    task = warmup_dashboard_cache.delay()
                    st.success(f"✅ Cache warmup queued! ID: `{task.id}`")
                else:
                    # Fallback: direct run
                    if cache_ok:
                        inval_tenant(tid)
                        st.success("✅ Cache cleared (direct)!")
                    else:
                        alert(t("cache.cache_or_celery_not_running"),"warning")

            if st.button(t("cache.btn_monthly_report"), use_container_width=True):
                if celery_ok:
                    from workers.tasks import generate_monthly_report
                    task = generate_monthly_report.delay()
                    st.success(f"✅ Report generation queued! ID: `{task.id}`")
                else:
                    alert(t("cache.celery_not_running"),"danger")

            if st.button(t("cache.btn_health_check"), use_container_width=True):
                if celery_ok:
                    from workers.tasks import system_health_check
                    task = system_health_check.apply()  # Sync for immediate result
                    result = task.result
                    if result.get("healthy"):
                        st.success(t("cache.system_healthy"))
                    else:
                        st.error(t("cache.system_issue", issues=result.get('issues')))
                    st.json(result)
                else:
                    alert(t("cache.celery_not_running"),"danger")

            # Exam result notification
            divider()
            st.markdown(f"**{t('cache.exam_result_notify')}**")
            exams = []
            try:
                from db import fetchall as db_fetchall
                exams = db_fetchall(
                    "SELECT id, exam_name FROM exams WHERE tenant_id=%s ORDER BY id DESC LIMIT 10",
                    (tid,),
                )
            except Exception:
                pass

            if exams:
                exam_map = {e["exam_name"]: e["id"] for e in exams}
                sel_exam = st.selectbox(t("cache.select_exam"), list(exam_map.keys()), key="notif_exam")
                if st.button(t("cache.btn_send_result_notify"), use_container_width=True):
                    if celery_ok:
                        from workers.tasks import send_exam_results_notification
                        task = send_exam_results_notification.delay(tid, exam_map[sel_exam])
                        st.success(f"✅ Notification queued! ID: `{task.id}`")
                    else:
                        alert(t("cache.celery_not_running"),"danger")

    # ── Scheduled Tasks ──
    with tab_schedule:
        st.markdown(f"#### {t('cache.scheduled_tasks_heading')}")

        col_time, col_desc = t("cache.col_time"), t("cache.col_desc")
        schedules = [
            {"Task": t("cache.sched_task1"), col_time: t("cache.sched_time1"), col_desc: t("cache.sched_desc1")},
            {"Task": t("cache.sched_task2"), col_time: t("cache.sched_time2"), col_desc: t("cache.sched_desc2")},
            {"Task": t("cache.sched_task3"),          col_time: t("cache.sched_time3"), col_desc: "overdue fee reminder"},
            {"Task": "🔥 Cache Warmup",          col_time: t("cache.sched_time4"), col_desc: "Dashboard cache pre-load"},
            {"Task": "📊 Monthly Report",         col_time: t("cache.sched_time5"), col_desc: t("cache.sched_desc5")},
            {"Task": "🏥 Health Check",           col_time: t("cache.sched_time6"), col_desc: t("cache.sched_desc6")},
        ]
        st.dataframe(schedules, use_container_width=True, hide_index=True)

        divider()
        st.markdown(f"**{t('cache.beat_start_cmd_heading')}**")
        st.code(
            t("cache.code_worker_scheduler"),
            language="bash"
        )
        st.markdown(f"**{t('cache.docker_compose_heading')}**")
        st.code(
            "docker-compose up -d celery_worker celery_beat",
            language="bash"
        )
