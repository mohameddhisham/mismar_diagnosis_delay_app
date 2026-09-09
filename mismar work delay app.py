import html
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta
import requests
import streamlit as st

# قائمة الموديلات بالترتيب: لو الأول مزدحم (503) أو مش موجود (404)، الكود يجرب اللي بعده تلقائيًا
MODEL_FALLBACK_LIST = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash"]
RETRY_DELAYS_SECONDS = [4, 10]  # نجرب نفس الموديل 3 مرات إجمالي (محاولة أولى + محاولتين إعادة) قبل الانتقال للموديل التالي

# إعدادات الصفحة الرسمية لمسمار
st.set_page_config(
    page_title="نظام تدقيق تعطل جاري العمل | مسمار MisMar",
    page_icon="🛠️",
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
        background: linear-gradient(135deg, #134E4A 0%, #0F172A 100%);
        padding: 28px;
        border-radius: 20px;
        border: 1px solid #14B8A633;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        margin-bottom: 28px;
        text-align: center;
    }
    
    .mismar-header h1 {
        color: #14B8A6;
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
        border-right: 6px solid #14B8A6;
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
        background: linear-gradient(90deg, #14B8A6 0%, #0D9488 100%);
        color: #FFFFFF;
        font-weight: 700;
        font-size: 1.15rem;
        padding: 14px;
        border-radius: 12px;
        border: none;
        box-shadow: 0 4px 14px rgba(20, 184, 166, 0.3);
        transition: all 0.3s ease;
    }
    
    .stButton>button:hover {
        background: linear-gradient(90deg, #0D9488 0%, #0F766E 100%);
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

# اسم الحالة بالظبط زي ما بيرجع من status_history
WORK_STATUS_NAME = "جاري العمل"

# التصنيفات المسموح بها حصريًا للنتيجة النهائية لتأخير جاري العمل
WORK_CLASSIFICATIONS = [
    "قطع الغيار", "التشغيل", "عميل", "مركز", "لا يوجد تأخير", "تأخير في الشحن",
    "خلل تقني", "تم التسليم", "يوم الجمعة", "تشخيص إضافي", "مكرر",
    "قطع الغيار والمركز", "نقل مركز آخر", "التشغيل والمركز", "قطع الغيار والتشغيل",
    "قطع الغيار وطلبية", "تشخيص مركز خاطئ"
]

# ساعات العمل المعتبرة لمرحلة جاري العمل تحديدًا: 9 صباحًا - 6 مساءً
WORK_START_HOUR = 9
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
    """يحسب ساعات العمل الفعلية بين تاريخين، مستبعدًا خارج 9ص-6م ويوم الجمعة بالكامل"""
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


def compute_last_status_duration(status_history, status_name):
    """
    يحسب مدة *آخر* مرة فقط دخل فيها الطلب حالة معينة (مش مجموع كل المرات).
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


def analyze_work_delay(api_key: str, order_id: int):
    """يرجع tuple: (نص رد الموديل, ملخص محسوب للتحقق منه في الواجهة)"""
    order_data = fetch_order_data(order_id)
    last_work_status = compute_last_status_duration(order_data.get('status_history'), WORK_STATUS_NAME)

    # 🛑 قصير الدائرة: لو آخر مرة دخل فيها الطلب حالة "جاري العمل" كانت أقل من 4 ساعات
    # عمل فعلية، نرجّع "لا يوجد تأخير" مباشرة من غير استدعاء الموديل
    NO_DELAY_THRESHOLD_HOURS = 4
    if last_work_status and not last_work_status["still_active"]:
        business_hours = last_work_status["business_duration"].total_seconds() / 3600
        if business_hours < NO_DELAY_THRESHOLD_HOURS:
            no_delay_text = (
                f"لا يوجد تأخير، حيث استغرقت مرحلة {WORK_STATUS_NAME} "
                f"{_format_timedelta(last_work_status['business_duration'])} ساعات عمل فعلية فقط، وهي مدة طبيعية."
                f"\n===SPLIT===\n"
                f"مدة حالة {WORK_STATUS_NAME} بساعات العمل الفعلية: {_format_timedelta(last_work_status['business_duration'])} "
                f"(من {last_work_status['opened']} إلى {last_work_status['closed']})، "
                f"وهي أقل من الحد الأدنى المعتبر تأخيرًا (4 ساعات عمل)."
                f"\n===CLASSIFICATION===\nلا يوجد تأخير"
            )
            return no_delay_text, no_delay_text

    if not last_work_status:
        status_history_raw = order_data.get('status_history')

        if isinstance(status_history_raw, list) and status_history_raw:
            # نجمع كل أسماء الحالات الفعلية الموجودة في البيانات عشان نعرف فورًا
            # هل الاسم "جاري العمل" مختلف شوية، أو الحالة دي مش موجودة أصلاً في رحلة الطلب ده
            found_statuses = []
            for row in status_history_raw:
                if isinstance(row, dict):
                    name = row.get("Status_Name") or row.get("status_name") or row.get("status")
                    if name and name not in found_statuses:
                        found_statuses.append(name)
            diag_detail = (
                f"عدد الحالات المسترجعة: {len(status_history_raw)}.\n"
                f"أسماء الحالات الفعلية الموجودة في هذا الطلب:\n- " + "\n- ".join(found_statuses)
                if found_statuses else
                "البيانات مسترجعة لكن مفيش فيها أسماء حالات واضحة (شكل غير متوقع للـ JSON)."
            )
        elif isinstance(status_history_raw, str):
            # يبقى fetch_order_data سجّل رسالة خطأ (Error HTTP.../Error:...) بدل بيانات فعلية
            diag_detail = f"تعذر جلب سجل الحالات أصلاً من Metabase. الرسالة الخام: {status_history_raw[:300]}"
        else:
            diag_detail = "لم يتم استرجاع أي بيانات لسجل الحالات لهذا الطلب (النتيجة فاضية أو None)."

        no_data_text = (
            "تعذر العثور على مرحلة جاري العمل واضحة في سجل حالات هذا الطلب."
            f"\n===SPLIT===\n{diag_detail}"
            "\n===CLASSIFICATION===\nلا يوجد تأخير"
        )
        return no_data_text, diag_detail

    last_status_note = (
        f"⏱️ مدة *آخر* مرة دخل فيها الطلب حالة '{WORK_STATUS_NAME}':\n"
        f"- المدة الخام (شاملة كل الأوقات): {_format_timedelta(last_work_status['duration'])}\n"
        f"- مدة ساعات العمل الفعلية فقط (بعد استبعاد ما هو خارج 9ص-6م ويوم الجمعة): "
        f"{_format_timedelta(last_work_status['business_duration'])}"
        + (" (لسه شغالة/جارية حتى الآن)" if last_work_status["still_active"] else "")
        + f"\nمن {last_work_status['opened']} إلى {last_work_status['closed']}.\n"
        + "⚠️ تنبيه: هذا الرقم وحده لا يعني تلقائيًا وجود تأخير. افحص التذاكر والتعليقات أولاً "
        + "لمعرفة هل هذا وقت عمل فعلي وتحديثات مستمرة، أم فجوة صمت وتعطل حقيقي."
    )

    prompt_text = f"""
    أنت كبير مدققي العمليات في شركة صيانة السيارات (مسمار - MisMar).
    مطلوب تحديد السبب الجذري وراء التأخير (إن وجد) في آخر مرة دخل فيها الطلب رقم #{order_id} مرحلة "{WORK_STATUS_NAME}".

    ⚠️ === الحقيقة الزمنية الحاسمة (إلزامية الاستخدام، ولا تحسب من عندك) === ⚠️
    {last_status_note}

    البيانات المتاحة للطلب (التركيز الأساسي هنا على التذاكر والتعليقات):
    1. 🎫 تذاكر الشكاوى والمتابعة — المصدر الأهم هنا لمعرفة سبب تعطل التنفيذ (خذ نظرة عامة على كل التذاكر، وابحث عن أي تذكرة تخص نقص قطع غيار، تأخير شحن، خلل تقني أثناء العمل، تشخيص إضافي اكتُشف أثناء التنفيذ، أو نقل السيارة لمركز آخر):
    {json.dumps(order_data.get('tickets'), ensure_ascii=False, indent=2)}

    2. 💬 محادثات الشات والتعليقات الداخلية — قارن مدة العمل الفعلية بمحتوى المتابعات: هل فيه تحديثات دورية من المركز تدل على عمل فعلي مستمر (صور تقدم، تحديث حالة، طلب قطعة)، أم فجوات صمت طويلة بدون أي تحديث تدل على تعطل حقيقي؟:
    {json.dumps(order_data.get('comments'), ensure_ascii=False, indent=2)}

    🔍 === سيناريوهات محتملة يجب فحصها (إلزامي) === 🔍
    - هل التأخير بسبب انتظار توفر قطعة غيار (سواء من المورد، طلبية جديدة، أو نقص في المخزون)؟ → صنّف كـ"قطع الغيار" أو "قطع الغيار وطلبية" حسب السياق.
    - هل هناك تأخير شحن قطعة تم طلبها بالفعل؟ → صنّف كـ"تأخير في الشحن".
    - هل ظهر خلل تقني جديد أثناء التنفيذ يستلزم وقتًا إضافيًا لم يكن متوقعًا؟ → صنّف كـ"خلل تقني".
    - هل اكتشف المركز أثناء العمل حاجة لتشخيص إضافي لم يظهر في الفحص الأول؟ → صنّف كـ"تشخيص إضافي".
    - هل السبب الأساسي أن الفحص الأول كان خاطئًا وأدى لإعادة عمل أو تصحيح مسار؟ → صنّف كـ"تشخيص مركز خاطئ".
    - هل تم نقل السيارة لمركز آخر أثناء التنفيذ؟ → صنّف كـ"نقل مركز آخر".
    - هل هذا طلب عمل مكرر فعليًا لنفس المهمة؟ → صنّف كـ"مكرر".
    - هل السيارة تم تسليمها فعليًا للعميل والحالة لم تُحدّث بعد في النظام؟ → صنّف كـ"تم التسليم".
    - هل التأخير ناتج عن العميل نفسه (مثل عدم رده على استفسار ضروري لاستكمال العمل)؟ → صنّف كـ"عميل".
    - 🕐 مهم: أي فجوة زمنية تقع كليًا أو جزئيًا خارج ساعات العمل الرسمية (9ص-6م) أو يوم الجمعة لا تُحتسب كتأخير من أي طرف. لو الجزء الأكبر من المدة وقع في هذا النطاق → صنّف كـ"يوم الجمعة" إن كان هو السبب الرئيسي.
    - ⚠️ تذكّر: مدة طويلة مع تحديثات منتظمة من المركز في التعليقات تعني عملاً حقيقيًا مستمرًا وليست تأخيرًا، بغض النظر عن الرقم الإجمالي.

    3. ⏱️ التسلسل الزمني الكامل للحالات (مرجعي فقط لفهم السياق العام للطلب):
    {json.dumps(order_data.get('status_history'), ensure_ascii=False, indent=2)}

    === 🎯 تعليمات وقواعد الصياغة الصارمة ===
    قسّم إجابتك إلى ثلاثة أقسام يفصل بينها السطر `===SPLIT===` قبل القسم الثاني، والسطر `===CLASSIFICATION===` قبل القسم الثالث:

    القسم الأول: [السبب الجذري المباشر]
    - فقرة واحدة متصلة ومباشرة فقط (من 4 إلى 5 سطور كحد أقصى).
    - 🟢 ابدأ الفقرة **فورًا** بذكر السبب الجذري نفسه (الطرف أو العامل المتسبب وماذا حدث بالضبط)، بدون أي مقدمات إطلاقًا.
    - 🛑 ممنوع تمامًا افتتاح الفقرة بعبارات مثل "يعود السبب الجذري إلى" أو أي صياغة تمهيدية مشابهة.
    - 🛑 ممنوع ذكر رقم الطلب نهائيًا في هذه الفقرة.
    - 🛑 يُمنع استخدام جمل فضفاضة دون تحديد السبب الحقيقي من نص التذاكر أو الشات.
    - يُمنع استخدام القوائم، العناوين، أو أرقام التذاكر والعروض الداخلية.

    ===SPLIT===

    القسم الثاني: [الأدلة والوقائع التفصيلية]
    - التوقيت الدقيق لبداية (واستمرار أو انتهاء) مرحلة جاري العمل.
    - اقتباس نصوص التذاكر والتعليقات ذات الصلة كلمة بكلمة.
    - سرد أي فجوات صمت فعلية أو غياب تحديثات تدعم استنتاجك.
    - أي ارتباط بقطع غيار، شحن، تشخيص إضافي، أو نقل بين مراكز إن وجد.

    ===CLASSIFICATION===
    - اكتب هنا تصنيفاً واحداً فقط (بالضبط كما هو، بدون أي شرح إضافي) من هذه القائمة الحصرية:
      {" | ".join(WORK_CLASSIFICATIONS)}
    - اختر التصنيف الأقرب لواقع الأدلة فقط، ولا تخترع تصنيفاً من عندك خارج هذه القائمة.
    """

    return call_gemini_with_fallback(api_key, prompt_text), last_status_note


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

st.markdown("""
<div class="mismar-header">
    <h1>🛠️ نظام تدقيق تعطل جاري العمل (MisMar Work Delay Audit)</h1>
    <p>استخراج السبب الجذري وراء تأخر تنفيذ الإصلاحات وتحديد الطرف أو العامل المتسبب</p>
</div>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("📋 بيانات الطلب")
    order_id = st.number_input("رقم الطلب (Order ID)", value=1029480, step=1)
    st.markdown("<br>", unsafe_allow_html=True)
    analyze_btn = st.button("🚀 استخراج تبرير تعطل جاري العمل")

with col2:
    st.subheader("📊 مخرجات التقرير والتدقيق")

    if analyze_btn:
        if not api_key_input:
            st.error("⚠️ يرجى إدخال Gemini API Key أولاً من القائمة الجانبية.")
        else:
            with st.spinner("⏳ جاري فحص أسباب تعطل مرحلة جاري العمل..."):
                try:
                    full_response, work_debug = analyze_work_delay(
                        api_key_input, order_id
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

                    st.session_state['work_audit_result'] = {
                        'justification': justification.strip(),
                        'evidence': evidence.strip(),
                        'classification': classification.strip(),
                        'order_id': order_id,
                        'work_debug': work_debug,
                    }

                except Exception as e:
                    st.error(f"❌ حدث خطأ أثناء التحليل: {str(e)}")

    if 'work_audit_result' in st.session_state and st.session_state['work_audit_result']:
        res = st.session_state['work_audit_result']

        safe_justification = html.escape(res["justification"])
        safe_evidence = html.escape(res["evidence"])
        safe_classification = html.escape(res.get("classification", "غير محدد"))

        st.markdown(
            f'<div style="display:inline-block; background:#14B8A6; color:#0B0F19; '
            f'font-weight:800; padding:8px 18px; border-radius:999px; margin-bottom:14px; '
            f'font-size:1.05rem;">🏷️ التصنيف النهائي: {safe_classification}</div>',
            unsafe_allow_html=True
        )

        st.markdown("### 📝 التبرير التشغيلي لتعطل جاري العمل:")
        st.markdown(f'<div class="justification-card">{safe_justification}</div>', unsafe_allow_html=True)

        st.text_area("📋 اضغط Ctrl+A ثم Ctrl+C للنسخ المباشر:", value=res["justification"], height=120)

        st.markdown("### 🔍 الأدلة والوقائع التفصيلية:")
        st.markdown(f'<div class="evidence-card">{safe_evidence}</div>', unsafe_allow_html=True)

        with st.expander("🛠️ (Debug) مدة حالة جاري العمل المحسوبة برمجيًا فعليًا"):
            st.markdown(
                "الأرقام دي محسوبة مباشرة بكود بايثون من التواريخ الخام (مش من الموديل)، "
                "قارنها بالتقرير فوق للتأكد إن الموديل التزم بيها حرفيًا:"
            )
            st.text(res.get("work_debug", "لا توجد بيانات."))
    elif not analyze_btn:
        st.info("👈 قم بإدخال رقم الطلب والضغط على زر التحليل لعرض تبرير تعطل جاري العمل هنا.")
