# نشر CELA.sh محليًا عبر Docker

هذا إعداد حاويات لخدمة **تخطيط فقط** مع Ollama وذاكرة `sqlite-vec` الرسمية. لا ينفذ الأفعال، ولا يربط Python بتطبيق Worker، ولا يحمّل النماذج تلقائيًا.

> الإعداد المحلي يبقى مفتوحًا افتراضيًا، وتُنشر المنافذ على `127.0.0.1` فقط. يمكن تفعيل Bearer auth بتعيين `CELA_API_KEY`؛ لا يحوّل المفتاح المشترك `session_id` إلى هوية أو تفويض. للنشر السحابي ذي المشغّل الواحد، استخدم overlay الآمن ودليل [النشر السحابي](cloud-deployment.md)، ولا تعرض API بلا TLS ومفتاح قوي.

## بدء الحاويات

المتطلبات: Docker Engine وDocker Compose v2، ومساحة وذاكرة تكفيان للنماذج المختارة. من جذر المستودع:

```bash
docker compose --profile api config --quiet
bash ./cela.sh services-up-api
# أو: docker compose --profile api up -d --build

docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull nomic-embed-text

curl --fail http://127.0.0.1:8000/health
```

افتح `http://127.0.0.1:8000/docs` لاستعراض عقد OpenAPI. خدمة `cela-sh-api` اختيارية ضمن profile `api`، لذلك يظل `bash ./cela.sh services-up` يبدأ Ollama فقط كما سابقًا. Qdrant اختياري ضمن profile `memory`، وليس مطلوبًا لمسار SQLite.

لا تنتظر API نجاح Ollama قبل البدء: التخطيط اعتماد اختياري، فإذا تعثّر Ollama أو ما يزال يحمّل النماذج، تجيب `/plan` بخطأ `503` مصنّف (وتفتح دائرة المخطط بعد 3 فشل متتالٍ) بينما يستمر `/health` ومسارا الذاكرة بالعمل — انظر [عزل الأعطال والتوسع](fault-tolerance-and-scaling.md). `/health` فحص حياة API وإتمام تهيئة مخزنها فقط؛ **ليس** فحص جاهزية النموذج. الطلبات التي تحتاج نموذجًا غير مثبت تفشل بخطأ واضح، ولا تعيد خطة بديلة.

## الذاكرة والتخطيط

الكتابة صريحة، والتخطيط لا يضيف ذكريات:

```bash
curl --fail http://127.0.0.1:8000/memory \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo","content":"نناقش معمارية الوكلاء المحليين","metadata":{"source":"manual"}}'

curl --fail http://127.0.0.1:8000/memory/search \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo","query":"معمارية الوكلاء","limit":3}'

curl --fail http://127.0.0.1:8000/plan \
  -H 'Content-Type: application/json' \
  -d '{"goal":"ابحث عن مقال حول معمارية الوكلاء","screen_context":"المتصفح مفتوح","session_id":"demo"}'
```

الخطة تحمل دائمًا `dry_run=true` و`execution_enabled=false`. لا يوجد `/execute`، و`ready` ليست إذنًا بالفعل.

| المسار           | الطريقة | النجاح | الغرض                                          |
| ---------------- | ------- | ------ | ---------------------------------------------- |
| `/health`        | GET     | 200    | حياة API، دون اتصال بنموذج                     |
| `/plan`          | POST    | 200    | خطة بالسياسة الحالية، دون حفظ تلقائي           |
| `/memory`        | POST    | 201    | إضافة صريحة؛ `{"stored":true}`                 |
| `/memory/search` | POST    | 200    | نتائج معزولة بالجلسة: content، metadata، score |
| `/docs`          | GET     | 200    | توثيق Swagger                                  |
| `/openapi.json`  | GET     | 200    | عقد OpenAPI                                    |

حدود API وأكواد الأخطاء موثّقة في **[عقد أخطاء API](api-errors.md)**، وهو المرجع الوحيد لها؛ لا تُنسخ جداوله إلى هنا حتى لا ينجرّ العقد بين نسختين.

المختصر التشغيلي الكافي للنشر: API تعيد **غلافين** — أخطاء التطبيق (المخطط، الذاكرة، الاستثناءات غير المتوقعة) تعيد `{"error":{"code":"...","message":"..."}}` برمز وجملة ثابتة، بينما تحقق جسم الطلب والمسارات والطرق الخاطئة (`422`/`404`/`405`) تعيد صيغة FastAPI القياسية `{"detail":...}`. الحالة وحدها لا تميّز بينهما؛ افحص الغلاف. عند تعطيل الذاكرة يعيد مساراها `409 memory_disabled`. الاستثناءات غير المتوقعة تعيد `500 internal_error` دون نوع الاستثناء أو نصّه أو الـtraceback، وتبقى التفاصيل في سجلّ الخادم فقط:

```bash
docker compose logs cela-sh-api   # الـtraceback الكامل للعطل، لا ما يراه المتصل
```

المدخلات صارمة ولا تقبل حقول تنفيذ إضافية، وحدودها الرقمية (الهدف، السياق، المحتوى، الاستعلام، معرّف الجلسة، `limit`) في جدول الحدود في العقد.

## إعداد Docker وحدود الشبكة

الصورة متعددة المراحل: تبني wheel من تخطيط `src` وتثبّت الحزمة مع extra `server`، ثم تنقل الاعتماديات المثبتة فقط إلى صورة runtime. لا تُنسخ `.git` أو الأسرار أو الاختبارات أو Worker. المستخدم `10001:10001` غير root، ونظام ملفات API للقراءة فقط، مع volume بيانات و`/tmp` مؤقت، دون capabilities أو امتيازات جديدة.

يستخدم Compose **أسماء الإعداد الفعلية**:

```text
CELA_OLLAMA_URL=http://ollama:11434
CELA_ALLOW_OLLAMA_CONTAINER_HOST=true
CELA_MEMORY_BACKEND=sqlite-vec
CELA_SQLITE_VEC_PATH=/app/data/cela_memory.db
```

يسمح الخيار الجديد باسم `ollama` فقط، وليس بعناوين LAN أو خدمات DNS عشوائية. افتراضيًا هو `false` ويظل عمل CLI/Python محصورًا على loopback. يجب أن تكون شبكة Docker وDNS الخاصة بها موثوقين. رفض redirects و`trust_env=False` باقيان في كلا العميلين.

يمكن تجاوز النماذج والمُهَل والمنفذ عبر بيئة Compose أو `.env` المحلية (Compose يقرأها، أما Python فلا يحمّلها). مثال:

```bash
CELA_API_PORT=8080 CELA_PLANNER_MODEL=qwen2.5:7b bash ./cela.sh services-up-api
# حمّل النموذج الذي اخترته صراحة قبل إرسال طلب تخطيط.
```

Ollama مثبت على tag محدد افتراضيًا. قبل نشر مُدار، ثبّت `PYTHON_IMAGE` و`OLLAMA_IMAGE` على digests معتمدة، وثبّت الاعتماديات وفق سياسة مؤسستك؛ نطاقات اعتماديات Python ليست lockfile. `sqlite-vec` مثبت على `0.1.9` لأن الامتداد لا يزال pre-v1. صورة Qdrant الاختيارية تبقى للتجربة المحلية ويمكن تجاوزها بـ`QDRANT_IMAGE`.

## التخزين والترحيل

- `cela_data` يحتفظ بقاعدة SQLite وملفات WAL، و`ollama_data` بالنماذج؛ إعادة إنشاء الحاوية لا تحذفهما.
- `memories` يحتفظ بالنص وJSON وأبعاد المتجه. المتجهات في `vec_memories_<dimension>`، باستخدام `vec0` و`distance_metric=cosine` وعمود metadata قابل للترشيح `session_id`.
- البحث `MATCH` يرشح الجلسة **قبل top-k**، ثم يعيد التحقق منها عند ربط النص؛ لا توجد محاكاة cosine أو فك BLOB في Python. استخدام metadata بدل partition منفصل لكل جلسة يجنب تضخم التخزين عندما تكون الذكريات قليلة لكل جلسة.
- النتيجة تحفظ العقد القائم: `session_id`, `content`, `metadata`, `score`. الدرجة `1 - distance` ضمن `[-1, 1]`، لا مسافة L2 ولا تقريبًا مقصوصًا إلى الصفر. الحد الأصلي للمخزن `min(limit, 4096)` بسبب حد `vec0`، وحد HTTP أصغر لحماية الموارد.
- تُبحث أبعاد الاستعلام فقط. تغيير نموذج embeddings، حتى مع الأبعاد نفسها، يستلزم قاعدة جديدة أو إعادة تضمين صريحة؛ لا يمكن اعتبار فضاءَي نموذجين متكافئين.
- يفشل المتجه الصفري أو غير المنتهي أو غير الصالح بعد التحويل إلى float32؛ لا يُخزّن ولا يُعاد score غير منتهٍ.

عند فتح قاعدة النسخة القديمة ذات عمود `embedding BLOB`، يرحّل المخزن الذكريات والـrowids والمتجهات الموجودة إلى `vec0` في معاملة واحدة، بلا اتصال بـOllama أو إعادة embedding. تكرار الفتح لا يكرر الترحيل. أي سجل تالف يُفشل العملية ويرجع المعاملة كاملة بدل إسقاط السجل أو فقد البيانات.

**انسخ القاعدة احتياطيًا قبل أول تشغيل للإصدار الجديد.** مخططها بعد الترحيل لا تقرؤه النسخة القديمة؛ للعودة إلى تلك النسخة، استعد نسخة ما قبل الترحيل. استخدم SQLite backup API أو snapshot متسق للـvolume، ولا تنسخ ملف `.db` منفردًا أثناء الكتابة في وضع WAL. مثال لقاعدة محلية (أوقف المستهلكين القدامى أولًا):

```bash
python - <<'PY'
import sqlite3
from contextlib import closing
with closing(sqlite3.connect('cela_memory.db')) as source:
    with closing(sqlite3.connect('cela_memory.before-vec0.db')) as backup:
        source.backup(backup)
PY
```

صلاحيات volume موجود يملكه root يجب ضبطها للمستخدم `10001:10001` قبل التشغيل؛ لا تعالجها بتشغيل API كـroot. الذكريات غير مشفرة، فاحمِ التخزين والنسخ الاحتياطية على الجهاز.

## التشغيل دون Docker

```bash
python -m pip install -e '.[server]'
mkdir -p data
CELA_MEMORY_BACKEND=sqlite-vec CELA_SQLITE_VEC_PATH=data/cela_memory.db \
  uvicorn cela_sh.server:app --host 127.0.0.1 --port 8000
```

يلزم SQLite مع دعم تحميل الامتدادات ونسخة 3.35+ لاستعلام CTE المستخدم. حدّث Python/SQLite إذا كان تحميل `sqlite-vec` معطلًا. يبقى extra `server` اختياريًا لمستخدمي CLI؛ `requirements-server.txt` بديل لتثبيت اعتماديات API.

## التحقق والإيقاف

```bash
python -m pip install -e '.[dev]'
pytest tests/test_extension_contracts.py -v
pytest tests/test_sqlite_vec_store.py tests/test_server.py tests/test_container_config.py -v
bash ./cela.sh preflight

docker compose --profile api ps
docker compose --profile api logs --tail=100 cela-sh-api ollama
bash ./cela.sh services-stop
```

اختبارات SQLite تستخدم **الامتداد الحقيقي** مع embeddings حتمية محلية. CI يراجع Compose، ويبني الصورة، ويختبر `vec0` ومستخدمها غير المميز، ويفحص حياة API دون تشغيل Ollama أو تنزيل نماذج. نجاح هذه الفحوص لا يثبت جاهزية Ollama على جهاز النشر: اختبر الطلبات السابقة بعد تنزيل النموذجين هناك.

لا تستخدم `docker compose down -v` عند الإيقاف؛ يحذف بيانات الذاكرة والنماذج.
