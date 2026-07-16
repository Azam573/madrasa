"""
api/models/schemas.py — Pydantic Request/Response Models  v9.0
Request validation ও Response serialization।

পরিবর্তন (v9.0):
- LoginRequest.password: min_length=4 → min_length=8 (auth.py-এর সাথে consistent)
- UserCreate schema যোগ (নতুন): password min=8, role whitelist, username whitelist
- VoucherCreate.amount: gt=0 → gt=0, le=500000 (অস্বাভাবিক বড় মান প্রতিরোধ)
- PaymentCreate.amount_paid: gt=0 → gt=0, le=500000
- NoticeCreate.category: whitelist pattern যোগ
- mobile_no validator: আরো robust করা হয়েছে
- BulkAttendanceRequest: max_items 500→200 (DoS prevention)
- BulkMarksRequest: max_items 1000→500 (DoS prevention)
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal
import re


# ── Allowed values — centralized whitelist ───────────────────────
ALLOWED_ROLES      = {"admin", "staff", "teacher", "accountant"}
ALLOWED_GENDERS    = {"Male", "Female"}
ALLOWED_FUND_TYPES = {"general", "zakat", "lillah_boarding"}
ALLOWED_ATT_STATUS = {"present", "absent", "late", "holiday"}
ALLOWED_CATEGORIES = {"general", "academic", "exam", "fee", "holiday", "event", "urgent"}
ALLOWED_PAY_METHODS = {"cash", "bkash", "nagad", "bank", "cheque", "other"}


# ── Auth ────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    tenant_id: int = Field(..., gt=0, description="মাদ্রাসার Tenant ID")
    username:  str = Field(..., min_length=2, max_length=50,
                           description="ব্যবহারকারীর নাম")
    # ✅ Fix: min_length=4 → min_length=8 (auth.py-এর সাথে consistent)
    password:  str = Field(..., min_length=8, max_length=128,
                           description="পাসওয়ার্ড (কমপক্ষে ৮ অক্ষর)")


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    user_id:       int
    username:      str
    role:          str
    full_name:     Optional[str]
    tenant_id:     int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class UserCreate(BaseModel):
    """নতুন staff/teacher অ্যাকাউন্ট তৈরি — admin only।"""
    username:  str = Field(..., min_length=3, max_length=50,
                           pattern=r"^[a-zA-Z0-9_]+$",
                           description="শুধু অক্ষর, সংখ্যা ও _ অনুমোদিত")
    password:  str = Field(..., min_length=8, max_length=128)
    role:      str = Field(..., description=f"Allowed: {ALLOWED_ROLES}")
    full_name: str = Field(..., min_length=2, max_length=100)
    email:     Optional[str] = Field(None, max_length=200)

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in ALLOWED_ROLES:
            raise ValueError(f"role অবশ্যই এর মধ্যে হতে হবে: {ALLOWED_ROLES}")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("সঠিক email ফরম্যাটে দিন।")
        return v


class PasswordChangeRequest(BaseModel):
    """পাসওয়ার্ড পরিবর্তন।"""
    current_password: str = Field(..., min_length=8)
    new_password:     str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("নতুন পাসওয়ার্ড ও নিশ্চিত পাসওয়ার্ড মিলছে না।")
        return self


# ── Students ────────────────────────────────────────────────────

class StudentCreate(BaseModel):
    name:             str            = Field(..., min_length=2, max_length=100)
    father_name:      Optional[str]  = Field(None, max_length=100)
    mother_name:      Optional[str]  = Field(None, max_length=100)
    mobile_no:        Optional[str]  = Field(None, max_length=15)
    date_of_birth:    Optional[date] = None
    gender:           str            = Field(default="Male")
    blood_group:      Optional[str]  = Field(None, max_length=5)
    present_address:  Optional[str]  = Field(None, max_length=300)

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v):
        if v not in ALLOWED_GENDERS:
            raise ValueError(f"gender অবশ্যই Male বা Female হতে হবে।")
        return v

    @field_validator("mobile_no")
    @classmethod
    def validate_mobile(cls, v):
        if v:
            v = v.strip()
            if not re.match(r"^(\+?880|0)?1[3-9]\d{8}$", v):
                raise ValueError("বাংলাদেশি মোবাইল নম্বর ফরম্যাটে দিন। যেমন: 01712345678")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob(cls, v):
        if v:
            today = date.today()
            age = (today - v).days / 365
            if age < 3 or age > 60:
                raise ValueError("জন্ম তারিখ অস্বাভাবিক মনে হচ্ছে।")
        return v

    @field_validator("blood_group")
    @classmethod
    def validate_blood_group(cls, v):
        allowed = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", ""}
        if v and v not in allowed:
            raise ValueError(f"blood_group অবশ্যই এর মধ্যে হতে হবে: {allowed}")
        return v


class StudentOut(BaseModel):
    id:              int
    tenant_id:       int
    name:            str
    father_name:     Optional[str]
    mobile_no:       Optional[str]
    gender:          Optional[str]
    status:          str
    created_at:      Optional[datetime]

    class Config:
        from_attributes = True


class EnrollmentCreate(BaseModel):
    student_id:  int   = Field(..., gt=0)
    session_id:  int   = Field(..., gt=0)
    class_id:    int   = Field(..., gt=0)
    roll_no:     Optional[int] = Field(None, ge=1, le=9999)
    monthly_fee: float = Field(default=0, ge=0, le=50000,
                               description="মাসিক ফি (সর্বোচ্চ ৫০,০০০ টাকা)")


class EnrollmentOut(BaseModel):
    id:                int
    student_id:        int
    session_id:        int
    class_id:          int
    roll_no:           Optional[int]
    monthly_fee:       float
    enrollment_status: str


# ── Fee ─────────────────────────────────────────────────────────

class VoucherCreate(BaseModel):
    enrollment_id: int   = Field(..., gt=0)
    student_id:    int   = Field(..., gt=0)
    month_name:    str   = Field(..., min_length=2, max_length=20)
    year:          int   = Field(..., ge=2020, le=2040)
    # ✅ Fix: উপরের সীমা যোগ — অস্বাভাবিক বড় মান প্রতিরোধ
    amount:        Decimal = Field(..., gt=0, le=500_000, decimal_places=2,
                                 description="ভাউচারের পরিমাণ (সর্বোচ্চ ৫ লাখ)")
    fund_type:     str   = Field(default="general")
    due_date:      Optional[date] = None
    remarks:       Optional[str]  = Field(None, max_length=300)

    @field_validator("fund_type")
    @classmethod
    def validate_fund_type(cls, v):
        if v not in ALLOWED_FUND_TYPES:
            raise ValueError(f"fund_type অবশ্যই এর মধ্যে হতে হবে: {ALLOWED_FUND_TYPES}")
        return v

    @field_validator("month_name")
    @classmethod
    def validate_month(cls, v):
        months = {
            "january","february","march","april","may","june",
            "july","august","september","october","november","december",
            "জানুয়ারি","ফেব্রুয়ারি","মার্চ","এপ্রিল","মে","জুন",
            "জুলাই","আগস্ট","সেপ্টেম্বর","অক্টোবর","নভেম্বর","ডিসেম্বর",
        }
        if v.lower() not in months:
            raise ValueError("সঠিক মাসের নাম দিন।")
        return v


class PaymentCreate(BaseModel):
    voucher_id:     int   = Field(..., gt=0)
    # ✅ Fix: উপরের সীমা যোগ
    amount_paid:    Decimal = Field(..., gt=0, le=500_000, decimal_places=2,
                                  description="পরিশোধিত পরিমাণ (সর্বোচ্চ ৫ লাখ)")
    payment_method: str   = Field(default="cash")
    notes:          Optional[str] = Field(None, max_length=300)

    @field_validator("payment_method")
    @classmethod
    def validate_method(cls, v):
        if v not in ALLOWED_PAY_METHODS:
            raise ValueError(f"payment_method অবশ্যই এর মধ্যে হতে হবে: {ALLOWED_PAY_METHODS}")
        return v


class PaymentOut(BaseModel):
    id:             int
    voucher_id:     int
    amount_paid:    float
    payment_date:   date
    receipt_no:     Optional[str]


# ── Attendance ──────────────────────────────────────────────────

class AttendanceRecord(BaseModel):
    enrollment_id: int = Field(..., gt=0)
    status:        str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in ALLOWED_ATT_STATUS:
            raise ValueError(f"status অবশ্যই এর মধ্যে হতে হবে: {ALLOWED_ATT_STATUS}")
        return v


class BulkAttendanceRequest(BaseModel):
    date:    date
    # ✅ Fix: max_items 500 → 200 (DoS prevention)
    records: List[AttendanceRecord] = Field(..., min_length=1, max_length=200)

    @field_validator("date")
    @classmethod
    def validate_date(cls, v):
        today = date.today()
        diff = (today - v).days
        if diff > 365 or diff < -7:
            raise ValueError("তারিখ অস্বাভাবিক। ১ বছরের বেশি আগের বা ৭ দিনের বেশি ভবিষ্যতের তারিখ দেওয়া যাবে না।")
        return v


class AttendanceOut(BaseModel):
    enrollment_id: int
    date:          date
    status:        str


# ── Marks ───────────────────────────────────────────────────────

class MarkEntry(BaseModel):
    enrollment_id:      int  = Field(..., gt=0)
    subject_id:         int  = Field(..., gt=0)
    written_obtained:   int  = Field(default=0, ge=0, le=200)
    mcq_obtained:       int  = Field(default=0, ge=0, le=100)
    practical_obtained: int  = Field(default=0, ge=0, le=100)
    is_absent:          bool = False


class BulkMarksRequest(BaseModel):
    exam_id: int = Field(..., gt=0)
    # ✅ Fix: max_items 1000 → 500 (DoS prevention)
    marks:   List[MarkEntry] = Field(..., min_length=1, max_length=500)


# ── Notice ──────────────────────────────────────────────────────

class NoticeCreate(BaseModel):
    title:        str            = Field(..., min_length=3, max_length=200)
    body:         str            = Field(..., min_length=5, max_length=5000)
    # ✅ Fix: category whitelist যোগ
    category:     str            = Field(default="general")
    target_class: Optional[int]  = Field(None, gt=0)
    is_pinned:    bool           = False
    expiry_date:  Optional[date] = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"category অবশ্যই এর মধ্যে হতে হবে: {ALLOWED_CATEGORIES}")
        return v

    @field_validator("expiry_date")
    @classmethod
    def validate_expiry(cls, v):
        if v and v < date.today():
            raise ValueError("expiry_date অতীতের তারিখ হতে পারবে না।")
        return v


# ── Pagination ──────────────────────────────────────────────────

class PaginationParams(BaseModel):
    page:     int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


class PaginatedResponse(BaseModel):
    data:        list
    total:       int
    page:        int
    per_page:    int
    total_pages: int


# ── Generic responses ───────────────────────────────────────────

class SuccessResponse(BaseModel):
    success: bool = True
    message: str
    data:    Optional[dict] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error:   str
    detail:  Optional[str] = None


# ── Online Payments (bKash / Nagad) ─────────────────────────────

ALLOWED_GATEWAYS = {"bkash", "nagad"}


class OnlinePaymentInitiate(BaseModel):
    """Online payment session শুরু করার request body।"""
    voucher_id:   int  = Field(..., gt=0)
    gateway:      str  = Field(..., description="bkash | nagad")
    callback_url: Optional[str] = Field(
        default=None,
        description="Payment শেষে gateway যে URL-এ redirect করবে। "
                    "না দিলে APP_URL থেকে default তৈরি হয়।",
        max_length=500,
    )

    @field_validator("gateway")
    @classmethod
    def validate_gateway(cls, v):
        v = v.strip().lower()
        if v not in ALLOWED_GATEWAYS:
            raise ValueError(f"gateway অবশ্যই এর মধ্যে হতে হবে: {sorted(ALLOWED_GATEWAYS)}")
        return v

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, v):
        # Open-redirect রোধ: শুধু https (বা dev-এ http://localhost) allow
        if v is None:
            return v
        v = v.strip()
        if not (v.startswith("https://") or v.startswith("http://localhost")):
            raise ValueError("callback_url অবশ্যই https:// হতে হবে।")
        return v


class OnlinePaymentOut(BaseModel):
    id:               int
    voucher_id:       Optional[int] = None
    student_id:       Optional[int] = None
    payment_method:   str
    transaction_id:   Optional[str] = None
    merchant_invoice: Optional[str] = None
    amount:           float
    currency:         str = "BDT"
    status:           str
    initiated_at:     Optional[datetime] = None
    completed_at:     Optional[datetime] = None
    failure_reason:   Optional[str] = None


# ── Online Exams ────────────────────────────────────────────────

_ALLOWED_OPTIONS = {"A", "B", "C", "D"}


class ExamCreate(BaseModel):
    title:        str = Field(..., min_length=3, max_length=200)
    class_id:     Optional[int] = Field(default=None, gt=0)
    session_id:   Optional[int] = Field(default=None, gt=0)
    subject_id:   Optional[int] = Field(default=None, gt=0)
    duration_mins: int = Field(default=30, ge=5, le=480)
    instructions: Optional[str] = Field(default=None, max_length=2000)
    shuffle_questions: bool = True


class QuestionCreate(BaseModel):
    question_text: str = Field(..., min_length=3, max_length=2000)
    option_a:      str = Field(..., min_length=1, max_length=500)
    option_b:      str = Field(..., min_length=1, max_length=500)
    option_c:      Optional[str] = Field(default=None, max_length=500)
    option_d:      Optional[str] = Field(default=None, max_length=500)
    correct_option: str
    marks:         int = Field(default=1, ge=1, le=100)
    explanation:   Optional[str] = Field(default=None, max_length=2000)

    @field_validator("correct_option")
    @classmethod
    def validate_correct(cls, v):
        v = v.strip().upper()
        if v not in _ALLOWED_OPTIONS:
            raise ValueError("correct_option অবশ্যই A/B/C/D হতে হবে।")
        return v

    @model_validator(mode="after")
    def correct_option_must_exist(self):
        # C বা D correct হলে সেই option-টা থাকতেই হবে
        if self.correct_option == "C" and not self.option_c:
            raise ValueError("correct_option='C' কিন্তু option_c দেওয়া হয়নি।")
        if self.correct_option == "D" and not self.option_d:
            raise ValueError("correct_option='D' কিন্তু option_d দেওয়া হয়নি।")
        return self


class ExamSubmission(BaseModel):
    enrollment_id: int = Field(..., gt=0)
    # {"<question_id>": "A", ...}
    answers: dict = Field(..., description='{"question_id": "A|B|C|D"}')

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, v):
        if not v:
            raise ValueError("answers খালি হতে পারবে না।")
        if len(v) > 500:
            raise ValueError("প্রশ্নসংখ্যা সীমার বাইরে।")
        for k, val in v.items():
            if not str(k).isdigit():
                raise ValueError(f"Invalid question id: {k}")
            if str(val).strip().upper() not in _ALLOWED_OPTIONS:
                raise ValueError(f"Invalid answer '{val}' — A/B/C/D হতে হবে।")
        return {str(k): str(val).strip().upper() for k, val in v.items()}


# ── Teachers ────────────────────────────────────────────────────

class TeacherCreate(BaseModel):
    name:           str = Field(..., min_length=2, max_length=150)
    father_name:    Optional[str] = Field(default=None, max_length=150)
    mobile_no:      Optional[str] = None
    email:          Optional[str] = Field(default=None, max_length=150)
    nid_no:         Optional[str] = Field(default=None, max_length=30)
    designation:    str = Field(default="Teacher", max_length=100)
    joining_date:   Optional[date] = None
    monthly_salary: Decimal = Field(default=Decimal("0"), ge=0, le=10_000_000, decimal_places=2)
    qualification:  Optional[str] = Field(default=None, max_length=300)
    present_address: Optional[str] = Field(default=None, max_length=500)

    @field_validator("mobile_no")
    @classmethod
    def validate_mobile(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not re.fullmatch(r"01[3-9]\d{8}", v):
            raise ValueError("mobile_no অবশ্যই 01XXXXXXXXX ফরম্যাটে (১১ সংখ্যা) হতে হবে।")
        return v


class TeacherAssignment(BaseModel):
    class_id:   int = Field(..., gt=0)
    subject_id: Optional[int] = Field(default=None, gt=0)
    session_id: Optional[int] = Field(default=None, gt=0)
    is_class_teacher: bool = False


class SalaryPayment(BaseModel):
    month_name:   str = Field(..., min_length=3, max_length=20)
    year:         int = Field(..., ge=2000, le=2100)
    basic_salary: Decimal = Field(..., ge=0, le=10_000_000, decimal_places=2)
    bonus:        Decimal = Field(default=Decimal("0"), ge=0, le=10_000_000, decimal_places=2)
    deduction:    Decimal = Field(default=Decimal("0"), ge=0, le=10_000_000, decimal_places=2)
    payment_method: str = Field(default="cash", max_length=30)
    remarks:      Optional[str] = Field(default=None, max_length=500)


# ── Parent Portal (OTP auth) ────────────────────────────────────

class ParentOTPRequest(BaseModel):
    tenant_id: int = Field(..., gt=0)
    mobile_no: str

    @field_validator("mobile_no")
    @classmethod
    def validate_mobile(cls, v):
        v = v.strip()
        if not re.fullmatch(r"01[3-9]\d{8}", v):
            raise ValueError("mobile_no অবশ্যই 01XXXXXXXXX ফরম্যাটে হতে হবে।")
        return v


class ParentOTPVerify(BaseModel):
    tenant_id: int = Field(..., gt=0)
    mobile_no: str
    otp:       str = Field(..., min_length=4, max_length=8)

    @field_validator("mobile_no")
    @classmethod
    def validate_mobile(cls, v):
        v = v.strip()
        if not re.fullmatch(r"01[3-9]\d{8}", v):
            raise ValueError("mobile_no অবশ্যই 01XXXXXXXXX ফরম্যাটে হতে হবে।")
        return v

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v):
        v = v.strip()
        if not v.isdigit():
            raise ValueError("OTP শুধু সংখ্যা হবে।")
        return v


# ── Notices ─────────────────────────────────────────────────────

_NOTICE_CATEGORIES = {"general", "exam", "holiday", "fee", "event", "urgent"}


class NoticeCreate(BaseModel):
    title:        str = Field(..., min_length=3, max_length=200)
    body:         str = Field(..., min_length=3, max_length=5000)
    category:     str = Field(default="general")
    target_class: Optional[int] = Field(default=None, gt=0)
    is_pinned:    bool = False
    expiry_date:  Optional[date] = None

    @field_validator("category")
    @classmethod
    def validate_category_notice(cls, v):
        v = v.strip().lower()
        if v not in _NOTICE_CATEGORIES:
            raise ValueError(f"category অবশ্যই: {sorted(_NOTICE_CATEGORIES)}")
        return v

    @field_validator("expiry_date")
    @classmethod
    def validate_notice_expiry(cls, v):
        if v and v < date.today():
            raise ValueError("expiry_date অতীতের তারিখ হতে পারবে না।")
        return v


# ── Timetable ───────────────────────────────────────────────────

_TIMETABLE_DAYS = {"Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}


class TimetableEntryCreate(BaseModel):
    class_id:    int = Field(..., gt=0)
    session_id:  int = Field(..., gt=0)
    day_of_week: str
    period_no:   int = Field(..., ge=1, le=12)
    start_time:  Optional[str] = Field(default=None, description="HH:MM")
    end_time:    Optional[str] = Field(default=None, description="HH:MM")
    subject_id:  Optional[int] = Field(default=None, gt=0)
    teacher_id:  Optional[int] = Field(default=None, gt=0)
    room_no:     Optional[str] = Field(default=None, max_length=30)

    @field_validator("day_of_week")
    @classmethod
    def validate_day(cls, v):
        v = v.strip().capitalize()
        if v not in _TIMETABLE_DAYS:
            raise ValueError(f"day_of_week অবশ্যই: {sorted(_TIMETABLE_DAYS)}")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, v):
        if v is None:
            return v
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v.strip()):
            raise ValueError("সময় HH:MM (24-ঘণ্টা) ফরম্যাটে দিন।")
        return v.strip()


# ── Zakat ───────────────────────────────────────────────────────

class ZakatCollectionCreate(BaseModel):
    donor_name:   Optional[str] = Field(default=None, max_length=150)
    donor_mobile: Optional[str] = Field(default=None, max_length=15)
    amount:       Decimal = Field(..., gt=0, le=100_000_000, decimal_places=2)
    zakat_type:   str = Field(default="zakat")
    notes:        Optional[str] = Field(default=None, max_length=500)

    @field_validator("zakat_type")
    @classmethod
    def validate_zakat_type(cls, v):
        v = v.strip().lower()
        if v not in {"zakat", "fitra", "sadaqah", "donation"}:
            raise ValueError("zakat_type: zakat/fitra/sadaqah/donation")
        return v


class ZakatDistributionCreate(BaseModel):
    student_id:     Optional[int] = Field(default=None, gt=0)
    recipient_name: Optional[str] = Field(default=None, max_length=150)
    amount:         Decimal = Field(..., gt=0, le=100_000_000, decimal_places=2)
    purpose:        Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def student_or_recipient(self):
        if not self.student_id and not self.recipient_name:
            raise ValueError("student_id অথবা recipient_name — অন্তত একটি দিতে হবে।")
        return self


# ── QR Attendance ───────────────────────────────────────────────

class QRPunch(BaseModel):
    """enrollment_id (manual entry) অথবা qr_token (স্ক্যান) — অন্তত একটি।
    Fix (brutal review #3): স্ক্যান path-এ signed token verify হয়।"""
    enrollment_id: Optional[int] = Field(default=None, gt=0)
    qr_token:      Optional[str] = Field(default=None, max_length=100)
    status:        str = Field(default="present")
    punch_date:    Optional[date] = None   # না দিলে আজ

    @model_validator(mode="after")
    def token_or_id(self):
        if not self.enrollment_id and not self.qr_token:
            raise ValueError("enrollment_id অথবা qr_token — অন্তত একটি দিতে হবে।")
        return self

    @field_validator("status")
    @classmethod
    def validate_punch_status(cls, v):
        v = v.strip().lower()
        if v not in {"present", "late", "absent", "leave"}:
            raise ValueError("status: present/late/absent/leave")
        return v


# ── Branding ────────────────────────────────────────────────────

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class BrandingUpdate(BaseModel):
    """সব field optional — শুধু পাঠানোগুলোই আপডেট হয়।"""
    name_arabic:      Optional[str] = Field(default=None, max_length=200)
    name_english:     Optional[str] = Field(default=None, max_length=200)
    tagline:          Optional[str] = Field(default=None, max_length=300)
    established_year: Optional[int] = Field(default=None, ge=1000, le=2100)
    eiin_no:          Optional[str] = Field(default=None, max_length=30)
    reg_no:           Optional[str] = Field(default=None, max_length=50)
    principal_name:   Optional[str] = Field(default=None, max_length=150)
    principal_title:  Optional[str] = Field(default=None, max_length=100)
    primary_color:    Optional[str] = None
    secondary_color:  Optional[str] = None
    receipt_footer:   Optional[str] = Field(default=None, max_length=500)
    tc_footer:        Optional[str] = Field(default=None, max_length=500)
    show_logo:        Optional[bool] = None

    @field_validator("primary_color", "secondary_color")
    @classmethod
    def validate_hex(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not _HEX_RE.match(v):
            raise ValueError("রঙ #RRGGBB ফরম্যাটে দিন (যেমন: #0F4C5C)।")
        return v


class ImageUpload(BaseModel):
    """Base64 image (PNG/JPEG, সর্বোচ্চ 200KB) — server magic-bytes verify করে।"""
    image_base64: str = Field(..., min_length=8)
