import difflib
import html
import json
import re
import time
from datetime import datetime, timedelta
import requests
import streamlit as st

# قائمة الموديلات بالترتيب: لو الأول مزدحم (503) أو مش موجود (404)، الكود يجرب اللي بعده تلقائيًا
MODEL_FALLBACK_LIST = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash"]
RETRY_DELAYS_SECONDS = [4, 10]  # نجرب نفس الموديل 3 مرات إجمالي قبل الانتقال للموديل التالي

st.set_page_config(
    page_title="نظام تصنيف الشكاوى | مسمار MisMar",
    page_icon="🗂️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap');

    html, body, [class*="css"]  {
        font-family: 'Tajawal', sans-serif;
        direction: rtl;
        text-align: right;
    }

    .stApp {
        background-color: #0B0F19;
        color: #F3F4F6;
    }

    .mismar-header {
        background: linear-gradient(135deg, #881337 0%, #0F172A 100%);
        padding: 28px;
        border-radius: 20px;
        border: 1px solid #E11D4833;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        margin-bottom: 28px;
        text-align: center;
    }

    .mismar-header h1 {
        color: #FB7185;
        font-weight: 800;
        font-size: 2.2rem;
        margin-bottom: 8px;
    }

    .mismar-header p {
        color: #9CA3AF;
        font-size: 1.05rem;
    }

    .ticket-card {
        background: linear-gradient(180deg, #111827 0%, #1F2937 100%);
        border-right: 6px solid #E11D48;
        padding: 20px 22px;
        border-radius: 14px;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        margin-bottom: 18px;
    }

    .ticket-card h4 {
        color: #FB7185;
        margin: 0 0 14px 0;
        font-weight: 800;
    }

    .field-row {
        padding: 9px 0;
        border-bottom: 1px dashed #374151;
        line-height: 1.8;
    }

    .field-row:last-of-type {
        border-bottom: none;
    }

    .field-label {
        color: #9CA3AF;
        font-size: 0.92rem;
        display: block;
    }

    .field-value {
        color: #F9FAFB;
        font-size: 1.12rem;
        font-weight: 700;
    }

    .badge-ok   { color: #34D399; font-size: 0.85rem; margin-right: 8px; }
    .badge-warn { color: #FBBF24; font-size: 0.85rem; margin-right: 8px; }
    .badge-bad  { color: #F87171; font-size: 0.85rem; margin-right: 8px; }

    .reasoning-box {
        background-color: #0F172A;
        border: 1px solid #374151;
        border-radius: 10px;
        padding: 12px 16px;
        margin-top: 12px;
        color: #D1D5DB;
        line-height: 1.85;
        white-space: pre-wrap;
    }

    .stButton>button {
        width: 100%;
        background: linear-gradient(90deg, #E11D48 0%, #BE123C 100%);
        color: #FFFFFF;
        font-weight: 700;
        font-size: 1.15rem;
        padding: 14px;
        border-radius: 12px;
        border: none;
        box-shadow: 0 4px 14px rgba(225, 29, 72, 0.3);
        transition: all 0.3s ease;
    }

    .stButton>button:hover {
        background: linear-gradient(90deg, #BE123C 0%, #9F1239 100%);
        transform: translateY(-2px);
    }
</style>
""", unsafe_allow_html=True)

# ====== مصادر البيانات في Metabase (أرقام الأسئلة الداخلية Card ID) ======
METABASE_BASE_URL = "https://analysis.mismarapp.com"
METABASE_CARD_IDS = {
    "tickets": 15395,          # تذاكر الطلب
    "comments": 15394,         # محادثات الطلب والتعليقات الداخلية
    "status_history": 15393,   # تسلسل حالات الطلب
    "pricing": 15392,          # طلبات التسعير وعروض الأسعار
    "updates": 16192,          # updates per order
    "ticket_comments": 16191,  # ticket comments per order
}

# أقصى عدد حروف من كل مصدر بيتبعت للموديل (عشان منضربش حد التوكنز في الفري تير)
MAX_SOURCE_CHARS = 30000

# توقيتات جدول التذاكر بترجع من الـ SQL بتوقيت الرياض فعلاً (Asia/Riyadh)، عكس سجل الحالات
# اللي بيرجع بتوقيت UTC. لو لقيت وقت فتح التذكرة المعروض في قسم التحقق متأخر 3 ساعات
# عن الحقيقي في لوحة التحكم، غيّر القيمة دي لـ False.
TICKET_TIMES_ALREADY_RIYADH = True

# ساعات العمل المعتبرة لحساب مدة الإغلاق (لو وقت الإغلاق متاح في البيانات): 8ص-6م، والجمعة إجازة
WORK_START_HOUR = 8
WORK_END_HOUR = 18
FRIDAY_WEEKDAY = 4    # في بايثون: الإثنين=0 ... الخميس=3 ... الجمعة=4
THURSDAY_WEEKDAY = 3
ARABIC_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]

TICKET_ID_KEYS = ["Ticket_Id", "ticket_id", "TicketId", "id"]
TICKET_CREATED_KEYS = ["Ticket_Created_At", "Created_At", "created_at", "createdAt"]
TICKET_CLOSED_KEYS = ["Ticket_Closed_At", "Closed_At", "closed_at", "Resolved_At", "Ticket_Resolved_At"]

# ====== القوائم المعتمدة (القيم مكتوبة حرفيًا زي ما هي في الشيت) ======
MAIN_CATEGORIES = [
    "جودة الخدمة",
    "تأخير في الخدمة",
    "الغاء الطلب",
    "ملاحظات او استفسارات",
    "التشخيص",
    "مشكلة لها علاقة بالتأمين",
    "مكرر",
    "مشكلة في التحديثات",
    "شكوى غير صحيحة",
    "جودة الاصلاح",
    "جودة النقل",
    "اعتراض على اخلاء المسؤولية",
    "جودة قطع الغيار",
    "غير واضح",
    "تغيير مركز",
    "مشكلة في التطبيق/تقمية",
]

SUB_CATEGORIES = {
    "مشاكل الإصلاح والجودة": [
        "ظهور مشكله بعد الاصلاح / الخدمة",
        "الاصلاح غير مرضي",
        "استمرار المشكلة بعد الاصلاح",
        "عودة المشكلة بعد الاصلاح / بعد فترة من الاصلاح",
        "الاصلاح غير مكتمل",
        "عدم الاصلاح",
        "جودة عامة سيئة",
        "جودة تشطيب او نظافة غير مرضية",
        "سوء معاملة السيارة بالمركز",
    ],
    "قطع الغيار والتسعير": [
        "جودة قطع الغيار",
        "قطع غيار غير مطابقة / متضررة",
        "فلتر غير اصلي - صيانة",
        "غرض / اغراض مفقودة",
        "قطعة / قطع مفقودة",
        "تأخير قطع الغيار",
        "تأخير التسعير",
        "تاخير التحديث بعرض السعر",
        "تاخير التحديث من مشرفين قطع الغيار",
        "اعتراض على عرض السعر / ملاحظات تخص عرض السعر",
        "رفض عرض السعر / قبول عرض السعر بالخطأ / رفع عرض سعر بالخطأ",
        "طلب فواتير / تعديل فاتورة / حذف فاتورة / تاخير اصدار الفواتير",
    ],
    "التشخيص": [
        "اعتراض على بند/بنود في التشخيص",
        "تشخيص خاطئ",
        "تشخيص غير كامل",
        "صعوبة في التشخيص",
        "تأخير في التشخيص",
        "اعتراض على مدة التشخيص",
        "طلب اعادة فحص",
    ],
    "التأخير والمواعيد": [
        "تأخير الاصلاح / البدء في الاصلاح",
        "عدم الالتزام بالموعد",
        "نأخير تسليم السيارة للعميل / موعد ارجاع السيارة للعميل",
        "تاخير التقدير / اعتراض علي التقدير / اعتراض على تقدير المركز",
        "تاخير تقديم الخدمة / تاخير سحب المركبة / جدولة سطحة",
        "اعادة جدولة موعد / تاجيل موعد الخدمة / جدولة موعد",
    ],
    "التأمين والمطالبات": [
        "توقف الاصلاح بناء على طلب التأمين",
        "رفض التأمين للقطع",
        "نسبة استهلاك /تحمل",
        "رفع مطالبة اضافي / تأخير رفع المطالبة/ اضافي",
        "انتظار الرد على المطالبة / انتظار تعميد / تعميد اضافي من التأمين",
        "اعتراض على التعميد / طلب تغيير في التعميد",
        "تحديثات متأخرة من التأمين / اثبات انه تم الرفع للتأمين",
        "الغاء المطالبة / تغيير مطالبة / رفع مطالبة خطأ",
        "اعتراض على الاتلاف / اعتراض على عدم الاتلاف / اتلاف المركبة",
    ],
    "الخدمة والتواصل والعملاء": [
        "عدم الرد بشكل واضح على العميل / عدم الرد على العميل",
        "عدم التجاوب / التواصل مع العميل / تأخير التواصل مع العميل",
        "عدم التحديث للعميل / تأخر التحديث للعميل / تحديثات غير واضحة للعميل",
        "عدم خدمة العميل بالشكل المطلوب / عدم تقديم الخدمة",
        "شكوى على خدمة العملاء / شكوى على المركز / شكوى على مزود الخدمة / شكوى على المشرف / شكوى على عامل السطحة",
        "اسلوب التعامل / سوء تفاهم / سوء متابعة وتنسيق",
        "اعطاء معلومات خاطئة / خطأ في البيانات",
    ],
    "المدفوعات والاسترجاع": [
        "ارجاع مبلغ / استفسار عن موعد ارجاع المبلغ / تاخير ارجاع مبلغ / طلب اعادة مبلغ التحميل",
        "مشكلة في الدفع / طريقة الدفع / العميل يرغب بالدفع تابي /تمارا / تاخير ارسال رابط دفع",
    ],
    "حالات أخرى": [
        "الغاء الطلب / اعتراض على الغاء الطلب / تاخير الغاء الطلب / اعادة فتح طلب",
        "تغيير مركز / اعتراض على تغيير المركز / توجيه العميل لمركز خطأ / المركز مغلق / رفض المركز استلام السيارة",
        "مكرر شكوى سابقة / شكوى غير صحيحة / طلب تعويض / مشكلة تقنية / مشكلة في التطبيق",
    ],
}
ALL_SUB_CATEGORIES = [opt for group in SUB_CATEGORIES.values() for opt in group]

RESOLVED_OPTIONS = ["نعم", "لا", "غير واضح"]

RESPONSIBLE_OPTIONS = [
    "مركز", "قطع غيار", "مشرف BO", "عميل", "مزود الخدمة", "سبب خارجي- التأمين",
    "Customer Care", "لا يوجد", "سبب خارجي - السطحة", "غير واضح", "العامل",
    "سبب خارجي -الوكيل", "ادارة داخليه", "Account manger", "سبب خارجي - مركز التقدير",
    "CONF B2C", "REPA BO", "PERA B2C", "INSP B2C", "CONF BO", "INSP BO", "PREP BO",
    "خطأ تقني", "REPA B2C", "ops", "منتجات",
]

DELAY_REASONS = [
    "لا يوجد تأخير",
    "تاخر اغلاق الشكوى",
    "تاخر استجابة العميل",
    "تاخر المعالجة من المشرف",
    "غير واضح",
    "تم فتح الشكوى يوم الخميس مساءا او الجمعة",
    "ارسال التأمين للخطاب",
    "المتابعة مع الشكوى الاخرى لانها مكررة",
    "البحث في امر القطعة",
    "المتابعة حتى تم تركيب القطعة",
    "حتى تم رفع المطالبة",
    "المتابعة في امر الشكوى",
    "اسناد الشكوى كان خطأ",
    "تاخر رد مشرفين قطع الغيار",
    "تاخر رد المركز",
    "المتابعه حتى تم وصول القطع",
]


# ====== الاتصال بـ Metabase (تسجيل دخول برمجي + استعلام الأسئلة الداخلية) ======
def get_metabase_session_token(username: str, password: str) -> str:
    """يسجّل دخول Metabase برمجيًا بيوزر/باسورد حقيقيين ويرجع سيشن توكن صالح"""
    try:
        res = requests.post(
            f"{METABASE_BASE_URL}/api/session",
            json={"username": username, "password": password},
            timeout=15
        )
    except requests.exceptions.RequestException as e:
        raise Exception(f"تعذر الوصول إلى Metabase (مشكلة شبكة): {str(e)}")

    if not (200 <= res.status_code < 300):
        raise Exception(
            f"فشل تسجيل الدخول لـ Metabase (كود {res.status_code}). "
            f"تأكد من صحة اليوزر نيم والباسورد. الرد: {res.text[:200]}"
        )

    return res.json()["id"]


def card_result_to_objects(query_result: dict) -> list:
    """يحوّل رد Metabase (data.cols + data.rows منفصلين) لقائمة dict لكل صف"""
    data = query_result.get("data", {})
    cols = data.get("cols", [])
    rows = data.get("rows", [])
    if not cols or not rows:
        return []
    col_names = [c.get("name") or c.get("display_name") for c in cols]
    return [dict(zip(col_names, row)) for row in rows]


def fetch_card_data(card_id: int, order_id: int, session_token: str):
    """يستعلم سؤال داخلي واحد في Metabase بالسيشن توكن ورقم الطلب كمتغير"""
    parameters = [
        {
            "type": "number/=",
            "target": ["variable", ["template-tag", "order_id"]],
            "value": str(order_id)
        }
    ]
    try:
        res = requests.post(
            f"{METABASE_BASE_URL}/api/card/{card_id}/query",
            headers={"X-Metabase-Session": session_token},
            json={"parameters": parameters},
            timeout=60
        )
    except requests.exceptions.RequestException as e:
        return f"Error: {str(e)}"

    # Metabase بيرجّع 202 (Accepted) كرد ناجح لاستعلامات الأسئلة، فنقبل أي كود 2xx
    if not (200 <= res.status_code < 300):
        return f"Error HTTP {res.status_code}: {res.text[:200]}"

    try:
        body = res.json()
    except ValueError:
        return f"Error: Response wasn't valid JSON: {res.text[:200]}"

    # لو الاستعلام نفسه فشل، Metabase بيرجّع برضو 202 لكن جواه status=failed ورسالة الخطأ الحقيقية
    if isinstance(body, dict) and (body.get("status") == "failed" or body.get("error")):
        return f"Error (Metabase query failed): {str(body.get('error'))[:300]}"

    return card_result_to_objects(body)


def fetch_order_data(order_id: int, session_token: str) -> dict:
    """جلب بيانات الطلب من كل المصادر الستة في Metabase"""
    return {
        key: fetch_card_data(card_id, order_id, session_token)
        for key, card_id in METABASE_CARD_IDS.items()
    }


def summarize_sources(order_data: dict) -> str:
    """ملخص صحة البيانات المسترجعة من كل مصدر (للتحقق السريع في الواجهة)"""
    lines = []
    for key, value in order_data.items():
        if isinstance(value, list):
            if value:
                columns = ", ".join(str(c) for c in value[0].keys()) if isinstance(value[0], dict) else "—"
                lines.append(f"✅ {key}: {len(value)} صف | الأعمدة: {columns}")
            else:
                lines.append(f"⚠️ {key}: رجع فاضي (0 صف)")
        else:
            lines.append(f"❌ {key}: {str(value)[:250]}")
    return "\n".join(lines)


# ====== أدوات الوقت وحقائق التذاكر المحسوبة برمجيًا ======
def _parse_naive_dt(value):
    """يحاول يفهم صيغة التاريخ القادمة من Metabase (مثال: 2026-08-10 04:04 PM)"""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_ticket_dt(value):
    dt = _parse_naive_dt(value)
    if dt and not TICKET_TIMES_ALREADY_RIYADH:
        dt += timedelta(hours=3)
    return dt


def _format_timedelta(td) -> str:
    total_minutes = int(td.total_seconds() // 60)
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours:
        parts.append(f"{hours} ساعة")
    if minutes or not parts:
        parts.append(f"{minutes} دقيقة")
    return " و".join(parts)


def business_hours_duration(start: datetime, end: datetime) -> timedelta:
    """ساعات العمل الفعلية بين تاريخين: 8ص-6م ومن غير يوم الجمعة"""
    if start >= end:
        return timedelta(0)
    total = timedelta(0)
    current = start
    while current < end:
        day_start = current.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
        day_end = current.replace(hour=WORK_END_HOUR, minute=0, second=0, microsecond=0)
        if current.weekday() != FRIDAY_WEEKDAY:
            seg_start = max(current, day_start)
            seg_end = min(end, day_end)
            if seg_start < seg_end:
                total += seg_end - seg_start
        current = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return total


def _first_value(row: dict, keys: list):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def build_ticket_facts(tickets: list) -> str:
    """
    حقائق محسوبة بدقة ببايثون لكل تذكرة: وقت الفتح ويومه، وهل اتفتحت الخميس مساءً أو الجمعة،
    ومدة الإغلاق (لو وقت الإغلاق متاح في بيانات التذكرة). الموديل مش بيحسب أي وقت بنفسه.
    """
    lines = []
    for ticket in tickets:
        if not isinstance(ticket, dict):
            continue
        ticket_id = _first_value(ticket, TICKET_ID_KEYS)
        created = _parse_ticket_dt(_first_value(ticket, TICKET_CREATED_KEYS))
        closed = _parse_ticket_dt(_first_value(ticket, TICKET_CLOSED_KEYS))
        status = ticket.get("Ticket_Status")

        parts = [f"- تذكرة {ticket_id}"]
        if status:
            parts.append(f"(حالتها: {status})")

        if created:
            weekday = created.weekday()
            day_name = ARABIC_WEEKDAYS[weekday]
            parts.append(f": فُتحت {created.strftime('%Y-%m-%d %H:%M')} يوم {day_name}.")
            thursday_evening = weekday == THURSDAY_WEEKDAY and created.hour >= WORK_END_HOUR
            if weekday == FRIDAY_WEEKDAY or thursday_evening:
                parts.append("⚠️ فُتحت الخميس مساءً أو يوم الجمعة (خارج أيام/ساعات العمل).")
            else:
                parts.append("فُتحت داخل أيام العمل (ليست الخميس مساءً ولا الجمعة).")
        else:
            parts.append(": وقت فتح التذكرة غير متوفر في البيانات.")

        if created and closed:
            raw_gap = closed - created
            work_gap = business_hours_duration(created, closed)
            parts.append(
                f"أُغلقت {closed.strftime('%Y-%m-%d %H:%M')}، "
                f"مدة الإغلاق الخام {_format_timedelta(raw_gap)} | بساعات العمل الفعلية {_format_timedelta(work_gap)}."
            )
        else:
            parts.append("وقت الإغلاق غير متوفر في جدول التذاكر؛ استنتجه من تعليقات التذكرة والتحديثات إن وُجد.")

        lines.append(" ".join(parts))

    return "\n".join(lines) if lines else "لا توجد تذاكر."


# ====== تجهيز البيانات للبرومبت ======
def compact_dump(data, max_chars: int = MAX_SOURCE_CHARS) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        return text[:max_chars] + f"... [تم اقتطاع باقي البيانات لتجاوز {max_chars} حرف]"
    return text


def format_sub_categories() -> str:
    parts = []
    for group, options in SUB_CATEGORIES.items():
        parts.append(f"[{group}]")
        parts.extend(f"- {option}" for option in options)
    return "\n".join(parts)


OUTPUT_EXAMPLE = """[
  {
    "ticket_id": "رقم التذكرة كما ورد في البيانات",
    "main_category": "قيمة واحدة من قائمة التصنيف الأساسي",
    "sub_category": "قيمة واحدة من قائمة التصنيف الفرعي",
    "resolved": "نعم | لا | غير واضح",
    "responsible": ["قيمة أو أكثر من قائمة مسؤول الضرر"],
    "delay_reason": "قيمة واحدة من قائمة أسباب التأخير",
    "reasoning": "جملة أو جملتان توضحان الدليل الذي بُني عليه التصنيف"
  }
]"""


def build_prompt(order_id: int, order_data: dict, tickets: list, ticket_facts: str) -> str:
    return f"""
أنت مدقق جودة وشكاوى في شركة صيانة السيارات (مسمار - MisMar).
مهمتك تصنيف تذاكر الشكاوى الخاصة بالطلب رقم #{order_id} وفق القوائم المعتمدة أدناه، بالاعتماد فقط على البيانات الفعلية الموجودة، ودون أي تخمين.

=== 🎫 التذاكر المطلوب تصنيفها (المصدر الأساسي: Description = وصف الشكوى، Result = نتيجة التذكرة) ===
{compact_dump(tickets)}

=== ⏱️ حقائق محسوبة برمجيًا لكل تذكرة (إلزامية الاستخدام، ولا تحسب أي وقت بنفسك) ===
{ticket_facts}

=== 💬 تعليقات التذاكر (ticket comments) ===
{compact_dump(order_data.get('ticket_comments'))}

=== 🔄 تحديثات الطلب (updates) ===
{compact_dump(order_data.get('updates'))}

=== 💬 محادثات الطلب والتعليقات الداخلية (التشغيل / المركز / العميل) ===
{compact_dump(order_data.get('comments'))}

=== ⏳ سجل حالات الطلب (ملحوظة: توقيتاته بتوقيت UTC أي متأخرة 3 ساعات عن توقيت الرياض) ===
{compact_dump(order_data.get('status_history'))}

=== 💰 طلبات التسعير وعروض الأسعار ===
{compact_dump(order_data.get('pricing'))}

=== 🎯 قواعد التصنيف (إلزامية) ===
1. التصنيف الأساسي: قيمة واحدة بالضبط من القائمة، حسب جوهر الشكوى كما في وصف التذكرة ونتيجتها وتعليقاتها.
2. التصنيف الفرعي: قيمة واحدة بالضبط من القائمة الفرعية، الأقرب لتفاصيل الشكوى، ويفضل أن تكون متسقة مع التصنيف الأساسي.
3. هل تم حل المشكلة: "نعم" فقط لو وُجد دليل صريح (نتيجة التذكرة أو تعليقاتها أو التحديثات) على حل المشكلة فعليًا أو رضا العميل؛ "لا" لو ثبت استمرارها أو رفض العميل للحل؛ وغير ذلك "غير واضح".
4. مسؤول الضرر: اختيار متعدد. حدد الجهات التي تسببت فعليًا في المشكلة محل الشكوى (وليس في تأخر إغلاقها).
   - "لا يوجد" تُستخدم وحدها فقط (لو الشكوى غير صحيحة أو لا طرف مسؤول)، ولا تُدمج مع جهات أخرى.
   - "غير واضح" لو لا يمكن تحديد المسؤول من البيانات.
   - لا تخترع جهات من خارج القائمة.
5. سبب التأخير في غلق الشكوى: قيمة واحدة. استخدم وقت فتح التذكرة (والإغلاق إن وُجد) وتسلسل تعليقاتها والتحديثات لتحديد سبب الفجوة بين الفتح والإغلاق.
   - لا يوجد حد زمني رسمي للتأخير؛ احكم من الأدلة وليس من الرقم وحده، و"لا يوجد تأخير" لو أُغلقت الشكوى بمعالجة معقولة ومتتابعة.
   - لو الحقائق المحسوبة تقول إن التذكرة فُتحت الخميس مساءً أو الجمعة وكانت العطلة هي سبب الفجوة، اختر "تم فتح الشكوى يوم الخميس مساءا او الجمعة"، إلا لو في التعليقات سبب أوضح وأقوى (مثل تأخر رد المركز) فاختره بدلًا منها.
6. لو الأدلة غير كافية لأي حقل فيه خيار "غير واضح" فاختره، ولا تخترع معلومات.
7. اكتب القيم حرفيًا كما هي في القوائم، بدون أي تغيير في الكتابة أو المسافات.
8. حقل reasoning: جملة أو جملتان بالعربية توضحان أهم دليل اعتمدت عليه، بدون ذكر أرقام التذاكر الداخلية.

=== 📋 قائمة التصنيف الأساسي ===
{chr(10).join('- ' + option for option in MAIN_CATEGORIES)}

=== 📋 قائمة التصنيف الفرعي (مقسمة لمجموعات للتسهيل فقط؛ القيمة المختارة تُكتب بدون اسم المجموعة) ===
{format_sub_categories()}

=== 📋 قائمة هل تم حل المشكلة ===
{' | '.join(RESOLVED_OPTIONS)}

=== 📋 قائمة مسؤول الضرر (اختيار متعدد) ===
{' | '.join(RESPONSIBLE_OPTIONS)}

=== 📋 قائمة سبب التأخير في غلق الشكوى ===
{chr(10).join('- ' + option for option in DELAY_REASONS)}

=== 📤 شكل الإخراج ===
أخرج JSON فقط (بدون أي نص خارجه)، عبارة عن مصفوفة فيها عنصر واحد لكل تذكرة من التذاكر أعلاه وبنفس الترتيب:
{OUTPUT_EXAMPLE}
"""


# ====== استدعاء Gemini مع موديلات بديلة وإعادة محاولة ======
def call_gemini_with_fallback(api_key: str, prompt_text: str) -> str:
    """
    يجرب كل موديل في MODEL_FALLBACK_LIST بالترتيب، وبيعيد المحاولة على نفس الموديل
    كذا مرة (بفاصل زمني) لو الخطأ 503 (زحمة مؤقتة) قبل ما ينتقل للموديل اللي بعده.
    خطأ 401 (مصادقة) بيوقف فورًا لأنه مش هيتحل بتغيير الموديل.
    """
    attempt_errors = []

    for model_name in MODEL_FALLBACK_LIST:
        for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            headers = {'Content-Type': 'application/json', 'x-goog-api-key': api_key}
            data = {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "topP": 0.9,
                    "responseMimeType": "application/json"
                }
            }

            try:
                response = requests.post(url, headers=headers, json=data, timeout=120)
            except requests.exceptions.RequestException as e:
                attempt_errors.append(f"{model_name}: تعذر الوصول (مشكلة شبكة) — {str(e)}")
                break

            if response.status_code == 200:
                result_json = response.json()
                try:
                    return result_json['candidates'][0]['content']['parts'][0]['text']
                except (KeyError, IndexError):
                    attempt_errors.append(
                        f"{model_name}: رد بشكل غير متوقع (200 لكن بدون نص) — "
                        f"{json.dumps(result_json, ensure_ascii=False)[:200]}"
                    )
                    break

            elif response.status_code == 401:
                raise Exception(
                    "خطأ مصادقة (401): المفتاح غير صالح أو منتهي الصلاحية. "
                    "اعمل مفتاح جديد من https://aistudio.google.com/app/apikey"
                )

            elif response.status_code == 503 and attempt < len(RETRY_DELAYS_SECONDS):
                time.sleep(RETRY_DELAYS_SECONDS[attempt])
                continue

            else:
                attempt_errors.append(
                    f"{model_name}: خطأ {response.status_code} (بعد {attempt + 1} محاولة/محاولات) — "
                    f"{response.text[:200]}"
                )
                break

    raise Exception("فشلت كل الموديلات المتاحة:\n" + "\n".join(attempt_errors))


# ====== قراءة رد الموديل والتحقق من القيم مقابل القوائم المعتمدة ======
def parse_model_json(text: str) -> list:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end <= start:
            raise Exception(f"تعذر قراءة رد الموديل كـ JSON. الرد الخام:\n{text[:500]}")
        parsed = json.loads(cleaned[start:end + 1])
    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_choice(value, options: list):
    """
    يرجّع (القيمة النهائية، الحالة): exact لو مطابقة تامة للقائمة، corrected لو اتصلحت لأقرب
    قيمة في القائمة (فرق بسيط في الكتابة)، invalid لو مفيش قيمة قريبة في القائمة.
    """
    cleaned = _norm(value)
    norm_map = {_norm(option): option for option in options}
    if cleaned in norm_map:
        return norm_map[cleaned], "exact"
    close = difflib.get_close_matches(cleaned, list(norm_map.keys()), n=1, cutoff=0.8)
    if close:
        return norm_map[close[0]], "corrected"
    return cleaned, "invalid"


def _worst_flag(flags: list) -> str:
    if not flags or "invalid" in flags:
        return "invalid"
    return "corrected" if "corrected" in flags else "exact"


def normalize_item(raw) -> dict:
    if not isinstance(raw, dict):
        raw = {}

    main, main_flag = normalize_choice(raw.get("main_category", ""), MAIN_CATEGORIES)
    sub, sub_flag = normalize_choice(raw.get("sub_category", ""), ALL_SUB_CATEGORIES)
    resolved, resolved_flag = normalize_choice(raw.get("resolved", ""), RESOLVED_OPTIONS)
    delay, delay_flag = normalize_choice(raw.get("delay_reason", ""), DELAY_REASONS)

    responsible_raw = raw.get("responsible", [])
    if isinstance(responsible_raw, str):
        responsible_raw = re.split(r"[,،;]\s*", responsible_raw)

    responsible, responsible_flags = [], []
    for entry in responsible_raw:
        if not str(entry).strip():
            continue
        value, flag = normalize_choice(entry, RESPONSIBLE_OPTIONS)
        if value not in responsible:
            responsible.append(value)
            responsible_flags.append(flag)

    # "لا يوجد" لازم تكون لوحدها
    if len(responsible) > 1 and "لا يوجد" in responsible:
        idx = responsible.index("لا يوجد")
        responsible.pop(idx)
        responsible_flags.pop(idx)

    return {
        "ticket_id": raw.get("ticket_id", ""),
        "main_category": main,
        "sub_category": sub,
        "resolved": resolved,
        "responsible": responsible,
        "delay_reason": delay,
        "reasoning": str(raw.get("reasoning", "")).strip(),
        "flags": {
            "main_category": main_flag,
            "sub_category": sub_flag,
            "resolved": resolved_flag,
            "responsible": _worst_flag(responsible_flags),
            "delay_reason": delay_flag,
        },
    }


# ====== التحليل الرئيسي ======
def analyze_complaints(api_key: str, metabase_username: str, metabase_password: str,
                       order_id: int, ticket_id_filter: int):
    session_token = get_metabase_session_token(metabase_username, metabase_password)
    order_data = fetch_order_data(order_id, session_token)
    sources_summary = summarize_sources(order_data)

    tickets = order_data.get("tickets")
    if not isinstance(tickets, list):
        raise Exception(f"تعذر جلب تذاكر هذا الطلب من Metabase:\n{sources_summary}")
    if not tickets:
        raise Exception(f"لا توجد أي تذاكر لهذا الطلب.\n{sources_summary}")

    if ticket_id_filter:
        selected = [
            t for t in tickets
            if isinstance(t, dict) and str(_first_value(t, TICKET_ID_KEYS)) == str(ticket_id_filter)
        ]
        if not selected:
            available = ", ".join(
                str(_first_value(t, TICKET_ID_KEYS)) for t in tickets if isinstance(t, dict)
            )
            raise Exception(
                f"لا توجد تذكرة برقم {ticket_id_filter} ضمن تذاكر هذا الطلب. "
                f"التذاكر الموجودة: {available}"
            )
        tickets = selected

    ticket_facts = build_ticket_facts(tickets)
    prompt_text = build_prompt(order_id, order_data, tickets, ticket_facts)

    raw_text = call_gemini_with_fallback(api_key, prompt_text)
    items = [normalize_item(raw) for raw in parse_model_json(raw_text)]

    truncated = [
        key for key, value in order_data.items()
        if len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) > MAX_SOURCE_CHARS
    ]

    return {
        "items": items,
        "raw_model_text": raw_text,
        "ticket_facts": ticket_facts,
        "sources_summary": sources_summary,
        "truncated_sources": truncated,
        "order_id": order_id,
    }


# ====== عرض النتائج ======
FIELD_LABELS = [
    ("main_category", "التصنيف الأساسي"),
    ("sub_category", "تصنيف فرعي"),
    ("resolved", "هل تم حل المشكلة"),
    ("responsible", "مسؤول الضرر"),
    ("delay_reason", "سبب التأخير في غلق الشكوى"),
]

FLAG_BADGES = {
    "exact": ('badge-ok', "✅"),
    "corrected": ('badge-warn', "⚠️ صُحّح تلقائيًا لأقرب قيمة في القائمة"),
    "invalid": ('badge-bad', "❌ خارج القائمة — راجعه يدويًا"),
}


def display_value(item: dict, field: str) -> str:
    value = item[field]
    if field == "responsible":
        return ", ".join(value) if value else "—"
    return value or "—"


def render_ticket_result(item: dict):
    rows_html = ""
    for field, label in FIELD_LABELS:
        css_class, badge_text = FLAG_BADGES[item["flags"][field]]
        rows_html += (
            f'<div class="field-row">'
            f'<span class="field-label">{html.escape(label)}</span>'
            f'<span class="field-value">{html.escape(display_value(item, field))}</span>'
            f'<span class="{css_class}">{html.escape(badge_text)}</span>'
            f'</div>'
        )

    reasoning_html = (
        f'<div class="reasoning-box"><b>سبب التصنيف:</b>\n{html.escape(item["reasoning"])}</div>'
        if item["reasoning"] else ""
    )

    st.markdown(
        f'<div class="ticket-card"><h4>🎫 تذكرة {html.escape(str(item["ticket_id"]))}</h4>'
        f'{rows_html}{reasoning_html}</div>',
        unsafe_allow_html=True
    )

    # نصوص جاهزة للنسخ في الشيت (عمود الإجراء/الحالة مش متضمن لأنه بيتكتب يدويًا)
    st.caption("نسخ للشيت — التصنيف الأساسي + التصنيف الفرعي (خليتين متجاورتين):")
    st.code("\t".join([item["main_category"], item["sub_category"]]), language=None)
    st.caption("نسخ للشيت — هل تم الحل + مسؤول الضرر + سبب التأخير (3 خلايا متجاورة):")
    st.code("\t".join([item["resolved"], display_value(item, "responsible"), item["delay_reason"]]), language=None)


# ====== الواجهة ======
with st.sidebar:
    st.image("https://mismarapp.com/static/media/logo.f6cf70e4.svg", width=200)
    st.markdown("### ⚙️ إعدادات النظام")

    api_key_input = st.text_input(
        "Gemini API Key",
        value="",
        type="password",
        help="أدخل مفتاح الـ API الخاص بـ Gemini"
    )

    st.caption(
        "🔁 الموديلات المستخدمة بالترتيب (فولباك تلقائي لو موديل مزدحم أو غير متاح): "
        + " ← ".join(MODEL_FALLBACK_LIST)
    )

    st.markdown("---")
    st.markdown("**بيانات دخول Metabase**")
    st.caption("الروابط العامة اتلغت، فالكود بيسجّل دخول ببيانات حسابك الحقيقية في كل مرة.")

    metabase_username_input = st.text_input(
        "Metabase Username/Email",
        value="",
        help="يوزر نيم أو إيميل حساب Metabase بتاعك"
    )

    metabase_password_input = st.text_input(
        "Metabase Password",
        value="",
        type="password",
        help="باسورد حساب Metabase بتاعك"
    )

st.markdown("""
<div class="mismar-header">
    <h1>🗂️ نظام تصنيف الشكاوى (MisMar Complaints Classifier)</h1>
    <p>قراءة تذكرة الشكوى ونتيجتها وتعليقاتها وربطها ببيانات الطلب، ثم تصنيفها وفق القوائم المعتمدة</p>
</div>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("📋 بيانات الطلب")
    order_id = st.number_input("رقم الطلب (Order ID)", value=1037315, step=1)
    ticket_id_filter = st.number_input(
        "رقم التذكرة (اختياري)",
        value=0,
        min_value=0,
        step=1,
        help="سيبها 0 عشان يصنّف كل تذاكر الطلب، أو اكتب رقم تذكرة بعينها."
    )
    st.markdown("<br>", unsafe_allow_html=True)
    analyze_btn = st.button("🚀 تصنيف الشكوى")

with col2:
    st.subheader("📊 نتيجة التصنيف")

    if analyze_btn:
        if not api_key_input:
            st.error("⚠️ يرجى إدخال Gemini API Key أولاً من القائمة الجانبية.")
        elif not metabase_username_input or not metabase_password_input:
            st.error("⚠️ يرجى إدخال بيانات دخول Metabase (يوزر نيم وباسورد) أولاً من القائمة الجانبية.")
        else:
            with st.spinner("⏳ جاري قراءة التذاكر وربطها ببيانات الطلب وتصنيفها..."):
                try:
                    st.session_state['complaints_result'] = analyze_complaints(
                        api_key_input,
                        metabase_username_input,
                        metabase_password_input,
                        order_id,
                        ticket_id_filter
                    )
                except Exception as e:
                    st.session_state.pop('complaints_result', None)
                    st.error(f"❌ حدث خطأ أثناء التحليل: {str(e)}")

    result = st.session_state.get('complaints_result')
    if result:
        for item in result["items"]:
            render_ticket_result(item)

        if result["truncated_sources"]:
            st.warning(
                "⚠️ تم اقتطاع بيانات المصادر التالية لأنها طويلة جدًا: "
                + ", ".join(result["truncated_sources"])
            )

        with st.expander("🛠️ (Debug) صحة البيانات المسترجعة والحقائق المحسوبة"):
            st.markdown("**حالة كل مصدر بيانات** (لو لقيت ❌ يبقى فيه مشكلة في الاتصال أو الاستعلام):")
            st.text(result["sources_summary"])
            st.markdown("**حقائق التذاكر المحسوبة برمجيًا** (قارن وقت الفتح بالوقت الحقيقي في لوحة التحكم):")
            st.text(result["ticket_facts"])
            st.markdown("**رد الموديل الخام:**")
            st.code(result["raw_model_text"], language="json")
    elif not analyze_btn:
        st.info("👈 قم بإدخال رقم الطلب والضغط على زر التصنيف لعرض النتائج هنا.")
