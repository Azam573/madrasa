"""
tests/test_sanitize.py — XSS Prevention Tests
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestEsc:

    def test_script_tag_escaped(self):
        from sanitize import esc
        result = esc("<script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_plain_text_unchanged(self):
        from sanitize import esc
        assert esc("মোহাম্মদ আলী") == "মোহাম্মদ আলী"

    def test_quote_escaped(self):
        from sanitize import esc
        result = esc('"quoted"')
        assert '"' not in result

    def test_empty_string(self):
        from sanitize import esc
        assert esc("") == ""

    def test_none_like_empty(self):
        from sanitize import esc
        assert esc("") == ""

    def test_html_entities(self):
        from sanitize import esc
        result = esc("5 > 3 & 2 < 4")
        assert "&gt;" in result
        assert "&lt;" in result
        assert "&amp;" in result


class TestSanitizeNoticeBody:

    def test_safe_bold_tag_kept(self):
        from sanitize import sanitize_notice_body
        result = sanitize_notice_body("<b>গুরুত্বপূর্ণ</b>")
        assert "<b>" in result
        assert "গুরুত্বপূর্ণ" in result

    def test_script_tag_escaped(self):
        from sanitize import sanitize_notice_body
        result = sanitize_notice_body("<script>alert(1)</script>")
        assert "<script>" not in result
        assert "alert" in result  # content রাখা হয়, tag escape হয়

    def test_img_onerror_removed(self):
        from sanitize import sanitize_notice_body
        result = sanitize_notice_body('<img src=x onerror=alert(1)>')
        assert "onerror" not in result.lower() or "<img" not in result

    def test_iframe_escaped(self):
        from sanitize import sanitize_notice_body
        result = sanitize_notice_body('<iframe src="evil.com"></iframe>')
        assert "<iframe>" not in result

    def test_allowed_tags_preserved(self):
        from sanitize import sanitize_notice_body
        html = "<b>Bold</b> <i>Italic</i> <u>Underline</u>"
        result = sanitize_notice_body(html)
        assert "<b>" in result
        assert "<i>" in result
        assert "<u>" in result

    def test_max_length_enforced(self):
        from sanitize import sanitize_notice_body
        long_body = "A" * 10000
        result = sanitize_notice_body(long_body, max_len=5000)
        assert len(result) <= 5000

    def test_empty_body(self):
        from sanitize import sanitize_notice_body
        assert sanitize_notice_body("") == ""


class TestSanitizeUrl:

    def test_http_url_safe(self):
        from sanitize import sanitize_url
        assert sanitize_url("http://example.com") == "http://example.com"

    def test_https_url_safe(self):
        from sanitize import sanitize_url
        result = sanitize_url("https://madrasa.edu.bd")
        assert result == "https://madrasa.edu.bd"

    def test_javascript_blocked(self):
        from sanitize import sanitize_url
        assert sanitize_url("javascript:alert(1)") == "#"

    def test_vbscript_blocked(self):
        from sanitize import sanitize_url
        assert sanitize_url("vbscript:msgbox(1)") == "#"

    def test_data_uri_blocked(self):
        from sanitize import sanitize_url
        assert sanitize_url("data:text/html,<script>alert(1)</script>") == "#"

    def test_empty_url(self):
        from sanitize import sanitize_url
        assert sanitize_url("") == "#"


class TestCsvSafe:

    def test_equals_prefix_escaped(self):
        from sanitize import csv_safe
        assert csv_safe("=SUM(A1:A9)") == "'=SUM(A1:A9)"

    def test_plus_prefix_escaped(self):
        from sanitize import csv_safe
        assert csv_safe("+1+1") == "'+1+1"

    def test_minus_prefix_escaped(self):
        from sanitize import csv_safe
        assert csv_safe("-1") == "'-1"

    def test_at_prefix_escaped(self):
        from sanitize import csv_safe
        assert csv_safe("@SUM(1,1)") == "'@SUM(1,1)"

    def test_tab_prefix_escaped(self):
        from sanitize import csv_safe
        assert csv_safe("\tmalicious") == "'\tmalicious"

    def test_plain_name_unchanged(self):
        from sanitize import csv_safe
        assert csv_safe("মোহাম্মদ আলী") == "মোহাম্মদ আলী"

    def test_empty_string(self):
        from sanitize import csv_safe
        assert csv_safe("") == ""

    def test_none(self):
        from sanitize import csv_safe
        assert csv_safe(None) == ""


class TestMaskPii:

    def test_phone_masked(self):
        from mask_pii import mask_phone
        result = mask_phone("01712345678")
        assert "01712345678" != result
        assert "678" in result  # শেষ ৩ দেখা যায়
        assert "*" in result

    def test_name_masked(self):
        from mask_pii import mask_name
        result = mask_name("মোহাম্মদ আলী")
        assert "মোহাম্মদ" != result.split()[0]  # পুরো নাম নেই
        assert "*" in result

    def test_email_masked(self):
        from mask_pii import mask_email
        result = mask_email("admin@madrasa.edu.bd")
        assert "admin" not in result
        assert "@" in result
        assert "*" in result

    def test_empty_phone(self):
        from mask_pii import mask_phone
        assert mask_phone("") == "***"

    def test_safe_log_format(self):
        from mask_pii import safe_log
        result = safe_log("SMS sent", phone="01712345678",
                          name="মোহাম্মদ", tenant_id=1)
        assert "01712345678" not in result  # Plain number নেই
        assert "SMS sent" in result
        assert "tenant#1" in result
