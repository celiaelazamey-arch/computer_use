# CELA Monorepo

## مخطط التخطيط المحلي cela.sh

نظام تخطيط محلي قابل لإعادة الاستخدام: CLI وAPI محلية، إعداد مركزي، وOllama مع ذاكرة اختيارية عبر `sqlite-vec` الرسمي أو Qdrant. يستقبل هدفاً وسياقاً نصياً، ويتحقق من الخطة ويصنفها. لا يلتقط الشاشة ولا ينفذ النقرات أو الأوامر؛ كل مخرجاته `dry_run=true` و`execution_enabled=false`.

## المستودع كنظام واحد

يجمع هذا المستودع واجهتين لـ CELA مع إبقاء حدود التشغيل والأمان واضحة:

- `cela.sh`: مخطط محلي بـ Python وOllama، يعيد خطة JSON فقط ولا ينفذها.
- `ccelia2027/`: واجهة ويب وCloudflare Worker لقراءة صفحات HTTPS عامة فقط عبر جلسات Browser Run قصيرة ومعزولة.

هما مكوّنان للمنتج نفسه لكنهما ليسا خدمة واحدة وقت التشغيل: لا يستدعي Worker مخطط Python، ولا يستخدم Python أسرار Cloudflare أو Browser Run. مخططاهما ومزوّداهما مختلفان عمداً؛ CI والجودة والتوثيق وإدارة الاعتماديات هي الطبقة الموحدة للمستودع. راجع [معمارية النظام](docs/system-architecture.md).

لتثبيت وفحص المستودع كاملًا، يلزم Python 3.11+ وBash وNode.js 22.13+ مع npm:

```bash
bash ./cela.sh install-all
bash ./cela.sh check-all
```

`check-all` يشغّل اختبارات Python وبناء حزمة Python، واختبارات Worker وLint والتنسيق وتدقيق npm وبناء Wrangler التجريبي. لا يشغّل Ollama أو Browser Run حيًا ولا ينشر إلى الإنتاج. لتشغيل واجهة Worker محليًا بعد التثبيت: `bash ./cela.sh worker-dev`؛ قد تتطلب الربط مع خدمات Cloudflare الحية اعتمادًا وإذنًا منفصلين.

## البدء

المتطلبات: Python 3.11+، وBash، وخدمة Ollama محلية. لا يلزم Docker أو Qdrant لمسار التخطيط الأول.

للتثبيت المباشر لـ Ollama استخدم [صفحة التنزيل الرسمية](https://ollama.com/download)، ثم شغّل الخدمة محلياً.

```bash
bash ./cela.sh install
bash ./cela.sh services-up
docker compose exec ollama ollama pull qwen2.5:3b
bash ./cela.sh probe
bash ./cela.sh --goal "ابحث عن مقال حول معمارية الوكلاء" --context "المتصفح مفتوح على صفحة البداية"
```

ينشئ `install` بيئة `.venv` داخل المشروع ويثبت CELA.sh. يبدأ `services-up` خدمة Ollama فقط؛ تنزيل النموذج خطوة منفصلة وقد يحتاج مساحة ووقتاً. يعيد `probe` خطة فعلية من النموذج ويكتب تأكيد النجاح إلى stderr. إذا كان Ollama مثبتاً مباشرة، شغّله محلياً ثم تخطَّ أوامر Docker. المنافذ المنشورة عبر Compose مربوطة على `127.0.0.1` فقط.

النموذج الافتراضي `qwen2.5:3b`، وقد اختُبر على بيئة Linux بذاكرة 8GB. عند تثبيت Ollama مباشرة على الجهاز، حمّله قبل التشغيل:

```bash
ollama pull qwen2.5:3b
CELA_PLANNER_MODEL=qwen2.5:3b bash ./cela.sh probe
```

## الذاكرة والإعداد

الذاكرة الدائمة معطلة افتراضياً في CLI/Python. لتفعيل SQLite المحلي مع امتداد `vec0` الحقيقي:

```bash
export CELA_MEMORY_BACKEND=sqlite-vec
export CELA_SQLITE_VEC_PATH=cela_memory.db
docker compose exec ollama ollama pull nomic-embed-text
bash ./cela.sh --goal "تابع موضوع التخطيط" --session-id "جلسة-123"
```

يثبّت المشروع مكتبة `sqlite-vec` الرسمية. البحث cosine يتم داخل `vec0` باستخدام `MATCH`، مع ترشيح الجلسة قبل top-k. تُرحّل القواعد القديمة تلقائيًا بمعاملة ذرية دون إعادة embedding؛ **انسخها احتياطيًا قبل أول فتح بالإصدار الجديد**. راجع [دليل التخزين والترحيل](docs/docker-deployment.md#التخزين-والترحيل).

لتفعيل استرجاع Qdrant بدلًا منه، عيّن المتغيرات في بيئة العملية:

```bash
export CELA_MEMORY_BACKEND=qdrant
export CELA_QDRANT_COLLECTION=agent_memory
bash ./cela.sh --goal "تابع موضوع التخطيط" --session-id "جلسة-123"
```

يجب تمرير `--session-id` عند تفعيلها. الاسترجاع معزول حسب الجلسة، والكتابة إلى الذاكرة لا تحدث تلقائياً. الإعدادات الكاملة موثقة في `.env.example`؛ البرنامج يقرأ متغيرات البيئة ولا يحمّل ملف `.env` تلقائياً. لا تحفظ مفتاح Qdrant في ملفات عامة.

يمكن استعمال النظام من Python أيضاً:

```python
from cela_sh import Settings, create_planner

planner = create_planner(Settings.from_env())
plan = planner.generate_plan("Find an article", session_id=None)
print(plan.model_dump_json(indent=2))
```

`create_planner` هو نقطة التركيب: يجهز المحرك ويحقن مخزن الذاكرة المختار. يمكن حقن `MemoryStore` بديل مباشرةً في `OllamaPlanner` للاختبارات أو للتكاملات المستقبلية.

## API والحاويات

```bash
bash ./cela.sh services-up-api
docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull nomic-embed-text
curl --fail http://127.0.0.1:8000/health
```

تشغّل خدمة `cela-sh-api` الاختيارية ضمن profile `api`، مع صورة متعددة المراحل ومستخدم غير root وvolume دائم للذاكرة. العقد في `/docs`؛ المسارات `/plan` و`/memory` و`/memory/search` تعيد التخطيط الجاف والذاكرة المعزولة فقط. الكتابة إلى الذاكرة صريحة، لا نتيجة جانبية للتخطيط.

**لا توجد مصادقة API بعد**؛ Compose ينشر المنافذ على loopback فقط، و`session_id` ليس تفويضًا. `/health` لا يثبت تنزيل النماذج. التشغيل والإعداد والنسخ الاحتياطية والقيود في [دليل Docker](docs/docker-deployment.md).

كل فشل في API له شكل واستقرار محدّدان: أخطاء التطبيق تعيد `{"error":{"code":"...","message":"..."}}` برمز وجملة ثابتة، وأخطاء الإطار (تحقق جسم الطلب، 404، 405) تعيد `{"detail":...}` القياسية. الاستثناءات غير المتوقعة تعيد `500` بـ`internal_error` دون نصّ الاستثناء أو نوعه أو الـtraceback. الجدول الكامل والحدود وملحق التحقق في **[عقد أخطاء API](docs/api-errors.md)** — وهو المرجع الوحيد؛ لا تُضف نسخة ثانية منه هنا.

## بنية المشروع

- `cela.sh`: واجهة تشغيل موحدة للتثبيت والاختبار والخدمات والتخطيط وبوابة `preflight`.
- `Dockerfile` و`docker-compose.yml`: صورة API متعددة المراحل، وOllama، وQdrant الاختياري.
- `src/cela_sh/server.py`: API محلية اختيارية؛ تُثبت عبر `pip install -e '.[server]'`.
- `src/cela_sh/settings.py`: إعدادات البيئة والتحقق منها.
- `src/cela_sh/factory.py`: تركيب الخدمات والمزودات دون ربط المستهلك بتفاصيل Qdrant.
- `src/cela_sh/planner/`: عقد المخطط (`Planner`)، سجل مزودي التخطيط المحليين (`registry.py`)، مخططات البيانات، عميل Ollama، والسياسة المستقلة.
- `src/cela_sh/memory/`: عقد الذاكرة، مخزن اختباري مؤقت، embeddings، محول Qdrant REST، ومخزن `vec0` الحقيقي، و`registry.py` لتسجيل مخازن جديدة.
- `tests/`: اختبارات offline، وعقود سلوكية مشتركة في `contract_memory_store.py` و`contract_planner.py`، وبدائل غير متصلة في `fakes.py`؛ `integration_plan_probe.py` لفحص توليد الخطة الحي و`integration_live_probe.py` لفحص الذاكرة الحي الاختياري.
- `tools/api_surface.py`: يلتقط سطح العقد العام للمستودع في `tests/snapshots/api_surface.json` لكشف أي كسر توافق.
- `docs/`: المعمارية والبروتوكول ودليل التوسعة وقائمة تسليم التشغيل، و[عقد أخطاء API](docs/api-errors.md) كمرجع وحيد لأشكال الاستجابة ورموزها.
- `ccelia2027/`: تطبيق Worker مستقل داخل المستودع؛ استخدم [دليل تشغيله](ccelia2027/README.md) و[دليل النشر الموحد](CCELIA2027-DEPLOYMENT.md).
- `.github/workflows/`: CI لكلا التطبيقين، CodeQL لـ JavaScript وPython، ونشر Worker يدوي فقط.
- `.github/`: حوكمة المستودع كلها في الجذر — `CODEOWNERS` وقوالب Issues وPull Request. لا يحتفظ `ccelia2027/` بأي ملفات workflows أو حوكمة خاصة به.

## الأوامر والفحوص

```bash
bash ./cela.sh preflight
bash ./cela.sh test
bash ./cela.sh api-surface --check
bash ./cela.sh check-all
bash ./cela.sh probe
bash ./cela.sh probe-memory
bash ./cela.sh services-stop
```

`preflight` هي البوابة التي تُشغَّل قبل الدفع: اختبارات Python، وفحص لقطة العقد العام، وبناء الحزمة، وفحوص Worker كاملة. لا تبدأ Docker ولا Ollama ولا تنشر شيئًا، وتُعلن ما تخطّته بدل النجاح الصامت.

الاختبارات المعتادة لا تشغل خدمات ولا تتصل بالإنترنت. `probe` يتصل بـ Ollama الحقيقي ويولد خطة صالحة؛ لا يحتاج Qdrant. فحص الذاكرة منفصل: شغّل `bash ./cela.sh services-up-memory` ثم `bash ./cela.sh probe-memory`؛ ينشئ collection اختبارية مؤقتة ويتأكد من عزل الجلسات وحذفها. راجع `docs/phase-1-milestone.md` قبل تشغيل الفحوص الحية.

## التوسعة دون كسر النظام

التوسعة إضافة لا جراحة. مخازن الذاكرة تُسجَّل عبر `register_memory_backend` ومزودو التخطيط المحليون يُسجَّلون عبر `register_planner_provider` دون تعديل `factory.py` أو `settings.py`، وكل إضافة جديدة تخضع لمجموعات العقود السلوكية المشتركة في `tests/contract_memory_store.py` و`tests/contract_planner.py` — بما فيها عزل الجلسات، وقفل `dry_run=true`، وحجب أوامر النظام/`sudo` بشكل موحد.

سطح العقد العام (الأسماء المصدَّرة، التواقيع، أعضاء `Action`، حقول النماذج، أعلام CLI، أكواد الخروج، ومسارات Worker) مُثبَّت في `tests/snapshots/api_surface.json`. أي حذف أو إعادة تسمية أو تغيير كود خروج يُفشل CI بدل أن يمر بصمت. بعد تغيير مقصود ومتوافق:

```bash
bash ./cela.sh api-surface --update
git diff tests/snapshots/api_surface.json
```

الوصفات خطوة بخطوة — مخزن ذاكرة، مزوّد تخطيط، فعل جديد، قاعدة سياسة، علم CLI، مسار Worker، متغير إعداد، خدمة Compose — في [دليل التوسعة](docs/extension-guide.md).

## حدود الأمان والحالة

- Ollama محصور على loopback افتراضيًا. الاستثناء الصريح `CELA_ALLOW_OLLAMA_CONTAINER_HOST=true` يسمح باسم خدمة `ollama` فقط داخل شبكة Docker موثوقة؛ redirects والبروكسيات البيئية معطلة.
- عنوان Qdrant البعيد يتطلب HTTPS ومفتاح API. لا تنشر منافذ الخدمات على الإنترنت.
- صورة Ollama تستخدم tag محددًا؛ ثبّت digests معتمدة لـ`PYTHON_IMAGE` و`OLLAMA_IMAGE` قبل نشر مُدار. Qdrant الاختياري يستخدم `latest` للتجربة المحلية، ويمكن تجاوزه بـ`QDRANT_IMAGE`.
- مدخلات السياق والذكريات غير موثوقة؛ تصنيف السياسة أولي ولا يثبت سلامة الخطة.
- لا توجد طبقة إدراك أو تنفيذ. الخطة `ready` ليست موافقة ولا إذناً بالفعل.
- التكامل الحي مع Ollama يبقى غير متحقق حتى ينجح `bash ./cela.sh probe` على الجهاز المقصود. Qdrant اختياري ويُفحص منفصلاً عبر `bash ./cela.sh probe-memory`.

لا تستخدم `docker compose down -v` عند إيقاف النظام، لأنه يحذف وحدات تخزين النماذج والذاكرة.

## الترخيص والحوكمة

المستودع مرخّص بـ Apache License 2.0؛ راجع [LICENSE](LICENSE). أيقونات Lucide المضمّنة تحتفظ بإشعارها الخاص (ISC/MIT) في `ccelia2027/docs/site/lucide-notice.txt`.

كل ملفات الحوكمة في الجذر: [CONTRIBUTING.md](CONTRIBUTING.md) لسير العمل وقواعد التوسعة، و[SECURITY.md](SECURITY.md) لنطاق الثغرات والإبلاغ الخاص وحدود الأمان الموعودة، و`.github/CODEOWNERS` وقوالب Issues وPull Request. الإصدار موحَّد في `pyproject.toml` و`ccelia2027/package.json` و`docs/architecture.md` و`docs/protocol.md`، و`CHANGELOG.md` يصف الإصدار الحالي وحده دون سجل إصدارات سابق.
