import html
import json
import os
import re
import urllib.parse
from datetime import datetime, timedelta
import requests
import streamlit as st

# إعدادات الصفحة الرسمية لمسمار
st.set_page_config(
    page_title="نظام تدقيق تعطل التسعير | مسمار MisMar",
    page_icon="💰",
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
        background: linear-gradient(135deg, #1E3A8A 0%, #0F172A 100%);
        padding: 28px;
        border-radius: 20px;
        border: 1px solid #3B82F633;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        margin-bottom: 28px;
        text-align: center;
    }
    
    .mismar-header h1 {
        color: #3B82F6;
        font-weight: 800;
        font-size: 2.2rem;
        margin-bottom: 8px;
    }

    .mismar-header p {
        color: #9CA3AF;
        font-size: 1.05rem;
    }

    .justification-card {
        background: linear-gradient(180deg, #111827 0%, #1F2937 100%);
        border-right: 6px solid #3B82F6;
        padding: 22px;
        border-radius: 14px;
        font-size: 1.15rem;
        line-height: 1.95;
        color: #F9FAFB;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        margin-bottom: 16px;
        white-space: pre-wrap;
    }
    
    .evidence-card {
        background-color: #111827;
        border: 1px solid #374151;
        padding: 22px;
        border-radius: 14px;
        color: #D1D5DB;
        line-height: 1.8;
        white-space: pre-wrap;
    }

    .stButton>button {
        width: 100%;
        background: linear-gradient(90deg, #3B82F6 0%, #2563EB 100%);
        color: #FFFFFF;
        font-weight: 700;
        font-size: 1.15rem;
        padding: 14px;
        border-radius: 12px;
        border: none;
        box-shadow: 0 4px 14px rgba(59, 130, 246, 0.3);
        transition: all 0.3s ease;
    }
    
    .stButton>button:hover {
        background: linear-gradient(90deg, #2563EB 0%, #1D4ED8 100%);
        transform: translateY(-2px);
    }
</style>
""", unsafe_allow_html=True)

METABASE_ENDPOINTS = {
    "tickets": "https://analysis.mismarapp.com/public/question/5f313cbe-6bb4-43bc-9b4d-70b8de7d17d4.json",
    "comments": "https://analysis.mismarapp.com/public/question/82aba25f-d368-44e3-8392-dce163d78e23.json",
    "status_history": "https://analysis.mismarapp.com/public/question/98fe13e6-298a-4775-8244-3015c9c720fe.json",
    "pricing": "https://analysis.mismarapp.com/public/question/b0114e1f-8577-4faa-a790-eaa2412f39f6.json"
}

# التصنيفات المسموح بها حصريًا للنتيجة النهائية لتأخير التسعير
PRICING_CLASSIFICATIONS = [
    "قطع غيار", "تشغيل", "لا يوجد تأخير", "مركز", "مكرر", "يوم الجمعة", "العميل",
    "خارج أوقات العمل", "الوكالة", "نقل بين مركزين", "المركز و قطع الغيار",
    "قطع الغيار والتشغيل", "المركز والوكالة"
]

WORK_START_HOUR = 8
WORK_END_HOUR = 18
FRIDAY_WEEKDAY = 4  # في بايثون: الإثنين=0 ... الجمعة=4 ... الأحد=6


def build_metabase_url(base_url: str, order_id: int) -> str:
    """يبني رابط الطلب بالصيغة الرسمية اللي Metabase محتاجها لتمرير قيمة لمتغير SQL اسمه order_id"""
    parameters = [
        {
            "type": "number/=",
            "target": ["variable", ["template-tag", "order_id"]],
            "value": str(order_id)
        }
    ]
    encoded_params = urllib.parse.quote(json.dumps(parameters))
    return f"{base_url}?parameters={encoded_params}"


def fetch_order_data(order_id: int) -> dict:
    """جلب بيانات الطلب الكاملة من المصادر الأربعة في Metabase"""
    payload = {}
    for key, url in METABASE_ENDPOINTS.items():
        try:
            full_url = build_metabase_url(url, order_id)
            res = requests.get(full_url, timeout=15)
            if res.status_code == 200:
                try:
                    payload[key] = res.json()
                except ValueError:
                    payload[key] = f"Error: Response wasn't valid JSON: {res.text[:200]}"
            else:
                payload[key] = f"Error HTTP {res.status_code}"
        except requests.exceptions.RequestException as e:
            payload[key] = f"Error: {str(e)}"
    return payload


def _parse_dt(value: str):
    """
    يحاول يفهم صيغة التاريخ القادمة من Metabase (مثال: 2026-08-10 04:04 PM)
    ⚠️ Metabase بيرجّع التوقيت متأخر 3 ساعات عن التوقيت الفعلي بالسعودية،
    فبنضيف 3 ساعات هنا في نقطة التحليل نفسها قبل أي حساب لاحق.
    """
    if not value:
        return None
    formats = [
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(value.strip(), fmt)
            return parsed + timedelta(hours=3)
        except (ValueError, AttributeError):
            continue
    return None


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
    """يحسب ساعات العمل الفعلية بين تاريخين، مستبعدًا خارج 8ص-6م ويوم الجمعة بالكامل"""
    if start >= end:
        return timedelta(0)
    total = timedelta(0)
    current = start
    while current < end:
        day_work_start = current.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
        day_work_end = current.replace(hour=WORK_END_HOUR, minute=0, second=0, microsecond=0)
        if current.weekday() != FRIDAY_WEEKDAY:
            segment_start = max(current, day_work_start)
            segment_end = min(end, day_work_end)
            if segment_start < segment_end:
                total += segment_end - segment_start
        next_day = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        current = next_day
    return total


# اسم الحالة بالظبط زي ما بيرجع من status_history، دي المرجع الحقيقي لمدة التسعير
# (مش وقت إنشاء طلب التسعير في جدول OrderPricingRequests، لأنه ممكن يختلف عن التوقيت الفعلي لدخول الحالة)
PRICING_STATUS_NAME = "جاري التسعير"


def compute_last_status_duration(status_history, status_name):
    """
    يحسب مدة *آخر* مرة فقط دخل فيها الطلب حالة معينة (مش مجموع كل المرات).
    نفس المنطق المستخدم في موديول الفحص والتشخيص، لكن معمم لأي اسم حالة.
    يرجع dict فيه: duration (خام)، business_duration (ساعات عمل فعلية)، opened، closed، still_active
    أو None لو الحالة دي مش موجودة خالص في تاريخ الطلب.
    """
    if not isinstance(status_history, list) or not status_history:
        return None

    matches = []
    for row in status_history:
        if not isinstance(row, dict):
            continue
        row_status_name = row.get("Status_Name") or row.get("status_name") or row.get("status")
        if row_status_name != status_name:
            continue
        opened_raw = row.get("Opened_At") or row.get("opened_at") or row.get("created_at")
        closed_raw = row.get("Closed_At") or row.get("closed_at") or row.get("ended_at")
        opened_dt = _parse_dt(opened_raw)

        still_active = bool(closed_raw and str(closed_raw).strip().lower() == "currently active")
        closed_dt = datetime.now() if still_active else _parse_dt(closed_raw)

        if opened_dt and closed_dt:
            matches.append({
                "opened": opened_dt,
                "closed": closed_dt,
                "duration": closed_dt - opened_dt,
                "business_duration": business_hours_duration(opened_dt, closed_dt),
                "still_active": still_active,
            })

    if not matches:
        return None

    matches.sort(key=lambda r: r["opened"])
    return matches[-1]


def compute_pricing_cycles(pricing_data):
    """
    يحلل كل دورات التسعير (طلب تسعير ← عرض سعر) للطلب كـ **دليل مساند فقط**
    (مين رفع العرض، إمتى، حالة كل طلب)، وليس كمصدر رئيسي لحساب مدة التأخير.
    المصدر الرئيسي للمدة هو compute_last_status_duration على حالة "جاري التسعير" في status_history.
    بيرجع tuple: (قائمة كل الدورات مرتبة، آخر دورة، نص ملخص جاهز للبرومبت)
    """
    if not isinstance(pricing_data, list) or not pricing_data:
        return [], None, "⚠️ لا توجد بيانات تسعير صالحة لهذا الطلب."

    cycles = []
    for row in pricing_data:
        if not isinstance(row, dict):
            continue
        request_created_raw = row.get("Request_Created_At")
        quotation_created_raw = row.get("Quotation_Created_At")
        request_status = row.get("Pricing_Request_Status") or "غير معروف"

        request_created = _parse_dt(request_created_raw)
        if not request_created:
            continue

        quotation_created = _parse_dt(quotation_created_raw)
        still_waiting = quotation_created is None

        reference_end = datetime.now() if still_waiting else quotation_created

        cycles.append({
            "pricing_request_id": row.get("Pricing_Request_Id"),
            "quotation_id": row.get("Quotation_Id"),
            "request_status": request_status,
            "quotation_status": row.get("Quotation_Status") or "لا يوجد",
            "requester_name": row.get("Requester_Name") or "غير معروف",
            "quotation_creator_name": row.get("Quotation_Creator_Name") or "غير معروف",
            "request_created": request_created,
            "quotation_created": quotation_created,
            "still_waiting": still_waiting,
            "raw_gap": reference_end - request_created,
            "business_gap": business_hours_duration(request_created, reference_end),
            "approval_duration": row.get("Approval_Duration"),
            "rejection_duration": row.get("Rejection_Duration"),
        })

    if not cycles:
        return [], None, "⚠️ تعذر تفسير تواريخ دورات التسعير تلقائيًا."

    cycles.sort(key=lambda c: c["request_created"])
    last_cycle = cycles[-1]

    lines = [f"📊 عدد دورات التسعير الكلي لهذا الطلب: {len(cycles)}"]
    for i, c in enumerate(cycles, 1):
        status_line = (
            f"لسه بينتظر رفع عرض سعر (لم يُرفع حتى الآن)"
            if c["still_waiting"] else
            f"تم رفع عرض السعر بواسطة {c['quotation_creator_name']}"
        )
        lines.append(
            f"- الدورة {i}: طلب تسعير أُنشئ في {c['request_created']} بواسطة {c['requester_name']} "
            f"(حالة الطلب: {c['request_status']}). {status_line}. "
            f"الفجوة حتى {'الآن' if c['still_waiting'] else 'رفع العرض'}: "
            f"خام = {_format_timedelta(c['raw_gap'])} | ساعات عمل فعلية = {_format_timedelta(c['business_gap'])}."
        )

    summary = "\n".join(lines)
    return cycles, last_cycle, summary


def analyze_pricing_delay(api_key: str, model_name: str, order_id: int):
    """يرجع tuple: (نص رد الموديل, ملخص محسوب للتحقق منه في الواجهة)"""
    order_data = fetch_order_data(order_id)

    # المصدر الرئيسي للمدة: آخر مرة دخل فيها الطلب حالة "جاري التسعير" في status_history
    last_pricing_status = compute_last_status_duration(order_data.get('status_history'), PRICING_STATUS_NAME)

    # بيانات جدول التسعير (طلب/عرض) بتستخدم كدليل مساند بس، مش كمصدر للحساب
    cycles, last_cycle, pricing_facts = compute_pricing_cycles(order_data.get('pricing'))

    combined_debug = (
        f"⏱️ مدة آخر حالة '{PRICING_STATUS_NAME}' (المصدر الرئيسي للمدة):\n"
        + (
            f"- المدة الخام: {_format_timedelta(last_pricing_status['duration'])}\n"
            f"- ساعات العمل الفعلية: {_format_timedelta(last_pricing_status['business_duration'])}"
            + (" (لسه شغالة/جارية حتى الآن)\n" if last_pricing_status["still_active"] else "\n")
            + f"من {last_pricing_status['opened']} إلى {last_pricing_status['closed']}.\n"
            if last_pricing_status else
            f"⚠️ لا توجد حالة '{PRICING_STATUS_NAME}' واضحة في سجل هذا الطلب.\n"
        )
        + "\n" + pricing_facts
    )

    # 🛑 قصير الدائرة: لو آخر مرة دخل فيها الطلب حالة "جاري التسعير" كانت أقل من 4 ساعات
    # عمل فعلية، نرجّع "لا يوجد تأخير" مباشرة من غير استدعاء الموديل
    NO_DELAY_THRESHOLD_HOURS = 4
    if last_pricing_status and not last_pricing_status["still_active"]:
        business_hours = last_pricing_status["business_duration"].total_seconds() / 3600
        if business_hours < NO_DELAY_THRESHOLD_HOURS:
            no_delay_text = (
                f"لا يوجد تأخير، حيث استغرقت مرحلة {PRICING_STATUS_NAME} "
                f"{_format_timedelta(last_pricing_status['business_duration'])} ساعات عمل فعلية فقط، وهي مدة طبيعية."
                f"\n===SPLIT===\n"
                f"مدة حالة {PRICING_STATUS_NAME} بساعات العمل الفعلية: {_format_timedelta(last_pricing_status['business_duration'])} "
                f"(من {last_pricing_status['opened']} إلى {last_pricing_status['closed']})، "
                f"وهي أقل من الحد الأدنى المعتبر تأخيرًا (4 ساعات عمل)."
                f"\n===CLASSIFICATION===\nلا يوجد تأخير"
            )
            return no_delay_text, combined_debug

    if not last_pricing_status:
        no_data_text = (
            "تعذر العثور على مرحلة تسعير واضحة في سجل حالات هذا الطلب."
            "\n===SPLIT===\nلا توجد بيانات كافية للتحليل."
            "\n===CLASSIFICATION===\nلا يوجد تأخير"
        )
        return no_data_text, combined_debug

    last_status_note = (
        f"⏱️ مدة *آخر* مرة دخل فيها الطلب حالة '{PRICING_STATUS_NAME}' (المصدر الرئيسي والوحيد للمدة، "
        f"وليس وقت إنشاء طلب التسعير في الجدول):\n"
        f"- المدة الخام (شاملة كل الأوقات): {_format_timedelta(last_pricing_status['duration'])}\n"
        f"- مدة ساعات العمل الفعلية فقط (بعد استبعاد ما هو خارج 8ص-6م ويوم الجمعة): "
        f"{_format_timedelta(last_pricing_status['business_duration'])}"
        + (" (لسه شغالة/جارية حتى الآن)" if last_pricing_status["still_active"] else "")
        + f"\nمن {last_pricing_status['opened']} إلى {last_pricing_status['closed']}.\n"
        + "⚠️ تنبيه: هذا الرقم وحده لا يعني تلقائيًا أن التشغيل هو المتسبب، ولا حتى أنه يعني تأخيرًا أصلاً. "
        + "افحص التعليقات لمعرفة هل كان التشغيل ينتظر توضيح المركز لأجور اليد أو تفاصيل فنية قبل رفع العرض.\n\n"
        + f"📋 بيانات جدول طلبات/عروض التسعير (دليل مساند فقط لمعرفة من رفع العرض ومتى، وليس مصدر الحساب):\n"
        + pricing_facts
    )

    prompt_text = f"""
    أنت كبير مدققي العمليات في شركة صيانة السيارات (مسمار - MisMar).
    مطلوب تحديد السبب الجذري وراء التأخير (إن وجد) في آخر مرة دخل فيها الطلب رقم #{order_id} مرحلة "{PRICING_STATUS_NAME}".

    ⚠️ === الحقيقة الزمنية الحاسمة (إلزامية الاستخدام، ولا تحسب من عندك) === ⚠️
    {last_status_note}

    البيانات المتاحة للطلب:
    1. 🎫 تذاكر الشكاوى والمتابعة (خذ نظرة عامة على كل التذاكر، وابحث عن أي تذكرة تصعيد أو ملاحظة متعلقة بالتسعير أو أجور اليد أو قطع الغيار أو الوكالة):
    {json.dumps(order_data.get('tickets'), ensure_ascii=False, indent=2)}

    2. 💬 محادثات الشات والتعليقات الداخلية — المصدر الأهم لتحديد المتسبب الفعلي:
    {json.dumps(order_data.get('comments'), ensure_ascii=False, indent=2)}

    🔍 === سيناريوهات محتملة يجب فحصها في التعليقات (إلزامي) === 🔍
    - هل طلب التسعير اترفع من التشغيل، لكن المركز لم يوضح تفاصيل أجور اليد أو قطع الغيار المطلوبة، مما منع التشغيل من رفع عرض السعر؟ → هذا يشير لتأخير من المركز.
    - هل عرض السعر لم يُرفع حتى الآن رغم أن كل المعلومات المطلوبة من المركز كانت متوفرة منذ فترة، والتشغيل ببساطة لم يرفع العرض؟ → هذا يشير لتأخير من التشغيل.
    - هل التأخير بسبب انتظار توفر قطعة غيار معينة (سواء من المورد أو الوكالة) قبل تحديد سعرها النهائي؟ → صنّف كـ"قطع غيار" أو "الوكالة" حسب المصدر الظاهر في البيانات.
    - هل هناك نقل للسيارة بين مركزين أثّر على استكمال التسعير؟ → صنّف كـ"نقل بين مركزين".
    - هل جزء كبير من الفجوة وقع خارج ساعات العمل الرسمية (8ص-6م) أو يوم الجمعة؟ راجع التسلسل الزمني جيدًا، فهذا لا يُحتسب كتأخير فعلي من أي طرف.
    - هل طلب التسعير هذا مكرر فعليًا لطلب سابق لنفس الغرض (نفس القطعة/المشكلة)؟ → صنّف كـ"مكرر".
    - 🕐 مهم: أي فجوة زمنية تقع كليًا أو جزئيًا خارج ساعات العمل الرسمية أو يوم الجمعة لا تُحتسب كتأخير من أي طرف.

    3. ⏱️ التسلسل الزمني الكامل للحالات (مرجعي فقط لفهم السياق العام للطلب):
    {json.dumps(order_data.get('status_history'), ensure_ascii=False, indent=2)}

    4. 💰 كامل بيانات طلبات التسعير وعروض الأسعار (البيانات الخام، استخدم الحقائق المحسوبة أعلاه للأرقام فقط):
    {json.dumps(order_data.get('pricing'), ensure_ascii=False, indent=2)}

    === 🎯 تعليمات وقواعد الصياغة الصارمة ===
    قسّم إجابتك إلى ثلاثة أقسام يفصل بينها السطر `===SPLIT===` قبل القسم الثاني، والسطر `===CLASSIFICATION===` قبل القسم الثالث:

    القسم الأول: [السبب الجذري المباشر]
    - فقرة واحدة متصلة ومباشرة فقط (من 4 إلى 5 سطور كحد أقصى).
    - 🟢 ابدأ الفقرة **فورًا** بذكر السبب الجذري نفسه (الطرف المتسبب وماذا حدث بالضبط)، بدون أي مقدمات إطلاقًا.
    - 🛑 ممنوع تمامًا افتتاح الفقرة بعبارات مثل "يعود السبب الجذري إلى" أو أي صياغة تمهيدية مشابهة.
    - 🛑 ممنوع ذكر رقم الطلب نهائيًا في هذه الفقرة.
    - 🛑 يُمنع استخدام جمل فضفاضة دون تحديد السبب الحقيقي من نص التذاكر أو الشات.
    - يُمنع استخدام القوائم، العناوين، أو أرقام التذاكر والعروض الداخلية.

    ===SPLIT===

    القسم الثاني: [الأدلة والوقائع التفصيلية]
    - التوقيت الدقيق لإنشاء طلب التسعير ورفع العرض (أو حالة الانتظار المستمرة).
    - اقتباس نصوص التذاكر والتعليقات ذات الصلة كلمة بكلمة.
    - سرد الفجوات الزمنية الفعلية بين رسائل التشغيل والمركز التي تدعم استنتاجك.
    - أي ارتباط بقطع غيار أو الوكالة أو نقل بين مركزين إن وجد.

    ===CLASSIFICATION===
    - اكتب هنا تصنيفاً واحداً فقط (بالضبط كما هو، بدون أي شرح إضافي) من هذه القائمة الحصرية:
      {" | ".join(PRICING_CLASSIFICATIONS)}
    - اختر التصنيف الأقرب لواقع الأدلة فقط، ولا تخترع تصنيفاً من عندك خارج هذه القائمة.
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {
        'Content-Type': 'application/json',
        'x-goog-api-key': api_key
    }
    data = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.9
        }
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
    except requests.exceptions.RequestException as e:
        raise Exception(f"تعذر الوصول إلى خدمة Gemini (مشكلة شبكة): {str(e)}")

    if response.status_code == 200:
        result_json = response.json()
        try:
            model_text = result_json['candidates'][0]['content']['parts'][0]['text']
            return model_text, combined_debug
        except (KeyError, IndexError):
            raise Exception(
                "الاتصال نجح لكن شكل الرد غير متوقع (على الأرجح تم حظر المحتوى أو انتهت الحصة/الـ quota). "
                f"الرد الكامل: {json.dumps(result_json, ensure_ascii=False)[:800]}"
            )
    elif response.status_code == 401:
        raise Exception(
            "خطأ مصادقة (401): المفتاح غير صالح أو منتهي الصلاحية. "
            "اعمل مفتاح جديد من https://aistudio.google.com/app/apikey"
        )
    elif response.status_code == 404:
        raise Exception(
            f"اسم الموديل '{model_name}' غير موجود أو غير متاح لحسابك (404). "
            "جرّب اسم موديل آخر من صفحة الموديلات المتاحة في حسابك."
        )
    elif response.status_code == 503:
        raise Exception(
            "خطأ 503: الموديل مزدحم مؤقتًا من عند Google، جرب تاني بعد شوية أو غيّر اسم الموديل مؤقتًا."
        )
    else:
        raise Exception(f"خطأ في الاتصال بالذكاء الاصطناعي ({response.status_code}): {response.text}")


with st.sidebar:
    st.image("https://mismarapp.com/static/media/logo.f6cf70e4.svg", width=200)
    st.markdown("### ⚙️ إعدادات النظام")

    api_key_input = st.text_input(
        "Gemini API Key",
        value="",
        type="password",
        help="أدخل مفتاح الـ API الخاص بـ Gemini"
    )

    model_name_input = st.text_input(
        "اسم الموديل (Model Name)",
        value="gemini-3.6-flash",
        help="غيّرها هنا لو ظهر خطأ 404 يفيد إن الموديل غير متاح، بدون الحاجة لتعديل الكود"
    )

st.markdown("""
<div class="mismar-header">
    <h1>💰 نظام تدقيق تعطل التسعير (MisMar Pricing Delay Audit)</h1>
    <p>استخراج السبب الجذري وراء تأخر رفع عرض السعر وتحديد الطرف المتسبب</p>
</div>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("📋 بيانات الطلب")
    order_id = st.number_input("رقم الطلب (Order ID)", value=1029480, step=1)
    st.markdown("<br>", unsafe_allow_html=True)
    analyze_btn = st.button("🚀 استخراج تبرير تعطل التسعير")

with col2:
    st.subheader("📊 مخرجات التقرير والتدقيق")

    if analyze_btn:
        if not api_key_input:
            st.error("⚠️ يرجى إدخال Gemini API Key أولاً من القائمة الجانبية.")
        else:
            with st.spinner("⏳ جاري فحص أسباب تعطل دورة التسعير..."):
                try:
                    full_response, pricing_debug = analyze_pricing_delay(
                        api_key_input, model_name_input, order_id
                    )

                    if "===CLASSIFICATION===" in full_response:
                        main_part, classification = full_response.split("===CLASSIFICATION===", 1)
                    else:
                        main_part = full_response
                        classification = "غير محدد"

                    if "===SPLIT===" in main_part:
                        justification, evidence = main_part.split("===SPLIT===", 1)
                    else:
                        justification = main_part
                        evidence = "لم يتم تفكيك الأدلة بشكل منفصل."

                    st.session_state['pricing_audit_result'] = {
                        'justification': justification.strip(),
                        'evidence': evidence.strip(),
                        'classification': classification.strip(),
                        'order_id': order_id,
                        'pricing_debug': pricing_debug,
                    }

                except Exception as e:
                    st.error(f"❌ حدث خطأ أثناء التحليل: {str(e)}")

    if 'pricing_audit_result' in st.session_state and st.session_state['pricing_audit_result']:
        res = st.session_state['pricing_audit_result']

        safe_justification = html.escape(res["justification"])
        safe_evidence = html.escape(res["evidence"])
        safe_classification = html.escape(res.get("classification", "غير محدد"))

        st.markdown(
            f'<div style="display:inline-block; background:#3B82F6; color:#0B0F19; '
            f'font-weight:800; padding:8px 18px; border-radius:999px; margin-bottom:14px; '
            f'font-size:1.05rem;">🏷️ التصنيف النهائي: {safe_classification}</div>',
            unsafe_allow_html=True
        )

        st.markdown("### 📝 التبرير التشغيلي لتعطل التسعير:")
        st.markdown(f'<div class="justification-card">{safe_justification}</div>', unsafe_allow_html=True)

        st.text_area("📋 اضغط Ctrl+A ثم Ctrl+C للنسخ المباشر:", value=res["justification"], height=120)

        st.markdown("### 🔍 الأدلة والوقائع التفصيلية:")
        st.markdown(f'<div class="evidence-card">{safe_evidence}</div>', unsafe_allow_html=True)

        with st.expander("🛠️ (Debug) دورات التسعير المحسوبة برمجيًا فعليًا"):
            st.markdown(
                "الأرقام دي محسوبة مباشرة بكود بايثون من التواريخ الخام (مش من الموديل)، "
                "قارنها بالتقرير فوق للتأكد إن الموديل التزم بيها حرفيًا:"
            )
            st.text(res.get("pricing_debug", "لا توجد بيانات."))
    elif not analyze_btn:
        st.info("👈 قم بإدخال رقم الطلب والضغط على زر التحليل لعرض تبرير تعطل التسعير هنا.")