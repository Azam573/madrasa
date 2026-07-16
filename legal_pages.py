"""
legal_pages.py — পাবলিক Terms of Service ও Privacy Policy পেজ।

⚠️ গুরুত্বপূর্ণ নোট: এই কনটেন্ট একটা যুক্তিসঙ্গত সূচনা-টেমপ্লেট মাত্র —
আইনজীবী দ্বারা লেখা বা যাচাই করা নথি নয়। বাংলাদেশে বাণিজ্যিকভাবে ব্যবহারের
আগে অবশ্যই একজন যোগ্য আইনজীবীর মাধ্যমে পর্যালোচনা করিয়ে নিন, বিশেষ করে
ছাত্র/শিক্ষার্থীর ডেটা (যাদের অনেকেই নাবালক) ও বাংলাদেশের প্রযোজ্য
তথ্য-সুরক্ষা আইন সংক্রান্ত বিষয়ে।

কনটেন্ট UI-chrome-এর মতো ছোট i18n key দিয়ে না রেখে সম্পূর্ণ Markdown
ব্লক হিসেবে রাখা হয়েছে — আইনি লেখা অনুচ্ছেদ-ভিত্তিক, প্রতিটা বাক্যের
জন্য আলাদা key ব্যবস্থাপনাযোগ্য না।
"""

import streamlit as st
from utils import inject_css, PALETTE

TERMS_BN = """
## সেবা ব্যবহারের শর্তাবলী

**সর্বশেষ হালনাগাদ: ১৫ জুলাই, ২০২৬**

### ১. ভূমিকা
Smart Madrasa ERP ("আমরা", "সেবা") একটি মাল্টি-টেন্যান্ট SaaS প্ল্যাটফর্ম,
যা মাদ্রাসা/শিক্ষাপ্রতিষ্ঠানকে ছাত্র ভর্তি, হাজিরা, পরীক্ষা-ফলাফল, ফি
আদায়, ও প্রাতিষ্ঠানিক ব্যবস্থাপনার সফটওয়্যার সরবরাহ করে। এই সেবা
ব্যবহার করার মাধ্যমে আপনি এই শর্তাবলীতে সম্মত হচ্ছেন।

### ২. অ্যাকাউন্ট ও বিনামূল্যে ট্রায়াল
- নিজে-নিবন্ধন (self-registration) করলে আপনি **৭ দিনের বিনামূল্যে
  ট্রায়াল** পাবেন। মেয়াদ শেষে চালিয়ে যেতে আমাদের সাথে যোগাযোগ করে
  সাবস্ক্রিপশন সক্রিয় করতে হবে।
- ট্রায়াল/সাবস্ক্রিপশনের মেয়াদ শেষ হয়ে গেলে অ্যাকাউন্ট স্বয়ংক্রিয়ভাবে
  সাময়িকভাবে স্থগিত হতে পারে (গ্রেস পিরিয়ড শেষে) — ডেটা মুছে ফেলা হয়
  না, শুধু অ্যাক্সেস সাময়িকভাবে বন্ধ থাকে।
- আপনার অ্যাকাউন্টের নিরাপত্তা (পাসওয়ার্ড, ২FA) বজায় রাখার দায়িত্ব
  আপনার নিজের।

### ৩. সাবস্ক্রিপশন ও পেমেন্ট
- পেমেন্ট বর্তমানে ম্যানুয়ালি ট্র্যাক করা হয় (bKash/Nagad/ব্যাংক
  ট্রান্সফারের মাধ্যমে) — কোনো স্বয়ংক্রিয় কার্ড-চার্জিং নেই।
- পেমেন্ট নিশ্চিত হওয়ার পর আমাদের টিম সাবস্ক্রিপশনের মেয়াদ বাড়িয়ে দেয়।
- ফি ফেরতযোগ্য কিনা তা কেস-বাই-কেস আলোচনা সাপেক্ষে।

### ৪. আপনার দায়িত্ব
- সঠিক ও আইনসম্মত তথ্য প্রদান করা।
- অন্য কারো ব্যক্তিগত তথ্য অননুমোদিতভাবে সংগ্রহ/প্রদর্শন না করা।
- অ্যাকাউন্টের লগইন তথ্য গোপন রাখা।

### ৫. ডেটার মালিকানা
আপনার প্রতিষ্ঠানের সব ডেটা (ছাত্র তথ্য, ফি রেকর্ড, ফলাফল ইত্যাদি) আপনার
প্রতিষ্ঠানেরই মালিকানাধীন থাকে। আমরা শুধু সেবা প্রদানের জন্য এটা
প্রসেস/সংরক্ষণ করি।

### ৬. সাপোর্ট অ্যাক্সেস
প্রযুক্তিগত সহায়তার প্রয়োজনে আমাদের প্ল্যাটফর্ম অ্যাডমিন সাময়িকভাবে
আপনার অ্যাকাউন্টে "impersonation" মোডে প্রবেশ করতে পারেন — প্রতিটি
এমন প্রবেশ audit log-এ রেকর্ড থাকে। বিস্তারিত জানতে আমাদের Privacy
Policy দেখুন।

### ৭. সেবা স্থগিত/বাতিলকরণ
মেয়াদোত্তীর্ণ পেমেন্ট, শর্ত ভঙ্গ, বা অপব্যবহারের ক্ষেত্রে আমরা অ্যাকাউন্ট
স্থগিত/বাতিল করার অধিকার রাখি। যুক্তিসঙ্গত ক্ষেত্রে আগে থেকে জানানোর
চেষ্টা করব।

### ৮. দায়বদ্ধতার সীমাবদ্ধতা
এই সেবা "যেমন আছে" ভিত্তিতে প্রদান করা হয়। ডেটা ক্ষতি/সেবা বিঘ্নের জন্য
আমাদের দায়বদ্ধতা প্রযোজ্য আইন অনুযায়ী সর্বোচ্চ সীমা পর্যন্ত সীমাবদ্ধ।

### ৯. পরিবর্তন
এই শর্তাবলী সময়ে সময়ে পরিবর্তিত হতে পারে। বড় পরিবর্তনের ক্ষেত্রে
বিজ্ঞপ্তি দেওয়ার চেষ্টা করব।

### ১০. যোগাযোগ
প্রশ্ন থাকলে আপনার প্ল্যাটফর্ম অ্যাডমিনিস্ট্রেটরের সাথে যোগাযোগ করুন।
"""

TERMS_EN = """
## Terms of Service

**Last updated: July 15, 2026**

### 1. Introduction
Smart Madrasa ERP ("we", "the Service") is a multi-tenant SaaS platform
providing student admission, attendance, exam/results, fee collection, and
institutional management software to madrasas/educational institutions. By
using this Service, you agree to these Terms.

### 2. Accounts & Free Trial
- Self-registration grants a **7-day free trial**. To continue after it
  ends, contact us to activate a paid subscription.
- When a trial/subscription expires, the account may be automatically
  suspended (after a grace period) — data is not deleted, access is simply
  paused.
- You are responsible for maintaining your account's security (password, 2FA).

### 3. Subscription & Payment
- Payments are currently tracked manually (via bKash/Nagad/bank transfer) —
  there is no automatic card billing.
- Once payment is confirmed, our team extends your subscription period.
- Refund eligibility is handled on a case-by-case basis.

### 4. Your Responsibilities
- Provide accurate and lawful information.
- Do not collect/display others' personal data without authorization.
- Keep your account credentials confidential.

### 5. Data Ownership
All data belonging to your institution (student records, fee records,
results, etc.) remains owned by your institution. We process/store it only
to provide the Service.

### 6. Support Access
For technical support, our platform administrators may temporarily enter
your account in "impersonation" mode — every such access is recorded in an
audit log. See our Privacy Policy for details.

### 7. Suspension/Termination
We reserve the right to suspend/terminate accounts for expired payment,
breach of terms, or misuse. We will attempt to notify you in advance where
reasonable.

### 8. Limitation of Liability
This Service is provided "as is." Our liability for data loss or service
interruption is limited to the maximum extent permitted by applicable law.

### 9. Changes
These Terms may change from time to time. We will attempt to notify you of
material changes.

### 10. Contact
For questions, please contact your platform administrator.
"""

PRIVACY_BN = """
## গোপনীয়তা নীতি

**সর্বশেষ হালনাগাদ: ১৫ জুলাই, ২০২৬**

### ১. আমরা কী তথ্য সংগ্রহ করি
- **ছাত্রের তথ্য**: নাম, পিতা-মাতার নাম, মোবাইল, জন্ম তারিখ, ঠিকানা,
  ছবি, ভর্তি নথি, জাতীয় পরিচয়পত্র/জন্ম নিবন্ধন নম্বর।
- **স্টাফ/অ্যাডমিনের তথ্য**: নাম, মোবাইল, লগইন তথ্য।
- **আর্থিক তথ্য**: ফি পেমেন্টের রেকর্ড, দাতার তথ্য।
- **ব্যবহারের তথ্য**: লগইন লগ, audit trail (কে কী পরিবর্তন করেছে)।

### ২. আমরা কীভাবে ব্যবহার করি
শুধুমাত্র সেবা প্রদানের জন্য — ভর্তি প্রক্রিয়া, হাজিরা, ফলাফল, ফি
আদায়, SMS/WhatsApp-এ বিজ্ঞপ্তি পাঠানো। বিজ্ঞাপনের জন্য বিক্রি করা হয় না।

### ৩. জাতীয় পরিচয়পত্র নম্বর — বিশেষ সুরক্ষা
ছাত্রের জাতীয় পরিচয়পত্র/জন্ম নিবন্ধন নম্বর ডাটাবেজে **এনক্রিপ্ট করা**
অবস্থায় সংরক্ষণ করা হয় (plaintext না) — সিস্টেম নিজেও এটা সরাসরি পড়তে
পারে না, শুধু নির্দিষ্ট প্রয়োজনে ডিক্রিপ্ট করা হয়।

### ৪. মুছে ফেলার অধিকার (Right to Erasure)
কোনো অভিভাবক/ছাত্র চাইলে প্রতিষ্ঠানের অ্যাডমিনের মাধ্যমে ব্যক্তিগত তথ্য
(নাম, NID, মোবাইল, ঠিকানা, ছবি, ডকুমেন্ট) স্থায়ীভাবে মুছে ফেলার অনুরোধ
করতে পারেন। একাডেমিক/আর্থিক রেকর্ড (নম্বর, হাজিরা, ফি ইতিহাস) প্রাতিষ্ঠানিক
রেকর্ড-রক্ষণ প্রয়োজনে সংরক্ষিত থাকতে পারে, ব্যক্তি-শনাক্তকারী তথ্য ছাড়াই।

### ৫. তৃতীয় পক্ষের সেবা
আমরা নিম্নলিখিত তৃতীয়-পক্ষ সেবা ব্যবহার করি: SMS/WhatsApp গেটওয়ে
(বিজ্ঞপ্তি পাঠাতে), bKash/Nagad (পেমেন্ট), ক্লাউড স্টোরেজ (ছবি/ডকুমেন্ট
রাখতে)। এদের নিজস্ব গোপনীয়তা নীতি প্রযোজ্য হতে পারে।

### ৬. ডেটা সংরক্ষণ ও ব্যাকআপ
নিয়মিত স্বয়ংক্রিয় ব্যাকআপ নেওয়া হয় (এনক্রিপ্টেড ফর্মে) দুর্ঘটনাজনিত
ডেটা হারানো ঠেকাতে।

### ৭. প্ল্যাটফর্ম সাপোর্ট অ্যাক্সেস
আমাদের প্ল্যাটফর্ম অ্যাডমিনিস্ট্রেটর প্রযুক্তিগত সহায়তার জন্য সাময়িকভাবে
আপনার প্রতিষ্ঠানের অ্যাকাউন্টে প্রবেশ করতে পারেন ("impersonation")।
প্রতিটি প্রবেশ ও প্রস্থান audit log-এ স্থায়ীভাবে রেকর্ড থাকে, স্বচ্ছতার
জন্য।

### ৮. শিশু/নাবালকের তথ্য
এই সেবার একটা বড় অংশ নাবালক ছাত্র-ছাত্রীর তথ্য নিয়ে কাজ করে। এই তথ্য
সংগ্রহ/সংরক্ষণ প্রতিষ্ঠান ও অভিভাবকের সম্মতিতে হয় বলে ধরে নেওয়া হয় —
প্রতিষ্ঠানের নিজস্ব দায়িত্ব এই সম্মতি নিশ্চিত করা।

### ৯. আপনার অধিকার
আপনার তথ্য দেখা, সংশোধন, বা মুছে ফেলার অনুরোধ করার অধিকার আছে —
আপনার প্রতিষ্ঠানের অ্যাডমিনের মাধ্যমে যোগাযোগ করুন।

### ১০. পরিবর্তন
এই নীতি সময়ে সময়ে পরিবর্তিত হতে পারে।

### ১১. যোগাযোগ
গোপনীয়তা সংক্রান্ত প্রশ্নে আপনার প্ল্যাটফর্ম অ্যাডমিনিস্ট্রেটরের সাথে
যোগাযোগ করুন।
"""

PRIVACY_EN = """
## Privacy Policy

**Last updated: July 15, 2026**

### 1. What Data We Collect
- **Student data**: name, parents' names, mobile number, date of birth,
  address, photo, admission documents, national ID/birth registration number.
- **Staff/admin data**: name, mobile number, login credentials.
- **Financial data**: fee payment records, donor information.
- **Usage data**: login logs, audit trail (who changed what).

### 2. How We Use It
Solely to provide the Service — admissions, attendance, results, fee
collection, SMS/WhatsApp notifications. We do not sell it for advertising.

### 3. National ID Numbers — Special Protection
Student national ID/birth registration numbers are stored in the database
in **encrypted** form (not plaintext) — even the system itself cannot read
it directly; it is decrypted only when specifically needed.

### 4. Right to Erasure
A guardian/student may, through their institution's admin, request
permanent erasure of personal data (name, NID, mobile, address, photo,
documents). Academic/financial records (marks, attendance, fee history) may
be retained for institutional record-keeping, without identifying personal
information.

### 5. Third-Party Services
We use the following third-party services: SMS/WhatsApp gateways (for
notifications), bKash/Nagad (payments), cloud storage (for photos/
documents). Their own privacy policies may apply.

### 6. Data Retention & Backups
Regular automated backups (in encrypted form) are taken to prevent
accidental data loss.

### 7. Platform Support Access
Our platform administrators may temporarily access your institution's
account for technical support ("impersonation"). Every such entry and exit
is permanently recorded in an audit log, for transparency.

### 8. Children's Data
A large part of this Service handles data belonging to minor students. This
collection/storage is assumed to occur with the institution's and
guardian's consent — it is the institution's own responsibility to ensure
this consent.

### 9. Your Rights
You have the right to view, correct, or request erasure of your data —
please contact your institution's admin.

### 10. Changes
This policy may change from time to time.

### 11. Contact
For privacy-related questions, please contact your platform administrator.
"""


def _current_lang() -> str:
    return st.session_state.get("language", "bn")


def _render_legal_page(content_bn: str, content_en: str, back_label_bn: str, back_label_en: str):
    inject_css()
    content = content_bn if _current_lang() == "bn" else content_en
    back_label = back_label_bn if _current_lang() == "bn" else back_label_en

    st.markdown(
        f"""<div style="max-width:760px;margin:2rem auto 0 auto">
          <div style="text-align:center;margin-bottom:1rem">
            <div style="font-size:2rem">🕌</div>
            <div style="font-weight:700;color:{PALETTE['primary']}">Smart Madrasa ERP</div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 6, 1])
    with col:
        st.markdown(content)
        st.markdown(
            f'<div style="text-align:center;margin:1.5rem 0;font-size:0.85rem">'
            f'<a href="/" target="_self">{back_label}</a></div>',
            unsafe_allow_html=True,
        )


def render_terms():
    _render_legal_page(TERMS_BN, TERMS_EN, "← লগইন পেজে ফিরে যান", "← Back to login")


def render_privacy():
    _render_legal_page(PRIVACY_BN, PRIVACY_EN, "← লগইন পেজে ফিরে যান", "← Back to login")
