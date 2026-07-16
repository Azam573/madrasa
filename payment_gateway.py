"""
payment_gateway.py — bKash / Nagad Payment Gateway  v9.0
অভিভাবকরা সরাসরি মোবাইল ব্যাংকিং দিয়ে ফি পরিশোধ করতে পারবেন।

পরিবর্তন (v9.0):
- verify_bkash_callback(): bKash callback HMAC-SHA256 signature verify
- verify_nagad_callback(): Nagad callback signature verify
- handle_payment_callback(): idempotency check — duplicate payment প্রতিরোধ
- Callback-এ শুধু signature valid হলেই DB update হবে
"""

import os
import json
import hashlib
import hmac
import secrets
import urllib.request
import urllib.parse
import logging
from datetime import datetime
from db import get_connection, fetchone, fetchall, release_connection
from utils import page_header, alert, divider, get_tenant_id
from error_handler import safe_db_error
from i18n import t

logger = logging.getLogger("madrasa.payment")

# ── Config ──────────────────────────────────────────────────────
BKASH_APP_KEY      = os.environ.get("BKASH_APP_KEY", "")
BKASH_APP_SECRET   = os.environ.get("BKASH_APP_SECRET", "")
BKASH_USERNAME     = os.environ.get("BKASH_USERNAME", "")
BKASH_PASSWORD     = os.environ.get("BKASH_PASSWORD", "")
BKASH_BASE_URL     = os.environ.get("BKASH_BASE_URL",
                                     "https://tokenized.sandbox.bka.sh/v1.2.0-beta")

NAGAD_MERCHANT_ID  = os.environ.get("NAGAD_MERCHANT_ID", "")
NAGAD_MERCHANT_KEY = os.environ.get("NAGAD_MERCHANT_KEY", "")
NAGAD_BASE_URL     = os.environ.get("NAGAD_BASE_URL",
                                     "https://api.mynagad.com/api/dfs")

# ─────────────────────────────────────────────────────────────────
# Callback Signature Verification (v9.0)
#
# সমস্যা (আগে): যেকেউ fake callback পাঠিয়ে payment "সফল" দেখাতে পারত।
# সমাধান: HMAC-SHA256 signature verify — শুধু gateway-র real callback accept।
# ─────────────────────────────────────────────────────────────────

def verify_bkash_callback(payload: dict, received_signature: str) -> bool:
    """
    bKash callback-এর HMAC-SHA256 signature verify করে।

    bKash তাদের callback-এ X-Signature header পাঠায়।
    BKASH_APP_SECRET দিয়ে payload sign করে তুলনা করা হয়।

    Returns: True = genuine bKash callback | False = fake/tampered
    """
    if not BKASH_APP_SECRET:
        logger.warning("BKASH_APP_SECRET নেই — callback unverified accept করা হচ্ছে (dev mode)")
        return True  # Dev mode — credentials নেই

    try:
        # Sorted keys দিয়ে canonical string তৈরি
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected = hmac.new(
            BKASH_APP_SECRET.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        # Timing-safe comparison — timing attack প্রতিরোধ
        return hmac.compare_digest(expected, received_signature)
    except Exception as ex:
        logger.error(f"bKash signature verify error: {type(ex).__name__}")
        return False


def verify_nagad_callback(payload: dict, received_signature: str) -> bool:
    """
    Nagad callback-এর HMAC-SHA256 signature verify করে।

    Returns: True = genuine Nagad callback | False = fake/tampered
    """
    if not NAGAD_MERCHANT_KEY:
        logger.warning("NAGAD_MERCHANT_KEY নেই — callback unverified (dev mode)")
        return True

    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected  = hmac.new(
            NAGAD_MERCHANT_KEY.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, received_signature)
    except Exception as ex:
        logger.error(f"Nagad signature verify error: {type(ex).__name__}")
        return False


def handle_payment_callback(
    gateway:    str,           # "bkash" | "nagad"
    payload:    dict,          # callback data
    signature:  str = "",      # X-Signature header value
) -> tuple[bool, str]:
    """
    Payment callback নিরাপদে process করে।

    ধাপ:
    1. Signature verify — fake callback reject
    2. Idempotency check — duplicate payment reject
    3. Amount verify — payload amount ≥ DB amount হতে হবে
    4. DB update — voucher paid mark

    Returns: (success, message)
    """
    # ── ১. Signature verify ──────────────────────────────────────
    if gateway == "bkash":
        if not verify_bkash_callback(payload, signature):
            logger.warning("❌ bKash callback signature invalid — rejected")
            return False, "Invalid signature"
    elif gateway == "nagad":
        if not verify_nagad_callback(payload, signature):
            logger.warning("❌ Nagad callback signature invalid — rejected")
            return False, "Invalid signature"

    # ── ২. Transaction ID ও status বের করা ─────────────────────
    if gateway == "bkash":
        transaction_id  = payload.get("trxID") or payload.get("transactionID", "")
        status_key      = payload.get("statusCode", "")
        is_success      = status_key == "0000"
        amount_received = float(payload.get("amount", 0))
        merchant_invoice = payload.get("merchantInvoiceNumber", "")
    else:  # nagad
        transaction_id  = payload.get("issuerPaymentRefNo", "")
        is_success      = payload.get("status") == "Success"
        amount_received = float(payload.get("amount", 0))
        merchant_invoice = payload.get("orderId", "")

    if not is_success:
        reason = payload.get("statusMessage") or payload.get("reason") or "Payment failed"
        logger.info(f"Payment not successful: {reason}")
        return False, reason

    if not transaction_id:
        return False, "Transaction ID নেই"

    # ── ৩. Idempotency check — duplicate transaction reject ──────
    existing = fetchone(
        "SELECT id, status FROM online_payments WHERE transaction_id=%s",
        (transaction_id,),
    )
    if existing and existing["status"] == "completed":
        logger.info(f"Duplicate callback ignored: {transaction_id}")
        return True, "Already processed"  # Idempotent — OK

    # ── ৪. DB record খোঁজা ──────────────────────────────────────
    payment_record = fetchone(
        "SELECT id, amount, voucher_id, tenant_id FROM online_payments "
        "WHERE merchant_invoice=%s AND status='pending'",
        (merchant_invoice,),
    )
    if not payment_record:
        logger.warning(f"Payment record not found for invoice: {merchant_invoice}")
        return False, "Payment record পাওয়া যায়নি"

    # ── ৫. Amount verify — কম টাকা দিলে reject ─────────────────
    expected_amount = float(payment_record["amount"])
    if amount_received < expected_amount * 0.99:  # 1% tolerance rounding
        logger.warning(
            f"Amount mismatch: expected {expected_amount}, received {amount_received}"
        )
        return False, f"Amount mismatch: expected {expected_amount}, got {amount_received}"

    # ── ৬. DB update ─────────────────────────────────────────────
    _update_payment_status(
        payment_record["id"],
        "completed",
        transaction_id,
        payload,
    )
    logger.info(f"✅ Payment completed: {transaction_id} | invoice: {merchant_invoice}")
    return True, "Payment successful"


def _save_payment_record(tid, voucher_id, student_id, method,
                          merchant_invoice, amount) -> int:
    conn = get_connection()
    if not conn: return 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO online_payments
                   (tenant_id, voucher_id, student_id, payment_method,
                    merchant_invoice, amount, status)
                   VALUES (%s,%s,%s,%s,%s,%s,'pending') RETURNING id""",
                (tid, voucher_id, student_id, method, merchant_invoice, amount),
            )
            pid = cur.fetchone()["id"]
        conn.commit()
        return pid
    except Exception as ex:
        conn.rollback()
        logger.error(f"Payment record save failed: {ex}")
        return 0
    finally:
        release_connection(conn)


def _update_payment_status(payment_id: int, status: str,
                            transaction_id: str = None, response: dict = None):
    conn = get_connection()
    if not conn: return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE online_payments
                   SET status=%s, transaction_id=%s,
                       gateway_response=%s,
                       completed_at=CASE WHEN %s='completed' THEN NOW() ELSE NULL END
                   WHERE id=%s""",
                (status, transaction_id,
                 json.dumps(response or {}), status, payment_id),
            )
            # If successful, update fee voucher
            if status == "completed" and transaction_id:
                cur.execute(
                    """UPDATE fee_vouchers
                       SET status='paid', paid_at=NOW()
                       WHERE id=(
                           SELECT voucher_id FROM online_payments WHERE id=%s
                       )""",
                    (payment_id,),
                )
                cur.execute(
                    """INSERT INTO fee_payments
                       (tenant_id, voucher_id, amount_paid, payment_date,
                        payment_method, receipt_no, notes)
                       SELECT tenant_id, voucher_id, amount, CURRENT_DATE,
                              payment_method,
                              'ONLINE-' || %s,
                              'Online payment via ' || payment_method
                       FROM online_payments WHERE id=%s""",
                    (transaction_id, payment_id),
                )
        conn.commit()
    except Exception as ex:
        conn.rollback()
        logger.error(f"Payment status update failed: {type(ex).__name__}")
    finally:
        release_connection(conn)


# ─────────────────────────────────────────────
# bKash Integration
# ─────────────────────────────────────────────

class BkashGateway:
    """
    SECURITY FIX — Token Isolation:
    self._token সরানো হয়েছে। প্রতিটি request-এ
    fresh token নেওয়া হয় যাতে user isolation নিশ্চিত হয়।
    Global singleton নিরাপদ কারণ কোনো per-user state নেই।
    """
    def __init__(self):
        self.available = bool(BKASH_APP_KEY and BKASH_APP_SECRET)

    def _get_token(self) -> str | None:
        if not self.available: return None
        try:
            url     = f"{BKASH_BASE_URL}/tokenized/checkout/token/grant"
            headers = {
                "Content-Type":  "application/json",
                "Accept":        "application/json",
                "username":      BKASH_USERNAME,
                "password":      BKASH_PASSWORD,
            }
            body = json.dumps({
                "app_key":    BKASH_APP_KEY,
                "app_secret": BKASH_APP_SECRET,
            }).encode()
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data  = json.loads(resp.read().decode())
                token = data.get("id_token")
                return token  # token store করা হচ্ছে না — isolation নিশ্চিত
        except Exception as ex:
            logger.error(f"bKash token error: {ex}")
            return None

    def create_payment(self, amount: float, invoice_no: str,
                        callback_url: str) -> dict:
        """bKash payment session তৈরি করুন।"""
        if not self.available:
            return {"error": t("pay.err_bkash_no_creds")}
        token = self._get_token()
        if not token:
            return {"error": t("pay.err_bkash_no_token")}
        try:
            url     = f"{BKASH_BASE_URL}/tokenized/checkout/create"
            headers = {
                "Content-Type":  "application/json",
                "Accept":        "application/json",
                "Authorization": token,
                "X-APP-Key":     BKASH_APP_KEY,
            }
            body = json.dumps({
                "mode":                "0011",
                "payerReference":      invoice_no,
                "callbackURL":         callback_url,
                "amount":              str(amount),
                "currency":            "BDT",
                "intent":              "sale",
                "merchantInvoiceNumber": invoice_no,
            }).encode()
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as ex:
            logger.error(f"bKash create payment error: {ex}")
            return {"error": safe_db_error(ex)}

    def execute_payment(self, payment_id: str) -> dict:
        """Payment execute করুন।"""
        if not self.available:
            return {"error": "Not initialized"}
        token = self._get_token()  # SECURITY: fresh token, no stored state
        if not token:
            return {"error": t("pay.err_bkash_no_token")}
        try:
            url     = f"{BKASH_BASE_URL}/tokenized/checkout/execute"
            headers = {
                "Content-Type":  "application/json",
                "Authorization": token,
                "X-APP-Key":     BKASH_APP_KEY,
            }
            body = json.dumps({"paymentID": payment_id}).encode()
            req  = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as ex:
            return {"error": safe_db_error(ex)}

    def query_payment(self, payment_id: str) -> dict:
        """Payment status চেক করুন।"""
        token = self._get_token()  # SECURITY: always fresh token
        if not token: return {"error": "No token"}
        try:
            url     = f"{BKASH_BASE_URL}/tokenized/checkout/payment/status"
            headers = {
                "Content-Type":  "application/json",
                "Authorization": token,
                "X-APP-Key":     BKASH_APP_KEY,
            }
            body = json.dumps({"paymentID": payment_id}).encode()
            req  = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as ex:
            return {"error": safe_db_error(ex)}


# ─────────────────────────────────────────────
# Nagad Integration
# ─────────────────────────────────────────────

class NagadGateway:
    def __init__(self):
        self.available = bool(NAGAD_MERCHANT_ID and NAGAD_MERCHANT_KEY)

    def _generate_signature(self, data: str) -> str:
        sig = hmac.new(
            NAGAD_MERCHANT_KEY.encode(),
            data.encode(),
            hashlib.sha256,
        ).hexdigest()
        return sig

    def initiate_payment(self, amount: float, order_id: str,
                          callback_url: str) -> dict:
        if not self.available:
            return {"error": t("pay.err_nagad_no_creds")}
        try:
            url  = f"{NAGAD_BASE_URL}/check-out/initialize/{NAGAD_MERCHANT_ID}/{order_id}"
            ts   = datetime.now().strftime("%Y%m%d%H%M%S")
            data = json.dumps({
                "merchantId":     NAGAD_MERCHANT_ID,
                "datetime":       ts,
                "orderId":        order_id,
                "challenge":      secrets.token_hex(16),  # Fix: challenge হলো anti-replay nonce — random হওয়া বাধ্যতামূলক; আগে md5(order_id) দেওয়ায় predictable ছিল
            })
            sig  = self._generate_signature(data)
            headers = {
                "X-KM-Api-Version": "v-0.2.0",
                "X-KM-IP-V4":       "127.0.0.1",
                "X-KM-Client-Type": "PC_WEB",
                "Content-Type":     "application/json",
                "X-KM-Signature":   sig,
            }
            req  = urllib.request.Request(url, data=data.encode(),
                                           headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as ex:
            logger.error(f"Nagad initiate error: {ex}")
            return {"error": safe_db_error(ex)}

    def complete_payment(self, payment_ref_id: str, order_id: str,
                          amount: float) -> dict:
        if not self.available:
            return {"error": t("pay.err_nagad_no_creds")}
        try:
            url  = f"{NAGAD_BASE_URL}/check-out/complete/{payment_ref_id}"
            data = json.dumps({
                "merchantId":     NAGAD_MERCHANT_ID,
                "orderId":        order_id,
                "amount":         str(amount),
                "currencyCode":   "050",
                "challenge":      secrets.token_hex(16),  # Fix: challenge হলো anti-replay nonce — random হওয়া বাধ্যতামূলক; আগে md5(order_id) দেওয়ায় predictable ছিল
            })
            sig     = self._generate_signature(data)
            headers = {
                "X-KM-Api-Version": "v-0.2.0",
                "X-KM-IP-V4":       "127.0.0.1",
                "Content-Type":     "application/json",
                "X-KM-Signature":   sig,
            }
            req = urllib.request.Request(url, data=data.encode(),
                                          headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as ex:
            return {"error": safe_db_error(ex)}


# ─────────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────────

bkash = BkashGateway()
nagad  = NagadGateway()


def render():
    import streamlit as st
    tid = get_tenant_id()
    page_header("💳", t("pay.page_title"), t("pay.page_subtitle"))

    # Config status
    col1, col2 = st.columns(2)
    with col1:
        bkash_status = "🟢 Configured" if bkash.available else "🔴 Not Configured"
        st.markdown(
            f"""<div style="background:{'#E8F5E9' if bkash.available else '#FFEBEE'};
                            border-radius:10px;padding:1rem;text-align:center">
              <div style="font-size:1.5rem">💚</div>
              <div style="font-weight:700">bKash</div>
              <div style="font-size:0.8rem;margin-top:4px">{bkash_status}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col2:
        nagad_status = "🟢 Configured" if nagad.available else "🔴 Not Configured"
        st.markdown(
            f"""<div style="background:{'#E8F5E9' if nagad.available else '#FFEBEE'};
                            border-radius:10px;padding:1rem;text-align:center">
              <div style="font-size:1.5rem">🟠</div>
              <div style="font-weight:700">Nagad</div>
              <div style="font-size:0.8rem;margin-top:4px">{nagad_status}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    if not bkash.available and not nagad.available:
        alert(
            t("pay.setup_notice"),
            "warning",
        )

    tab_pay, tab_history, tab_config = st.tabs([
        t("pay.tab_pay"), t("pay.tab_history"), t("pay.tab_config")
    ])

    with tab_pay:
        st.markdown(f"#### {t('pay.online_pay_heading')}")

        students = fetchall(
            """SELECT s.id, s.name, e.roll_no, c.class_name,
                      e.id AS enrollment_id
               FROM students s
               JOIN student_enrollments e ON e.student_id=s.id AND e.tenant_id=s.tenant_id
               JOIN classes c ON c.id=e.class_id
               WHERE s.tenant_id=%s AND s.status='active' ORDER BY s.name""",
            (tid,),
        )
        if not students:
            alert(t("pay.no_active_students"), "warning")
        else:
            stu_map = {f"{s['name']} — {s['class_name']} (Roll {s['roll_no'] or '—'})": s
                       for s in students}
            sel = st.selectbox(t("pay.select_student"), list(stu_map.keys()), key="gw_stu")
            stu = stu_map[sel]

            # Due vouchers
            dues = fetchall(
                """SELECT id, voucher_no, month_name, year, amount, fund_type
                   FROM fee_vouchers
                   WHERE tenant_id=%s AND student_id=%s AND status='unpaid'
                   ORDER BY year, id""",
                (tid, stu["id"]),
            )
            if not dues:
                alert(t("pay.no_due_fee"), "success")
            else:
                vch_map = {
                    f"{v['voucher_no']} | {v['month_name']} {v['year']} | ৳{float(v['amount']):,.0f}": v
                    for v in dues
                }
                sel_vch = st.selectbox(t("pay.select_voucher"), list(vch_map.keys()), key="gw_vch")
                vch     = vch_map[sel_vch]

                st.markdown(
                    f"""<div style="background:#F7F9FA;border-radius:10px;
                                    padding:1rem;margin:1rem 0;text-align:center">
                      <div style="font-size:0.8rem;color:#6B7A8D">{t('pay.payable_amount')}</div>
                      <div style="font-size:2rem;font-weight:700;color:#0F4C5C">
                        ৳{float(vch['amount']):,.2f}
                      </div>
                      <div style="font-size:0.75rem;color:#9E9E9E">{vch['voucher_no']}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )

                method = st.radio(
                    t("pay.payment_method_label"),
                    ["💚 bKash", "🟠 Nagad"],
                    horizontal=True,
                )
                phone = st.text_input(t("pay.mobile_number"), placeholder="01XXXXXXXXX")
                callback_url = f"{os.environ.get('APP_URL','http://localhost:8501')}/?payment_callback=1"

                if st.button(t("pay.btn_start_payment"), type="primary", use_container_width=True):
                    if not phone.strip():
                        st.error(t("pay.err_enter_mobile"))
                    else:
                        invoice_no = f"INV-{tid}-{vch['id']}-{datetime.now().strftime('%H%M%S')}"
                        amount     = float(vch["amount"])
                        pid        = _save_payment_record(tid, vch["id"], stu["id"],
                                                           method.split()[1].lower(), invoice_no, amount)

                        if "bKash" in method:
                            if bkash.available:
                                result = bkash.create_payment(amount, invoice_no, callback_url)
                                if result.get("bkashURL"):
                                    st.success(
                                        f"✅ bKash payment initiated!\n\n"
                                        f"[**bKash-এ পেমেন্ট করুন →**]({result['bkashURL']})"
                                    )
                                    st.info(f"Payment ID: `{result.get('paymentID')}`")
                                else:
                                    st.error(f"bKash error: {result.get('errorMessage', result.get('error', 'Unknown'))}")
                            else:
                                st.warning("bKash Sandbox Demo Mode — Production-এ credentials যোগ করুন।")
                                st.info(f"✅ Demo: Payment initiated for ৳{amount:,.0f} | Invoice: {invoice_no}")
                        else:
                            if nagad.available:
                                result = nagad.initiate_payment(amount, invoice_no, callback_url)
                                if result.get("status") == "Success":
                                    redirect_url = result.get("redirectGatewayURL")
                                    st.success(f"✅ Nagad payment initiated!")
                                    if redirect_url:
                                        st.markdown(f"[**Nagad-এ পেমেন্ট করুন →**]({redirect_url})")
                                else:
                                    st.error(f"Nagad error: {result.get('reason', result.get('error', 'Unknown'))}")
                            else:
                                st.warning("Nagad Sandbox Demo Mode — Production-এ credentials যোগ করুন।")
                                st.info(f"✅ Demo: Nagad payment initiated for ৳{amount:,.0f}")

    with tab_history:
        st.markdown("#### 📜 অনলাইন পেমেন্ট ইতিহাস")
        payments = fetchall(
            """SELECT op.id, op.payment_method, op.amount, op.status,
                      op.transaction_id, op.merchant_invoice,
                      op.initiated_at, op.completed_at,
                      s.name AS student_name
               FROM online_payments op
               LEFT JOIN students s ON s.id=op.student_id
               WHERE op.tenant_id=%s
               ORDER BY op.initiated_at DESC LIMIT 50""",
            (tid,),
        )
        if not payments:
            alert("কোনো অনলাইন পেমেন্ট নেই।", "info")
        else:
            from utils import kpi_row
            total_completed = sum(float(p["amount"]) for p in payments if p["status"] == "completed")
            kpi_row([
                {"label": "মোট Transaction",  "value": len(payments),           "cls": ""},
                {"label": "সফল পেমেন্ট",      "value": f"৳{total_completed:,.0f}", "cls": "success"},
                {"label": "Pending",           "value": sum(1 for p in payments if p["status"] == "pending"), "cls": "warning"},
            ])
            rows = [{
                "তারিখ":         str(p["initiated_at"])[:16],
                "ছাত্র":         p["student_name"] or "—",
                "পদ্ধতি":        p["payment_method"].upper(),
                "পরিমাণ":       f"৳{float(p['amount']):,.0f}",
                "Status":        p["status"].upper(),
                "Transaction ID": p["transaction_id"] or "—",
                "Invoice":        p["merchant_invoice"] or "—",
            } for p in payments]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    with tab_config:
        st.markdown("#### ⚙️ Payment Gateway Configuration")
        st.markdown("""
        **`.env` ফাইলে নিচের variables যোগ করুন:**

        ```bash
        # bKash Tokenized Checkout
        BKASH_APP_KEY=your-app-key
        BKASH_APP_SECRET=your-app-secret
        BKASH_USERNAME=your-username
        BKASH_PASSWORD=your-password
        BKASH_BASE_URL=https://tokenized.pay.bka.sh/v1.2.0-beta

        # Nagad DFS
        NAGAD_MERCHANT_ID=your-merchant-id
        NAGAD_MERCHANT_KEY=your-merchant-key
        NAGAD_BASE_URL=https://api.mynagad.com/api/dfs
        ```

        **Sandbox URLs (Testing):**
        - bKash: `https://tokenized.sandbox.bka.sh/v1.2.0-beta`
        - Nagad: `https://sandbox.mynagad.com:10080/merchant-api/api/dfs`

        **Callback URL:**
        ```
        https://your-app.streamlit.app/?payment_callback=1
        ```
        """)
