"""
mask_pii.py — PII (Personal Identifiable Information) Masking Utility  v9.0

Log file-এ phone number, student name, email কখনো plain text-এ যাবে না।
এই module সব PII log করার আগে mask করে।

ব্যবহার:
    from mask_pii import mask_phone, mask_name, mask_email, safe_log

    logger.info(f"SMS sent → {mask_phone(phone)} ({mask_name(name)})")
"""

import re


def mask_phone(phone: str) -> str:
    """
    বাংলাদেশি মোবাইল নম্বর mask করে।
    01712345678  →  017*****678
    +8801712345678  →  +88017*****678
    """
    if not phone:
        return "***"
    phone = str(phone).strip()
    # শুধু digits রাখি length বের করতে
    digits = re.sub(r"\D", "", phone)
    if len(digits) >= 10:
        # শেষ ৩ রাখি, মাঝেরগুলো mask
        return phone[:5] + "*" * (len(phone) - 8) + phone[-3:]
    return "***"


def mask_name(name: str) -> str:
    """
    Student/অভিভাবকের নাম mask করে।
    'মোহাম্মদ আলী'  →  'মো*** আ**'
    'John Doe'  →  'Jo** D**'
    """
    if not name:
        return "***"
    parts = str(name).strip().split()
    masked = []
    for part in parts:
        if len(part) <= 2:
            masked.append(part[0] + "*")
        else:
            masked.append(part[:2] + "*" * (len(part) - 2))
    return " ".join(masked)


def mask_email(email: str) -> str:
    """
    Email mask করে।
    'john@gmail.com'  →  'jo**@g*****.com'
    """
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    masked_local = local[:2] + "*" * max(2, len(local) - 2)
    domain_parts = domain.split(".")
    masked_domain = domain_parts[0][:1] + "*" * (len(domain_parts[0]) - 1)
    ext = ".".join(domain_parts[1:])
    return f"{masked_local}@{masked_domain}.{ext}"


def mask_tenant_data(tenant_id: int) -> str:
    """Tenant ID log করার safe format।"""
    return f"tenant#{tenant_id}"


def safe_log(event: str, phone: str = "", name: str = "",
             email: str = "", tenant_id: int = 0) -> str:
    """
    Log message নিরাপদে তৈরি করে — সব PII mask করে।

    উদাহরণ:
        logger.info(safe_log("SMS sent", phone=phone, name=student_name))
        # → "SMS sent | phone=017*****678 | name=মো*** আ**"
    """
    parts = [event]
    if tenant_id:
        parts.append(f"tenant={mask_tenant_data(tenant_id)}")
    if phone:
        parts.append(f"phone={mask_phone(phone)}")
    if name:
        parts.append(f"name={mask_name(name)}")
    if email:
        parts.append(f"email={mask_email(email)}")
    return " | ".join(parts)
