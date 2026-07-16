"""
api/core/audit.py — API endpoint-এর audit trail

Fix (brutal review #5): টাকা ও certificate-সংক্রান্ত কাজে "কে, কখন, কী
করল" — এতদিন শুধু Streamlit-এ ছিল, API-তে ছিল না। এই helper Streamlit-এর
audit_module-এর একই audit_logs টেবিলে লেখে, তাই দুই জগতের trail এক
জায়গায় দেখা যায়।

ব্যবহার (endpoint-এর বিদ্যমান transaction-এর cursor দিয়ে — আলাদা
connection নয়, যাতে মূল কাজ rollback হলে audit-ও হয়):

    audit(cur, tid, current_user, "CREATE", "Zakat",
          f"যাকাত আদায়: ৳{amount} | receipt={receipt_no}")
"""
import logging

logger = logging.getLogger("madrasa_api.audit")


def audit(cur, tenant_id: int, user: dict | None,
          action: str, module: str, description: str,
          record_id: int | None = None) -> None:
    """
    Best-effort audit insert। ব্যর্থ হলে মূল operation আটকায় না —
    কিন্তু error log হয় যাতে নীরবে হারিয়ে না যায়।
    """
    try:
        cur.execute(
            """INSERT INTO audit_logs
               (tenant_id, user_id, username, action, module,
                record_id, description)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                tenant_id,
                (user or {}).get("user_id"),
                (user or {}).get("username") or (user or {}).get("full_name") or "system",
                action,
                module,
                record_id,
                description[:1000],
            ),
        )
    except Exception as ex:   # noqa: BLE001 — audit ব্যর্থতা মূল কাজ ভাঙবে না
        logger.error(f"Audit write failed [{module}/{action}]: {ex}")
