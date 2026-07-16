# -*- coding: utf-8 -*-
"""
branding.py — প্রতিষ্ঠানভিত্তিক branding-এর shared core

Streamlit UI ও FastAPI — দুই জায়গা থেকেই ব্যবহারযোগ্য, তাই এখানে কোনো
streamlit বা fastapi import নেই। DB access-এর জন্য caller একটি cursor
পাস করে (Streamlit db.py-র connection, API get_db()-র cursor — যেকোনোটা)।

দেয়:
    get_branding(cur, tid)        — tenants + tenant_branding merge করা dict
    upsert_branding(cur, tid, d)  — whitelisted field আপডেট
    set_image(cur, tid, kind, b64, mime) — logo/signature (validation সহ)
    validate_image_b64(b64)       — magic-byte sniff + size limit
    brand_header_html(brand)      — সব printable document-এর কমন হেডার
    document_css(brand)           — print-ready CSS (A4, @media print)
    receipt_html(...)             — ফি রিসিট (branded, printable)
    tc_html(...)                  — ছাড়পত্র (branded, printable)
"""
import base64
import html as _html
import re
from datetime import date

# ── Limits & whitelists ──────────────────────────────────────────
MAX_IMAGE_BYTES = 200 * 1024   # 200KB — DB-তে রাখার জন্য যথেষ্ট ও নিরাপদ

_EDITABLE_FIELDS = {
    "name_arabic", "name_english", "tagline", "established_year",
    "eiin_no", "reg_no", "principal_name", "principal_title",
    "primary_color", "secondary_color", "receipt_footer", "tc_footer",
    "show_logo",
}

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

_DEFAULTS = {
    "name_arabic": None, "name_english": None, "tagline": None,
    "established_year": None, "eiin_no": None, "reg_no": None,
    "principal_name": None, "principal_title": "মুহতামিম",
    "logo_base64": None, "logo_mime": None,
    "signature_base64": None, "signature_mime": None,
    "primary_color": "#0F4C5C", "secondary_color": "#C9A227",
    "receipt_footer": "এই রিসিটটি সংগ্রহে রাখুন।", "tc_footer": None,
    "show_logo": True,
}


def _esc(v) -> str:
    """XSS guard — branding text ব্যবহারকারীর input, HTML-এ বসার আগে escape।"""
    return _html.escape(str(v)) if v is not None else ""


# ── Image validation ─────────────────────────────────────────────

def validate_image_b64(b64_data: str) -> tuple[bool, str, str]:
    """
    Returns (ok, mime, error)।
    Magic bytes দিয়ে সত্যিকারের ফাইল-টাইপ sniff করা হয় — শুধু PNG/JPEG।
    Content-Type header বা extension বিশ্বাস করা হয় না (spoof-able)।
    """
    try:
        raw = base64.b64decode(b64_data, validate=True)
    except Exception:
        return False, "", "Invalid base64 data।"
    if len(raw) > MAX_IMAGE_BYTES:
        return False, "", f"ছবি {MAX_IMAGE_BYTES // 1024}KB-এর বেশি হতে পারবে না।"
    if len(raw) < 8:
        return False, "", "ফাইলটি ছবি নয়।"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return True, "image/png", ""
    if raw[:3] == b"\xff\xd8\xff":
        return True, "image/jpeg", ""
    return False, "", "শুধু PNG বা JPEG ছবি গ্রহণযোগ্য।"


# ── DB access (cursor-agnostic) ──────────────────────────────────

def get_branding(cur, tid: int) -> dict:
    """tenants + tenant_branding merge — সবসময় সম্পূর্ণ dict ফেরত দেয়।"""
    cur.execute(
        "SELECT madrasa_name, address, phone, email FROM tenants WHERE id=%s",
        (tid,),
    )
    tenant = cur.fetchone()
    brand = dict(_DEFAULTS)
    brand.update({
        "madrasa_name": (tenant or {}).get("madrasa_name") or "Smart Madrasa",
        "address":      (tenant or {}).get("address") or "",
        "phone":        (tenant or {}).get("phone") or "",
        "email":        (tenant or {}).get("email") or "",
    })
    cur.execute("SELECT * FROM tenant_branding WHERE tenant_id=%s", (tid,))
    row = cur.fetchone()
    if row:
        for k, v in dict(row).items():
            if k != "tenant_id" and v is not None:
                brand[k] = v
    return brand


def upsert_branding(cur, tid: int, fields: dict) -> list[str]:
    """
    Whitelisted field গুলোই কেবল আপডেট হয়। রঙ hex-validate হয়।
    Returns: আপডেট হওয়া field-এর তালিকা।
    """
    clean: dict = {}
    for k, v in fields.items():
        if k not in _EDITABLE_FIELDS or v is None:
            continue
        if k in ("primary_color", "secondary_color"):
            if not _HEX_COLOR.match(str(v)):
                raise ValueError(f"{k} অবশ্যই #RRGGBB ফরম্যাটে হতে হবে।")
        if k == "established_year":
            v = int(v)
            if not (1000 <= v <= date.today().year):
                raise ValueError("established_year সঠিক নয়।")
        clean[k] = v
    if not clean:
        return []

    cols    = list(clean.keys())
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
    cur.execute(
        f"""INSERT INTO tenant_branding (tenant_id, {', '.join(cols)}, updated_at)
            VALUES (%s, {', '.join(['%s'] * len(cols))}, NOW())
            ON CONFLICT (tenant_id)
            DO UPDATE SET {updates}, updated_at=NOW()""",
        (tid, *clean.values()),
    )
    return cols


def set_image(cur, tid: int, kind: str, b64_data: str) -> str:
    """kind: 'logo' | 'signature'। Returns detected mime। Invalid হলে ValueError।"""
    if kind not in ("logo", "signature"):
        raise ValueError("kind অবশ্যই logo বা signature।")
    ok, mime, err = validate_image_b64(b64_data)
    if not ok:
        raise ValueError(err)
    cur.execute(
        f"""INSERT INTO tenant_branding (tenant_id, {kind}_base64, {kind}_mime, updated_at)
            VALUES (%s,%s,%s,NOW())
            ON CONFLICT (tenant_id)
            DO UPDATE SET {kind}_base64=EXCLUDED.{kind}_base64,
                          {kind}_mime=EXCLUDED.{kind}_mime, updated_at=NOW()""",
        (tid, b64_data, mime),
    )
    return mime


# ── HTML builders ────────────────────────────────────────────────

def brand_header_html(brand: dict) -> str:
    """
    সব printable document-এর কমন হেডার: লোগো (থাকলে), আরবি নাম,
    মূল নাম, ঠিকানা, EIIN/রেজি নম্বর।
    """
    p = _esc(brand["primary_color"])
    logo = ""
    if brand.get("show_logo") and brand.get("logo_base64"):
        logo = (
            f'<img src="data:{_esc(brand["logo_mime"] or "image/png")};'
            f'base64,{brand["logo_base64"]}" '
            f'style="height:64px;max-width:90px;object-fit:contain" alt="logo">'
        )
    else:
        logo = f'<div style="font-size:40px">🕌</div>'

    arabic = (
        f'<div style="font-size:15px;color:{p};font-weight:600" dir="rtl">'
        f'{_esc(brand["name_arabic"])}</div>'
        if brand.get("name_arabic") else ""
    )
    english = (
        f'<div style="font-size:11px;color:#555;letter-spacing:1px">'
        f'{_esc(brand["name_english"])}</div>'
        if brand.get("name_english") else ""
    )
    tagline = (
        f'<div style="font-size:10px;color:#777;font-style:italic">'
        f'{_esc(brand["tagline"])}</div>'
        if brand.get("tagline") else ""
    )
    regline_parts = []
    if brand.get("established_year"):
        regline_parts.append(f"স্থাপিত: {_esc(brand['established_year'])}")
    if brand.get("eiin_no"):
        regline_parts.append(f"EIIN: {_esc(brand['eiin_no'])}")
    if brand.get("reg_no"):
        regline_parts.append(f"রেজি নং: {_esc(brand['reg_no'])}")
    regline = (
        f'<div style="font-size:10px;color:#777">{" | ".join(regline_parts)}</div>'
        if regline_parts else ""
    )

    return f"""
    <table style="width:100%;border:none;border-collapse:collapse;margin-bottom:6px">
      <tr>
        <td style="width:100px;border:none;vertical-align:middle">{logo}</td>
        <td style="border:none;text-align:center;vertical-align:middle">
          {arabic}
          <div style="font-size:19px;font-weight:800;color:{p}">
            {_esc(brand["madrasa_name"])}
          </div>
          {english}{tagline}
          <div style="font-size:11px;color:#555">
            {_esc(brand["address"])}{" | ☎ " + _esc(brand["phone"]) if brand.get("phone") else ""}
          </div>
          {regline}
        </td>
        <td style="width:100px;border:none"></td>
      </tr>
    </table>
    <div style="border-top:3px double {p};margin:4px 0 10px 0"></div>
    """


def _signature_block(brand: dict) -> str:
    sig = ""
    if brand.get("signature_base64"):
        sig = (
            f'<img src="data:{_esc(brand["signature_mime"] or "image/png")};'
            f'base64,{brand["signature_base64"]}" '
            f'style="height:42px;object-fit:contain"><br>'
        )
    return f"""
    <table style="width:100%;border:none;margin-top:38px">
      <tr>
        <td style="border:none;width:60%"></td>
        <td style="border:none;text-align:center">
          {sig}
          <div style="border-top:1px solid #333;padding-top:4px;font-size:12px">
            <strong>{_esc(brand.get("principal_name") or "")}</strong><br>
            {_esc(brand.get("principal_title") or "মুহতামিম")},
            {_esc(brand["madrasa_name"])}
          </div>
        </td>
      </tr>
    </table>
    """


def document_css(brand: dict) -> str:
    p = _esc(brand["primary_color"])
    return f"""
    <style>
      * {{ box-sizing: border-box; font-family: 'Noto Sans Bengali','SolaimanLipi',
           'Segoe UI', sans-serif; }}
      body {{ margin: 0; padding: 24px; background: #F2F2F2; }}
      .doc {{ background: white; max-width: 800px; margin: 0 auto;
              padding: 28px 34px; border: 1px solid #CCC; border-radius: 6px; }}
      table {{ border-collapse: collapse; width: 100%; }}
      td, th {{ border: 1px solid #DDD; padding: 6px 10px; font-size: 12px; }}
      .accent {{ color: {p}; }}
      .print-btn {{ position: fixed; top: 14px; right: 14px; background: {p};
                    color: white; border: none; padding: 10px 22px; font-size: 14px;
                    border-radius: 6px; cursor: pointer; }}
      @media print {{
        body {{ background: white; padding: 0; }}
        .doc {{ border: none; max-width: none; padding: 10mm; }}
        .print-btn {{ display: none; }}
        @page {{ size: A4; margin: 12mm; }}
      }}
    </style>
    """


def receipt_html(brand: dict, student: dict, payment: dict, voucher: dict) -> str:
    """Branded ফি রিসিট — সম্পূর্ণ printable HTML পেজ।"""
    p = _esc(brand["primary_color"])
    s = _esc(brand["secondary_color"])
    return f"""<!DOCTYPE html>
<html lang="bn"><head><meta charset="utf-8">
<title>Money Receipt — {_esc(payment.get("receipt_no"))}</title>
{document_css(brand)}</head><body>
<button class="print-btn" onclick="window.print()">🖨 প্রিন্ট করুন</button>
<div class="doc">
  {brand_header_html(brand)}
  <div style="text-align:center;margin-bottom:14px">
    <span style="background:{p};color:white;padding:5px 24px;border-radius:20px;
                 font-size:14px;font-weight:700">টাকা প্রাপ্তির রসিদ</span>
  </div>
  <table style="border:none;margin-bottom:10px">
    <tr>
      <td style="border:none;font-size:12px">
        রিসিট নং: <strong class="accent">{_esc(payment.get("receipt_no"))}</strong></td>
      <td style="border:none;text-align:right;font-size:12px">
        তারিখ: <strong>{_esc(payment.get("payment_date"))}</strong></td>
    </tr>
  </table>
  <table>
    <tr><td style="width:35%;font-weight:600;background:#FAFAFA">ছাত্রের নাম</td>
        <td>{_esc(student.get("name"))}</td></tr>
    <tr><td style="font-weight:600;background:#FAFAFA">পিতার নাম</td>
        <td>{_esc(student.get("father_name") or "—")}</td></tr>
    <tr><td style="font-weight:600;background:#FAFAFA">শ্রেণী / রোল</td>
        <td>{_esc(student.get("class_name") or "—")} / {_esc(student.get("roll_no") or "—")}</td></tr>
    <tr><td style="font-weight:600;background:#FAFAFA">বাবদ</td>
        <td>{_esc(voucher.get("month_name"))} {_esc(voucher.get("year"))} —
            ভাউচার {_esc(voucher.get("voucher_no"))}</td></tr>
    <tr><td style="font-weight:600;background:#FAFAFA">পরিশোধ মাধ্যম</td>
        <td>{_esc(payment.get("payment_method") or "নগদ")}</td></tr>
    <tr><td style="font-weight:600;background:{s};color:white">প্রাপ্ত টাকা</td>
        <td style="font-size:16px;font-weight:800" class="accent">
          ৳ {float(payment.get("amount_paid") or 0):,.2f}</td></tr>
  </table>
  {_signature_block(brand)}
  <div style="text-align:center;font-size:10px;color:#888;margin-top:16px;
              border-top:1px dashed #CCC;padding-top:8px">
    {_esc(brand.get("receipt_footer") or "")}
  </div>
</div></body></html>"""


def tc_html(brand: dict, student: dict, tc: dict) -> str:
    """Branded ছাড়পত্র (Transfer Certificate) — printable HTML পেজ।"""
    p = _esc(brand["primary_color"])
    footer = (
        f'<div style="text-align:center;font-size:10px;color:#888;margin-top:14px;'
        f'border-top:1px dashed #CCC;padding-top:8px">{_esc(brand["tc_footer"])}</div>'
        if brand.get("tc_footer") else ""
    )
    return f"""<!DOCTYPE html>
<html lang="bn"><head><meta charset="utf-8">
<title>Transfer Certificate — {_esc(tc.get("tc_number"))}</title>
{document_css(brand)}</head><body>
<button class="print-btn" onclick="window.print()">🖨 প্রিন্ট করুন</button>
<div class="doc">
  {brand_header_html(brand)}
  <div style="text-align:center;margin-bottom:14px">
    <span style="border:2px solid {p};color:{p};padding:5px 26px;border-radius:6px;
                 font-size:15px;font-weight:800;letter-spacing:1px">
      ছাড়পত্র (Transfer Certificate)</span>
  </div>
  <table style="border:none;margin-bottom:12px">
    <tr>
      <td style="border:none;font-size:12px">
        টিসি নং: <strong class="accent">{_esc(tc.get("tc_number"))}</strong></td>
      <td style="border:none;text-align:right;font-size:12px">
        ইস্যু তারিখ: <strong>{_esc(tc.get("issue_date"))}</strong></td>
    </tr>
  </table>
  <p style="font-size:13px;line-height:2;text-align:justify">
    এই মর্মে প্রত্যয়ন করা যাচ্ছে যে, <strong>{_esc(student.get("name"))}</strong>,
    পিতা: <strong>{_esc(student.get("father_name") or "—")}</strong>,
    অত্র প্রতিষ্ঠানের <strong>{_esc(tc.get("last_class") or "—")}</strong> শ্রেণীর
    ({_esc(tc.get("last_session") or "—")} শিক্ষাবর্ষ) একজন নিয়মিত ছাত্র ছিল।
    প্রতিষ্ঠানে অধ্যয়নকালে তার আচরণ ছিল
    <strong>{_esc(tc.get("conduct") or "ভালো")}</strong>
    {"এবং উপস্থিতির হার ছিল <strong>" + _esc(tc.get("attendance_pct")) + "%</strong>"
     if tc.get("attendance_pct") else ""}।
    তার নিকট প্রতিষ্ঠানের কোনো পাওনা নেই।
  </p>
  <table style="margin-top:6px">
    <tr><td style="width:35%;font-weight:600;background:#FAFAFA">ছাড়পত্রের কারণ</td>
        <td>{_esc(tc.get("reason") or "—")}</td></tr>
    <tr><td style="font-weight:600;background:#FAFAFA">মন্তব্য</td>
        <td>{_esc(tc.get("remarks") or "—")}</td></tr>
  </table>
  <p style="font-size:12px;margin-top:14px">
    আমরা তার ভবিষ্যৎ জীবনের সর্বাঙ্গীণ সাফল্য কামনা করি।</p>
  {_signature_block(brand)}
  {footer}
</div></body></html>"""
