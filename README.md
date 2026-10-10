# CELA Monorepo

## مخطط التخطيط المحلي cela.sh

نظام تخطيط محلي قابل لإعادة الاستخدام: CLI موحدة، إعداد مركزي، وحقن اعتمادات اختياري لـ Ollama وQdrant. يستقبل هدفاً وسياقاً نصياً، ويتحقق من الخطة ويصنفها. لا يلتقط الشاشة ولا ينفذ النقرات أو الأوامر؛ كل مخرجاته `dry_run=true` و`execution_enabled=false`.

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

الذاكرة الدائمة معطلة افتراضياً. لتفعيل استرجاع Qdrant، عيّن المتغيرات في بيئة العملية:

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

## بنية المشروع

- `cela.sh`: واجهة تشغيل موحدة للتثبيت والاختبار والخدمات والتخطيط.
- `src/cela_sh/settings.py`: إعدادات البيئة والتحقق منها.
- `src/cela_sh/factory.py`: تركيب الخدمات والمزودات دون ربط المستهلك بتفاصيل Qdrant.
- `src/cela_sh/planner/`: مخططات البيانات، عميل Ollama، والسياسة المستقلة.
- `src/cela_sh/memory/`: عقد الذاكرة، مخزن اختباري مؤقت، embeddings، ومحول Qdrant REST.
- `tests/`: اختبارات offline؛ `integration_plan_probe.py` لفحص توليد الخطة الحي و`integration_live_probe.py` لفحص الذاكرة الحي الاختياري.
- `docs/`: المعمارية والبروتوكول وقائمة تسليم التشغيل.
- `ccelia2027/`: تطبيق Worker مستقل داخل المستودع؛ استخدم [دليل تشغيله](ccelia2027/README.md) و[دليل النشر الموحد](CCELIA2027-DEPLOYMENT.md).
- `.github/workflows/`: CI لكلا التطبيقين، CodeQL لـ JavaScript وPython، ونشر Worker يدوي فقط.

## الأوامر والفحوص

```bash
bash ./cela.sh test
bash ./cela.sh check-all
bash ./cela.sh probe
bash ./cela.sh probe-memory
bash ./cela.sh services-stop
```

الاختبارات المعتادة لا تشغل خدمات ولا تتصل بالإنترنت. `probe` يتصل بـ Ollama الحقيقي ويولد خطة صالحة؛ لا يحتاج Qdrant. فحص الذاكرة منفصل: شغّل `bash ./cela.sh services-up-memory` ثم `bash ./cela.sh probe-memory`؛ ينشئ collection اختبارية مؤقتة ويتأكد من عزل الجلسات وحذفها. راجع `docs/phase-1-milestone.md` قبل تشغيل الفحوص الحية.

## حدود الأمان والحالة

- Ollama محصور على loopback، والـ HTTP redirects والبروكسيات البيئية غير مستخدمة.
- عنوان Qdrant البعيد يتطلب HTTPS ومفتاح API. لا تنشر منافذ الخدمات على الإنترنت.
- صور Compose تستخدم `latest` للتجربة المحلية؛ ثبّت tags أو digests معروفة قبل بيئة مشتركة.
- مدخلات السياق والذكريات غير موثوقة؛ تصنيف السياسة أولي ولا يثبت سلامة الخطة.
- لا توجد طبقة إدراك أو تنفيذ. الخطة `ready` ليست موافقة ولا إذناً بالفعل.
- التكامل الحي مع Ollama يبقى غير متحقق حتى ينجح `bash ./cela.sh probe` على الجهاز المقصود. Qdrant اختياري ويُفحص منفصلاً عبر `bash ./cela.sh probe-memory`.

لا تستخدم `docker compose down -v` عند إيقاف النظام، لأنه يحذف وحدات تخزين النماذج والذاكرة.
