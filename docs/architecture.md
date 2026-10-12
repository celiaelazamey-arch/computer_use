# معمارية cela.sh

**الإصدار:** 1.0.1

**الحالة:** نظام تخطيط محلي؛ فحص Ollama الحي ناجح في بيئة الاختبار، ويجب تكراره على الجهاز المستهدف

**النطاق:** التخطيط، CLI وAPI محلية، الإعداد، وذاكرة اختيارية معزولة بالجلسة

## الهدف والتدفق

يستقبل النظام هدفاً وسياقاً نصياً يقدمه المستدعي، ثم ينتج خطة JSON عبر نموذج Ollama محلي. يتحقق من المخطط ويضيف تصنيف سياسة قبل إعادة الخطة.

```text
CLI أو Python caller أو server.py
        |
        v
settings.py -> factory.py (تركيب الاعتمادات)
        |
        +--> SqliteVecMemoryStore أو QdrantMemoryStore، مع session_id إلزامي
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
│   ├── server.py             # API محلية اختيارية، دون تنفيذ أو مصادقة
│   ├── settings.py           # تحقق إعدادات البيئة
│   ├── factory.py            # تركيب Planner ومخزن الذاكرة
│   ├── planner/              # Ollama، نماذج الخطة، والسياسة
│   └── memory/               # العقد والمخازن وembeddings وسجل الـ backends
├── tests/                    # اختبارات offline، عقود مشتركة، وفحوص تكامل حية اختيارية
├── tools/api_surface.py      # لقطة سطح العقد العام لكشف كسر التوافق
└── docs/                     # البروتوكول والمعمارية والتوسعة والتشغيل
```

تُثبت الحزمة بتخطيط `src`؛ لا تعتمد الاختبارات أو المستخدمون على تشغيلها من مجلد المصدر مباشرةً. `create_planner(settings)` هو composition root، ويمكن للمستدعي حقن `MemoryStore` بديل عند إنشاء `OllamaPlanner`.

## وحدات النظام

- `settings.py`: يقرأ متغيرات `CELA_*` ويضبط نماذج Ollama، المهلات، والذاكرة المعطلة افتراضياً في CLI/Python.
- `factory.py`: يسجّل المخازن المدمجة ويبني المخزن المفعّل عبر السجلّ، ثم يربطه بالمخطط ومزود embeddings.
- `planner/client.py`: اتصال Ollama، الاسترجاع الاختياري، التحقق من المدخل والمخرج، وتطبيق السياسة.
- `planner/models.py`: مخطط Pydantic صارم، أفعال محددة، حد أقصى للخطوات، وفرض أن `STOP` آخر خطوة.
- `planner/policy.py`: تصنيف قواعدي مستقل عن النموذج؛ ليس مصنفاً شاملاً للمخاطر.
- `memory/base.py`: عقد `MemoryStore` و`MemoryStoreError`.
- `memory/registry.py`: سجلّ مخازن الذاكرة؛ مصدر الحقيقة لقيم `CELA_MEMORY_BACKEND` ونقطة الإضافة لأي مخزن جديد.
- `memory/mock_store.py`: مخزن حتمي مؤقت للاختبارات.
- `memory/embeddings.py` و`memory/qdrant_store.py`: embeddings محلية وREST لـ Qdrant، بلا `qdrant-client`.
- `memory/sqlite_vec_store.py`: تحميل الامتداد الرسمي، وجداول `vec0` بحسب الأبعاد، وKNN cosine مع ترشيح الجلسة داخل المحرك؛ ترحيل BLOB القديم وكتابة النص والمتجه ذريّان.
- `server.py`: `/health` و`/plan` وعمليتا الذاكرة الصريحتان، بلا مصادقة بعد؛ لا I/O أثناء import ولا حفظ خطط تلقائي.

## حدود الثقة

- اتصالات Ollama محصورة على loopback افتراضيًا؛ خيار `CELA_ALLOW_OLLAMA_CONTAINER_HOST` يسمح بخدمة `ollama` فقط داخل Docker. لا يُسمح بعناوين بعيدة أخرى، ولا تُستخدم البروكسيات البيئية أو التحويلات.
- Qdrant على loopback يستخدم HTTP. العنوان البعيد مسموح فقط مع HTTPS ومفتاح API.
- عند تفعيل الذاكرة، يجب توفير `session_id`؛ ترشيح الجلسة داخل محرك Qdrant أو vec0 ثم إعادة تحقق محلية. هذا عزل تعاوني وليس Auth/Z.
- API غير مصادقة؛ منافذ Compose محصورة على host loopback، ولا يجوز نشرها لعامة المستخدمين.
- لا تحفظ الذكريات تلقائياً. استرجاعها يضيف محتوى تاريخياً غير موثوق إلى prompt؛ لا يتبع المخطط تعليماتها.
- `screen_context` مدخل غير موثوق. تعليمات النظام تقلل أثر حقن prompt لكنها ليست حدّاً أمنياً.
- حالة `ready` لا تمنح موافقة. لا توجد طبقة تنفيذ أو صلاحية أدوات.

## التشغيل والحالة

الاختبارات الوهمية لا تثبت أن Ollama أو Qdrant مثبتان أو يعملان. استخدم `bash ./cela.sh probe` على الجهاز المقصود لتوليد خطة حقيقية والتحقق من مخططها. عند تفعيل الذاكرة، استخدم `bash ./cela.sh probe-memory` لفحص embeddings وعزل الجلسات والتنظيف. يتوقف النظام عند الفشل ولا يحول الخطأ إلى خطة بديلة.

إعداد [Docker المحلي](docker-deployment.md) يشمل API اختيارية وذاكرة sqlite-vec. المصادقة وتحديد المعدل ما زالا مرحلتين مطلوبتين قبل API مشتركة أو عامة. الإدراك البصري وأي تنفيذ يحتاجان نموذج تهديد وsandbox وموافقة خارج النموذج واختبارات رفض مستقلة.
