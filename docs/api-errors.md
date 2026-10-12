# عقد أخطاء API في cela.sh

**الإصدار:** 1.0.1

**الحالة:** مرجع وحيد. هذا الملف هو مصدر الحقيقة لعقد أخطاء API المحلية؛ بقية الوثائق تشير إليه ولا تعيد سرد جداوله.

**النطاق:** خدمة `cela-sh-api` في `src/cela_sh/server.py` (المسارات `/health` و`/plan` و`/memory` و`/memory/search`). لا يشمل Worker في `ccelia2027/`، فهو تطبيق مستقل بسطح أخطاء خاص به.

**طريقة التوثيق:** كل صف في الجدولين أدناه مُتحقَّق منه بتشغيل التطبيق فعليًا، لا بقراءة الكود فقط. أوامر إعادة التحقق في [ملحق التحقق](#ملحق-التحقق).

---

## شكلان للاستجابة، لا شكل واحد

هذا هو البند الأهم في العقد، وهو الذي يُساء نقله عادةً. API تعيد **غلافين مختلفين** حسب مصدر الخطأ:

| الغلاف                | الشكل                                          | المصدر                                                                                                           |
| --------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **أ** — أخطاء التطبيق | `{"error": {"code": "...", "message": "..."}}` | أعطال النطاق المعروفة (المخطط، الذاكرة) والاستثناءات غير المتوقعة. كل `message` جملة ثابتة مكتوبة في الكود.      |
| **ب** — أخطاء الإطار  | `{"detail": ...}`                              | تحقق FastAPI/Pydantic من جسم الطلب، والمسارات غير الموجودة، والطريقة غير المسموحة. لا `code` ولا جملة ثابتة هنا. |

النتيجة العملية للمستهلك: **افحص الغلاف قبل أن تفترض `code`.** خطأ إدخال في جسم الطلب يعيد `422` بغلاف `detail`، بينما فشل تحقق داخل المخطط يعيد `422` بغلاف `error`. الحالة وحدها لا تكفي للتمييز.

كلا الغلافين `application/json`. لا يوجد مسار يعيد نصًا عاديًا (`text/plain`) أو HTML.

---

## الجدول أ — أخطاء التطبيق (غلاف `error`)

| الحالة | `code`                     | `message` الثابتة                               | المسار                      | المحفّز                                                 |
| -----: | -------------------------- | ----------------------------------------------- | --------------------------- | ------------------------------------------------------- |
|    409 | `memory_disabled`          | `Memory is disabled in this deployment.`        | `/memory`, `/memory/search` | الذاكرة غير مهيّئة في هذا النشر                         |
|    422 | `planner_validation_error` | `Planner input validation failed.`              | `/plan`                     | `PlannerValidationError`                                |
|    422 | `memory_validation_error`  | `Invalid memory input.`                         | `/memory`                   | `ValueError` من مخزن الذاكرة                            |
|    422 | `memory_validation_error`  | `Invalid memory search input.`                  | `/memory/search`            | `ValueError` من مخزن الذاكرة                            |
|    500 | `planner_error`            | `An internal planner error occurred.`           | `/plan`                     | أي `PlannerError` غير مصنَّف أعلاه                      |
|    500 | `internal_error`           | `Internal server error.`                        | أي مسار HTTP                | استثناء غير متوقع (الوسيطة `UnexpectedErrorMiddleware`) |
|    502 | `planner_response_error`   | `Planner service returned an invalid response.` | `/plan`                     | `PlannerResponseError` — رد Ollama غير صالح             |
|    503 | `planner_connection_error` | `Unable to connect to planner service.`         | `/plan`                     | `PlannerConnectionError` — تعذّر الوصول إلى Ollama      |
|    503 | `planner_memory_error`     | `Planner memory backend is unavailable.`        | `/plan`                     | `PlannerMemoryError`                                    |
|    503 | `memory_store_error`       | `Memory store is unavailable.`                  | `/memory`, `/memory/search` | `MemoryStoreError`                                      |

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

| الحقل            | الحدّ                                   | المسارات         |
| ---------------- | --------------------------------------- | ---------------- |
| `goal`           | 1–2,000 حرف، ولا يُقبل نص من مسافات فقط | `/plan`          |
| `screen_context` | ≤ 8,000 حرف (اختياري، افتراضيًا `""`)   | `/plan`          |
| `content`        | 1–8,000 حرف                             | `/memory`        |
| `query`          | 1–8,000 حرف                             | `/memory/search` |
| `session_id`     | 1–256 حرف، ولا يُقبل نص من مسافات فقط   | الثلاثة          |
| `limit`          | عدد صحيح 1–100 (افتراضيًا 3)            | `/memory/search` |
| `metadata`       | كائن، افتراضيًا `{}`                    | `/memory`        |

النماذج صارمة: أي حقل إضافي غير معرَّف يعيد `422` بـ`extra_forbidden`. لا يوجد `/execute`، والخطة تحمل دائمًا `dry_run=true` و`execution_enabled=false`.

---

## ملاحظة أمان

API **بلا مصادقة**، و`session_id` مُعرِّف اختيار لا تفويض. Compose ينشر المنفذ على loopback للمضيف فقط. لا تعرّض هذه الخدمة لشبكة غير موثوقة قبل إضافة مصادقة وتخويل. راجع `SECURITY.md` و`docs/threat-model-cela.md` (البند T11).

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

# النجاح — /health لا يستدعي أي نموذج
curl -s http://127.0.0.1:8000/health
```

بقية صفوف الجدول أ تحتاج شرط العطل نفسه (إيقاف Ollama، أو إفساد مخزن الذاكرة، أو حقن استثناء)، وهي مُغطّاة آليًا:

```bash
bash ./cela.sh test              # المجموعة كلها offline: بلا شبكة ولا Docker ولا Ollama
bash ./cela.sh api-surface --check
```

الأسطر المُتحقَّق منها آليًا في `tests/test_server.py`: `UnexpectedErrorMiddlewareTests` (تعقيم الاستثناء على مستوى ASGI، وحدّا الردّ المبدوء والنطاق غير HTTP)، و`test_unexpected_errors_return_a_stable_json_500_without_details`، و`test_a_sanitized_500_still_answers_a_client_that_surfaces_exceptions`.

### إذا غيّر الكود هذا العقد

حدّث الجدولين هنا أولًا، ثم `CHANGELOG.md`. لا تُضف نسخة ثانية من الجدول في README أو `docs/docker-deployment.md` — أشر إلى هذا الملف. الاختلاف بين جدولين هو المصدر الأول لانجراف العقد.
