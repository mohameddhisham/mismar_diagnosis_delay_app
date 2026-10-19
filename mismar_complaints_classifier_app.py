import difflib
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
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

    .evidence-list {
        margin: 8px 0 0 0;
        padding-right: 18px;
        list-style: none;
    }

    .evidence-list li {
        padding: 4px 0;
        line-height: 1.7;
        font-size: 0.97rem;
    }

    .ev-ok   { color: #A7F3D0; }
    .ev-warn { color: #FDE68A; }
    .ev-src  { color: #9CA3AF; font-size: 0.85rem; }
    .ev-none { color: #9CA3AF; margin-top: 6px; }

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

# نوع باراميتر order_id مختلف من سؤال لسؤال: السؤالين الجداد (updates / ticket comments)
# المتغير فيهم Text مش Number (Metabase رجّع خطأ 500: Parameter type must be one of :category, :text, :string/=)
METABASE_PARAM_TYPES = {
    "updates": "string/=",
    "ticket_comments": "string/=",
}
DEFAULT_PARAM_TYPE = "number/="
# لو Metabase رفض نوع الباراميتر، الكود بيجرب الأنواع دي بالترتيب تلقائيًا
PARAM_TYPE_FALLBACKS = ["number/=", "string/=", "category"]

# أقصى عدد حروف من كل مصدر بيتبعت للموديل (عشان منضربش حد التوكنز في الفري تير)
MAX_SOURCE_CHARS = 30000

# توقيتات جدول التذاكر بترجع من الـ SQL بتوقيت الرياض فعلاً (Asia/Riyadh)، عكس سجل الحالات
# اللي بيرجع بتوقيت UTC (فبنضيف له 3 ساعات). لو وقت فتح التذكرة المعروض في قسم التحقق
# متأخر 3 ساعات عن الحقيقي في لوحة التحكم، غيّر القيمة دي لـ False.
TICKET_TIMES_ALREADY_RIYADH = True

# ساعات العمل المعتبرة لحساب المدد: 8ص-6م، والجمعة إجازة
WORK_START_HOUR = 8
WORK_END_HOUR = 18
FRIDAY_WEEKDAY = 4    # في بايثون: الإثنين=0 ... الخميس=3 ... الجمعة=4
THURSDAY_WEEKDAY = 3
ARABIC_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]

TICKET_ID_KEYS = ["Ticket_Id", "ticket_id", "TicketId", "id"]
TICKET_CREATED_KEYS = ["Ticket_Created_At", "Created_At", "created_at", "createdAt"]
TICKET_CLOSED_KEYS = ["Ticket_Closed_At", "Closed_At", "closed_at", "Resolved_At", "Ticket_Resolved_At"]

# ====== مراحل الطلب (B2C والتأمين) وفريق كل مرحلة ======
# Confirmation: بداية الطلب، تأكيد الطلب، الاتفاق على الميعاد وعلى ميعاد سحب السيارة
# Inspection: جاري الفحص والتشخيص / التسعير / بانتظار قطع الغيار
# REPA: مراحل الإصلاح وتسليم السيارة وحتى نهاية الطلب
# ⚠️ ده مؤشر مساعد للموديل مش حكم نهائي. الحالات اللي مش مذكورة هنا (معلق، تم الإلغاء، تم الرفض)
# بتاخد مرحلة آخر حالة معروفة قبلها.
PHASE_LABELS = {"CONF": "Confirmation", "INSP": "Inspection", "REPA": "REPA"}

# نوع الطلب موجود في عمود بجدول التذاكر: تأمين / B2C / MM / WALKIN
ORDER_TYPE_LABELS = {"INSURANCE": "تأمين", "B2C": "B2C", "MM": "MM", "WALKIN": "WALKIN"}

# فرق المراحل حسب نوع الطلب. التأمين فرقه BO. أما MM وWALKIN فمفيهمش مراحل أصلًا
# (المسؤول الداخلي فيهم: مشرف BO أو مزود الخدمة).
STAGE_TEAMS = {
    "B2C": {"CONF": "CONF B2C", "INSP": "INSP B2C", "REPA": "REPA B2C"},
    "INSURANCE": {"CONF": "CONF BO", "INSP": "INSP BO", "REPA": "REPA BO"},
}
ALL_STAGE_TEAMS = [team for teams in STAGE_TEAMS.values() for team in teams.values()]
NO_STAGE_TYPES = {"MM", "WALKIN"}

# لو عارف اسم عمود نوع الطلب بالظبط في جدول التذاكر اكتبه هنا، وإلا الكود بيكتشفه تلقائيًا
ORDER_TYPE_COLUMN = ""
ORDER_TYPE_NAME_HINTS = ["type", "نوع"]
# أعمدة معروفة مش هي عمود النوع (عشان منلخبطش بين قيمة زي "تأمين" كتصنيف تذكرة ونوع الطلب)
NON_TYPE_COLUMNS = {
    "Ticket_Category_Name", "Ticket_Description", "Ticket_Result", "Ticket_Status",
    "Ticket_Creator", "Current_Order_Status", "City_Name",
}
# الصيغ المقبولة لكل نوع (بعد التطبيع: بدون تشكيل أو علامات ترقيم، وأشكال الألف موحدة)
ORDER_TYPE_ALIASES = {
    "تامين": "INSURANCE", "التامين": "INSURANCE", "insurance": "INSURANCE", "ins": "INSURANCE",
    "b2c": "B2C", "mm": "MM", "walkin": "WALKIN", "walk in": "WALKIN",
}
STATUS_TO_PHASE = {
    "طلب جديد": "CONF", "طلب فوري": "CONF", "تم التأكيد": "CONF", "تم تأكيد موعدك": "CONF",
    "بانتظار موعد أخذ السيارة": "CONF", "تم تنسيق السطحة": "CONF", "تغيير الموعد": "CONF",
    "بانتظار الجدولة": "CONF", "مسمار في الطريق إليك": "CONF", "تم أخذ السيارة": "CONF",
    "تم التسليم للمركز": "CONF",
    "جاري الفحص والتشخيص": "INSP", "جاري التسعير": "INSP",
    "بانتظار الموافقة على عرض السعر": "INSP", "عرض السعر قبل الاستلام": "INSP",
    "بانتظار البدء بالإصلاحات": "REPA", "جاري العمل": "REPA", "اختبار جودة الإصلاحات": "REPA",
    "جاهزة": "REPA", "بانتظار الدفع": "REPA", "بانتظار جدولة الاستلام": "REPA",
    "بانتظار موعد الاستلام": "REPA", "سيارتك في الطريق إليك": "REPA",
    "جاهزة للاستلام": "REPA", "تم التسليم": "REPA",
}

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

# ====== قاعدة الإلغاء ======
# لو الشكوى عن إلغاء طلب:
#   - الإلغاء من العميل  ← أساسي "ملاحظات او استفسارات" + فرعي خيار الإلغاء الوحيد في القايمة الفرعية
#   - الإلغاء من جهتنا   ← أساسي "الغاء الطلب"
# الموديل بيحدد مين صاحب الإلغاء في حقل cancellation، وبايثون هو اللي بيطبق القاعدة (مش الموديل).
CANCELLATION_OPTIONS = ["لا ينطبق", "من العميل", "من مسمار"]
CANCEL_BY_CUSTOMER_MAIN = "ملاحظات او استفسارات"
CANCEL_BY_CUSTOMER_SUB = SUB_CATEGORIES["حالات أخرى"][0]  # "الغاء الطلب / اعتراض على الغاء الطلب / ..."
CANCEL_BY_US_MAIN = "الغاء الطلب"

# حكم الموديل على صحة الشكوى بعد مراجعتها مع تفاصيل الطلب (بيظهر في الكارت للمراجعة)
VALIDITY_OPTIONS = ["صحيحة", "صحيحة جزئيًا", "غير صحيحة", "غير واضح"]

# قيمة عمود الإجراء/الحالة لما الطلب رجيع (فقط "رجيع" لا غير)
RETURN_ACTION_VALUE = "رجيع"


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


def fetch_card_data(card_id: int, order_id: int, session_token: str, preferred_type: str = DEFAULT_PARAM_TYPE):
    """
    يستعلم سؤال داخلي واحد في Metabase بالسيشن توكن ورقم الطلب كمتغير.
    لو Metabase رفض نوع الباراميتر (Invalid parameter type) بيجرب الأنواع التانية تلقائيًا.
    """
    types_to_try = [preferred_type] + [t for t in PARAM_TYPE_FALLBACKS if t != preferred_type]
    last_error = "Error: لم تتم أي محاولة"

    for param_type in types_to_try:
        parameters = [
            {
                "type": param_type,
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
            last_error = f"Error HTTP {res.status_code}: {res.text[:300]}"
            if "Invalid parameter type" in res.text:
                continue  # نوع الباراميتر مرفوض، نجرب اللي بعده
            return last_error

        try:
            body = res.json()
        except ValueError:
            return f"Error: Response wasn't valid JSON: {res.text[:200]}"

        # لو الاستعلام نفسه فشل، Metabase بيرجّع برضو 202 لكن جواه status=failed ورسالة الخطأ الحقيقية
        if isinstance(body, dict) and (body.get("status") == "failed" or body.get("error")):
            last_error = f"Error (Metabase query failed): {str(body.get('error'))[:300]}"
            if "Invalid parameter type" in str(body.get("error")):
                continue
            return last_error

        return card_result_to_objects(body)

    return last_error


def fetch_order_data(order_id: int, session_token: str) -> dict:
    """جلب بيانات الطلب من كل المصادر الستة في Metabase"""
    return {
        key: fetch_card_data(card_id, order_id, session_token, METABASE_PARAM_TYPES.get(key, DEFAULT_PARAM_TYPE))
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


# ====== أدوات الوقت وسجل الحالات والحقائق المحسوبة برمجيًا ======
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


def _now_riyadh() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)


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


def parse_status_rows(status_history) -> list:
    """
    يحوّل سجل الحالات لقائمة مرتبة بتوقيت الرياض. سجل الحالات بيرجع بتوقيت UTC
    (متأخر 3 ساعات) فبنضيف 3 ساعات هنا عشان يتقارن صح مع توقيتات التذاكر.
    """
    if not isinstance(status_history, list):
        return []
    rows = []
    for row in status_history:
        if not isinstance(row, dict):
            continue
        name = _first_value(row, ["Status_Name", "status_name", "status"])
        opened = _parse_naive_dt(_first_value(row, ["Opened_At", "opened_at", "created_at"]))
        closed_raw = _first_value(row, ["Closed_At", "closed_at", "ended_at"])
        if not name or not opened:
            continue
        opened += timedelta(hours=3)

        if closed_raw and str(closed_raw).strip().lower() == "currently active":
            closed, active = _now_riyadh(), True
        else:
            closed, active = _parse_naive_dt(closed_raw), False
            if closed:
                closed += timedelta(hours=3)
        if closed is None:
            continue
        rows.append({"status": str(name).strip(), "opened": opened, "closed": closed, "active": active})
    rows.sort(key=lambda r: r["opened"])
    return rows


def build_status_timeline_facts(rows: list) -> str:
    """الجدول الزمني للحالات بتوقيت الرياض، مع المدة الخام وبساعات العمل الفعلية لكل حالة"""
    if not rows:
        return "⚠️ لا توجد بيانات سجل حالات صالحة للحساب."
    lines = []
    for r in rows:
        raw_gap = r["closed"] - r["opened"]
        work_gap = business_hours_duration(r["opened"], r["closed"])
        end_text = "لسه شغالة حتى الآن" if r["active"] else r["closed"].strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"- {r['status']}: من {r['opened'].strftime('%Y-%m-%d %H:%M')} إلى {end_text} | "
            f"المدة الخام {_format_timedelta(raw_gap)} | بساعات العمل الفعلية {_format_timedelta(work_gap)}"
        )
    total = rows[-1]["closed"] - rows[0]["opened"]
    lines.append(
        f"- إجمالي عمر الطلب من أول حالة لآخر حالة: {_format_timedelta(total)} "
        f"(بساعات العمل الفعلية {_format_timedelta(business_hours_duration(rows[0]['opened'], rows[-1]['closed']))})"
    )
    return "\n".join(lines)


def status_to_phase(status_name: str):
    name = re.sub(r"\s+", " ", str(status_name)).strip()
    if name in STATUS_TO_PHASE:
        return STATUS_TO_PHASE[name]
    if "قطع" in name:  # مثلاً: بانتظار قطع الغيار
        return "INSP"
    return None


def phase_at_time(rows: list, when):
    """حالة الطلب ومرحلته (CONF/INSP/REPA) وقت لحظة معينة (وقت فتح الشكوى)"""
    if not rows or when is None:
        return None, None
    current = rows[0]
    for r in rows:
        if r["opened"] <= when:
            current = r
        else:
            break
    phase = status_to_phase(current["status"])
    if phase is None:
        # حالة مش مصنفة (معلق/إلغاء/رفض): ناخد مرحلة آخر حالة معروفة قبلها
        idx = rows.index(current)
        for earlier in reversed(rows[:idx]):
            phase = status_to_phase(earlier["status"])
            if phase:
                break
    return current["status"], phase


def _match_order_type(value):
    if value is None:
        return None
    return ORDER_TYPE_ALIASES.get(_loose(value))


def detect_order_type(ticket: dict):
    """يرجّع (نوع الطلب، اسم العمود اللي اتقرا منه) من صف تذكرة، أو (None, None) لو مش لاقيه"""
    if not isinstance(ticket, dict):
        return None, None
    if ORDER_TYPE_COLUMN and ORDER_TYPE_COLUMN in ticket:
        found = _match_order_type(ticket[ORDER_TYPE_COLUMN])
        return (found, ORDER_TYPE_COLUMN) if found else (None, None)
    # 1) أعمدة اسمها بيدل على النوع (مثلاً Order_Type)
    for key, value in ticket.items():
        name = str(key).lower()
        if any(hint in name for hint in ORDER_TYPE_NAME_HINTS):
            found = _match_order_type(value)
            if found:
                return found, key
    # 2) أي عمود تاني قيمته بالكامل تساوي نوع معروف (ماعدا الأعمدة المعروفة إنها مش نوع)
    for key, value in ticket.items():
        if key in NON_TYPE_COLUMNS:
            continue
        found = _match_order_type(value)
        if found:
            return found, key
    return None, None


def detect_order_type_for_tickets(tickets: list, target_ids: list):
    """نوع الطلب من التذاكر المطلوبة أولًا، وبعدها باقي تذاكر الطلب"""
    ordered = [t for t in tickets if isinstance(t, dict) and str(_first_value(t, TICKET_ID_KEYS)) in target_ids]
    ordered += [t for t in tickets if isinstance(t, dict) and t not in ordered]
    for ticket in ordered:
        order_type, column = detect_order_type(ticket)
        if order_type:
            return order_type, column
    return None, None


def describe_order_type(order_type, column) -> str:
    if order_type is None:
        return ("نوع الطلب: غير متوفر في بيانات التذاكر ← افترض B2C ما لم تدل الأدلة صراحة على غير ذلك، "
                "وبيّن في الأسباب إن النوع مش معروف.")
    label = ORDER_TYPE_LABELS[order_type]
    source = f" (من عمود {column})" if column else ""
    if order_type in NO_STAGE_TYPES:
        return (f"نوع الطلب: {label}{source} ← لا توجد مراحل؛ ممنوع استخدام أي فريق مرحلة، "
                f"والمسؤول الداخلي هو «مشرف BO» أو «مزود الخدمة».")
    teams = STAGE_TEAMS[order_type]
    return (f"نوع الطلب: {label}{source} ← فرق المراحل: Confirmation={teams['CONF']} | "
            f"Inspection={teams['INSP']} | REPA={teams['REPA']}.")


def apply_order_type_rules(responsible: list, order_type):
    """
    يراجع مسؤول الضرر مقابل نوع الطلب:
    - B2C: أي فريق مرحلة BO بيتحول لنظيره B2C. التأمين: العكس.
    - MM/WALKIN: مفيش مراحل، فأي فريق مرحلة بيتشال.
    يرجّع (القائمة النهائية، ملاحظات توضح اللي اتغيّر).
    """
    notes, result = [], []
    for team in responsible:
        new_team = team
        if order_type == "B2C" and team in STAGE_TEAMS["INSURANCE"].values():
            new_team = team.replace(" BO", " B2C")
            notes.append(f"تم تحويل «{team}» إلى «{new_team}» لأن نوع الطلب B2C.")
        elif order_type == "INSURANCE" and team in STAGE_TEAMS["B2C"].values():
            new_team = team.replace(" B2C", " BO")
            notes.append(f"تم تحويل «{team}» إلى «{new_team}» لأن نوع الطلب تأمين.")
        elif order_type in NO_STAGE_TYPES and team in ALL_STAGE_TEAMS:
            notes.append(
                f"تم حذف «{team}» لأن نوع الطلب {ORDER_TYPE_LABELS[order_type]} لا توجد فيه مراحل "
                f"(المسؤول الداخلي: مشرف BO أو مزود الخدمة)."
            )
            continue
        if new_team not in result:
            result.append(new_team)
    return result, notes


def build_ticket_facts(tickets: list, status_rows: list, target_ids: set, order_type=None, type_column=None) -> str:
    """
    حقائق محسوبة بدقة ببايثون: نوع الطلب، ولكل تذكرة: وقت الفتح ويومه، وهل اتفتحت الخميس مساءً أو
    الجمعة، ومرحلة الطلب وفريقها وقت فتح الشكوى (من سجل الحالات)، ومدة الإغلاق لو وقت الإغلاق متاح.
    """
    lines = [describe_order_type(order_type, type_column)]
    for ticket in tickets:
        if not isinstance(ticket, dict):
            continue
        ticket_id = _first_value(ticket, TICKET_ID_KEYS)
        created = _parse_ticket_dt(_first_value(ticket, TICKET_CREATED_KEYS))
        closed = _parse_ticket_dt(_first_value(ticket, TICKET_CLOSED_KEYS))
        status = ticket.get("Ticket_Status")
        is_target = str(ticket_id) in target_ids if target_ids else True

        parts = [f"- تذكرة {ticket_id}" + (" (🎯 المطلوب تصنيفها)" if is_target else " (سياق فقط)")]
        if status:
            parts.append(f"(حالتها: {status})")

        if created:
            weekday = created.weekday()
            parts.append(f": فُتحت {created.strftime('%Y-%m-%d %H:%M')} يوم {ARABIC_WEEKDAYS[weekday]}.")
            thursday_evening = weekday == THURSDAY_WEEKDAY and created.hour >= WORK_END_HOUR
            if weekday == FRIDAY_WEEKDAY or thursday_evening:
                parts.append("⚠️ فُتحت الخميس مساءً أو يوم الجمعة (خارج أيام/ساعات العمل).")
            else:
                parts.append("فُتحت داخل أيام العمل (ليست الخميس مساءً ولا الجمعة).")

            status_name, phase = phase_at_time(status_rows, created)
            if status_name and order_type in NO_STAGE_TYPES:
                parts.append(f"حالة الطلب وقت فتح الشكوى: «{status_name}» (لا توجد مراحل لنوع الطلب هذا).")
            elif status_name and phase:
                team = STAGE_TEAMS.get(order_type or "B2C")[phase]
                parts.append(
                    f"حالة الطلب وقت فتح الشكوى: «{status_name}» ← مرحلة {PHASE_LABELS[phase]} (فريق {team})."
                )
            elif status_name:
                parts.append(f"حالة الطلب وقت فتح الشكوى: «{status_name}» (مرحلتها غير محددة، استنتجها من الأدلة).")
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

    return "\n".join(lines)


# ====== التحقق من الأدلة: هل الاقتباس موجود فعلاً في البيانات؟ ======
_ARABIC_MARKS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")


def _loose(text) -> str:
    """تطبيع متسامح للمقارنة: يشيل التشكيل والتطويل وعلامات الترقيم ويوحّد أشكال الألف والياء والتاء المربوطة"""
    t = _ARABIC_MARKS.sub("", str(text))
    t = re.sub("[أإآ]", "ا", t).replace("ى", "ي").replace("ة", "ه")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _collect_strings(obj, out: list):
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_strings(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _collect_strings(value, out)
    elif obj is not None:
        out.append(str(obj))


def build_haystack(order_data: dict, extra_texts: list) -> str:
    """كل النصوص اللي الموديل شافها (البيانات الخام + الحقائق المحسوبة)، لاستخدامها في التحقق من الاقتباسات"""
    strings = []
    _collect_strings(order_data, strings)
    strings.extend(extra_texts)
    return _loose(" ".join(strings))


def verify_evidence(evidence, haystack: str) -> list:
    """يرجّع قائمة {source, quote, verified}. verified=True لو الاقتباس موجود فعلاً في البيانات"""
    checked = []
    if not isinstance(evidence, list):
        return checked
    for entry in evidence:
        if isinstance(entry, dict):
            source = str(entry.get("source", "")).strip()
            quote = str(entry.get("quote", "")).strip()
        else:
            source, quote = "", str(entry).strip()
        if not quote:
            continue
        loose_quote = _loose(quote)
        checked.append({
            "source": source,
            "quote": quote,
            "verified": len(loose_quote) >= 6 and loose_quote in haystack,
        })
    return checked


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


# التحقق من صحة الشكوى: وصف التذكرة ادعاء من العميل مش حقيقة
COMPLAINT_VERIFICATION_GUIDE = """وصف التذكرة هو ادعاء العميل مش حقيقة. قبل أي تصنيف، تحقق من الادعاء من كل تفاصيل الطلب (تعليقات التذاكر، التحديثات، المحادثات، سجل الحالات، التسعير)، وطلّع كل التصنيفات من الحقيقة اللي ثبتت، مش من كلام العميل.

خطوات التحقق:
 1) إيه اللي العميل بيدّعيه بالظبط؟
 2) إيه اللي حصل فعلًا في الطلب؟ وفي اتفاق أو سبب مبرر (موافقة العميل على مدة، طلب قطع غيار له مدة معروفة، عرض سعر اتقبل... إلخ)؟
 3) الشكوى صحيحة، صحيحة جزئيًا، غير صحيحة، ولا غير واضح؟

مثال: العميل بيشتكي من تأخير والطلب له شهور. لو الأدلة بتبيّن إن فيه اتفاق على طلبية قطع غيار مدتها 90 يوم والـ 90 يوم لسه ما خلصوش ← مفيش تأخير حقيقي، فالشكوى غير صحيحة ومنعتمدش على كلام العميل.

أثر التحقق على الإخراج:
 - غير صحيحة ومفيش مشكلة تانية حقيقية: التصنيف الأساسي «شكوى غير صحيحة»، ومسؤول الضرر «لا يوجد».
 - غير صحيحة لكن الأدلة بتبيّن مشكلة تانية حقيقية (مثلًا العميل متحدّثش في مدة طويلة): صنّف المشكلة الحقيقية، واحكم على مسؤول الضرر عنها.
 - صحيحة جزئيًا: صنّف الجزء الصحيح بس.
 - مسؤول الضرر بيتحدد من الأدلة مش من اتهام العميل، وممكن مفيش مسؤول أصلًا.
 - الأدلة (evidence) لازم تكون من تفاصيل الطلب اللي أثبتت صحة الشكوى أو بطلانها، مش من وصف العميل بس."""


# شرح منطق اختيار مسؤول الضرر (أمثلة توضّح طريقة التفكير، مش جدول ثابت)
RESPONSIBLE_GUIDE = """الفكرة العامة: مسؤول الضرر يتكوّن من جزئين:
 (أ) الجهة اللي نفّذت الشغل أو تسببت فعليًا في المشكلة (مركز، قطع غيار، سطحة، تأمين، عميل... إلخ).
 (ب) الفريق الداخلي المسؤول عن المرحلة اللي حصلت فيها المشكلة، بيدخل كمسؤول لما يكون التقصير في المتابعة أو التحديث أو التنسيق أو التأكد من الجودة أو الالتزام بالمواعيد.

نوع الطلب (موضح أول سطر في الحقائق المحسوبة، ومصدره عمود نوع الطلب في جدول التذاكر) هو اللي بيحدد الفرق:
 - B2C: فرق المراحل CONF B2C / INSP B2C / REPA B2C.
 - تأمين: نفس المراحل لكن فرقها BO: CONF BO / INSP BO / REPA BO.
 - MM أو WALKIN: مفيش مراحل أصلًا، فممنوع استخدام أي فريق مرحلة (CONF / INSP / REPA بنوعيها). المسؤول الداخلي هنا هو «مشرف BO» (لما يكون التقصير في المتابعة أو الجودة أو التحديث) و«مزود الخدمة» (الجهة المنفذة أو المتسببة)، أو الاتنين. والأسباب الخارجية (عميل، قطع غيار، تأمين، سطحة... إلخ) بتفضل متاحة لو الأدلة بتثبتها.

مراحل الطلب (للـ B2C والتأمين فقط):
 - Confirmation: بداية الطلب، تأكيد الطلب، الاتفاق على الميعاد، الاتفاق على ميعاد سحب السيارة، وهكذا.
 - Inspection: لما يكون الطلب في جاري الفحص والتشخيص أو التسعير أو بانتظار قطع الغيار.
 - REPA: مراحل الإصلاحات وتسليم السيارة وحتى نهاية الطلب.
 المرحلة بتتحدد من حالة الطلب وقت فتح الشكوى (موضحة لكل تذكرة في الحقائق المحسوبة مع اسم الفريق حسب نوع الطلب). اعتمد عليها، ولا تخالفها إلا لو الأدلة تثبت بوضوح إن الشكوى تخص مرحلة تانية.

أمثلة توضح طريقة التفكير (مكتوبة بأسماء فرق B2C؛ في التأمين استبدلها بفرق BO، وفي MM/WALKIN استبدل «مركز» بـ«مزود الخدمة» وفريق المرحلة بـ«مشرف BO»). طبّق نفس المنطق على أي شكوى، ولا تعتبرها حالات حصرية:
 1) العميل مش راضي عن جودة الإصلاح: المركز هو المسؤول عن تنفيذ الإصلاح، وREPA B2C هو المسؤول عن التأكد من جودة الإصلاحات ← "مركز" + "REPA B2C".
 2) تأخير في الإصلاح:
    - لو قطع الغيار هي سبب التأخير ← "قطع غيار".
    - لو مش قطع الغيار ← "مركز" هو المسؤول عن التأخير.
    - ويدخل معاه "REPA B2C" لو كان للطلب موعد/مدة محددة للعميل وتخطاها الطلب (مثلًا اتحدد له 5 أيام عمل واستغرق 7) ولم يتم تحديث العميل بأيام العمل الزيادة.
 القاعدة اللي بتتكرر: الجهة المنفّذة هي المسؤولة عن الشغل نفسه، والفريق الداخلي مسؤول عن الجودة والمتابعة وتحديث العميل في مرحلته. لو الدليل بيثبت إن الفريق الداخلي تابع وحدّث العميل بشكل سليم، متدخلوش."""


OUTPUT_EXAMPLE = """[
  {
    "ticket_id": "رقم التذكرة كما ورد في البيانات",
    "cancellation": "لا ينطبق | من العميل | من مسمار",
    "complaint_validity": "صحيحة | صحيحة جزئيًا | غير صحيحة | غير واضح (بعد مراجعتها مع تفاصيل الطلب)",
    "main_category": "قيمة واحدة من قائمة التصنيف الأساسي",
    "sub_category": "قيمة واحدة من قائمة التصنيف الفرعي",
    "is_return": false,
    "responsible": ["قيمة أو أكثر من قائمة مسؤول الضرر"],
    "responsible_reasoning": "من جملتين لثلاث: ليه اخترت الجهات دي، وإيه مرحلة الطلب وقت الشكوى",
    "responsible_evidence": [{"source": "نوع المصدر + التاريخ لو موجود", "quote": "اقتباس حرفي من البيانات"}],
    "delay_reason": "قيمة واحدة من قائمة أسباب التأخير",
    "reasoning": "جملة أو جملتان: سبب التصنيف الأساسي والفرعي وسبب التأخير",
    "evidence": [{"source": "نوع المصدر + التاريخ لو موجود", "quote": "اقتباس حرفي من البيانات"}]
  }
]"""


def build_prompt(order_id: int, order_data: dict, all_tickets: list, target_ids: list,
                 ticket_facts: str, status_timeline: str) -> str:
    target_text = "، ".join(target_ids)
    main_list = "\n".join("- " + option for option in MAIN_CATEGORIES)
    delay_list = "\n".join("- " + option for option in DELAY_REASONS)
    responsible_list = " | ".join(RESPONSIBLE_OPTIONS)

    return f"""
أنت مدقق جودة وشكاوى في شركة صيانة السيارات (مسمار - MisMar).
مهمتك تصنيف تذاكر الشكاوى المحددة أدناه للطلب رقم #{order_id} وفق القوائم المعتمدة، بالاعتماد فقط على البيانات الفعلية الموجودة، ودون أي تخمين.

=== 🎯 التذاكر المطلوب تصنيفها (أرقامها): {target_text} ===
باقي تذاكر الطلب المعروضة تحت هي للسياق فقط (قد تفسّر الشكوى وتحدد المسؤول)؛ استفد منها لكن لا تصنّفها، واكتب عنصرًا في الإخراج لكل تذكرة مطلوبة فقط.

=== 🎫 كل تذاكر الطلب (Description = ادعاء العميل وليس حقيقة، Result = نتيجة التذكرة) ===
{compact_dump(all_tickets)}

=== ⏱️ حقائق محسوبة برمجيًا لكل تذكرة (إلزامية الاستخدام، ولا تحسب أي وقت بنفسك) ===
{ticket_facts}

=== 💬 تعليقات التذاكر (ticket comments) ===
{compact_dump(order_data.get('ticket_comments'))}

=== 🔄 تحديثات الطلب (updates) ===
{compact_dump(order_data.get('updates'))}

=== 💬 محادثات الطلب والتعليقات الداخلية (التشغيل / المركز / العميل) ===
{compact_dump(order_data.get('comments'))}

=== ⏳ الجدول الزمني لحالات الطلب (محسوب برمجيًا، وكل التوقيتات بتوقيت الرياض) ===
{status_timeline}

=== 💰 طلبات التسعير وعروض الأسعار ===
{compact_dump(order_data.get('pricing'))}

=== 🔍 التحقق من الشكوى قبل التصنيف (إلزامي) ===
{COMPLAINT_VERIFICATION_GUIDE}

=== 🧭 كيف نختار مسؤول الضرر ===
{RESPONSIBLE_GUIDE}

=== 🎯 قواعد التصنيف (إلزامية) ===
1. التصنيف الأساسي: قيمة واحدة بالضبط من القائمة، حسب الحقيقة اللي ثبتت من تفاصيل الطلب بعد التحقق، مش حسب ادعاء العميل في وصف التذكرة.
2. التصنيف الفرعي: قيمة واحدة بالضبط من القائمة الفرعية، الأقرب للحقيقة اللي ثبتت، ويفضل أن تكون متسقة مع التصنيف الأساسي.
3. مسؤول الضرر: اختيار متعدد، من الأدلة وحدها مش من كلام العميل (الجهة المنفذة/المتسببة + الفريق الداخلي عند التقصير في المتابعة أو الجودة أو التحديث).
   - لو الشكوى غير صحيحة ومفيش مشكلة حقيقية، اختر "لا يوجد" لوحدها.
   - المقصود المسؤول عن المشكلة الحقيقية، وليس عن التأخر في إغلاق الشكوى.
   - "لا يوجد" تُستخدم وحدها فقط، ولا تُدمج مع جهات أخرى.
   - "غير واضح" لو لا يمكن تحديد المسؤول من البيانات.
   - لا تخترع جهات من خارج القائمة.
4. سبب التأخير في غلق الشكوى: قيمة واحدة. استخدم وقت فتح التذكرة (والإغلاق إن وُجد) وتسلسل تعليقاتها والتحديثات لتحديد سبب الفجوة بين الفتح والإغلاق.
   - لا يوجد حد زمني رسمي للتأخير؛ احكم من الأدلة وليس من الرقم وحده، و"لا يوجد تأخير" لو أُغلقت الشكوى بمعالجة معقولة ومتتابعة.
   - لو الحقائق المحسوبة تقول إن التذكرة فُتحت الخميس مساءً أو الجمعة وكانت العطلة هي سبب الفجوة، اختر "تم فتح الشكوى يوم الخميس مساءا او الجمعة"، إلا لو في التعليقات سبب أوضح وأقوى (مثل تأخر رد المركز) فاختره بدلًا منها.
5. الإجراء (is_return): اجعل القيمة true فقط لو الأدلة تثبت إن الطلب "رجيع"، يعني العميل رجّع السيارة للمركز/للخدمة مرة تانية بسبب نفس المشكلة أو مشكلة ظهرت بعد إصلاح سابق (غالبًا خلال فترة الضمان)، سواء بفحص جديد أو عرض سعر جديد. غير كده false. ولو true لازم يكون في evidence دليل يثبت ده.
6. الأدلة (إلزامية): كل تصنيف لازم يكون معاه دليل.
   - evidence: من 1 إلى 3 أدلة لسبب التصنيف الأساسي والفرعي وسبب التأخير والإجراء.
   - responsible_evidence: من 1 إلى 3 أدلة لسبب اختيار مسؤول الضرر.
   - كل دليل = source (نوع المصدر مع التاريخ/الوقت لو موجود، مثل: «وصف التذكرة»، «نتيجة التذكرة»، «تعليق تذكرة 2026-10-05 10:30»، «تحديث الطلب»، «محادثة الطلب»، «التسعير»، «الجدول الزمني للحالات») + quote (اقتباس حرفي متصل منسوخ كما هو من البيانات أعلاه بما فيها الحقائق المحسوبة، لا يتجاوز 20 كلمة، بدون تعديل أو اختصار أو علامات "..." ).
   - ممنوع تأليف اقتباسات. سيتم التحقق آليًا من وجود كل اقتباس في البيانات، وأي اقتباس غير موجود حرفيًا سيُعلَّم كغير موثوق.
   - لو مفيش أي دليل مباشر، اترك القائمة فاضية، واختر "غير واضح" في الحقول اللي فيها الخيار ده.
7. لو الأدلة غير كافية لأي حقل فيه خيار "غير واضح" فاختره، ولا تخترع معلومات.
8. اكتب القيم حرفيًا كما هي في القوائم، بدون أي تغيير في الكتابة أو المسافات.
9. حقلا responsible_reasoning وreasoning: جمل قصيرة بالعربية، بدون ذكر أرقام التذاكر الداخلية.
10. قاعدة الإلغاء: حدد في حقل cancellation هل الشكوى عن إلغاء طلب. "لا ينطبق" لو الشكوى ليست عن إلغاء، و"من العميل" لو العميل هو من طلب الإلغاء أو قرره، و"من مسمار" لو الإلغاء جاء من جهتنا (مسمار أو التشغيل أو المركز أو النظام). سيتم ضبط التصنيف الأساسي والفرعي تلقائيًا بناءً على هذا الحقل، فاختر قيمته بعناية وبناءً على الأدلة فقط.
11. صحة الشكوى (complaint_validity): حكمك على ادعاء العميل بعد مراجعته مع تفاصيل الطلب، حسب قسم التحقق أعلاه.

=== 📋 قائمة التصنيف الأساسي ===
{main_list}

=== 📋 قائمة التصنيف الفرعي (مقسمة لمجموعات للتسهيل فقط؛ القيمة المختارة تُكتب بدون اسم المجموعة) ===
{format_sub_categories()}

=== 📋 قائمة مسؤول الضرر (اختيار متعدد) ===
{responsible_list}

=== 📋 قائمة سبب التأخير في غلق الشكوى ===
{delay_list}

=== 📤 شكل الإخراج ===
أخرج JSON فقط (بدون أي نص خارجه)، عبارة عن مصفوفة فيها عنصر واحد لكل تذكرة من التذاكر المطلوب تصنيفها ({target_text}) وبنفس ترتيبها:
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


def _truthy(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "نعم", "yes", "1", "رجيع")


def normalize_item(raw) -> dict:
    if not isinstance(raw, dict):
        raw = {}

    main, main_flag = normalize_choice(raw.get("main_category", ""), MAIN_CATEGORIES)
    sub, sub_flag = normalize_choice(raw.get("sub_category", ""), ALL_SUB_CATEGORIES)
    delay, delay_flag = normalize_choice(raw.get("delay_reason", ""), DELAY_REASONS)
    validity, validity_flag = normalize_choice(raw.get("complaint_validity", "غير واضح"), VALIDITY_OPTIONS)
    if validity_flag == "invalid":
        validity = "غير واضح"

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

    # قاعدة الإلغاء (بتتطبق برمجيًا بعد رد الموديل)
    cancellation, cancellation_flag = normalize_choice(raw.get("cancellation", "لا ينطبق"), CANCELLATION_OPTIONS)
    if cancellation_flag == "invalid":
        cancellation = "لا ينطبق"

    rule_note = ""
    if cancellation == "من العميل":
        main, sub = CANCEL_BY_CUSTOMER_MAIN, CANCEL_BY_CUSTOMER_SUB
        main_flag = sub_flag = "exact"
        rule_note = (
            f"قاعدة الإلغاء: الإلغاء من العميل ← التصنيف الأساسي «{main}» "
            f"والتصنيف الفرعي «{sub}»."
        )
    elif cancellation == "من مسمار":
        main = CANCEL_BY_US_MAIN
        main_flag = "exact"
        rule_note = f"قاعدة الإلغاء: الإلغاء من جهتنا ← التصنيف الأساسي «{main}»."

    return {
        "ticket_id": raw.get("ticket_id", ""),
        "cancellation": cancellation,
        "complaint_validity": validity,
        "rule_note": rule_note,
        "main_category": main,
        "sub_category": sub,
        "action": RETURN_ACTION_VALUE if _truthy(raw.get("is_return", False)) else "",
        "responsible": responsible,
        "responsible_reasoning": str(raw.get("responsible_reasoning", "")).strip(),
        "responsible_evidence_raw": raw.get("responsible_evidence", []),
        "delay_reason": delay,
        "reasoning": str(raw.get("reasoning", "")).strip(),
        "evidence_raw": raw.get("evidence", []),
        "flags": {
            "main_category": main_flag,
            "sub_category": sub_flag,
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

    all_tickets = order_data.get("tickets")
    if not isinstance(all_tickets, list):
        raise Exception(f"تعذر جلب تذاكر هذا الطلب من Metabase:\n{sources_summary}")
    if not all_tickets:
        raise Exception(f"لا توجد أي تذاكر لهذا الطلب.\n{sources_summary}")

    all_ids = [str(_first_value(t, TICKET_ID_KEYS)) for t in all_tickets if isinstance(t, dict)]

    # رقم التذكرة بيحدد التذكرة (التذاكر) المطلوب تصنيفها فقط؛ باقي التذاكر بتفضل في سياق الموديل
    if ticket_id_filter:
        if str(ticket_id_filter) not in all_ids:
            raise Exception(
                f"لا توجد تذكرة برقم {ticket_id_filter} ضمن تذاكر هذا الطلب. "
                f"التذاكر الموجودة: {', '.join(all_ids)}"
            )
        target_ids = [str(ticket_id_filter)]
    else:
        target_ids = all_ids

    status_rows = parse_status_rows(order_data.get("status_history"))
    status_timeline = build_status_timeline_facts(status_rows)
    if not status_rows:
        # لو سجل الحالات مش متاح، بنبعت الخام عشان الموديل يشوف الخطأ/البيانات
        status_timeline += "\n(البيانات الخام): " + compact_dump(order_data.get("status_history"), 2000)

    order_type, order_type_column = detect_order_type_for_tickets(all_tickets, target_ids)
    ticket_facts = build_ticket_facts(all_tickets, status_rows, set(target_ids), order_type, order_type_column)
    prompt_text = build_prompt(order_id, order_data, all_tickets, target_ids, ticket_facts, status_timeline)

    raw_text = call_gemini_with_fallback(api_key, prompt_text)
    items = [normalize_item(raw) for raw in parse_model_json(raw_text)]

    # نحتفظ فقط بالتذاكر المطلوبة (لو الموديل رجّع تذاكر زيادة)، ولو الأرقام مش متطابقة نسيب كل النتائج
    filtered = [item for item in items if str(item["ticket_id"]) in target_ids]
    if filtered:
        items = filtered

    haystack = build_haystack(order_data, [ticket_facts, status_timeline])
    order_type_label = ORDER_TYPE_LABELS.get(order_type, "غير محدد")
    for item in items:
        item["evidence"] = verify_evidence(item.pop("evidence_raw"), haystack)
        item["responsible_evidence"] = verify_evidence(item.pop("responsible_evidence_raw"), haystack)

        # مراجعة مسؤول الضرر مقابل نوع الطلب (تحويل/حذف فرق المراحل غير المناسبة)
        item["responsible"], item["type_notes"] = apply_order_type_rules(item["responsible"], order_type)
        if not item["responsible"]:
            item["flags"]["responsible"] = "invalid"
        item["order_type_label"] = order_type_label

    truncated = [
        key for key, value in order_data.items()
        if len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) > MAX_SOURCE_CHARS
    ]

    return {
        "items": items,
        "raw_model_text": raw_text,
        "ticket_facts": ticket_facts,
        "status_timeline": status_timeline,
        "sources_summary": sources_summary,
        "truncated_sources": truncated,
        "target_ids": target_ids,
        "order_id": order_id,
        "order_type": order_type,
        "order_type_label": order_type_label,
        "order_type_column": order_type_column,
    }


# ====== عرض النتائج ======
FLAG_BADGES = {
    "exact": ('badge-ok', "✅"),
    "corrected": ('badge-warn', "⚠️ صُحّح تلقائيًا لأقرب قيمة في القائمة"),
    "invalid": ('badge-bad', "❌ خارج القائمة — راجعه يدويًا"),
}


def display_responsible(item: dict) -> str:
    return ", ".join(item["responsible"]) if item["responsible"] else "—"


def evidence_html(entries: list) -> str:
    if not entries:
        return '<div class="ev-none">لا توجد أدلة مباشرة مذكورة.</div>'
    items_html = ""
    for entry in entries:
        icon, css = ("✅", "ev-ok") if entry["verified"] else ("⚠️", "ev-warn")
        source = f' <span class="ev-src">({html.escape(entry["source"])})</span>' if entry["source"] else ""
        warning = "" if entry["verified"] else ' <span class="ev-src">— الاقتباس غير موجود حرفيًا في البيانات، راجعه</span>'
        items_html += f'<li class="{css}">{icon} «{html.escape(entry["quote"])}»{source}{warning}</li>'
    return f'<ul class="evidence-list">{items_html}</ul>'


def reason_box(title: str, text: str, entries: list) -> str:
    body = html.escape(text) if text else "—"
    return f'<div class="reasoning-box"><b>{html.escape(title)}</b>\n{body}{evidence_html(entries)}</div>'


def sheet_reason_text(text: str, entries: list) -> str:
    """سبب + أدلة في سطر واحد جاهز للصق في خلية واحدة في الشيت"""
    parts = [text.strip()] if text.strip() else []
    quotes = []
    for entry in entries:
        quote = f"«{entry['quote']}»"
        if entry["source"]:
            quote += f" ({entry['source']})"
        if not entry["verified"]:
            quote += " [غير موثّق]"
        quotes.append(quote)
    if quotes:
        parts.append("الأدلة: " + " ، ".join(quotes))
    return re.sub(r"\s*\n\s*", " | ", " — ".join(parts))


def render_ticket_result(item: dict):
    def row(label, value, flag=None):
        badge = ""
        if flag:
            css_class, badge_text = FLAG_BADGES[flag]
            badge = f'<span class="{css_class}">{html.escape(badge_text)}</span>'
        return (
            f'<div class="field-row"><span class="field-label">{html.escape(label)}</span>'
            f'<span class="field-value">{html.escape(value)}</span>{badge}</div>'
        )

    rows_html = (
        row("نوع الطلب", item.get("order_type_label", "غير محدد"))
        + row("صحة الشكوى بعد المراجعة", item.get("complaint_validity", "غير واضح"))
        + row("التصنيف الأساسي", item["main_category"], item["flags"]["main_category"])
        + row("تصنيف فرعي", item["sub_category"], item["flags"]["sub_category"])
        + row("الإجراء / الحالة", item["action"] or "— (يُترك فارغًا)")
        + row("مسؤول الضرر", display_responsible(item), item["flags"]["responsible"])
        + row("سبب التأخير في غلق الشكوى", item["delay_reason"], item["flags"]["delay_reason"])
    )

    notes = [n for n in [item.get("rule_note", "")] + item.get("type_notes", []) if n]
    note_html = "".join(
        f'<div class="reasoning-box">⚖️ {html.escape(note)}</div>' for note in notes
    )

    st.markdown(
        f'<div class="ticket-card"><h4>🎫 تذكرة {html.escape(str(item["ticket_id"]))}</h4>'
        f'{rows_html}{note_html}'
        f'{reason_box("سبب اختيار مسؤول الضرر", item["responsible_reasoning"], item["responsible_evidence"])}'
        f'{reason_box("سبب التصنيف", item["reasoning"], item["evidence"])}'
        f'</div>',
        unsafe_allow_html=True
    )

    # نصوص جاهزة للنسخ في الشيت
    st.caption("نسخ للشيت — التصنيف الأساسي + التصنيف الفرعي (خليتين متجاورتين):")
    st.code("\t".join([item["main_category"], item["sub_category"]]), language=None)
    if item["action"]:
        st.caption("نسخ للشيت — الإجراء/الحالة:")
        st.code(item["action"], language=None)
    else:
        st.caption("الإجراء/الحالة: سيب الخلية فاضية (الطلب مش رجيع).")
    st.caption("نسخ للشيت — مسؤول الضرر + سبب الاختيار + سبب التأخير (3 خلايا متجاورة):")
    st.code(
        "\t".join([
            display_responsible(item),
            sheet_reason_text(item["responsible_reasoning"], item["responsible_evidence"]),
            item["delay_reason"],
        ]),
        language=None
    )


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
    <p>قراءة تذكرة الشكوى ونتيجتها وتعليقاتها وربطها ببيانات الطلب، ثم تصنيفها وتحديد مسؤول الضرر بأدلة موثقة</p>
</div>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("📋 بيانات الطلب")
    order_id = st.number_input("رقم الطلب (Order ID)", value=1037315, step=1)
    ticket_id_filter = st.number_input(
        "رقم تذكرة الشكوى (اختياري)",
        value=0,
        min_value=0,
        step=1,
        help="رقم تذكرة الشكوى المطلوب تصنيفها. باقي تذاكر الطلب بتفضل مقروءة كسياق للموديل. "
             "سيبها 0 عشان يصنّف كل تذاكر الطلب."
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

        if result["order_type"] is None:
            st.warning(
                "⚠️ نوع الطلب (تأمين / B2C / MM / WALKIN) لم يُكتشف في جدول التذاكر، فتم افتراض B2C. "
                "راجع قسم الـ Debug وشوف أسماء أعمدة التذاكر، ولو عارف اسم العمود اكتبه في ORDER_TYPE_COLUMN."
            )

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
            st.markdown("**الجدول الزمني للحالات بتوقيت الرياض:**")
            st.text(result["status_timeline"])
            st.markdown("**رد الموديل الخام:**")
            st.code(result["raw_model_text"], language="json")
    elif not analyze_btn:
        st.info("👈 قم بإدخال رقم الطلب (ورقم تذكرة الشكوى) والضغط على زر التصنيف لعرض النتائج هنا.")
