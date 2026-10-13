# عقد أخطاء API في cela.sh

**الإصدار:** 1.0.2

**الحالة:** مرجع وحيد. هذا الملف هو مصدر الحقيقة لعقد أخطاء API المحلية/السحابية؛ بقية الوثائق تشير إليه ولا تعيد سرد جداوله.

**النطاق:** خدمة `cela-sh-api` في `src/cela_sh/server.py` (المسارات `/health` و`/readyz` و`/plan` و`/memory` و`/memory/search`)، بما فيها المصادقة الاختيارية برمز Bearer. لا يشمل Worker في `ccelia2027/`، فهو تطبيق مستقل بسطح أخطاء خاص به.

**طريقة التوثيق:** كل صف في الجدولين أدناه مُتحقَّق منه بتشغيل التطبيق فعليًا، لا بقراءة الكود فقط. أوامر إعادة التحقق في [ملحق التحقق](#ملحق-التحقق).

---

## شكلان للاستجابة، لا شكل واحد

هذا هو البند الأهم في العقد، وهو الذي يُساء نقله عادةً. API تعيد **غلافين مختلفين** حسب مصدر الخطأ:

| الغلاف                | الشكل                                          | المصدر                                                                                                                |
| --------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| **أ** — أخطاء التطبيق | `{"error": {"code": "...", "message": "..."}}` | المصادقة وأعطال النطاق المعروفة (المخطط، الذاكرة) والاستثناءات غير المتوقعة. كل `message` جملة ثابتة مكتوبة في الكود. |
| **ب** — أخطاء الإطار  | `{"detail": ...}`                              | تحقق FastAPI/Pydantic من جسم الطلب، والمسارات غير الموجودة، والطريقة غير المسموحة. لا `code` ولا جملة ثابتة هنا.      |

النتيجة العملية للمستهلك: **افحص الغلاف قبل أن تفترض `code`.** خطأ إدخال في جسم الطلب يعيد `422` بغلاف `detail`، بينما فشل تحقق داخل المخطط يعيد `422` بغلاف `error`. الحالة وحدها لا تكفي للتمييز.

كلا غلافي الخطأ `application/json`؛ لا تُعاد أخطاء API كنص عادي أو HTML. صفحة Swagger (`/docs`) نفسها تبقى HTML عامة.

---

## الجدول أ — أخطاء التطبيق (غلاف `error`)

| الحالة | `code`                     | `message` الثابتة                                                                     | المسار                               | المحفّز                                                                                 |
| -----: | -------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------- |
|    401 | `authentication_required`  | `A valid bearer token is required.`                                                   | `/plan`, `/memory`, `/memory/search` | `CELA_API_KEY` مفعّل والرمز مفقود أو غير صحيح                                           |
|    409 | `memory_disabled`          | `Memory is disabled in this deployment.`                                              | `/memory`, `/memory/search`          | الذاكرة غير مهيّئة في هذا النشر                                                         |
|    422 | `planner_validation_error` | `Planner input validation failed.`                                                    | `/plan`                              | `PlannerValidationError`                                                                |
|    422 | `memory_validation_error`  | `Invalid memory input.`                                                               | `/memory`                            | `ValueError` من مخزن الذاكرة                                                            |
|    422 | `memory_validation_error`  | `Invalid memory search input.`                                                        | `/memory/search`                     | `ValueError` من مخزن الذاكرة                                                            |
|    500 | `planner_error`            | `An internal planner error occurred.`                                                 | `/plan`                              | أي `PlannerError` غير مصنَّف أعلاه                                                      |
|    500 | `internal_error`           | `Internal server error.`                                                              | أي مسار HTTP                         | استثناء غير متوقع (الوسيطة `UnexpectedErrorMiddleware`)                                 |
|    502 | `planner_response_error`   | `Planner service returned an invalid response.`                                       | `/plan`                              | `PlannerResponseError` — رد Ollama غير صالح                                             |
|    503 | `planner_connection_error` | `Unable to connect to planner service.`                                               | `/plan`                              | `PlannerConnectionError` — تعذّر الوصول إلى Ollama                                      |
|    503 | `planner_memory_error`     | `Planner memory backend is unavailable.`                                              | `/plan`                              | `PlannerMemoryError`                                                                    |
|    503 | `planner_circuit_open`     | `The planner is temporarily unavailable after repeated failures. Retry shortly.`      | `/plan`                              | دائرة المخطط مفتوحة بعد 3 فشل متتالٍ ولم تمض مهلة 10 ثوانٍ؛ لا استدعاء HTTP على الإطلاق |
|    503 | `memory_store_error`       | `Memory store is unavailable.`                                                        | `/memory`, `/memory/search`          | `MemoryStoreError`                                                                      |
|    503 | `memory_circuit_open`      | `The memory store is temporarily unavailable after repeated failures. Retry shortly.` | `/memory`, `/memory/search`          | دائرة الذاكرة مفتوحة بعد 3 فشل متتالٍ ولم تمض مهلة 10 ثوانٍ                             |

### الدوائر و`/readyz`

الدوائر (`CircuitBreaker` في `src/cela_sh/resilience.py`) لكل اعتماد على حدة: `planner` و`memory` دائرتان مستقلتان، تفتحان بعد 3 فشل متتالٍ لمدة 10 ثوانٍ، ثم تسمحان بطلب استطلاع واحد يغلقها بالنجاح أو يعيد فتحها بالفشل — تعافٍ تلقائي. أخطاء تحقق الإدخال (`422`) لا تُحسب فشل اعتماد.

`GET /readyz` عام بلا رمز، يجيب `200` دائمًا بغلاف نجاح (ليس خطأ):

```json
{
  "status": "ok",
  "live": true,
  "dependencies": {
    "planner": {
      "state": "healthy|unavailable|unknown",
      "consecutive_failures": 0,
      "circuit": "closed|open|half_open"
    },
    "memory": {
      "state": "healthy|unavailable|unknown|disabled",
      "consecutive_failures": 0,
      "circuit": "closed|open|half_open"
    }
  },
  "dry_run": true,
  "execution_enabled": false
}
```

النظرة **سلبية**: الحالات تأتي من نتائج الطلبات الفعلية المسجّلة في مساراتها، لا من استكشاف؛ `unknown` تعني «لا دليل بعد» وليست صحة افتراضية، و`memory` تظهر `disabled` عند غياب المخزن. لا I/O على المسار نفسه، فهو لا يبطئ ولا يضيف حملًا، ولا يقدّم توفر وظيفة كحالة HTTP.

### تفصيلان يسهل أخطاؤهما

- **`memory_validation_error` له رسالتان ثابتتان مختلفتان**، واحدة لكل مسار (`Invalid memory input.` للكتابة و`Invalid memory search input.` للبحث). الكود واحد والرسالة ليست واحدة؛ لا تربط الرسالة بالكود وحده.
- **`422` لها ثلاثة مصادر مختلفة**: اثنان في الجدول أ (`planner_validation_error` و`memory_validation_error`) وواحد في الجدول ب (تحقق جسم الطلب). الغلاف هو الفارق الوحيد.

### الاستثناء غير المتوقع: 500 واحدة فقط

أي استثناء لا يملك معالجًا مخصّصًا يصل إلى `UnexpectedErrorMiddleware`، التي تعيد `500` بالغلاف أ مع `code: internal_error` ورسالة ثابتة، وتكتب الـtraceback في سجلّ الخادم (`cela_sh.server` بمستوى `ERROR`) بدل إعادته إلى المتصل.

الوسيطة تجيب داخل مسار الطلب نفسه، لا عبر معالج أخطاء Starlette؛ فالأخير إما يعيد جسمًا نصيًا عاديًا، أو — عند تسجيل معالج — يرسل الرد ثم يعيد رمي الاستثناء، ما يسمح لعميل اختبار أو خادم تصحيح بإظهار التفصيل الأصلي.

حدّان موثّقان لسلوك الوسيطة:

- **إذا بدأ الرد فعلًا** (أُرسلت `http.response.start`) فلا يُستبدل: الاستثناء يُعاد رميه لأن استبدال الجسم بعد بدء الإرسال يُفسد التدفق.
- **نطاقات غير HTTP** (مثل `lifespan`) تمرّ دون لمس، فلا تُجاب بغلاف أخطاء.

فشل طلب واحد لا يُعلّق التطبيق: `/health` يبقى `200` بعد `500`.

---

## الجدول ب — أخطاء الإطار (غلاف `detail`)

| الحالة | الشكل                                                   | المحفّز                                                                                  |
| -----: | ------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
|    422 | `{"detail": [{"type", "loc", "msg", "input", "ctx"?}]}` | حقل ناقص أو خارج الحدود، JSON غير صالح، حقل إضافي في نموذج صارم، نص مكوَّن من مسافات فقط |
|    404 | `{"detail": "Not Found"}`                               | مسار غير معروف                                                                           |
|    405 | `{"detail": "Method Not Allowed"}`                      | طريقة HTTP خاطئة على مسار موجود                                                          |

`type` في غلاف `detail` هو تصنيف Pydantic، وأشهره هنا: `missing`، `string_too_short`، `string_too_long`، `greater_than_equal`، `less_than_equal`، `extra_forbidden`، `json_invalid`، و`value_error` (الناتج عن مدقّقات النموذج، كنص مكوَّن من مسافات فقط).

هذه الرسائل **ليست** جملًا ثابتة من عندنا؛ صياغتها يولّدها Pydantic وتتغير بتغيّر المكتبة. لا تبني عليها منطقًا — اعتمد `type` و`loc` فقط.

---

## ما لا يُعاد أبدًا

ضمانات العقد، وهي مُختبَرة في `tests/test_server.py`:

- لا نص الاستثناء الأصلي.
- لا اسم صنف الاستثناء (`RuntimeError`، `PlannerError`، …).
- لا كلمة `Traceback` ولا أي مكدس استدعاء.
- لا مسارات ملفات داخلية (مثل `/app/data/cela_memory.db`) ولا أسماء نماذج (مثل `qwen2.5:3b`) ولا أسطر اتصال.

التفاصيل الكاملة تُكتب في سجلّ الخادم مرة واحدة بالضبط لكل عطل. الاستجابة الخاضعة للتعقيم لا تُخفي العطل؛ إنها تنقله إلى المكان الصحيح.

---

## حدود المدخلات

كل تجاوز لهذه الحدود يعيد `422` بغلاف `detail` (الجدول ب)، لا بغلاف `error`:

| الحقل            | الحدّ / السلوك                                                                        | المسارات         |
| ---------------- | ------------------------------------------------------------------------------------- | ---------------- |
| `goal`           | 1–2,000 حرف، ولا يُقبل نص من مسافات فقط                                               | `/plan`          |
| `screen_context` | ≤ 8,000 حرف (اختياري، افتراضيًا `""`)                                                 | `/plan`          |
| `content`        | 1–8,000 حرف                                                                           | `/memory`        |
| `query`          | اختياري؛ إن أُرسل فهو 1–8,000 حرف وغير فارغ، وعند غيابه يَعرض البحث حسب النوع/الوسوم  | `/memory/search` |
| `session_id`     | 1–256 حرف للمشروع؛ يُحذف عند global؛ مطلوب لـ`both`؛ `/plan` يتطلبه عند تفعيل الذاكرة | `/plan` والذاكرة |
| `scope`          | `project` أو `global`؛ ويقبل البحث أيضًا `both`                                       | الذاكرة          |
| `type`           | `note` أو `correction`; اختياري كفلتر بحث                                             | الذاكرة          |
| `tags`           | قائمة حتى 20 وسمًا؛ كل وسم 1–64 حرفًا/رقمًا أو `.` أو `-` أو `_`                      | الذاكرة          |
| `limit`          | عدد صحيح 1–100 (افتراضيًا 3)                                                          | `/memory/search` |
| `metadata`       | كائن، افتراضيًا `{}`                                                                  | `/memory`        |

النماذج صارمة: أي حقل إضافي غير معرَّف يعيد `422` بـ`extra_forbidden`. نتائج `/memory/search` تتضمن `scope`؛ يكون `session_id` فيها `null` لسجلات global. لا يوجد `/execute`، والخطة تحمل دائمًا `dry_run=true` و`execution_enabled=false`.

---

## ملاحظة أمان

المصادقة **اختيارية محليًا** وتُفعّل بضبط `CELA_API_KEY`؛ عند ضبطه تتطلب المسارات `/plan` و`/memory` و`/memory/search` رأس `Authorization: Bearer ...`. `/health` وواجهات التوثيق العامة لا تتطلب الرمز. ملف `docker-compose.cloud.yml` يرفض البدء دون قيمة للرمز، لكن يجب أيضًا أن يكون عشوائيًا بطول 32 محرفًا ASCII على الأقل. استخدم TLS عند ingress موثوق.

الرمز المشترك يمنح جميع حامليه الصلاحيات نفسها، ولا يجعل `session_id` تفويضًا أو عزلًا بين المستخدمين. لذلك يقتصر هذا الإعداد على مشغّل موثوق واحد؛ لا توزّع المفتاح في JavaScript أو على مستخدمين غير موثوقين. Compose يربط المنفذ على loopback للمضيف فقط. راجع [دليل النشر السحابي](cloud-deployment.md) و`SECURITY.md` و`docs/threat-model-cela.md`.

---

## ملحق التحقق

الغلافان والحدود أعلاه قابلة لإعادة التحقق دون Ollama ودون شبكة. الأوامر تفترض API على `127.0.0.1:8000` وذاكرة معطّلة.

```bash
bash ./cela.sh services-up-api   # أو: uvicorn cela_sh.server:app

# الغلاف ب — تحقق جسم الطلب (422، detail)
curl -s -o /dev/stdout -w '\n%{http_code}\n' \
  -X POST http://127.0.0.1:8000/plan -H 'Content-Type: application/json' -d '{}'

# الغلاف ب — نص من مسافات فقط (422، detail بـ type=value_error)
curl -s -X POST http://127.0.0.1:8000/plan \
  -H 'Content-Type: application/json' -d '{"goal":"اقرأ","session_id":" "}'

# الغلاف ب — خارج الحدود (422) وJSON غير صالح (422)
curl -s -X POST http://127.0.0.1:8000/memory/search \
  -H 'Content-Type: application/json' -d '{"session_id":"s","query":"q","limit":999}'
curl -s -X POST http://127.0.0.1:8000/plan \
  -H 'Content-Type: application/json' -d '{not json'

# الغلاف ب — مسار وطريقة (404 / 405)
curl -s http://127.0.0.1:8000/nope
curl -s http://127.0.0.1:8000/plan

# الغلاف أ — الذاكرة معطّلة (409، memory_disabled)
curl -s -X POST http://127.0.0.1:8000/memory \
  -H 'Content-Type: application/json' -d '{"session_id":"s","content":"نص"}'

# عند تفعيل CELA_API_KEY: 401 من دون رمز
curl -i -X POST http://127.0.0.1:8000/plan \
  -H 'Content-Type: application/json' -d '{"goal":"اقرأ صفحة عامة"}'

# النجاح عند تفعيل المصادقة (استبدل <API_KEY> سرًا من مخزن الأسرار)
curl -s -X POST http://127.0.0.1:8000/plan \
  -H 'Authorization: Bearer <API_KEY>' \
  -H 'Content-Type: application/json' -d '{"goal":"اقرأ صفحة عامة"}'

# النجاح — /health لا يستدعي أي نموذج ولا يحتاج رمزًا
curl -s http://127.0.0.1:8000/health

# /readyz — نظرة سلبية على حالة الاعتمادات (بلا رمز ولا I/O، 200 دائمًا)
curl -s http://127.0.0.1:8000/readyz
```

بقية صفوف الجدول أ تحتاج شرط العطل نفسه (إيقاف Ollama، أو إفساد مخزن الذاكرة، أو حقن استثناء)، وهي مُغطّاة آليًا:

```bash
bash ./cela.sh test              # المجموعة كلها offline: بلا شبكة ولا Docker ولا Ollama
bash ./cela.sh api-surface --check
```

الأسطر المُتحقَّق منها آليًا في `tests/test_server.py`: `UnexpectedErrorMiddlewareTests` (تعقيم الاستثناء على مستوى ASGI، وحدّا الردّ المبدوء والنطاق غير HTTP)، و`test_unexpected_errors_return_a_stable_json_500_without_details`، و`test_a_sanitized_500_still_answers_a_client_that_surfaces_exceptions`. وصفوف الدوائر (`planner_circuit_open` و`memory_circuit_open`) وسلوك `/readyz` تحت العطل مغطّاة في `tests/test_fault_isolation.py` و`tests/test_resilience.py`.

### إذا غيّر الكود هذا العقد

حدّث الجدولين هنا أولًا، ثم `CHANGELOG.md`. لا تُضف نسخة ثانية من الجدول في README أو `docs/docker-deployment.md` — أشر إلى هذا الملف. الاختلاف بين جدولين هو المصدر الأول لانجراف العقد.
