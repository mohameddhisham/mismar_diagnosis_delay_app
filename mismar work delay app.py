# -*- coding: utf-8 -*-
"""
تدقيق تعطل "جاري العمل" — مسمار (نسخة Streamlit)
نفس منطق سكريبت Apps Script بالظبط:
- ساعات العمل 9ص-6م، استبعاد يوم الجمعة
- تصحيح فرق التوقيت (+3 ساعات)
- عتبة 4 ساعات عمل فعلية
- نفس البرومبت والتصنيفات وقائمة الموديلات البديلة

طرق الإدخال:
1) لصق روابط/أرقام الطلبات أو رفع ملف CSV/Excel
2) Google Sheet مباشرة (عمود A رابط الطلب، S السبب الجذري، T التصنيف)
"""

import io
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

# ====== الإعدادات ======
COLUMNS = {
    "ORDER_ID": 1,        # العمود A
    "JUSTIFICATION": 19,  # العمود S
    "CLASSIFICATION": 20, # العمود T
}
HEADER_ROWS = 2

MODEL_FALLBACK_LIST = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash"]

METABASE_BASE_URL = "https://analysis.mismarapp.com"
METABASE_CARD_IDS = {
    "tickets": 15395,
    "comments": 15394,
    "status_history": 15393,
}

WORK_STATUS_NAME = "جاري العمل"
WORK_CLASSIFICATIONS = [
    "قطع الغيار", "التشغيل", "عميل", "مركز", "لا يوجد تأخير", "تأخير في الشحن",
    "خلل تقني", "تم التسليم", "يوم الجمعة", "تشخيص إضافي", "مكرر",
    "قطع الغيار والمركز", "نقل مركز آخر", "التشغيل والمركز", "قطع الغيار والتشغيل",
    "قطع الغيار وطلبية", "تشخيص مركز خاطئ",
]

WORK_START_HOUR = 9
WORK_END_HOUR = 18
FRIDAY_WEEKDAY = 4  # في بايثون: الاثنين=0 ... الجمعة=4
NO_DELAY_THRESHOLD_HOURS = 4
LOCAL_TZ = ZoneInfo("Asia/Riyadh")


# ====== الأسرار / الإعدادات ======
def get_secret(name, sidebar_value=""):
    if sidebar_value:
        return sidebar_value
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


# ====== Metabase ======
@st.cache_resource(ttl=6 * 3600)
def get_metabase_session_token(username, password):
    if not username or not password:
        raise Exception("محتاج تضبط بيانات دخول Metabase الأول (يوزر نيم وباسورد).")
    r = requests.post(
        METABASE_BASE_URL + "/api/session",
        json={"username": username, "password": password},
        timeout=60,
    )
    if r.status_code != 200:
        raise Exception(
            f"فشل تسجيل الدخول لـ Metabase (كود {r.status_code}). "
            f"تأكد من صحة اليوزر نيم والباسورد. الرد: {r.text[:200]}"
        )
    return r.json()["id"]


def card_result_to_objects(query_result):
    if not query_result or "data" not in query_result:
        return []
    data = query_result["data"]
    if "cols" not in data or "rows" not in data:
        return []
    col_names = [c.get("name") or c.get("display_name") for c in data["cols"]]
    return [dict(zip(col_names, row)) for row in data["rows"]]


def fetch_card_data(card_id, order_id, session_token):
    url = f"{METABASE_BASE_URL}/api/card/{card_id}/query"
    parameters = [{
        "type": "number/=",
        "target": ["variable", ["template-tag", "order_id"]],
        "value": str(order_id),
    }]
    r = requests.post(
        url,
        json={"parameters": parameters},
        headers={"X-Metabase-Session": session_token},
        timeout=120,
    )
    if r.status_code not in (200, 202):
        return f"Error HTTP {r.status_code}: {r.text[:200]}"
    try:
        return card_result_to_objects(r.json())
    except Exception:
        return f"Error: Response wasn't valid JSON: {r.text[:200]}"


def fetch_order_data(order_id, mb_user, mb_pass):
    token = get_metabase_session_token(mb_user, mb_pass)
    payload = {}
    for key, card_id in METABASE_CARD_IDS.items():
        try:
            payload[key] = fetch_card_data(card_id, order_id, token)
        except Exception as e:
            payload[key] = f"Error: {e}"
    return payload


# ====== التواريخ وساعات العمل ======
def parse_dt(value):
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2}) (\d{1,2}):(\d{2}) (AM|PM)$", s, re.I)
    if not m:
        return None
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    hour, minute = int(m[4]), int(m[5])
    ampm = m[6].upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    dt = datetime(year, month, day, hour, minute, 0)
    # Metabase بيرجّع التوقيت متأخر 3 ساعات عن التوقيت الفعلي بالسعودية
    return dt + timedelta(hours=3)


def now_local():
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def format_duration(td):
    total_minutes = int(td.total_seconds() // 60)
    days = total_minutes // (24 * 60)
    rem = total_minutes % (24 * 60)
    hours = rem // 60
    minutes = rem % 60
    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours:
        parts.append(f"{hours} ساعة")
    if minutes or not parts:
        parts.append(f"{minutes} دقيقة")
    return " و".join(parts)


def business_hours_duration(start, end):
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


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def compute_last_status_duration(status_history, status_name):
    if not isinstance(status_history, list) or not status_history:
        return None
    matches = []
    for row in status_history:
        if not isinstance(row, dict):
            continue
        row_status = row.get("Status_Name") or row.get("status_name") or row.get("status")
        if row_status != status_name:
            continue
        opened_raw = row.get("Opened_At") or row.get("opened_at") or row.get("created_at")
        closed_raw = row.get("Closed_At") or row.get("closed_at") or row.get("ended_at")
        opened_dt = parse_dt(opened_raw)
        still_active = bool(closed_raw and str(closed_raw).strip().lower() == "currently active")
        closed_dt = now_local() if still_active else parse_dt(closed_raw)
        if opened_dt and closed_dt:
            matches.append({
                "opened": opened_dt,
                "closed": closed_dt,
                "duration": closed_dt - opened_dt,
                "business": business_hours_duration(opened_dt, closed_dt),
                "still_active": still_active,
            })
    if not matches:
        return None
    matches.sort(key=lambda m: m["opened"])
    return matches[-1]


# ====== Gemini ======
def call_gemini_with_fallback(api_key, prompt_text):
    attempt_errors = []
    for model_name in MODEL_FALLBACK_LIST:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            r = requests.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt_text}]}],
                    "generationConfig": {"temperature": 0.3, "topP": 0.9},
                },
                timeout=300,
            )
        except Exception as e:
            attempt_errors.append(f"{model_name}: خطأ اتصال — {e}")
            continue

        if r.status_code == 200:
            try:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                attempt_errors.append(
                    f"{model_name}: رد بشكل غير متوقع (200 لكن بدون نص) — {r.text[:200]}"
                )
                continue
        elif r.status_code == 401:
            raise Exception("خطأ مصادقة (401): المفتاح غير صالح أو منتهي الصلاحية.")
        else:
            attempt_errors.append(f"{model_name}: خطأ {r.status_code} — {r.text[:200]}")
            continue

    raise Exception("فشلت كل الموديلات المتاحة:\n" + "\n".join(attempt_errors))


def to_json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# ====== التحليل الرئيسي ======
def analyze_work_delay(api_key, order_id, mb_user, mb_pass):
    order_data = fetch_order_data(order_id, mb_user, mb_pass)
    last = compute_last_status_duration(order_data.get("status_history"), WORK_STATUS_NAME)

    # قصير الدائرة: أقل من 4 ساعات عمل فعلية = لا يوجد تأخير بدون استدعاء الموديل
    if last and not last["still_active"]:
        business_hours = last["business"].total_seconds() / 3600
        if business_hours < NO_DELAY_THRESHOLD_HOURS:
            return (
                f"لا يوجد تأخير، حيث استغرقت مرحلة {WORK_STATUS_NAME} "
                f"{format_duration(last['business'])} ساعات عمل فعلية فقط، وهي مدة طبيعية."
                "\n===SPLIT===\n"
                f"مدة حالة {WORK_STATUS_NAME} بساعات العمل الفعلية: {format_duration(last['business'])} "
                f"(من {fmt(last['opened'])} إلى {fmt(last['closed'])})، "
                "وهي أقل من الحد الأدنى المعتبر تأخيرًا (4 ساعات عمل)."
                "\n===CLASSIFICATION===\nلا يوجد تأخير"
            )

    if not last:
        return (
            "تعذر العثور على مرحلة جاري العمل واضحة في سجل حالات هذا الطلب."
            "\n===SPLIT===\nلا توجد بيانات كافية للتحليل."
            "\n===CLASSIFICATION===\nلا يوجد تأخير"
        )

    last_status_note = (
        f"⏱️ مدة *آخر* مرة دخل فيها الطلب حالة '{WORK_STATUS_NAME}':\n"
        f"- المدة الخام (شاملة كل الأوقات): {format_duration(last['duration'])}\n"
        "- مدة ساعات العمل الفعلية فقط (بعد استبعاد ما هو خارج 9ص-6م ويوم الجمعة): "
        f"{format_duration(last['business'])}"
        + (" (لسه شغالة/جارية حتى الآن)" if last["still_active"] else "")
        + f"\nمن {fmt(last['opened'])} إلى {fmt(last['closed'])}.\n"
        "⚠️ تنبيه: هذا الرقم وحده لا يعني تلقائيًا وجود تأخير. افحص التذاكر والتعليقات أولاً "
        "لمعرفة هل هذا وقت عمل فعلي وتحديثات مستمرة، أم فجوة صمت وتعطل حقيقي."
    )

    prompt_text = (
        "أنت كبير مدققي العمليات في شركة صيانة السيارات (مسمار - MisMar).\n"
        f"مطلوب تحديد السبب الجذري وراء التأخير (إن وجد) في آخر مرة دخل فيها الطلب رقم #{order_id} مرحلة \"{WORK_STATUS_NAME}\".\n\n"
        "⚠️ === الحقيقة الزمنية الحاسمة (إلزامية الاستخدام، ولا تحسب من عندك) === ⚠️\n"
        + last_status_note + "\n\n"
        "البيانات المتاحة للطلب (التركيز الأساسي هنا على التذاكر والتعليقات):\n"
        "1. 🎫 تذاكر الشكاوى والمتابعة — المصدر الأهم هنا لمعرفة سبب تعطل التنفيذ:\n"
        + to_json(order_data.get("tickets")) + "\n\n"
        "2. 💬 محادثات الشات والتعليقات الداخلية — قارن مدة العمل الفعلية بمحتوى المتابعات:\n"
        + to_json(order_data.get("comments")) + "\n\n"
        "🔍 === سيناريوهات محتملة يجب فحصها (إلزامي) === 🔍\n"
        "- هل التأخير بسبب انتظار توفر قطعة غيار (سواء من المورد، طلبية جديدة، أو نقص في المخزون)؟ → صنّف كـ\"قطع الغيار\" أو \"قطع الغيار وطلبية\" حسب السياق.\n"
        "- هل هناك تأخير شحن قطعة تم طلبها بالفعل؟ → صنّف كـ\"تأخير في الشحن\".\n"
        "- هل ظهر خلل تقني جديد أثناء التنفيذ يستلزم وقتًا إضافيًا لم يكن متوقعًا؟ → صنّف كـ\"خلل تقني\".\n"
        "- هل اكتشف المركز أثناء العمل حاجة لتشخيص إضافي لم يظهر في الفحص الأول؟ → صنّف كـ\"تشخيص إضافي\".\n"
        "- هل السبب الأساسي أن الفحص الأول كان خاطئًا وأدى لإعادة عمل أو تصحيح مسار؟ → صنّف كـ\"تشخيص مركز خاطئ\".\n"
        "- هل تم نقل السيارة لمركز آخر أثناء التنفيذ؟ → صنّف كـ\"نقل مركز آخر\".\n"
        "- هل هذا طلب عمل مكرر فعليًا لنفس المهمة؟ → صنّف كـ\"مكرر\".\n"
        "- هل السيارة تم تسليمها فعليًا للعميل والحالة لم تُحدّث بعد في النظام؟ → صنّف كـ\"تم التسليم\".\n"
        "- هل التأخير ناتج عن العميل نفسه؟ → صنّف كـ\"عميل\".\n"
        "- 🕐 مهم: أي فجوة زمنية تقع كليًا أو جزئيًا خارج ساعات العمل الرسمية (9ص-6م) أو يوم الجمعة لا تُحتسب كتأخير من أي طرف. لو الجزء الأكبر من المدة وقع في هذا النطاق → صنّف كـ\"يوم الجمعة\" إن كان هو السبب الرئيسي.\n"
        "- ⚠️ تذكّر: مدة طويلة مع تحديثات منتظمة من المركز في التعليقات تعني عملاً حقيقيًا مستمرًا وليست تأخيرًا، بغض النظر عن الرقم الإجمالي.\n\n"
        "3. ⏱️ التسلسل الزمني الكامل للحالات (مرجعي فقط لفهم السياق العام للطلب):\n"
        + to_json(order_data.get("status_history")) + "\n\n"
        "=== 🎯 تعليمات وقواعد الصياغة الصارمة ===\n"
        "قسّم إجابتك إلى ثلاثة أقسام يفصل بينها السطر `===SPLIT===` قبل القسم الثاني، والسطر `===CLASSIFICATION===` قبل القسم الثالث:\n\n"
        "القسم الأول: [السبب الجذري المباشر]\n"
        "- فقرة واحدة متصلة ومباشرة فقط (من 4 إلى 5 سطور كحد أقصى).\n"
        "- ابدأ الفقرة فورًا بذكر السبب الجذري نفسه، بدون أي مقدمات إطلاقًا.\n"
        "- ممنوع تمامًا افتتاح الفقرة بعبارات مثل \"يعود السبب الجذري إلى\".\n"
        "- ممنوع ذكر رقم الطلب نهائيًا في هذه الفقرة.\n"
        "- يُمنع استخدام جمل فضفاضة دون تحديد السبب الحقيقي من نص التذاكر أو الشات.\n"
        "- يُمنع استخدام القوائم أو العناوين أو أرقام التذاكر الداخلية.\n\n"
        "===SPLIT===\n\n"
        "القسم الثاني: [الأدلة والوقائع التفصيلية]\n"
        "- التوقيت الدقيق لبداية (واستمرار أو انتهاء) مرحلة جاري العمل.\n"
        "- اقتباس نصوص التذاكر والتعليقات ذات الصلة كلمة بكلمة.\n"
        "- سرد أي فجوات صمت فعلية أو غياب تحديثات تدعم استنتاجك.\n\n"
        "===CLASSIFICATION===\n"
        "- اكتب هنا تصنيفاً واحداً فقط (بالضبط كما هو، بدون أي شرح إضافي) من هذه القائمة الحصرية:\n"
        "  " + " | ".join(WORK_CLASSIFICATIONS) + "\n"
        "- اختر التصنيف الأقرب لواقع الأدلة فقط، ولا تخترع تصنيفاً من عندك خارج هذه القائمة."
    )

    return call_gemini_with_fallback(api_key, prompt_text)


def run_single_order(api_key, order_id, mb_user, mb_pass):
    """بترجع (justification, classification, evidence, error)"""
    try:
        full = analyze_work_delay(api_key, order_id, mb_user, mb_pass)
        classification = "غير محدد"
        main_part = full
        if "===CLASSIFICATION===" in full:
            main_part, cls = full.split("===CLASSIFICATION===", 1)
            classification = cls.strip()
        justification = main_part
        evidence = ""
        if "===SPLIT===" in main_part:
            justification, evidence = main_part.split("===SPLIT===", 1)
        return justification.strip(), classification.strip(), evidence.strip(), None
    except Exception as e:
        return "", "", "", str(e)


# ====== استخراج رقم الطلب ======
def extract_order_id(text):
    if text is None:
        return None
    s = str(text).strip()
    m = re.search(r"/orders/(\d+)", s)
    if m:
        return m.group(1)
    last = s.rstrip("/").split("/")[-1]
    if re.fullmatch(r"\d+", last):
        return last
    # لو الخلية HYPERLINK formula
    m = re.search(r"/orders/(\d+)", s)
    return m.group(1) if m else None


# ====== Google Sheets ======
def open_worksheet(sheet_url, worksheet_name):
    import gspread
    creds = dict(st.secrets["gcp_service_account"])
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open_by_url(sheet_url)
    return sh.worksheet(worksheet_name) if worksheet_name else sh.sheet1


def run_google_sheet_mode(api_key, mb_user, mb_pass, sheet_url, worksheet_name, force):
    ws = open_worksheet(sheet_url, worksheet_name)
    col_a = ws.col_values(COLUMNS["ORDER_ID"], value_render_option="FORMULA")
    col_s = ws.col_values(COLUMNS["JUSTIFICATION"])
    last_row = len(col_a)

    processed = skipped = errors = 0
    progress = st.progress(0.0)
    status = st.empty()
    total = max(last_row - HEADER_ROWS, 1)

    for idx, row in enumerate(range(HEADER_ROWS + 1, last_row + 1), start=1):
        progress.progress(min(idx / total, 1.0))
        existing = col_s[row - 1] if row - 1 < len(col_s) else ""
        if existing and existing.strip() and not force:
            skipped += 1
            continue
        order_id = extract_order_id(col_a[row - 1])
        if not order_id:
            skipped += 1
            continue

        status.info(f"جاري تحليل الطلب {order_id} (صف {row})...")
        ws.update_cell(row, COLUMNS["JUSTIFICATION"], "جاري التحليل...")
        just, cls, _, err = run_single_order(api_key, order_id, mb_user, mb_pass)
        if err:
            ws.update_cell(row, COLUMNS["JUSTIFICATION"], "خطأ: " + err)
            errors += 1
        else:
            ws.update_cell(row, COLUMNS["JUSTIFICATION"], just)
            ws.update_cell(row, COLUMNS["CLASSIFICATION"], cls)
            processed += 1

    status.empty()
    st.success(
        f"انتهى الفحص:\n\n✅ صفوف اتعملت: {processed}\n\n"
        f"⏭️ صفوف اتقفزت: {skipped}\n\n❌ صفوف فيها خطأ: {errors}"
    )


# ====== الواجهة ======
st.set_page_config(page_title="تدقيق مسمار", page_icon="🔧", layout="wide")
st.markdown(
    "<style>html, body, [class*='css'] {direction: rtl; text-align: right;}</style>",
    unsafe_allow_html=True,
)
st.title("🔧 تدقيق تعطل «جاري العمل» — مسمار")

with st.sidebar:
    st.header("الإعدادات")
    st.caption("لو الأسرار متضبطة في Streamlit Secrets سيب الخانات فاضية.")
    gemini_in = st.text_input("مفتاح Gemini API", type="password")
    mb_user_in = st.text_input("يوزر نيم Metabase")
    mb_pass_in = st.text_input("باسورد Metabase", type="password")

api_key = get_secret("GEMINI_API_KEY", gemini_in)
mb_user = get_secret("METABASE_USERNAME", mb_user_in)
mb_pass = get_secret("METABASE_PASSWORD", mb_pass_in)

missing = []
if not api_key:
    missing.append("مفتاح Gemini API")
if not mb_user or not mb_pass:
    missing.append("بيانات دخول Metabase")
if missing:
    st.warning("محتاج تضبط: " + " و".join(missing))

tab1, tab2 = st.tabs(["📋 لصق طلبات / رفع ملف", "📊 Google Sheet مباشرة"])

with tab1:
    pasted = st.text_area(
        "الصق روابط الطلبات أو أرقامها (واحد في كل سطر)", height=150
    )
    uploaded = st.file_uploader(
        "أو ارفع ملف CSV / Excel (رابط الطلب في أول عمود)", type=["csv", "xlsx"]
    )

    if st.button("🚀 تشغيل التدقيق", type="primary", disabled=bool(missing), key="run1"):
        raw = []
        if pasted.strip():
            raw += pasted.splitlines()
        if uploaded is not None:
            if uploaded.name.endswith(".csv"):
                df_in = pd.read_csv(uploaded, header=None)
            else:
                df_in = pd.read_excel(uploaded, header=None)
            raw += df_in.iloc[:, 0].dropna().astype(str).tolist()

        ids = []
        for item in raw:
            oid = extract_order_id(item)
            if oid and oid not in ids:
                ids.append(oid)

        if not ids:
            st.error("مفيش أرقام طلبات صالحة.")
        else:
            results = []
            progress = st.progress(0.0)
            for i, oid in enumerate(ids, start=1):
                with st.spinner(f"جاري تحليل الطلب {oid} ({i}/{len(ids)})..."):
                    just, cls, evidence, err = run_single_order(api_key, oid, mb_user, mb_pass)
                results.append({
                    "رقم الطلب": oid,
                    "السبب الجذري": ("خطأ: " + err) if err else just,
                    "التصنيف": cls,
                    "الأدلة": evidence,
                })
                progress.progress(i / len(ids))

            df = pd.DataFrame(results)
            st.dataframe(df, use_container_width=True)

            buf = io.BytesIO()
            df.to_excel(buf, index=False)
            st.download_button(
                "⬇️ تحميل النتائج (Excel)",
                buf.getvalue(),
                file_name="audit_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

with tab2:
    st.caption(
        "محتاج تضيف service account في Secrets باسم gcp_service_account، "
        "وتشارك الشيت مع إيميل الـ service account (صلاحية Editor)."
    )
    sheet_url = st.text_input("رابط الشيت")
    ws_name = st.text_input("اسم الورقة (سيبها فاضية لأول ورقة)")
    force = st.checkbox("إعادة تشغيل حتى الصفوف اللي فيها نتيجة قبل كده")

    if st.button("🚀 تشغيل على الشيت", type="primary", disabled=bool(missing) or not sheet_url, key="run2"):
        try:
            run_google_sheet_mode(api_key, mb_user, mb_pass, sheet_url, ws_name, force)
        except Exception as e:
            st.error(f"خطأ: {e}")
