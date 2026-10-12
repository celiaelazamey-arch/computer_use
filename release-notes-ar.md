## CELA.sh 1.0.1

إصدار أمني وتوثيقي. لا يغيّر سلوك التخطيط نفسه: الخطة تبقى `dry_run=true` و`execution_enabled=false`، ولا يوجد `/execute`.

> **تنبيه الروابط:** كل رابط في هذه الملاحظات يشير إلى `main`. لا تعمل الروابط إلا بعد دمج Pull Request الخاص بهذا الإصدار في `main` ووسم الكوميت المدموج. قبل الدمج ستعيد `404`.

### المرجع الوحيد لعقد الأخطاء

أُضيفت **[docs/api-errors.md](https://github.com/celiaelazamey-arch/computer_use/blob/main/docs/api-errors.md)** كمرجع وحيد لعقد أخطاء API المحلية. الجدولان أدناه **إعادة عرض** لتلك الصفحة، لا نسخة مستقلة منها؛ إن اختلفا فالصفحة هي الصحيحة.

كل صف في الجدولين مُتحقَّق منه بتشغيل التطبيق فعليًا، لا بقراءة الكود فقط.

#### شكلان للاستجابة، لا شكل واحد

هذا أهم ما في الإصدار. API تعيد **غلافين مختلفين** حسب مصدر الخطأ:

| الغلاف                | الشكل                                          | المصدر                                                                   |
| --------------------- | ---------------------------------------------- | ------------------------------------------------------------------------ |
| **أ** — أخطاء التطبيق | `{"error": {"code": "...", "message": "..."}}` | أعطال النطاق المعروفة والاستثناءات غير المتوقعة؛ كل `message` جملة ثابتة |
| **ب** — أخطاء الإطار  | `{"detail": ...}`                              | تحقق FastAPI/Pydantic من جسم الطلب، و404، و405                           |

النتيجة العملية: **افحص الغلاف قبل أن تفترض `code`.** الحالة `422` وحدها لا تكفي — لها ثلاثة مصادر مختلفة عبر الغلافين.

#### الجدول أ — أخطاء التطبيق

| الحالة | `code`                     | `message` الثابتة                               | المسار                      |
| -----: | -------------------------- | ----------------------------------------------- | --------------------------- |
|    409 | `memory_disabled`          | `Memory is disabled in this deployment.`        | `/memory`, `/memory/search` |
|    422 | `planner_validation_error` | `Planner input validation failed.`              | `/plan`                     |
|    422 | `memory_validation_error`  | `Invalid memory input.`                         | `/memory`                   |
|    422 | `memory_validation_error`  | `Invalid memory search input.`                  | `/memory/search`            |
|    500 | `planner_error`            | `An internal planner error occurred.`           | `/plan`                     |
|    500 | `internal_error`           | `Internal server error.`                        | أي مسار HTTP                |
|    502 | `planner_response_error`   | `Planner service returned an invalid response.` | `/plan`                     |
|    503 | `planner_connection_error` | `Unable to connect to planner service.`         | `/plan`                     |
|    503 | `planner_memory_error`     | `Planner memory backend is unavailable.`        | `/plan`                     |
|    503 | `memory_store_error`       | `Memory store is unavailable.`                  | `/memory`, `/memory/search` |

`memory_validation_error` له رسالتان ثابتتان مختلفتان، واحدة لكل مسار. الكود واحد والرسالة ليست واحدة.

#### الجدول ب — أخطاء الإطار

| الحالة | الشكل                                                   | المحفّز                                                             |
| -----: | ------------------------------------------------------- | ------------------------------------------------------------------- |
|    422 | `{"detail": [{"type", "loc", "msg", "input", "ctx"?}]}` | حقل ناقص أو خارج الحدود، JSON غير صالح، حقل إضافي، نص من مسافات فقط |
|    404 | `{"detail": "Not Found"}`                               | مسار غير معروف                                                      |
|    405 | `{"detail": "Method Not Allowed"}`                      | طريقة خاطئة على مسار موجود                                          |

رسائل `detail` يولّدها Pydantic وليست جملًا ثابتة من عندنا؛ لا تبنِ عليها منطقًا، اعتمد `type` و`loc` فقط.

### ما الذي تغيّر في السلوك

**الاستثناءات غير المتوقعة لم تعد تتسرب.** قبل هذا الإصدار كانت الاستثناءات غير المعالَجة تصل إلى Starlette: إما جسم نصي عادي، أو معالج مسجَّل يرسل الرد ثم يعيد رمي الاستثناء — ما يسمح لعميل اختبار أو خادم تصحيح بإظهار التفصيل الأصلي. الآن تعيد الوسيطة `UnexpectedErrorMiddleware` داخل مسار الطلب نفسه:

```json
{ "error": { "code": "internal_error", "message": "Internal server error." } }
```

الضمانات، وكلّها مُختبَرة:

- لا نص الاستثناء الأصلي، ولا اسم صنفه، ولا `Traceback`، ولا مسارات ملفات داخلية ولا أسماء نماذج ولا أسطر اتصال.
- الـtraceback يُكتب في سجلّ الخادم (`cela_sh.server` بمستوى `ERROR`) مرة واحدة بالضبط لكل عطل. التعقيم ينقل التفصيل إلى المكان الصحيح، لا يخفيه.
- إذا بدأ الرد فعلًا (`http.response.start` أُرسلت) فلا يُستبدل، لأن استبدال الجسم بعد بدء الإرسال يُفسد التدفق.
- النطاقات غير HTTP (مثل `lifespan`) تمرّ دون لمس.
- فشل طلب واحد لا يُعلّق التطبيق: `/health` يبقى `200` بعد `500`.

### بقية بنود الإصدار

- **حجب موحّد لأوامر النظام:** `sudo` وأدوات تصعيد الصلاحيات (`doas`، `pkexec`، `su -`) وأوامر الصدفة (`rm -rf`، `bash -c`، `powershell -`، `cmd /c`، `chmod`، `chown`) وأهداف الصدفة (`terminal`، `PowerShell`، `الطرفية`، `سطر الأوامر`) محجوبة بشكل موحّد عبر كل أنواع الإجراءات وفي نص الهدف، مع `status: blocked` وإرشاد صريح بتنفيذ الأمر يدويًا في طرفية المستخدم.
- **عقد مخطط قابل للتوسعة:** استُخرجت الواجهة المجرّدة `Planner` (`cela_sh.planner.base`) وسجلّ المزودين المحليين (`register_planner_provider`، `build_planner`، `CELA_PLANNER_PROVIDER`) مع فرض `PlannerContract` مشترك، و`OllamaPlanner` يبقى المزود الافتراضي المحلي حصرًا.
- **الأفعال ذات الأثر تتطلب تأكيدًا:** send وsubmit وpost وpublish وupload وemail وreply وshare وmessage وorder وcheckout وsubscribe ومكافئاتها العربية لم تعد تعيد `ready` بخطورة `low`. التصنيف ينطبق على الهدف وعلى كل خطوة.
- **`ccelia2027`: رمز الخطة يُنفَّذ مرة واحدة.** إعادة التشغيل تعيد `409 plan_already_used` قبل انطلاق أي متصفح. الرموز الصادرة قبل هذا التغيير لا تحمل `jti` وتُرفض؛ خلال نافذة قصيرة أثناء النشر يحصل أصحاب الرموز القديمة غير المنتهية على `400` وعليهم إنشاء خطة جديدة.

### جرد الإصدار: ستة مواقع لرقم الإصدار، لا أربعة

رفع الرقم إلى `1.0.1` كشف أن `CHANGELOG.md` كان يوثّق أربعة مواقع فقط بينما الرقم موجود في **ستة**. الموقعان الناقصان ليسا تفصيلًا:

- `src/cela_sh/server.py` — `version=` المُمرَّر إلى FastAPI، وهو **منشور للمتصلين** عبر `/openapi.json` و`/docs`. رفع المواقع الأربعة وحدها كان سيشحن عقد OpenAPI يعلن إصدارًا قديمًا.
- `SECURITY.md` — جدول الإصدارات المدعومة.

أُضيف `tests/test_version_consistency.py` ليفشل البناء عند أي انجراف بين المواقع الستة وأحدث عنوان في `CHANGELOG.md`. وتحقّقنا من الحارس أثناء التطوير بتحوير كل موقع من السبعة (بما فيها `pyproject.toml` نفسه) إلى قيمة خاطئة، فتأكّد فشل الاختبار في الحالات السبع جميعها قبل إعادتها. التحوير إجراء تطويري يدوي، لا اختبار مُودَع في المستودع.

### حدود المدخلات

| الحقل            | الحدّ                                   | المسارات         |
| ---------------- | --------------------------------------- | ---------------- |
| `goal`           | 1–2,000 حرف، ولا يُقبل نص من مسافات فقط | `/plan`          |
| `screen_context` | ≤ 8,000 حرف (اختياري، افتراضيًا `""`)   | `/plan`          |
| `content`        | 1–8,000 حرف                             | `/memory`        |
| `query`          | 1–8,000 حرف                             | `/memory/search` |
| `session_id`     | 1–256 حرف، ولا يُقبل نص من مسافات فقط   | الثلاثة          |
| `limit`          | عدد صحيح 1–100 (افتراضيًا 3)            | `/memory/search` |
| `metadata`       | كائن، افتراضيًا `{}`                    | `/memory`        |

النماذج صارمة: أي حقل إضافي يعيد `422` بـ`extra_forbidden`.

### ملحق التحقق

لا حاجة إلى Ollama ولا إلى شبكة للتحقق من الغلافين والحدود:

```bash
bash ./cela.sh test                 # المجموعة كلها offline: بلا شبكة ولا Docker ولا Ollama
bash ./cela.sh api-surface --check  # لقطة العقد العام لم تتغير
```

على خادم حيّ (ذاكرة معطّلة):

```bash
bash ./cela.sh services-up-api

# الغلاف ب — 422 بـ detail
curl -s -X POST http://127.0.0.1:8000/plan -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:8000/plan -H 'Content-Type: application/json' -d '{"goal":"اقرأ","session_id":" "}'
curl -s -X POST http://127.0.0.1:8000/plan -H 'Content-Type: application/json' -d '{not json'
curl -s http://127.0.0.1:8000/nope     # 404 detail
curl -s http://127.0.0.1:8000/plan     # 405 detail

# الغلاف أ — 409 memory_disabled
curl -s -X POST http://127.0.0.1:8000/memory -H 'Content-Type: application/json' -d '{"session_id":"s","content":"نص"}'

# /health لا يستدعي أي نموذج
curl -s http://127.0.0.1:8000/health
```

بقية صفوف الجدول أ تحتاج شرط العطل نفسه (إيقاف Ollama، إفساد مخزن الذاكرة، أو حقن استثناء) وهي مغطّاة آليًا في `tests/test_server.py`: `UnexpectedErrorMiddlewareTests`، و`test_unexpected_errors_return_a_stable_json_500_without_details`، و`test_a_sanitized_500_still_answers_a_client_that_surfaces_exceptions`.

### خيارات الوسم

الوسم يُنشأ على **كوميت الدمج في `main`**، لا على كوميت العمل داخل فرع الـPR؛ وسم فرع غير مدموج يشير إلى شجرة لا تصل إلى أحد.

**الخيار ١ — وسم خفيف على كوميت الدمج (الموصى به هنا).** `main` هو خط التطوير الوحيد ولا توجد فروع صيانة، فالوسم معلَم لا فرع:

```bash
git checkout main && git pull --ff-only
git tag -a v1.0.1 -m "CELA.sh 1.0.1"
git push origin v1.0.1
```

**الخيار ٢ — وسم على SHA محدَّد صراحةً** إن أردت تثبيت المعلَم حتى لو تحرّك `main` قبل الدفع:

```bash
MERGE_SHA=$(gh pr view <PR> --json mergeCommit --jq .mergeCommit.oid)
git tag -a v1.0.1 "$MERGE_SHA" -m "CELA.sh 1.0.1"
git push origin v1.0.1
```

**الخيار ٣ — بلا وسم.** سياسة المستودع المعلنة «إصدار واحد حالي، لا سلسلة إصدارات تاريخية». الإبقاء على `CHANGELOG.md` وحده متسق معها حرفيًا، والوسم اختيار تحريري لا ضرورة تقنية. إن اخترت هذا فالأرقام الستة تبقى مرفوعة والحارس يبقى فعّالًا.

### النشر

```bash
# الملاحظات العربية كنص الإصدار
gh release create v1.0.1 --title "CELA.sh 1.0.1" --notes-file release-notes-ar.md

# الإنجليزية كتعليق مثبّت على الإصدار نفسه — لا إصدار ثانٍ ولا جدول ثانٍ
gh release comment v1.0.1 --body-file release-notes-en.md
```

`gh release comment` لا يثبّت التعليق آليًا؛ التثبيت يتم من واجهة GitHub على الإصدار. البديل إن أردت الإنجليزية داخل نص الإصدار نفسه:

```bash
cat release-notes-ar.md <(printf '\n\n---\n\n') release-notes-en.md > /tmp/notes-1.0.1.md
gh release create v1.0.1 --title "CELA.sh 1.0.1" --notes-file /tmp/notes-1.0.1.md
```

يبقى الجدول المرجعي واحدًا في الحالتين: كلا الملفين يعيد عرض `docs/api-errors.md`.

### ملاحظة أمان

API **بلا مصادقة**، و`session_id` مُعرِّف اختيار لا تفويض. Compose ينشر المنفذ على loopback للمضيف فقط. لا تعرّض الخدمة لشبكة غير موثوقة قبل إضافة مصادقة وتخويل. راجع [`SECURITY.md`](https://github.com/celiaelazamey-arch/computer_use/blob/main/SECURITY.md) و[`docs/threat-model-cela.md`](https://github.com/celiaelazamey-arch/computer_use/blob/main/docs/threat-model-cela.md) (البند T11).

**Full Changelog**: https://github.com/celiaelazamey-arch/computer_use/commits/v1.0.1
