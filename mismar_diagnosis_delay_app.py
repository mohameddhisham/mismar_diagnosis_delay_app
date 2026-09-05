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
    page_title="نظام تدقيق تعطل الفحص والتشخيص | مسمار MisMar",
    page_icon="🔧",
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
        background: linear-gradient(135deg, #7C2D12 0%, #0F172A 100%);
        padding: 28px;
        border-radius: 20px;
        border: 1px solid #F9731633;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        margin-bottom: 28px;
        text-align: center;
    }
    
    .mismar-header h1 {
        color: #F97316;
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
        border-right: 6px solid #F97316;
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
        background: linear-gradient(90deg, #F97316 0%, #EA580C 100%);
        color: #FFFFFF;
        font-weight: 700;
        font-size: 1.15rem;
        padding: 14px;
        border-radius: 12px;
        border: none;
        box-shadow: 0 4px 14px rgba(249, 115, 22, 0.3);
        transition: all 0.3s ease;
    }
    
    .stButton>button:hover {
        background: linear-gradient(90deg, #EA580C 0%, #C2410C 100%);
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

# اسم الحالة بالظبط زي ما بيرجع من status_history، مستخدم في تمييز مرحلة الفحص والتشخيص عن باقي المراحل
DIAGNOSIS_STATUS_NAME = "جاري الفحص والتشخيص"


def build_metabase_url(base_url: str, order_id: int) -> str:
    """
    يبني رابط الطلب بالصيغة الرسمية اللي Metabase محتاجها لتمرير قيمة
    لمتغير SQL (template-tag) اسمه order_id، بدل ?order_id=123 البسيطة
    اللي مبتشتغلش مع أسئلة SQL فيها متغيرات مطلوبة (Required Variables).
    """
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
    فبنضيف 3 ساعات هنا في نقطة التحليل نفسها (مش بس في العرض)، عشان تنعكس صح
    على أي حساب لاحق زي ساعات العمل (8ص-6م) وتحديد يوم الجمعة.
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


def compute_timeline_facts(status_history) -> str:
    """
    يحسب المدد الزمنية الحقيقية بكود بايثون (مش بالموديل) عشان نضمن دقة الأرقام.
    بيتوقع إن status_history عبارة عن list of dicts فيها حقول زي:
    Status_Name / Opened_At / Closed_At (أو أسماء قريبة منها).
    """
    if not isinstance(status_history, list) or not status_history:
        return "⚠️ لا توجد بيانات تسلسل زمني صالحة للحساب."

    rows = []
    for row in status_history:
        if not isinstance(row, dict):
            continue
        status_name = row.get("Status_Name") or row.get("status_name") or row.get("status")
        opened_raw = row.get("Opened_At") or row.get("opened_at") or row.get("created_at")
        closed_raw = row.get("Closed_At") or row.get("closed_at") or row.get("ended_at")

        opened_dt = _parse_dt(opened_raw)
        if closed_raw and str(closed_raw).strip().lower() == "currently active":
            closed_dt = datetime.now()
        else:
            closed_dt = _parse_dt(closed_raw)

        if status_name and opened_dt and closed_dt:
            rows.append({
                "status": status_name,
                "opened": opened_dt,
                "closed": closed_dt,
                "duration": closed_dt - opened_dt,
            })

    if not rows:
        return "⚠️ تعذر تفسير تواريخ التسلسل الزمني تلقائيًا، سيتم الاعتماد على البيانات الخام فقط."

    rows.sort(key=lambda r: r["opened"])

    total_start = rows[0]["opened"]
    total_end = rows[-1]["closed"]
    total_duration = total_end - total_start

    grouped = {}
    for r in rows:
        grouped.setdefault(r["status"], []).append(r["duration"])

    grouped_lines = []
    for status, durations in grouped.items():
        total_for_status = sum(durations, start=type(durations[0])())
        grouped_lines.append(
            f"- {status}: {_format_timedelta(total_for_status)} (تكررت {len(durations)} مرة/مرات)"
        )

    longest = max(rows, key=lambda r: r["duration"])

    # مدة مرحلة الفحص والتشخيص تحديدًا (بنجمع كل تكراراتها لو اتكررت أكتر من مرة)
    diagnosis_total = sum(
        (r["duration"] for r in rows if r["status"] == DIAGNOSIS_STATUS_NAME),
        start=total_duration * 0
    )
    diagnosis_occurrences = len([r for r in rows if r["status"] == DIAGNOSIS_STATUS_NAME])

    facts = (
        f"📊 حقائق زمنية محسوبة بدقة (مضمونة 100% ويجب الاعتماد عليها حرفيًا، وليس على حسابك الخاص من التواريخ الخام):\n"
        f"- إجمالي مدة الطلب الكاملة من أول حالة لآخر حالة: {_format_timedelta(total_duration)} "
        f"(من {total_start} إلى {total_end}).\n"
        f"- إجمالي مدة مرحلة '{DIAGNOSIS_STATUS_NAME}' تحديدًا: {_format_timedelta(diagnosis_total)} "
        f"(تكررت {diagnosis_occurrences} مرة/مرات لنفس الطلب).\n"
        f"- أطول محطة تعطيل فردية في كامل رحلة الطلب كانت في حالة '{longest['status']}' واستغرقت {_format_timedelta(longest['duration'])}.\n"
        f"- إجمالي المدة مجمّعة حسب كل حالة (بعد جمع التكرارات لنفس الحالة):\n"
        + "\n".join(grouped_lines)
    )
    return facts


WORK_START_HOUR = 8   # 8:00 صباحًا
WORK_END_HOUR = 18    # 6:00 مساءً
FRIDAY_WEEKDAY = 4    # في بايثون: الإثنين=0 ... الجمعة=4 ... الأحد=6 (بيتحدد من التاريخ نفسه مباشرة، مش تخمينًا)


def business_hours_duration(start: datetime, end: datetime) -> timedelta:
    """
    يحسب مدة ساعات العمل الفعلية بين تاريخين، مع استبعاد:
    - أي وقت خارج نطاق 8:00 صباحًا - 6:00 مساءً كل يوم.
    - يوم الجمعة بالكامل (إجازة أسبوعية).
    نعتمد على current.weekday() لمعرفة يوم الجمعة بدقة تامة من التاريخ نفسه،
    بدل استنتاجه من وجود/غياب تحديثات في البيانات (طريقة أدق وأضمن 100%).
    """
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

        # ننتقل لبداية اليوم التالي (منتصف الليل) عشان نكرر نفس المنطق على كل يوم لوحده
        next_day = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        current = next_day

    return total


def compute_last_diagnosis_duration(status_history):
    """
    يحسب مدة *آخر* مرة فقط دخل فيها الطلب حالة الفحص والتشخيص (مش مجموع كل المرات)،
    لأن المطلوب تحديدًا هو آخر محاولة فحص، مش تاريخ الفحص كامل.
    يرجع dict فيه: duration (timedelta أو None)، opened، closed، still_active (bool)
    أو None لو الحالة دي مش موجودة خالص في تاريخ الطلب.
    """
    if not isinstance(status_history, list) or not status_history:
        return None

    matches = []
    for row in status_history:
        if not isinstance(row, dict):
            continue
        status_name = row.get("Status_Name") or row.get("status_name") or row.get("status")
        if status_name != DIAGNOSIS_STATUS_NAME:
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

    # آخر مرة = أحدث "opened" بين كل التكرارات
    matches.sort(key=lambda r: r["opened"])
    return matches[-1]


def analyze_diagnosis_delay(api_key: str, model_name: str, order_id: int):
    """يرجع tuple: (نص رد الموديل, الحقائق الزمنية المحسوبة للتحقق منها في الواجهة)"""
    order_data = fetch_order_data(order_id)
    timeline_facts = compute_timeline_facts(order_data.get('status_history'))
    last_diagnosis = compute_last_diagnosis_duration(order_data.get('status_history'))

    # 🛑 قصير الدائرة: لو آخر مرة فحص وتشخيص كانت أقل من 4 *ساعات عمل فعلية* (بعد استبعاد
    # خارج الدوام 8ص-6م ويوم الجمعة)، نرجّع "لا يوجد تأخير" مباشرة من غير استدعاء الموديل
    NO_DELAY_THRESHOLD_HOURS = 4
    if last_diagnosis and not last_diagnosis["still_active"]:
        business_hours = last_diagnosis["business_duration"].total_seconds() / 3600
        if business_hours < NO_DELAY_THRESHOLD_HOURS:
            no_delay_text = (
                f"لا يوجد تأخير، حيث استغرقت مرحلة الفحص والتشخيص "
                f"{_format_timedelta(last_diagnosis['business_duration'])} ساعات عمل فعلية فقط "
                f"(المدة الخام {_format_timedelta(last_diagnosis['duration'])} شاملة خارج الدوام/الجمعة)، "
                f"وهي مدة طبيعية ولا تستدعي تدقيقًا إضافيًا."
                f"\n===SPLIT===\n"
                f"مدة الفحص بساعات العمل الفعلية: {_format_timedelta(last_diagnosis['business_duration'])} "
                f"(من {last_diagnosis['opened']} إلى {last_diagnosis['closed']}، بعد استبعاد ما هو خارج 8ص-6م ويوم الجمعة)، "
                f"وهي أقل من الحد الأدنى المعتبر تأخيرًا (4 ساعات عمل)."
                f"\n===CLASSIFICATION===\nلا يوجد تأخير"
            )
            return no_delay_text, timeline_facts

    last_diagnosis_summary = (
        f"⏱️ مدة *آخر* محاولة فحص وتشخيص فقط (وليس مجموع كل المحاولات):\n"
        f"- المدة الخام (شاملة كل الأوقات): {_format_timedelta(last_diagnosis['duration'])}\n"
        f"- مدة ساعات العمل الفعلية فقط (بعد استبعاد ما هو خارج 8ص-6م ويوم الجمعة): "
        f"{_format_timedelta(last_diagnosis['business_duration'])}"
        + (" (لسه شغالة/جارية حتى الآن)" if last_diagnosis["still_active"] else "")
        + f"\nمن {last_diagnosis['opened']} إلى {last_diagnosis['closed']}."
        + f"\n⚠️ تنبيه: هذا الرقم وحده (مهما كان كبيرًا) لا يعني تأخيرًا. احكم فقط بناءً على "
        + f"وجود فجوات صمت حقيقية من المركز بعد متابعة التشغيل، كما هو موضح في تعليمات تحليل التعليقات أدناه."
        if last_diagnosis else
        "⚠️ لا توجد محاولة فحص وتشخيص واضحة في سجل هذا الطلب."
    )

    prompt_text = f"""
    أنت كبير مدققي العمليات في شركة صيانة السيارات (مسمار - MisMar).
    الطلب رقم #{order_id} تحت الفحص بخصوص التأخير في مرحلة "{DIAGNOSIS_STATUS_NAME}".
    وظيفتك تحديد السبب الجذر الحقيقي وراء طول *آخر محاولة فحص وتشخيص فقط* (وليس تاريخ الفحص الكامل للطلب لو تكرر أكثر من مرة).

    ⚠️ === الحقيقة الزمنية الحاسمة (إلزامية الاستخدام، ولا تحسب من عندك) === ⚠️
    {last_diagnosis_summary}
    🛑 التزم بهذا الرقم فقط ولا تجمعه مع محاولات فحص سابقة إن وجدت في نفس الطلب.

    البيانات المتاحة للطلب:
    1. 🎫 تذاكر الشكاوى والمتابعة (خذ نظرة عامة على كل التذاكر، وليس فقط تذاكر التصعيد على المركز — قد تحمل تذاكر أخرى (تصعيد على التشغيل، شكوى عميل، ملاحظة داخلية) دلالة مختلفة تمامًا على المتسبب الحقيقي في التأخير):
    {json.dumps(order_data.get('tickets'), ensure_ascii=False, indent=2)}

    2. 💬 محادثات الشات والتعليقات الداخلية — هذا هو المصدر الأهم لتحديد المتسبب في التأخير:
    {json.dumps(order_data.get('comments'), ensure_ascii=False, indent=2)}

    🔍 === كيفية تحليل التعليقات لتحديد المتسبب الفعلي (إلزامي) === 🔍
    ⚠️ مبدأ أساسي يجب الالتزام به: **طول مدة الفحص وحده ليس دليلاً على وجود تأخير أبداً.**
    قد يستغرق الفحص 20 ساعة عمل فعلية أو أكثر وتكون هذه مدة طبيعية تمامًا وليست تأخيرًا،
    بشرط أن يكون المركز يرد بانتظام على كل متابعة من التشغيل (سواء بفيديو، صور، أو حتى رسالة نصية)
    دون ترك التشغيل بدون رد لفترة طويلة غير مبررة. في هذه الحالة الحكم الصحيح هو "لا يوجد تأخير"
    مهما كان الرقم الإجمالي كبيرًا، لأن الوقت انعكس تفاعلاً حقيقيًا ومجهودًا فعليًا في التشخيص وليس إهمالاً.
    الحكم الحقيقي على وجود تأخير من عدمه لا يُبنى على "كم استغرق الفحص إجمالاً؟" بل على:
    "هل توجد فجوة صمت طويلة غير مبررة بعد أن طلب التشغيل تحديثًا أو استفسر عن شيء، ولم يرد المركز خلالها؟"
    - افحص تسلسل الرسائل زمنيًا وحدد نمط التأخير من الحالات التالية:
    - إذا أرسل التشغيل رسالة/استفسار للمركز، واستغرق المركز عدة ساعات عمل فعلية بدون رد أو متابعة → هذا مؤشر على تأخير من طرف المركز.
    - إذا كان التشغيل نفسه لا يتابع مع المركز بشكل دوري ومتقارب (مثلاً يرسل رسالة متابعة ثم يترك فجوة طويلة قبل المتابعة التالية بدل المتابعة كل فترة قصيرة) → هذا مؤشر على تأخير ناتج عن التشغيل نفسه في عدم الضغط على المركز لاستكمال الفحص.
    - قارن توقيت كل رسالة بالرسالة التالية لها (بغض النظر عن مين المرسل) واحسب الفجوات الزمنية بينها من التوقيتات الظاهرة في البيانات نفسها، ولا تخترع فجوات غير موجودة.
    - 🕐 مهم جداً: أي فجوة زمنية تقع كليًا أو جزئيًا خارج ساعات العمل الرسمية (8:00 صباحًا حتى 6:00 مساءً) أو تقع يوم الجمعة (إجازة أسبوعية) لا تُحتسب كتأخير من أي طرف. مثال: لو أرسل التشغيل رسالة الساعة 5 مساءً ورد المركز الساعة 9 صباحًا في اليوم التالي، فهذه ليست فجوة تأخير حقيقية لأن معظمها خارج الدوام.
    - وجود تذكرة تصعيد على المركز يُعتبر دليلاً داعمًا (وليس حاسمًا وحده) على تأخر المركز، ويجب ربطه بمحتوى فعلي من التعليقات لتأكيده.

    3. ⏱️ التسلسل الزمني الكامل للحالات (مرجعي فقط لفهم السياق العام للطلب):
    {json.dumps(order_data.get('status_history'), ensure_ascii=False, indent=2)}
    {timeline_facts}

    4. 💰 طلبات التسعير وعروض الأسعار (افحص إذا كان هناك تعطل مرتبط بانتظار تسعير قطع قبل استكمال التشخيص):
    {json.dumps(order_data.get('pricing'), ensure_ascii=False, indent=2)}

    === 🎯 تعليمات وقواعد الصياغة الصارمة ===
    قسّم إجابتك إلى ثلاثة أقسام يفصل بينها السطر `===SPLIT===` قبل القسم الثاني، والسطر `===CLASSIFICATION===` قبل القسم الثالث:

    القسم الأول: [السبب الجذري المباشر]
    - فقرة واحدة متصلة ومباشرة فقط (من 4 إلى 5 سطور كحد أقصى).
    - 🟢 ابدأ الفقرة **فورًا** بذكر السبب الجذري نفسه (الطرف المتسبب وماذا حدث بالضبط)، بدون أي مقدمات أو عبارات افتتاحية إطلاقًا.
    - 🛑 ممنوع تمامًا افتتاح الفقرة بعبارات مثل "يعود السبب الجذري إلى" أو "يعود التأخير إلى" أو أي صياغة مشابهة تمهيدية.
    - 🛑 ممنوع ذكر رقم الطلب نهائيًا في هذه الفقرة.
    - 🛑 ممنوع ذكر عبارة "آخر محاولة فحص وتشخيص" أو الإشارة إلى كونها آخر محاولة — تحدث عن السبب مباشرة وكأنك تشرحه لأول مرة بدون سياق تمهيدي.
    - مثال على الأسلوب المطلوب بالضبط: "تقصير ومماطلة مركز [اسم المركز] في تنفيذ الفحص الفني لـ[كذا]، حيث [تفاصيل الموقف]." — وليس: "يعود السبب الجذري للتأخير في آخر محاولة فحص الطلب رقم #123 إلى تقصير المركز..."
    - 🛑 يُمنع استخدام جمل فضفاضة مثل "بسبب إجراءات الفحص المعتادة" دون تحديد السبب الحقيقي من نص التذاكر أو الشات.
    - يُمنع استخدام القوائم، العناوين، أو أرقام التذاكر والعروض الداخلية.

    ===SPLIT===

    القسم الثاني: [الأدلة والوقائع التفصيلية]
    - التوقيت الدقيق لبداية ونهاية (أو استمرار) آخر محاولة فحص وتشخيص.
    - اقتباس نصوص التذاكر ذات الصلة (description/result) كلمة بكلمة.
    - سرد الفجوات الزمنية الفعلية بين رسائل التشغيل والمركز التي تدعم استنتاجك.
    - أي ارتباط بين تأخر التشخيص وانتظار تسعير أو موافقة على قطع.

    ===CLASSIFICATION===
    - اكتب هنا تصنيفاً واحداً فقط (كلمة أو عبارة واحدة بالضبط بدون أي شرح إضافي) من هذه القائمة الحصرية:
      المركز | التشغيل | العميل | لا يوجد تأخير | المركز والتشغيل | يوم الجمعة | سطحات | خطأ في استخدام الحالة | المشكلة لم تظهر خارج وقت العمل | تشخيص إضافي | نقل بين المركزين
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
            return model_text, timeline_facts
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
    <h1>🔧 نظام تدقيق تعطل الفحص والتشخيص (MisMar Diagnosis Delay Audit)</h1>
    <p>استخراج السبب الجذري وراء بقاء الطلب في مرحلة الفحص والتشخيص لمدة طويلة</p>
</div>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("📋 بيانات الطلب")
    order_id = st.number_input("رقم الطلب (Order ID)", value=1029480, step=1)
    st.markdown("<br>", unsafe_allow_html=True)
    analyze_btn = st.button("🚀 استخراج تبرير تعطل الفحص والتشخيص")

with col2:
    st.subheader("📊 مخرجات التقرير والتدقيق")

    if analyze_btn:
        if not api_key_input:
            st.error("⚠️ يرجى إدخال Gemini API Key أولاً من القائمة الجانبية.")
        else:
            with st.spinner("⏳ جاري فحص أسباب تعطل مرحلة الفحص والتشخيص..."):
                try:
                    full_response, timeline_debug = analyze_diagnosis_delay(
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

                    st.session_state['diagnosis_audit_result'] = {
                        'justification': justification.strip(),
                        'evidence': evidence.strip(),
                        'classification': classification.strip(),
                        'order_id': order_id,
                        'timeline_debug': timeline_debug,
                    }

                except Exception as e:
                    st.error(f"❌ حدث خطأ أثناء التحليل: {str(e)}")

    if 'diagnosis_audit_result' in st.session_state and st.session_state['diagnosis_audit_result']:
        res = st.session_state['diagnosis_audit_result']

        safe_justification = html.escape(res["justification"])
        safe_evidence = html.escape(res["evidence"])
        safe_classification = html.escape(res.get("classification", "غير محدد"))

        st.markdown(
            f'<div style="display:inline-block; background:#F97316; color:#0B0F19; '
            f'font-weight:800; padding:8px 18px; border-radius:999px; margin-bottom:14px; '
            f'font-size:1.05rem;">🏷️ التصنيف النهائي: {safe_classification}</div>',
            unsafe_allow_html=True
        )

        st.markdown("### 📝 التبرير التشغيلي لتعطل الفحص والتشخيص:")
        st.markdown(f'<div class="justification-card">{safe_justification}</div>', unsafe_allow_html=True)

        st.text_area("📋 اضغط Ctrl+A ثم Ctrl+C للنسخ المباشر:", value=res["justification"], height=120)

        st.markdown("### 🔍 الأدلة والوقائع التفصيلية:")
        st.markdown(f'<div class="evidence-card">{safe_evidence}</div>', unsafe_allow_html=True)

        with st.expander("🛠️ (Debug) الحقائق الزمنية المحسوبة برمجيًا فعليًا"):
            st.markdown(
                "الأرقام دي محسوبة مباشرة بكود بايثون من التواريخ الخام (مش من الموديل)، "
                "قارنها بالتقرير فوق للتأكد إن الموديل التزم بيها حرفيًا:"
            )
            st.text(res.get("timeline_debug", "لا توجد بيانات."))
    elif not analyze_btn:
        st.info("👈 قم بإدخال رقم الطلب والضغط على زر التحليل لعرض تبرير تعطل الفحص والتشخيص هنا.")