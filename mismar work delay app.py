/**
 * سكريبت تدقيق تعطل "جاري العمل" — يعمل مباشرة جوه Google Sheets
 * ------------------------------------------------------------
 * نفس منطق كود بايثون بالظبط: نفس حساب ساعات العمل (9ص-6م)، استبعاد يوم
 * الجمعة، تصحيح فرق التوقيت (+3 ساعات)، عتبة الـ4 ساعات، ونفس البرومبت
 * والتصنيفات، لكن بيشتغل جوه الشيت مباشرة من غير Streamlit خالص.
 *
 * ====== شكل الشيت المتوقع ======
 * - عمود A: رابط الطلب (رقم الطلب بيتاخد تلقائيًا من آخر جزء في الرابط)
 * - عمود S: هنا بيتحط السبب الجذري (التبرير)
 * - عمود T: هنا بيتحط التصنيف النهائي
 * - أول صفين (1 و2) عناوين/تاريخ تحديث، البيانات الفعلية تبدأ من صف 3
 *
 * ⚠️ ملحوظة مهمة: الروابط العامة (Public Links) بتاعة Metabase اتلغت، فالكود
 * دلوقتي بيسجّل دخول برمجيًا بيوزر نيم وباسورد حقيقيين (محفوظين بأمان في
 * إعدادات المشروع، مش مكتوبين في الكود)، وبيستخدم أرقام الأسئلة الداخلية
 * (Card ID) مباشرة بدل الرابط العام.
 *
 * ====== طريقة التركيب ======
 * 1. افتح الشيت، من القائمة: Extensions > Apps Script
 * 2. امسح أي كود موجود في Code.gs والصق الكود ده مكانه بالكامل
 * 3. احفظ (Ctrl+S)
 * 4. ارجع للشيت واعمل Refresh للصفحة، هتلاقي قائمة "تدقيق مسمار" جديدة
 * 5. من القائمة دي: دوس "ضبط مفتاح Gemini API" واكتب مفتاحك مرة واحدة بس
 * 6. وبرضو دوس "ضبط بيانات دخول Metabase" واكتب يوزر نيم وباسورد حسابك في Metabase
 * 7. لو عايز التشغيل يبقى تلقائي كل يوم: دوس "تفعيل التشغيل التلقائي (9:30 صباحًا يوميًا)"
 * 8. أو شغّله يدويًا وقت ما تحب من "تشغيل التدقيق على الصفوف الجديدة"
 *
 * ملحوظة: لو ترتيب أعمدتك اتغيّر يومًا ما، غيّر أرقام COLUMNS تحت فقط.
 */

// ====== إعدادات الأعمدة — مطابقة لشكل شيت مسمار الفعلي ======
var COLUMNS = {
  ORDER_ID: 1,       // العمود A: رابط الطلب (رقم الطلب بيتاخد من آخر جزء في اللينك)
  JUSTIFICATION: 19, // العمود S: عمود "اخر تحديث" اللي هيتحط فيه السبب الجذري
  CLASSIFICATION: 20 // العمود T: التصنيف النهائي
};
var HEADER_ROWS = 2; // صف تاريخ التحديث + صف أسماء الأعمدة

// قائمة الموديلات بالترتيب: لو الأول مزدحم (503) أو مش موجود (404)، الكود يجرب اللي بعده تلقائيًا
// (gemini-2.5-flash اتشال من القائمة لأنه بيرجّع 404 فعليًا رغم إن موعد إيقافه الرسمي أكتوبر 2026)
var MODEL_FALLBACK_LIST = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash"];

// ⚠️ الروابط العامة (public links) بتاعة Metabase اتلغت، فبقينا نسجّل دخول
// برمجيًا بيوزر/باسورد حقيقيين، ونستخدم أرقام الأسئلة الداخلية (Card ID) مباشرة
var METABASE_BASE_URL = "https://analysis.mismarapp.com";
var METABASE_CARD_IDS = {
  tickets: 15395,
  comments: 15394,
  status_history: 15393
};

// بنخزّن التوكن في متغير على مستوى الملف عشان نسجّل دخول مرة واحدة بس لكل تشغيلة
var _cachedSessionToken = null;

function setMetabaseCredentials() {
  var ui = SpreadsheetApp.getUi();
  var userResponse = ui.prompt('أدخل يوزر نيم/إيميل حساب Metabase:', ui.ButtonSet.OK_CANCEL);
  if (userResponse.getSelectedButton() != ui.Button.OK) return;

  var passResponse = ui.prompt('أدخل باسورد حساب Metabase:', ui.ButtonSet.OK_CANCEL);
  if (passResponse.getSelectedButton() != ui.Button.OK) return;

  var props = PropertiesService.getScriptProperties();
  props.setProperty('METABASE_USERNAME', userResponse.getResponseText().trim());
  props.setProperty('METABASE_PASSWORD', passResponse.getResponseText().trim());
  ui.alert('تم حفظ بيانات دخول Metabase بنجاح.');
}

// ====== تسجيل دخول Metabase برمجيًا والحصول على سيشن توكن صالح ======
function getMetabaseSessionToken() {
  if (_cachedSessionToken) return _cachedSessionToken;

  var props = PropertiesService.getScriptProperties();
  var username = props.getProperty('METABASE_USERNAME');
  var password = props.getProperty('METABASE_PASSWORD');

  if (!username || !password) {
    throw new Error('محتاج تضبط بيانات دخول Metabase الأول من القائمة (يوزر نيم وباسورد).');
  }

  var response = UrlFetchApp.fetch(METABASE_BASE_URL + "/api/session", {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ username: username, password: password }),
    muteHttpExceptions: true
  });

  var code = response.getResponseCode();
  if (code !== 200) {
    throw new Error(
      "فشل تسجيل الدخول لـ Metabase (كود " + code + "). " +
      "تأكد من صحة اليوزر نيم والباسورد المحفوظين. الرد: " + response.getContentText().substring(0, 200)
    );
  }

  var result = JSON.parse(response.getContentText());
  _cachedSessionToken = result.id;
  return _cachedSessionToken;
}

// ====== تحويل نتيجة استعلام Metabase (data.cols + data.rows) لمصفوفة كائنات ======
function cardResultToObjects(queryResult) {
  if (!queryResult || !queryResult.data || !queryResult.data.cols || !queryResult.data.rows) {
    return [];
  }
  var colNames = queryResult.data.cols.map(function (c) { return c.name || c.display_name; });
  return queryResult.data.rows.map(function (row) {
    var obj = {};
    colNames.forEach(function (name, i) { obj[name] = row[i]; });
    return obj;
  });
}

var WORK_STATUS_NAME = "جاري العمل";
var WORK_CLASSIFICATIONS = [
  "قطع الغيار", "التشغيل", "عميل", "مركز", "لا يوجد تأخير", "تأخير في الشحن",
  "خلل تقني", "تم التسليم", "يوم الجمعة", "تشخيص إضافي", "مكرر",
  "قطع الغيار والمركز", "نقل مركز آخر", "التشغيل والمركز", "قطع الغيار والتشغيل",
  "قطع الغيار وطلبية", "تشخيص مركز خاطئ"
];

// ساعات العمل المعتبرة لمرحلة جاري العمل: 9 صباحًا - 6 مساءً
var WORK_START_HOUR = 9;
var WORK_END_HOUR = 18;
var FRIDAY_DAY = 5; // في JavaScript: Date.getDay() الأحد=0 ... الجمعة=5 ... السبت=6

// ====== القائمة المخصصة في الشيت ======
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('تدقيق مسمار')
    .addItem('تشغيل التدقيق على الصفوف الجديدة', 'runPendingRowsFromMenu')
    .addItem('إعادة تشغيل الصف الحالي بالقوة', 'forceRunCurrentRow')
    .addSeparator()
    .addItem('تفعيل التشغيل التلقائي (9:30 صباحًا يوميًا)', 'setupDailyTrigger')
    .addItem('إيقاف التشغيل التلقائي', 'removeDailyTrigger')
    .addSeparator()
    .addItem('ضبط بيانات دخول Metabase', 'setMetabaseCredentials')
    .addSeparator()
    .addItem('ضبط مفتاح Gemini API', 'setApiKey')
    .addToUi();
}

function setApiKey() {
  var ui = SpreadsheetApp.getUi();
  var response = ui.prompt('أدخل مفتاح Gemini API:', ui.ButtonSet.OK_CANCEL);
  if (response.getSelectedButton() == ui.Button.OK) {
    var key = response.getResponseText().trim();
    PropertiesService.getScriptProperties().setProperty('GEMINI_API_KEY', key);
    ui.alert('تم حفظ المفتاح بنجاح.');
  }
}

// ====== إعداد/إلغاء التشغيل التلقائي اليومي ======
function setupDailyTrigger() {
  removeDailyTrigger(); // نمسح أي trigger قديم الأول عشان منعملش تكرار
  ScriptApp.newTrigger('processAllPendingRows')
    .timeBased()
    .atHour(9)
    .nearMinute(30)
    .everyDays(1)
    .create();
  SpreadsheetApp.getUi().alert(
    'تم تفعيل التشغيل التلقائي يوميًا في حدود الساعة 9:30 صباحًا.\n' +
    'ملحوظة: Google Apps Script بيشغّل الـ trigger في أقرب وقت لـ9:30 مش بالثانية بالظبط، ' +
    'فممكن يشتغل أي وقت بين 9:15 و9:45 تقريبًا حسب ضغط السيرفرات عند جوجل.'
  );
}

function removeDailyTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  triggers.forEach(function (t) {
    if (t.getHandlerFunction() === 'processAllPendingRows') {
      ScriptApp.deleteTrigger(t);
    }
  });
}

// ====== استخراج رقم الطلب من رابط الطلب في العمود A ======
function extractOrderIdFromText(text) {
  if (!text) return null;
  var str = String(text).trim();
  var match = /\/orders\/(\d+)/.exec(str);
  if (match) return match[1];
  var parts = str.split('/');
  var last = parts[parts.length - 1];
  if (/^\d+$/.test(last)) return last;
  return null;
}

function getOrderIdFromCell(cell) {
  var value = cell.getValue();
  var id = extractOrderIdFromText(value);
  if (id) return id;
  // احتياطي: لو الخلية HYPERLINK بنص عرض مختلف عن الرابط نفسه
  try {
    var richValue = cell.getRichTextValue();
    if (richValue) {
      id = extractOrderIdFromText(richValue.getLinkUrl());
      if (id) return id;
    }
  } catch (e) {
    // تجاهل، مفيش رابط غني في الخلية دي
  }
  return null;
}

// ====== النسخة اللي بتظهر تقرير للمستخدم (اربطها بزرار القائمة) ======
function runPendingRowsFromMenu() {
  var ui = SpreadsheetApp.getUi();
  var summary;

  try {
    summary = processAllPendingRows();
  } catch (e) {
    ui.alert('خطأ: ' + e.message);
    return;
  }

  if (summary.noApiKey) {
    ui.alert('محتاج تضبط مفتاح Gemini API الأول من قائمة "تدقيق مسمار".');
    return;
  }

  ui.alert(
    'انتهى الفحص:\n' +
    '✅ صفوف اتعملت: ' + summary.processed + '\n' +
    '⏭️ صفوف اتقفزت (فاضية أو رقم طلب مش واضح أو معمولة قبل كده): ' + summary.skipped + '\n' +
    '❌ صفوف فيها خطأ: ' + summary.errors + '\n' +
    (summary.processed === 0 && summary.skipped > 0
      ? '\n(كل الصفوف اتقفزت — لو ده غير متوقع، افتح الشيت اللي فيه بيانات فعلاً وشغّل من قائمته هو تحديدًا، مش من شيت تاني مفتوح).'
      : '')
  );
}

// ====== النسخة الأساسية (تستخدمها القائمة والتريجر التلقائي على السوا) ======
// آمنة للتشغيل من التريجر لأنها مبتستخدمش SpreadsheetApp.getUi()
function processAllPendingRows() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var lastRow = sheet.getLastRow();
  var apiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');

  var summary = { processed: 0, skipped: 0, errors: 0, noApiKey: false };

  if (!apiKey) {
    Logger.log('لا يوجد مفتاح Gemini API مضبوط.');
    summary.noApiKey = true;
    return summary;
  }

  // نتأكد من صلاحية تسجيل الدخول لـ Metabase قبل ما نبدأ أي صف، عشان
  // منضيعش وقت في صفوف كتير هتفشل كلها بنفس السبب لو بيانات الدخول غلط
  getMetabaseSessionToken();

  for (var row = HEADER_ROWS + 1; row <= lastRow; row++) {
    var justificationCell = sheet.getRange(row, COLUMNS.JUSTIFICATION);
    var existing = justificationCell.getValue();
    if (existing && String(existing).trim() !== '') {
      summary.skipped++;
      continue; // الصف ده اتعمل قبل كده
    }

    var orderIdCell = sheet.getRange(row, COLUMNS.ORDER_ID);
    var orderId = getOrderIdFromCell(orderIdCell);
    if (!orderId) {
      summary.skipped++;
      continue; // مفيش رابط طلب صالح في الصف ده
    }

    var ok = runSingleRow(sheet, row, orderId, apiKey);
    if (ok) {
      summary.processed++;
    } else {
      summary.errors++;
    }
  }

  Logger.log(JSON.stringify(summary));
  return summary;
}

// ====== إعادة تشغيل الصف الحالي بالقوة حتى لو معمول قبل كده ======
function forceRunCurrentRow() {
  var ui = SpreadsheetApp.getUi();
  var sheet = SpreadsheetApp.getActiveSheet();
  var row = sheet.getActiveCell().getRow();

  if (row <= HEADER_ROWS) {
    ui.alert('اختر صف فيه بيانات فعلية (مش صفوف العناوين) ثم حاول تاني.');
    return;
  }

  var apiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!apiKey) {
    ui.alert('محتاج تضبط مفتاح Gemini API الأول من قائمة "تدقيق مسمار".');
    return;
  }

  var orderIdCell = sheet.getRange(row, COLUMNS.ORDER_ID);
  var orderId = getOrderIdFromCell(orderIdCell);
  if (!orderId) {
    ui.alert('تعذر استخراج رقم طلب صالح من رابط هذا الصف. تأكد إنك واقف على صف فيه رابط طلب فعلي.');
    return;
  }

  var ok = runSingleRow(sheet, row, orderId, apiKey);
  ui.alert(ok ? 'تم تحديث الصف بنجاح.' : 'حصل خطأ أثناء التحليل — شوف عمود السبب الجذري لتفاصيل الخطأ.');
}

// ====== تشغيل التحليل لصف واحد وكتابة النتيجة في عمودي التبرير والتصنيف ======
// بترجع true لو نجحت، false لو حصل خطأ
function runSingleRow(sheet, row, orderId, apiKey) {
  var justificationCell = sheet.getRange(row, COLUMNS.JUSTIFICATION);
  justificationCell.setValue('جاري التحليل...');
  SpreadsheetApp.flush();

  try {
    var fullResponse = analyzeWorkDelay(apiKey, orderId);

    var classification = 'غير محدد';
    var mainPart = fullResponse;
    if (fullResponse.indexOf('===CLASSIFICATION===') !== -1) {
      var parts = fullResponse.split('===CLASSIFICATION===');
      mainPart = parts[0];
      classification = parts[1].trim();
    }

    var justification = mainPart;
    if (mainPart.indexOf('===SPLIT===') !== -1) {
      justification = mainPart.split('===SPLIT===')[0].trim();
    }

    justificationCell.setValue(justification.trim());
    sheet.getRange(row, COLUMNS.CLASSIFICATION).setValue(classification.trim());
    return true;

  } catch (e) {
    justificationCell.setValue('خطأ: ' + e.message);
    return false;
  }
}

// ====== استعلام سؤال داخلي واحد في Metabase بالسيشن توكن + رقم الطلب كمتغير ======
function fetchCardData(cardId, orderId, sessionToken) {
  var url = METABASE_BASE_URL + "/api/card/" + cardId + "/query";
  var parameters = [{
    type: "number/=",
    target: ["variable", ["template-tag", "order_id"]],
    value: String(orderId)
  }];

  var response = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    headers: { "X-Metabase-Session": sessionToken },
    payload: JSON.stringify({ parameters: parameters }),
    muteHttpExceptions: true
  });

  var code = response.getResponseCode();
  if (code !== 200) {
    return "Error HTTP " + code + ": " + response.getContentText().substring(0, 200);
  }

  try {
    var queryResult = JSON.parse(response.getContentText());
    return cardResultToObjects(queryResult);
  } catch (e) {
    return "Error: Response wasn't valid JSON: " + response.getContentText().substring(0, 200);
  }
}

// ====== جلب بيانات الطلب من Metabase (تذاكر + تعليقات + تسلسل الحالات) ======
function fetchOrderData(orderId) {
  var sessionToken = getMetabaseSessionToken();
  var payload = {};

  Object.keys(METABASE_CARD_IDS).forEach(function (key) {
    try {
      payload[key] = fetchCardData(METABASE_CARD_IDS[key], orderId, sessionToken);
    } catch (e) {
      payload[key] = "Error: " + e.message;
    }
  });

  return payload;
}

// ====== تحليل تاريخ Metabase (مع تصحيح فرق التوقيت +3 ساعات) ======
function parseDt(value) {
  if (!value) return null;
  var str = String(value).trim();
  var match = /^(\d{4})-(\d{2})-(\d{2}) (\d{1,2}):(\d{2}) (AM|PM)$/i.exec(str);
  if (!match) return null;

  var year = parseInt(match[1], 10);
  var month = parseInt(match[2], 10) - 1;
  var day = parseInt(match[3], 10);
  var hour = parseInt(match[4], 10);
  var minute = parseInt(match[5], 10);
  var ampm = match[6].toUpperCase();

  if (ampm === 'PM' && hour !== 12) hour += 12;
  if (ampm === 'AM' && hour === 12) hour = 0;

  var dt = new Date(year, month, day, hour, minute, 0);
  // ⚠️ Metabase بيرجّع التوقيت متأخر 3 ساعات عن التوقيت الفعلي بالسعودية
  dt.setHours(dt.getHours() + 3);
  return dt;
}

// ====== تنسيق المدة الزمنية بالعربي (أيام/ساعات/دقايق) ======
function formatDuration(ms) {
  var totalMinutes = Math.floor(ms / 60000);
  var days = Math.floor(totalMinutes / (24 * 60));
  var remMinutes = totalMinutes % (24 * 60);
  var hours = Math.floor(remMinutes / 60);
  var minutes = remMinutes % 60;
  var parts = [];
  if (days) parts.push(days + ' يوم');
  if (hours) parts.push(hours + ' ساعة');
  if (minutes || parts.length === 0) parts.push(minutes + ' دقيقة');
  return parts.join(' و');
}

// ====== حساب ساعات العمل الفعلية بين تاريخين (9ص-6م، بدون يوم الجمعة) ======
function businessHoursDurationMs(start, end) {
  if (start >= end) return 0;
  var totalMs = 0;
  var current = new Date(start.getTime());

  while (current < end) {
    var dayWorkStart = new Date(current.getFullYear(), current.getMonth(), current.getDate(), WORK_START_HOUR, 0, 0);
    var dayWorkEnd = new Date(current.getFullYear(), current.getMonth(), current.getDate(), WORK_END_HOUR, 0, 0);

    if (current.getDay() !== FRIDAY_DAY) {
      var segStart = current > dayWorkStart ? current : dayWorkStart;
      var segEnd = end < dayWorkEnd ? end : dayWorkEnd;
      if (segStart < segEnd) {
        totalMs += (segEnd.getTime() - segStart.getTime());
      }
    }

    current = new Date(current.getFullYear(), current.getMonth(), current.getDate() + 1, 0, 0, 0);
  }
  return totalMs;
}

// ====== حساب مدة *آخر* مرة دخل فيها الطلب حالة معينة (مش المجموع) ======
function computeLastStatusDuration(statusHistory, statusName) {
  if (!Array.isArray(statusHistory) || statusHistory.length === 0) return null;

  var matches = [];
  statusHistory.forEach(function (row) {
    if (!row || typeof row !== 'object') return;
    var rowStatus = row.Status_Name || row.status_name || row.status;
    if (rowStatus !== statusName) return;

    var openedRaw = row.Opened_At || row.opened_at || row.created_at;
    var closedRaw = row.Closed_At || row.closed_at || row.ended_at;
    var openedDt = parseDt(openedRaw);

    var stillActive = !!(closedRaw && String(closedRaw).trim().toLowerCase() === 'currently active');
    var closedDt = stillActive ? new Date() : parseDt(closedRaw);

    if (openedDt && closedDt) {
      matches.push({
        opened: openedDt,
        closed: closedDt,
        durationMs: closedDt.getTime() - openedDt.getTime(),
        businessMs: businessHoursDurationMs(openedDt, closedDt),
        stillActive: stillActive
      });
    }
  });

  if (matches.length === 0) return null;
  matches.sort(function (a, b) { return a.opened - b.opened; });
  return matches[matches.length - 1];
}

// ====== التحليل الرئيسي: بناء البرومبت واستدعاء Gemini ======
function analyzeWorkDelay(apiKey, orderId) {
  var orderData = fetchOrderData(orderId);
  var lastWorkStatus = computeLastStatusDuration(orderData.status_history, WORK_STATUS_NAME);

  // 🛑 قصير الدائرة: لو أقل من 4 ساعات عمل فعلية، نرجّع "لا يوجد تأخير" من غير استدعاء الموديل
  var NO_DELAY_THRESHOLD_HOURS = 4;
  if (lastWorkStatus && !lastWorkStatus.stillActive) {
    var businessHours = lastWorkStatus.businessMs / 3600000;
    if (businessHours < NO_DELAY_THRESHOLD_HOURS) {
      return (
        "لا يوجد تأخير، حيث استغرقت مرحلة " + WORK_STATUS_NAME + " " +
        formatDuration(lastWorkStatus.businessMs) + " ساعات عمل فعلية فقط، وهي مدة طبيعية." +
        "\n===SPLIT===\n" +
        "مدة حالة " + WORK_STATUS_NAME + " بساعات العمل الفعلية: " + formatDuration(lastWorkStatus.businessMs) +
        " (من " + lastWorkStatus.opened + " إلى " + lastWorkStatus.closed + ")، " +
        "وهي أقل من الحد الأدنى المعتبر تأخيرًا (4 ساعات عمل)." +
        "\n===CLASSIFICATION===\nلا يوجد تأخير"
      );
    }
  }

  if (!lastWorkStatus) {
    return (
      "تعذر العثور على مرحلة جاري العمل واضحة في سجل حالات هذا الطلب." +
      "\n===SPLIT===\nلا توجد بيانات كافية للتحليل." +
      "\n===CLASSIFICATION===\nلا يوجد تأخير"
    );
  }

  var lastStatusNote =
    "⏱️ مدة *آخر* مرة دخل فيها الطلب حالة '" + WORK_STATUS_NAME + "':\n" +
    "- المدة الخام (شاملة كل الأوقات): " + formatDuration(lastWorkStatus.durationMs) + "\n" +
    "- مدة ساعات العمل الفعلية فقط (بعد استبعاد ما هو خارج 9ص-6م ويوم الجمعة): " +
    formatDuration(lastWorkStatus.businessMs) +
    (lastWorkStatus.stillActive ? " (لسه شغالة/جارية حتى الآن)" : "") +
    "\nمن " + lastWorkStatus.opened + " إلى " + lastWorkStatus.closed + ".\n" +
    "⚠️ تنبيه: هذا الرقم وحده لا يعني تلقائيًا وجود تأخير. افحص التذاكر والتعليقات أولاً " +
    "لمعرفة هل هذا وقت عمل فعلي وتحديثات مستمرة، أم فجوة صمت وتعطل حقيقي.";

  var promptText =
    "أنت كبير مدققي العمليات في شركة صيانة السيارات (مسمار - MisMar).\n" +
    "مطلوب تحديد السبب الجذري وراء التأخير (إن وجد) في آخر مرة دخل فيها الطلب رقم #" + orderId + " مرحلة \"" + WORK_STATUS_NAME + "\".\n\n" +
    "⚠️ === الحقيقة الزمنية الحاسمة (إلزامية الاستخدام، ولا تحسب من عندك) === ⚠️\n" +
    lastStatusNote + "\n\n" +
    "البيانات المتاحة للطلب (التركيز الأساسي هنا على التذاكر والتعليقات):\n" +
    "1. 🎫 تذاكر الشكاوى والمتابعة — المصدر الأهم هنا لمعرفة سبب تعطل التنفيذ:\n" +
    JSON.stringify(orderData.tickets, null, 2) + "\n\n" +
    "2. 💬 محادثات الشات والتعليقات الداخلية — قارن مدة العمل الفعلية بمحتوى المتابعات:\n" +
    JSON.stringify(orderData.comments, null, 2) + "\n\n" +
    "🔍 === سيناريوهات محتملة يجب فحصها (إلزامي) === 🔍\n" +
    "- هل التأخير بسبب انتظار توفر قطعة غيار (سواء من المورد، طلبية جديدة، أو نقص في المخزون)؟ → صنّف كـ\"قطع الغيار\" أو \"قطع الغيار وطلبية\" حسب السياق.\n" +
    "- هل هناك تأخير شحن قطعة تم طلبها بالفعل؟ → صنّف كـ\"تأخير في الشحن\".\n" +
    "- هل ظهر خلل تقني جديد أثناء التنفيذ يستلزم وقتًا إضافيًا لم يكن متوقعًا؟ → صنّف كـ\"خلل تقني\".\n" +
    "- هل اكتشف المركز أثناء العمل حاجة لتشخيص إضافي لم يظهر في الفحص الأول؟ → صنّف كـ\"تشخيص إضافي\".\n" +
    "- هل السبب الأساسي أن الفحص الأول كان خاطئًا وأدى لإعادة عمل أو تصحيح مسار؟ → صنّف كـ\"تشخيص مركز خاطئ\".\n" +
    "- هل تم نقل السيارة لمركز آخر أثناء التنفيذ؟ → صنّف كـ\"نقل مركز آخر\".\n" +
    "- هل هذا طلب عمل مكرر فعليًا لنفس المهمة؟ → صنّف كـ\"مكرر\".\n" +
    "- هل السيارة تم تسليمها فعليًا للعميل والحالة لم تُحدّث بعد في النظام؟ → صنّف كـ\"تم التسليم\".\n" +
    "- هل التأخير ناتج عن العميل نفسه؟ → صنّف كـ\"عميل\".\n" +
    "- 🕐 مهم: أي فجوة زمنية تقع كليًا أو جزئيًا خارج ساعات العمل الرسمية (9ص-6م) أو يوم الجمعة لا تُحتسب كتأخير من أي طرف. لو الجزء الأكبر من المدة وقع في هذا النطاق → صنّف كـ\"يوم الجمعة\" إن كان هو السبب الرئيسي.\n" +
    "- ⚠️ تذكّر: مدة طويلة مع تحديثات منتظمة من المركز في التعليقات تعني عملاً حقيقيًا مستمرًا وليست تأخيرًا، بغض النظر عن الرقم الإجمالي.\n\n" +
    "3. ⏱️ التسلسل الزمني الكامل للحالات (مرجعي فقط لفهم السياق العام للطلب):\n" +
    JSON.stringify(orderData.status_history, null, 2) + "\n\n" +
    "=== 🎯 تعليمات وقواعد الصياغة الصارمة ===\n" +
    "قسّم إجابتك إلى ثلاثة أقسام يفصل بينها السطر `===SPLIT===` قبل القسم الثاني، والسطر `===CLASSIFICATION===` قبل القسم الثالث:\n\n" +
    "القسم الأول: [السبب الجذري المباشر]\n" +
    "- فقرة واحدة متصلة ومباشرة فقط (من 4 إلى 5 سطور كحد أقصى).\n" +
    "- ابدأ الفقرة فورًا بذكر السبب الجذري نفسه، بدون أي مقدمات إطلاقًا.\n" +
    "- ممنوع تمامًا افتتاح الفقرة بعبارات مثل \"يعود السبب الجذري إلى\".\n" +
    "- ممنوع ذكر رقم الطلب نهائيًا في هذه الفقرة.\n" +
    "- يُمنع استخدام جمل فضفاضة دون تحديد السبب الحقيقي من نص التذاكر أو الشات.\n" +
    "- يُمنع استخدام القوائم أو العناوين أو أرقام التذاكر الداخلية.\n\n" +
    "===SPLIT===\n\n" +
    "القسم الثاني: [الأدلة والوقائع التفصيلية]\n" +
    "- التوقيت الدقيق لبداية (واستمرار أو انتهاء) مرحلة جاري العمل.\n" +
    "- اقتباس نصوص التذاكر والتعليقات ذات الصلة كلمة بكلمة.\n" +
    "- سرد أي فجوات صمت فعلية أو غياب تحديثات تدعم استنتاجك.\n\n" +
    "===CLASSIFICATION===\n" +
    "- اكتب هنا تصنيفاً واحداً فقط (بالضبط كما هو، بدون أي شرح إضافي) من هذه القائمة الحصرية:\n" +
    "  " + WORK_CLASSIFICATIONS.join(" | ") + "\n" +
    "- اختر التصنيف الأقرب لواقع الأدلة فقط، ولا تخترع تصنيفاً من عندك خارج هذه القائمة.";

  return callGeminiWithFallback(apiKey, promptText);
}

// ====== استدعاء Gemini مع تجربة موديلات بديلة تلقائيًا لو الأول مزدحم أو مش متاح ======
function callGeminiWithFallback(apiKey, promptText) {
  var attemptErrors = [];

  for (var i = 0; i < MODEL_FALLBACK_LIST.length; i++) {
    var modelName = MODEL_FALLBACK_LIST[i];
    var url = "https://generativelanguage.googleapis.com/v1beta/models/" + modelName + ":generateContent";
    var options = {
      method: "post",
      contentType: "application/json",
      headers: { "x-goog-api-key": apiKey },
      payload: JSON.stringify({
        contents: [{ parts: [{ text: promptText }] }],
        generationConfig: { temperature: 0.3, topP: 0.9 }
      }),
      muteHttpExceptions: true
    };

    var response = UrlFetchApp.fetch(url, options);
    var code = response.getResponseCode();
    var resultText = response.getContentText();

    if (code === 200) {
      var resultJson = JSON.parse(resultText);
      try {
        return resultJson.candidates[0].content.parts[0].text;
      } catch (e) {
        attemptErrors.push(modelName + ": رد بشكل غير متوقع (200 لكن بدون نص) — " + resultText.substring(0, 200));
        continue;
      }
    } else if (code === 401) {
      // خطأ مصادقة مش هيتحل بتغيير الموديل، نوقف فورًا من غير ما نكمل نجرب باقي القائمة
      throw new Error("خطأ مصادقة (401): المفتاح غير صالح أو منتهي الصلاحية.");
    } else {
      // أي كود تاني (404/503/400/غيره) نسجله ونكمل نجرب الموديل اللي بعده
      attemptErrors.push(modelName + ": خطأ " + code + " — " + resultText.substring(0, 200));
      continue;
    }
  }

  // لو خلصت القائمة كلها من غير نجاح، اعرض تفاصيل كل محاولة عشان يبقى التشخيص واضح
  throw new Error("فشلت كل الموديلات المتاحة:\n" + attemptErrors.join("\n"));
}
