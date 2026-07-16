"""
storage.py — File Storage Layer
Supabase Storage / AWS S3 / Local fallback।
Student photos, TC, certificates, documents।
"""

import os
import io
import base64
import hashlib
import logging
import mimetypes
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger("madrasa.storage")

STORAGE_PROVIDER = os.environ.get("STORAGE_PROVIDER", "supabase")  # supabase | s3 | local
STORAGE_BUCKET   = os.environ.get("STORAGE_BUCKET", "madrasa-files")
LOCAL_UPLOAD_DIR = os.environ.get("LOCAL_UPLOAD_DIR", "uploads")


# ─────────────────────────────────────────────
# Supabase Storage
# ─────────────────────────────────────────────

class SupabaseStorage:
    def __init__(self):
        try:
            from supabase import create_client
            url = os.environ.get("SUPABASE_URL", "")
            key = os.environ.get("SUPABASE_SERVICE_KEY", "")
            if url and key:
                self.client = create_client(url, key)
                self.available = True
            else:
                self.available = False
                logger.warning("SUPABASE_URL বা SUPABASE_SERVICE_KEY নেই।")
        except ImportError:
            self.available = False
            logger.warning("supabase package নেই। pip install supabase")

    def upload(self, file_bytes: bytes, path: str, content_type: str) -> Optional[str]:
        if not self.available:
            return None
        try:
            self.client.storage.from_(STORAGE_BUCKET).upload(
                path=path,
                file=file_bytes,
                file_options={"content-type": content_type, "upsert": "true"},
            )
            # Public URL
            url = self.client.storage.from_(STORAGE_BUCKET).get_public_url(path)
            return url
        except Exception as ex:
            logger.error(f"Supabase upload failed: {ex}")
            return None

    def delete(self, path: str) -> bool:
        if not self.available:
            return False
        try:
            self.client.storage.from_(STORAGE_BUCKET).remove([path])
            return True
        except Exception as ex:
            logger.error(f"Supabase delete failed: {ex}")
            return False

    def get_url(self, path: str) -> Optional[str]:
        if not self.available:
            return None
        try:
            return self.client.storage.from_(STORAGE_BUCKET).get_public_url(path)
        except Exception:
            return None


# ─────────────────────────────────────────────
# AWS S3 Storage
# ─────────────────────────────────────────────

class S3Storage:
    def __init__(self):
        try:
            import boto3
            self.s3 = boto3.client(
                "s3",
                aws_access_key_id     = os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY"),
                region_name           = os.environ.get("AWS_REGION", "ap-south-1"),
            )
            self.bucket    = os.environ.get("S3_BUCKET", STORAGE_BUCKET)
            self.cdn_url   = os.environ.get("CDN_URL", "")
            self.available = True
        except ImportError:
            self.available = False
            logger.warning("boto3 package নেই। pip install boto3")

    def upload(self, file_bytes: bytes, path: str, content_type: str) -> Optional[str]:
        if not self.available:
            return None
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=path,
                Body=file_bytes,
                ContentType=content_type,
                ACL="public-read",
            )
            if self.cdn_url:
                return f"{self.cdn_url}/{path}"
            return f"https://{self.bucket}.s3.amazonaws.com/{path}"
        except Exception as ex:
            logger.error(f"S3 upload failed: {ex}")
            return None

    def delete(self, path: str) -> bool:
        if not self.available:
            return False
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=path)
            return True
        except Exception as ex:
            logger.error(f"S3 delete failed: {ex}")
            return False

    def get_url(self, path: str) -> Optional[str]:
        if not self.available:
            return None
        if self.cdn_url:
            return f"{self.cdn_url}/{path}"
        return f"https://{self.bucket}.s3.amazonaws.com/{path}"


# ─────────────────────────────────────────────
# Local Storage (fallback)
# ─────────────────────────────────────────────

class LocalStorage:
    def __init__(self):
        os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)
        self.available = True

    def upload(self, file_bytes: bytes, path: str, content_type: str) -> Optional[str]:
        try:
            full_path = os.path.join(LOCAL_UPLOAD_DIR, path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(file_bytes)
            return f"/uploads/{path}"
        except Exception as ex:
            logger.error(f"Local storage failed: {ex}")
            return None

    def delete(self, path: str) -> bool:
        try:
            full_path = os.path.join(LOCAL_UPLOAD_DIR, path)
            if os.path.exists(full_path):
                os.remove(full_path)
            return True
        except Exception as ex:
            logger.error(f"Local delete failed: {ex}")
            return False

    def get_url(self, path: str) -> Optional[str]:
        return f"/uploads/{path}"


# ─────────────────────────────────────────────
# Storage Factory
# ─────────────────────────────────────────────

_storage = None

def get_storage():
    global _storage
    if _storage is not None:
        return _storage

    provider = STORAGE_PROVIDER.lower()
    if provider == "supabase":
        s = SupabaseStorage()
        if s.available:
            _storage = s
            logger.info("✅ Storage: Supabase Storage")
            return _storage
    elif provider == "s3":
        s = S3Storage()
        if s.available:
            _storage = s
            logger.info("✅ Storage: AWS S3")
            return _storage

    # Fallback to local
    _storage = LocalStorage()
    logger.info("⚠️  Storage: Local filesystem (production-এ S3/Supabase ব্যবহার করুন)")
    return _storage


# ─────────────────────────────────────────────
# High-level helpers
# ─────────────────────────────────────────────

def _generate_path(tenant_id: int, category: str, filename: str) -> str:
    """
    Path format: tenant_{id}/{category}/{date}/{hash}_{filename}
    Example: tenant_1/photos/2024-01/a3f9c2_student_photo.jpg
    """
    date_prefix = datetime.now().strftime("%Y-%m")
    file_hash   = hashlib.md5(filename.encode(), usedforsecurity=False).hexdigest()[:8]  # filename dedup only
    safe_name   = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    return f"tenant_{tenant_id}/{category}/{date_prefix}/{file_hash}_{safe_name}"


def upload_student_photo(
    tenant_id:   int,
    student_id:  int,
    file_bytes:  bytes,
    filename:    str,
) -> Tuple[bool, Optional[str]]:
    """ছাত্রের ছবি আপলোড — magic bytes validation + sanitization (v9.0)"""
    from validate_file import validate_photo, sanitize_filename

    # Magic bytes দিয়ে validate
    ok, mime, err = validate_photo(file_bytes, filename)
    if not ok:
        logger.warning(f"Photo upload rejected: {err}")
        return False, err

    safe_name = sanitize_filename(filename)
    path      = _generate_path(tenant_id, f"students/{student_id}/photos", safe_name)
    storage   = get_storage()
    url       = storage.upload(file_bytes, path, mime)
    if url:
        _update_student_photo_db(tenant_id, student_id, url)
        logger.info(f"✅ Photo uploaded for student {student_id}")
        return True, url
    return False, "আপলোড ব্যর্থ হয়েছে।"


def upload_document(
    tenant_id:   int,
    student_id:  int,
    doc_type:    str,
    file_bytes:  bytes,
    filename:    str,
) -> Tuple[bool, Optional[str]]:
    """TC, Certificate বা যেকোনো ডকুমেন্ট আপলোড — magic bytes validation (v9.0)"""
    from validate_file import validate_document, sanitize_filename

    ok, mime, err = validate_document(file_bytes, filename)
    if not ok:
        logger.warning(f"Document upload rejected: {err}")
        return False, err

    safe_name = sanitize_filename(filename)
    path      = _generate_path(tenant_id, f"students/{student_id}/{doc_type}", safe_name)
    storage   = get_storage()
    url       = storage.upload(file_bytes, path, mime)
    if url:
        _save_document_db(tenant_id, student_id, doc_type, url, safe_name, mime)
        logger.info(f"✅ Document uploaded: {doc_type} for student {student_id}")
        return True, url
    return False, "ডকুমেন্ট আপলোড ব্যর্থ।"


def upload_teacher_photo(
    tenant_id:  int,
    teacher_id: int,
    file_bytes: bytes,
    filename:   str,
) -> Tuple[bool, Optional[str]]:
    """শিক্ষকের ছবি আপলোড — magic bytes validation (v9.0)"""
    from validate_file import validate_photo, sanitize_filename

    ok, mime, err = validate_photo(file_bytes, filename)
    if not ok:
        logger.warning(f"Teacher photo rejected: {err}")
        return False, err

    safe_name = sanitize_filename(filename)
    path      = _generate_path(tenant_id, f"teachers/{teacher_id}/photos", safe_name)
    storage   = get_storage()
    url       = storage.upload(file_bytes, path, mime)
    if url:
        _update_teacher_photo_db(tenant_id, teacher_id, url)
        return True, url
    return False, "আপলোড ব্যর্থ।"


def get_file_url(path: str) -> Optional[str]:
    return get_storage().get_url(path)


def delete_file(path: str) -> bool:
    return get_storage().delete(path)


def base64_to_bytes(data_url: str) -> Tuple[bytes, str]:
    """data:image/jpeg;base64,... → (bytes, mime_type)"""
    if data_url.startswith("data:"):
        header, data = data_url.split(",", 1)
        mime = header.split(":")[1].split(";")[0]
        return base64.b64decode(data), mime
    return base64.b64decode(data_url), "application/octet-stream"


def bytes_to_base64(file_bytes: bytes, mime: str) -> str:
    """bytes → data URL (preview-এর জন্য)"""
    b64 = base64.b64encode(file_bytes).decode()
    return f"data:{mime};base64,{b64}"


# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────

def _update_student_photo_db(tenant_id: int, student_id: int, url: str):
    try:
        from db import execute
        execute(
            "UPDATE students SET photo=%s WHERE id=%s AND tenant_id=%s",
            (url, student_id, tenant_id),
        )
    except Exception as ex:
        logger.error(f"DB photo update failed: {ex}")


def _update_teacher_photo_db(tenant_id: int, teacher_id: int, url: str):
    try:
        from db import execute
        execute(
            "UPDATE teachers SET photo_url=%s WHERE id=%s AND tenant_id=%s",
            (url, teacher_id, tenant_id),
        )
    except Exception as ex:
        logger.error(f"DB teacher photo update failed: {ex}")


def _save_document_db(tenant_id: int, student_id: int, doc_type: str,
                       url: str, filename: str, mime: str):
    try:
        from db import execute
        execute(
            """INSERT INTO student_documents
               (tenant_id, student_id, doc_type, doc_title, file_name, file_mime, file_data)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (tenant_id, student_id, doc_type, filename, filename, mime, url),
        )
    except Exception as ex:
        logger.error(f"DB document save failed: {ex}")


# ─────────────────────────────────────────────
# Storage health check
# ─────────────────────────────────────────────

def storage_health() -> dict:
    storage = get_storage()
    return {
        "provider":  STORAGE_PROVIDER,
        "available": storage.available,
        "bucket":    STORAGE_BUCKET,
    }
