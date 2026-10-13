# معمارية cela.sh

**الإصدار:** 1.0.2

**الحالة:** نظام تخطيط محلي أولًا مع API سحابية اختيارية؛ دعم Docker موثق، لكن لم يُنشأ أو يُتحقق من نشر سحابي حي

**النطاق:** التخطيط، CLI وAPI، الإعداد، وذاكرة اختيارية بنطاقي project/global مع عزل الجلسات

## الهدف والتدفق

يستقبل النظام هدفاً وسياقاً نصياً يقدمه المستدعي، ثم ينتج خطة JSON عبر نموذج Ollama محلي. يتحقق من المخطط ويضيف تصنيف سياسة قبل إعادة الخطة.

```text
CLI أو Python caller أو server.py
        |
        v
settings.py -> factory.py (تركيب الاعتمادات)
        |
        +--> project session + global scope عبر SqliteVecMemoryStore أو QdrantMemoryStore
        |
        v
OllamaPlanner -> Ollama على loopback (أو خدمة Docker المسموح بها صراحة)
        |
        v
Pydantic validation -> policy -> JSON dry-run
```

لا يلتقط cela.sh الشاشة، ولا يتحكم بالواجهة، ولا يشغّل الأفعال. `execution_enabled` ثابت على `false`.

## بنية الملفات

```text
cela.sh/
├── cela.sh                  # تثبيت، اختبارات، تشغيل الخدمات، والتخطيط
├── pyproject.toml            # التثبيت والحزمة والأمر cela.sh
├── .env.example              # مرجع أسماء متغيرات الإعداد
├── Dockerfile                # بناء wheel ثم runtime غير root
├── docker-compose.yml        # Ollama وAPI ضمن profile api وQdrant اختياري
├── src/cela_sh/
│   ├── cli.py                # واجهة JSON ورموز الخروج في EXIT_CODES
│   ├── learning_cli.py       # كتابة ملاحظات صريحة إلى ذاكرة RAG
│   ├── server.py             # API اختيارية، بلا تنفيذ وبـBearer auth اختياري
│   ├── settings.py           # تحقق إعدادات البيئة
│   ├── factory.py            # تركيب Planner ومخزن الذاكرة
│   ├── planner/              # Ollama، نماذج الخطة، والسياسة
│   └── memory/               # المخازن وembeddings والوسوم والنطاقات واستخراج الملفات
├── tests/                    # اختبارات offline، عقود مشتركة، وفحوص تكامل حية اختيارية
├── tools/api_surface.py      # لقطة سطح العقد العام لكشف كسر التوافق
└── docs/                     # البروتوكول والمعمارية والتوسعة والتشغيل
```

تُثبت الحزمة بتخطيط `src`؛ لا تعتمد الاختبارات أو المستخدمون على تشغيلها من مجلد المصدر مباشرةً. `create_planner(settings)` هو composition root، ويمكن للمستدعي حقن `MemoryStore` بديل عند إنشاء `OllamaPlanner`.

## وحدات النظام

- `settings.py`: يقرأ متغيرات `CELA_*` ويضبط نماذج Ollama، المهلات، والذاكرة المعطلة افتراضياً في CLI/Python.
- `cli.py` و`learning_cli.py`: التخطيط وأوامر `learn` و`learn-file` و`search`؛ الإدخال صريح ويشمل note/correction ووسومًا ونطاق project/global.
- `factory.py`: يسجّل المخازن المدمجة ويبني المخزن المفعّل عبر السجلّ، ثم يربطه بالمخطط ومزود embeddings.
- `planner/client.py` و`planner/base.py`: اتصال Ollama والتحقق والسياسة؛ الاسترجاع يدمج project مع global ويقدّم التصحيحات ذات الصلة على الملاحظات.
- `planner/models.py`: مخطط Pydantic صارم، أفعال محددة، حد أقصى للخطوات، وفرض أن `STOP` آخر خطوة.
- `planner/policy.py`: تصنيف قواعدي مستقل عن النموذج؛ ليس مصنفاً شاملاً للمخاطر.
- `memory/base.py`: عقد `MemoryStore` و`MemoryStoreError`، مع `list_memory` للتصفح و`replace_source` للاستيراد المتكرر الآمن كقدرات اختيارية للـ backends.
- `memory/metadata.py` و`memory/scopes.py`: تحقق الوسوم والأنواع، ترتيب correction، وحجز global scope عن جلسات المشروع.
- `memory/document_loader.py` و`memory/sources.py`: استخراج محدود، مسارات مصدر نسبية آمنة، بصمات SHA-256 للملف والمقطع، والتحقق من دفعة الاستبدال كاملة قبل الكتابة.
- `memory/registry.py`: سجلّ مخازن الذاكرة؛ مصدر الحقيقة لقيم `CELA_MEMORY_BACKEND` ونقطة الإضافة لأي مخزن جديد.
- `memory/mock_store.py`: مخزن حتمي مؤقت للاختبارات.
- `memory/embeddings.py` و`memory/qdrant_store.py`: embeddings محلية وREST لـ Qdrant، بلا `qdrant-client`؛ استيراد المصدر يستخدم IDs حتمية ويزيل المقاطع القديمة بعد upsert.
- `memory/sqlite_vec_store.py`: تحميل الامتداد الرسمي، وجداول `vec0` بحسب الأبعاد، وKNN cosine مع ترشيح الجلسة داخل المحرك؛ manifest للمصادر وجدول يربط المقاطع بمعرّفات الذاكرة، واستبدال ذرّي للملف وموجهاته.
- `server.py`: `/health` (liveness عام) و`/readyz` (نظرة سلبية على حالة الاعتمادات) و`/plan` وعمليتا الذاكرة الصريحتان؛ `/health` و`/readyz` عامان، و`CELA_API_KEY` يفرض Bearer auth على التخطيط والذاكرة. لا I/O أثناء import ولا حفظ خطط تلقائي. لا قفل شامل للطلبات: جلسات HTTP لكل خيط (`thread_sessions.py`) واتصالات SQLite قصيرة العمر، ودوائر انقطاع (`resilience.py`) لكل اعتماد مع backpressure عبر `CELA_MAX_CONCURRENT_REQUESTS`.
- `thread_sessions.py` و`resilience.py`: جلسة مجمّعة لكل خيط خلف مقبض واحد، و`CircuitBreaker` و`DependencyHealth` (حالة سلبية من نتائج الطلبات الفعلية) لعزل أعطال كل اعتماد عن البقية.

## حدود الثقة

- اتصالات Ollama محصورة على loopback افتراضيًا؛ خيار `CELA_ALLOW_OLLAMA_CONTAINER_HOST` يسمح بخدمة `ollama` فقط داخل Docker. لا يُسمح بعناوين بعيدة أخرى، ولا تُستخدم البروكسيات البيئية أو التحويلات.
- Qdrant على loopback يستخدم HTTP. العنوان البعيد مسموح فقط مع HTTPS ومفتاح API.
- project memory تستخدم `session_id` وترشيحًا داخل Qdrant أو vec0 ثم إعادة تحقق محلية. global memory مخزنة في نطاق منفصل ومشتركة عمدًا بين المشاريع على المخزن نفسه؛ هذا ليس Auth/Z.
- API المحلية غير مصادقة افتراضيًا، بينما overlay السحابي يفرض `CELA_API_KEY`. منافذ Compose محصورة على host loopback؛ TLS يجب أن ينتهي عند ingress موثوق. المفتاح مشترك ولا يوفر هوية أو عزلًا لكل مستخدم.
- لا تحفظ الذكريات تلقائياً: الكتابة عبر `learn` أو `learn-file` أو `/memory` صريحة. الوسم `correction` يحدد ترتيب الاسترجاع فقط؛ المحتوى التاريخي يبقى غير موثوق ولا يتبع المخطط تعليماته حرفيًا.
- `screen_context` مدخل غير موثوق. تعليمات النظام تقلل أثر حقن prompt لكنها ليست حدّاً أمنياً.
- حالة `ready` لا تمنح موافقة. لا توجد طبقة تنفيذ أو صلاحية أدوات.

## عزل الأعطال والتوسع

فشل مكوّن اختياري (Ollama، مخزن الذاكرة) يدهور وظائفه فقط بأخطاء مصنّفة (`503` بكود ثابت) دون إسقاط الخدمة أو اختلاق نتيجة: `/health` يبقى حيًا (يعمل على حلقة الأحداث، لا ينتظر على مجمع الخيوط)، و`/readyz` يعرض حالة كل اعتماد نظرة سلبية بلا I/O، والدوائر تفتح بعد 3 فشل متتالٍ ثم تستطلع تلقائيًا بعد 10 ثوانٍ فيتعافى الاعتماد دون تدخّل. Worker في `ccelia2027/` يطابق ذلك بمهلات محدودة على DO الميزانية واستدعاءات AI، وبتمييز `429` (الحصة نفدت فعلًا) عن `503` (تعذّر التحقق بأمان). التفاصيل الكاملة وحدود التوسع المجانية (SQLite مقابل Qdrant، Ollama، Durable Objects) في [fault-tolerance-and-scaling.md](fault-tolerance-and-scaling.md)، وعقد أخطاء الدوائر في [api-errors.md](api-errors.md).

HA حقيقي (ازدواجية مع failover تلقائي) لـOllama أو المخزن المحلي غير متوفّر ضمن الحدود المجانية؛ ما يوفّره هذا التصميم هو التدهور الرشيق، لا الازدواجية، ولا يوصف أي منهما على أنه الآخر.

## التشغيل والحالة

الاختبارات الوهمية لا تثبت أن Ollama أو Qdrant مثبتان أو يعملان. استخدم `bash ./cela.sh probe` على الجهاز المقصود لتوليد خطة حقيقية والتحقق من مخططها. عند تفعيل الذاكرة، استخدم `bash ./cela.sh probe-memory` لفحص embeddings وعزل الجلسات والتنظيف. يتوقف النظام عند الفشل ولا يحول الخطأ إلى خطة بديلة.

إعداد [Docker المحلي](docker-deployment.md) يشمل API اختيارية وذاكرة sqlite-vec. يوفر [دليل النشر السحابي](cloud-deployment.md) مسار Linux VM أحادي المشغّل مع رمز Bearer وشبكة loopback؛ المصادقة متعددة المستخدمين وتحديد المعدل على مستوى التطبيق ما زالا مطلوبين لخدمة عامة مشتركة. الإدراك البصري وأي تنفيذ يحتاجان نموذج تهديد وsandbox وموافقة خارج النموذج واختبارات رفض مستقلة.
