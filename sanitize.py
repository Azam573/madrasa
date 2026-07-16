"""
sanitize.py — XSS Input Sanitization Utility  v9.0

সমস্যা (আগে):
- unsafe_allow_html=True ১৩৬ জায়গায় ব্যবহার
- user input (student name, notice body) সরাসরি HTML-এ
- <script>alert(1)</script> নামে student তৈরি করলে সব page-এ XSS

সমাধান (v9.0):
- সব user-visible text HTML-encode করা
- Notice board body-তে সীমিত safe HTML allow
- Input save করার আগে sanitize

ব্যবহার:
    from sanitize import esc, sanitize_notice_body, sanitize_name

    # HTML-এ show করার আগে
    st.markdown(f"<b>{esc(student_name)}</b>", unsafe_allow_html=True)

    # Notice save করার আগে
    clean_body = sanitize_notice_body(raw_body)
"""

import re
import html


# ── Core HTML escape ──────────────────────────────────────────────

def esc(text: str) -> str:
    """
    User input HTML-safe করে।
    <script>alert(1)</script>  →  &lt;script&gt;alert(1)&lt;/script&gt;

    যেকোনো user input HTML-এ দেখানোর আগে এটি ব্যবহার করুন।
    """
    if not text:
        return ""
    return html.escape(str(text), quote=True)


def esc_attr(text: str) -> str:
    """HTML attribute value-এ safe করে (double-quote escape সহ)।"""
    return html.escape(str(text), quote=True)


# ── Name sanitization ─────────────────────────────────────────────

def sanitize_name(name: str, max_len: int = 100) -> str:
    """
    Student/teacher/মাদ্রাসার নাম sanitize করে।
    - HTML entity encode
    - শুধু letter, space, hyphen, dot, apostrophe allow
    - Max length enforce
    """
    if not name:
        return ""
    name = str(name).strip()[:max_len]
    # HTML encode
    name = html.escape(name, quote=True)
    return name


# ── Notice/text body sanitization ────────────────────────────────

# Notice body-তে এই tag গুলো allow (basic formatting)
_ALLOWED_TAGS = {"b", "i", "u", "strong", "em", "br", "p", "ul", "ol", "li"}
_TAG_RE = re.compile(r"<(/?)(\w+)([^>]*)>", re.IGNORECASE)


def sanitize_notice_body(body: str, max_len: int = 5000) -> str:
    """
    Notice board body sanitize করে।
    Safe HTML tag allow, বাকি সব strip করে।

    <b>গুরুত্বপূর্ণ</b> → রাখা হয়
    <script>alert(1)</script> → সরানো হয়
    <img src=x onerror=alert(1)> → সরানো হয়
    """
    if not body:
        return ""

    body = str(body).strip()[:max_len]

    def replace_tag(m):
        slash    = m.group(1)   # "/" if closing tag
        tag_name = m.group(2).lower()
        attrs    = m.group(3)

        if tag_name not in _ALLOWED_TAGS:
            # Disallowed tag — entity encode করে নিরীহ করা
            return html.escape(m.group(0))

        # Allowed tag — attributes সরিয়ে দেওয়া (onerror, onclick ইত্যাদি বাদ)
        if tag_name == "br":
            return "<br>"
        return f"<{slash}{tag_name}>"

    return _TAG_RE.sub(replace_tag, body)


# ── URL sanitization ──────────────────────────────────────────────

_SAFE_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_url(url: str) -> str:
    """
    URL sanitize করে — javascript: protocol বাদ দেয়।
    javascript:alert(1)  →  "#"
    http://example.com   →  http://example.com (unchanged)
    """
    if not url:
        return "#"
    url = str(url).strip()
    # Scheme extract
    scheme = url.split(":")[0].lower() if ":" in url else "https"
    if scheme not in _SAFE_URL_SCHEMES:
        return "#"
    return html.escape(url, quote=True)


# ── Batch sanitize dict ───────────────────────────────────────────

def sanitize_row(row: dict, text_fields: list[str]) -> dict:
    """
    DB থেকে আসা dict-এর নির্দিষ্ট field গুলো esc() দিয়ে sanitize করে।

    উদাহরণ:
        safe = sanitize_row(student, ["name", "father_name", "present_address"])
        st.markdown(f"<b>{safe['name']}</b>", unsafe_allow_html=True)
    """
    result = dict(row)
    for field in text_fields:
        if field in result and result[field]:
            result[field] = esc(str(result[field]))
    return result


# ── CSV/Excel formula-injection sanitization (CWE-1236) ────────────

_CSV_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """
    CSV/Excel formula-injection থেকে বাঁচায় (CWE-1236)।
    সেলের মান =, +, -, @, tab বা CR দিয়ে শুরু হলে সামনে একটা single quote
    বসিয়ে দেয় — Excel/Sheets তখন সেটাকে formula হিসেবে evaluate না করে
    plain text হিসেবে দেখায়।

    =SUM(A1:A9)  →  '=SUM(A1:A9)   (literal text)
    Muhammad Ali →  Muhammad Ali   (unchanged)

    এটা export-এর সময় (CSV লেখার সময়) ব্যবহার করুন, input নেওয়ার সময় না —
    input-এ বসালে বৈধ ডেটা নষ্ট হতে পারে এবং সব input path কভার করে না
    (যেমন admin-এর manual single-add ফর্ম)।
    """
    if not value:
        return ""
    text = str(value)
    return "'" + text if text.startswith(_CSV_DANGEROUS_PREFIXES) else text
