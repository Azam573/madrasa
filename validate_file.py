"""
validate_file.py — File Upload Security Validation  v9.0

সমস্যা (আগে):
- mimetypes.guess_type(filename) শুধু extension দেখত — fake করা সহজ
- evil.php নাম করে .jpg বললেই upload হত
- কোনো file size limit ছিল না
- filename-এ path traversal সম্ভব ছিল (../../etc/passwd)

সমাধান (v9.0):
- Magic bytes (actual file header) দিয়ে real type detect
- Extension + MIME + content — তিনটিই মিলতে হবে
- File size limit enforce
- Filename sanitization
"""

import os
import re
from typing import Tuple

# ── Size limits ───────────────────────────────────────────────────
MAX_PHOTO_BYTES    = 5  * 1024 * 1024   # 5 MB
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024   # 10 MB

# ── Magic bytes — actual file content signature ───────────────────
MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    "image/jpeg":      [b"\xff\xd8\xff"],
    "image/png":       [b"\x89PNG\r\n\x1a\n"],
    "image/gif":       [b"GIF87a", b"GIF89a"],
    "image/webp":      [b"RIFF"],          # RIFF????WEBP
    "application/pdf": [b"%PDF-"],
}

# ── Whitelists per category ───────────────────────────────────────
PHOTO_ALLOWED: dict[str, list[str]] = {
    "image/jpeg": [".jpg", ".jpeg"],
    "image/png":  [".png"],
    "image/webp": [".webp"],
    "image/gif":  [".gif"],
}
DOCUMENT_ALLOWED: dict[str, list[str]] = {
    "application/pdf": [".pdf"],
    "image/jpeg":      [".jpg", ".jpeg"],
    "image/png":       [".png"],
}


def _magic_detect(file_bytes: bytes) -> str | None:
    """
    File-এর প্রথম ১২ byte (magic bytes) দেখে actual MIME বের করে।
    Extension বা declared MIME বিশ্বাস করে না।
    """
    header = file_bytes[:12]
    for mime, sigs in MAGIC_SIGNATURES.items():
        for sig in sigs:
            if header.startswith(sig):
                if mime == "image/webp":
                    # RIFF????WEBP — byte 8-12 must be WEBP
                    if len(header) >= 12 and header[8:12] == b"WEBP":
                        return mime
                    continue
                return mime
    return None


def sanitize_filename(filename: str) -> str:
    """
    Path traversal ও dangerous character সরিয়ে safe filename তৈরি করে।
    ../../etc/passwd → passwd
    evil<script>.jpg → evil_script_.jpg
    """
    # basename নেওয়া — path traversal প্রতিরোধ
    filename = os.path.basename(filename)
    # শুধু safe char রাখা
    filename = re.sub(r"[^\w.\-]", "_", filename)
    # Double dot বাদ
    filename = re.sub(r"\.{2,}", ".", filename)
    # Length limit
    name, ext = os.path.splitext(filename)
    return f"{name[:50]}{ext[:10]}" if ext else name[:50]


def validate_photo(file_bytes: bytes, filename: str) -> Tuple[bool, str, str]:
    """
    ছবি validate করে — magic bytes + whitelist + size।

    Returns: (is_valid, detected_mime, error_message)

    ব্যবহার:
        ok, mime, err = validate_photo(data, "photo.jpg")
        if not ok:
            st.error(err); return
    """
    if not file_bytes:
        return False, "", "ফাইল খালি।"

    if len(file_bytes) > MAX_PHOTO_BYTES:
        mb = len(file_bytes) / 1024 / 1024
        return False, "", f"ফাইল অনেক বড় ({mb:.1f} MB)। সর্বোচ্চ {MAX_PHOTO_BYTES // 1024 // 1024} MB।"

    mime = _magic_detect(file_bytes)
    if not mime:
        return False, "", "ফাইলের ধরন চেনা যায়নি। শুধু JPG, PNG, WebP অনুমোদিত।"

    if mime not in PHOTO_ALLOWED:
        return False, "", f"এই ধরনের ফাইল ({mime}) অনুমোদিত নয়। শুধু ছবি দিন।"

    # Extension মিলছে কিনা
    ext = os.path.splitext(filename.lower())[1]
    if ext and ext not in PHOTO_ALLOWED[mime]:
        return False, "", f"ফাইলের নাম ({ext}) ও আসল ধরন মিলছে না। ফাইলটি পরিবর্তন করা হয়েছে।"

    return True, mime, ""


def validate_document(file_bytes: bytes, filename: str) -> Tuple[bool, str, str]:
    """
    Document (PDF/image) validate করে।

    Returns: (is_valid, detected_mime, error_message)
    """
    if not file_bytes:
        return False, "", "ফাইল খালি।"

    if len(file_bytes) > MAX_DOCUMENT_BYTES:
        mb = len(file_bytes) / 1024 / 1024
        return False, "", f"ফাইল অনেক বড় ({mb:.1f} MB)। সর্বোচ্চ {MAX_DOCUMENT_BYTES // 1024 // 1024} MB।"

    mime = _magic_detect(file_bytes)
    if not mime:
        return False, "", "ফাইলের ধরন চেনা যায়নি। শুধু PDF, JPG, PNG অনুমোদিত।"

    if mime not in DOCUMENT_ALLOWED:
        return False, "", f"এই ধরনের ফাইল ({mime}) অনুমোদিত নয়। শুধু PDF ও ছবি দিন।"

    ext = os.path.splitext(filename.lower())[1]
    if ext and ext not in DOCUMENT_ALLOWED[mime]:
        return False, "", f"ফাইলের নাম ({ext}) ও আসল ধরন মিলছে না।"

    return True, mime, ""
