# بروتوكول cela.sh

**الإصدار:** 1.0.2

**النقل:** HTTP خاص إلى Ollama، ثم JSON إلى stdout أو API اختيارية محلية/سحابية

**الوضع:** تخطيط فقط؛ لا تنفيذ

## مدخلات CLI والإعداد

```bash
bash ./cela.sh --goal "Find an article" [--context "..." ] [--model "..." ] [--session-id "..."]
```

- `--goal`: مطلوب، من 1 إلى 2,000 حرف.
- `--context`: سياق نصي اختياري، حتى 8,000 حرف.
- `--model`: يتجاوز `CELA_PLANNER_MODEL` لهذه العملية فقط.
- `--session-id`: مطلوب عند تفعيل أي مخزن ذاكرة (`qdrant` أو `sqlite-vec`).

يقرأ البرنامج الإعدادات من بيئة العملية؛ لا يحمّل `.env` تلقائياً. تشمل المتغيرات `CELA_OLLAMA_URL`, `CELA_PLANNER_PROVIDER`, `CELA_PLANNER_MODEL`, `CELA_EMBEDDING_MODEL`, `CELA_MEMORY_BACKEND`, `CELA_QDRANT_URL`, `CELA_QDRANT_COLLECTION`, ومهل الاتصال. راجع `.env.example` للقيم الافتراضية.

`CELA_PLANNER_PROVIDER` يحدد مزوّد التخطيط المحلي (افتراضياً `ollama`)، ويُتحقق منه عبر `register_planner_provider(name, builder)` في `cela_sh.planner.registry`. `CELA_SQLITE_VEC_PATH` يحدد ملف الذاكرة المحلية. `CELA_ALLOW_OLLAMA_CONTAINER_HOST` افتراضيًا `false` (قيم البيئة: true/false/1/0)؛ تفعيله يسمح باسم `ollama` فقط لشبكة Docker الموثوقة، لا بعناوين LAN أو خدمات أخرى. `allow_container_host=False` معامل keyword اختياري جديد لكلا عميلَي Ollama، دون تغيير الاستدعاءات الموضعية السابقة.

### أمر التعلّم الصريح

```bash
bash ./cela.sh learn --session-id "project-notes" --content "تصحيح أو حقيقة يختار المشغّل حفظها"
```

يتضمن CLI أوامر الذاكرة التالية عند تفعيل `CELA_MEMORY_BACKEND=sqlite-vec` أو `qdrant`:

- `learn --content ...`: يخزن ملاحظة حتى 8,000 حرف؛ استخدم `--content -` لقراءتها من stdin. `--type` هو `note` أو `correction`، ويمكن تكرار `--tag`.
- `learn-file PATH`: يستورد Markdown أو UTF-8 text أو PDF نصيًا ويجزئه إلى مقاطع؛ PDF يتطلب `python -m pip install '.[documents]'`. يعامل `(scope/session_id, source_path)` كهوية للمصدر: الملف نفسه بلا تعديل يتخطى embeddings، والملف المعدّل يستبدل مقاطعه.
- `search`: بحث دلالي اختياري عبر `--query`، أو تصفح وفلترة بوسم/نوع دون query؛ كل الوسوم المحددة مطلوبة.
- `--scope project` هو الافتراضي ويتطلب `--session-id`. `--scope global` مشترك للمخزن كله ولا يقبل `--session-id`. التخطيط يدمج global مع جلسة المشروع، والتصحيحات تتقدم على الملاحظات عند ترتيب السياق.

هذه واجهات كتابة صريحة؛ لا تُلتقط المحادثات أو الخطط تلقائيًا. انظر [دليل بناء المعرفة](learning.md) لحدود استخراج الملفات والتخزين والخصوصية.

## API اختيارية محلية أو سحابية

تُثبت بـ`pip install -e '.[server]'` أو عبر `Dockerfile`. مسارات `/plan` و`/memory` و`/memory/search` موثقة في [دليل Docker](docker-deployment.md) و`/openapi.json`. الخطة نفس `Plan` المقفلة على dry-run. كتابة الذاكرة صريحة عبر POST وتقبل scope/type/tags؛ البحث يدعم الوسوم والنوع والنطاقين، و`both` يجمع global مع project. `/health` فحص حياة، لا جاهزية نموذج.

محليًا، المصادقة معطّلة افتراضيًا والمنافذ على loopback. ضبط `CELA_API_KEY` يفرض Bearer token على التخطيط والذاكرة؛ ويُلزم [overlay السحابي](cloud-deployment.md) بهذا المفتاح، مع إنهاء TLS عند ingress موثوق. المفتاح مشترك لمشغّل واحد، ولا يمنح تفويضًا بحسب `session_id`؛ لا تستخدمه لخدمة متعددة المستخدمين أو تضعه في JavaScript المتصفح. لا يوجد تحديد معدل على مستوى API، لذا أضف حماية ingress قبل فتحها لمستخدمين آخرين.

## الذاكرة

- `MemoryStore` يحدد `add_memory` و`search_memory`؛ ويوفر `list_memory` لتصفح الوسوم دون تضمين و`replace_source` لاستبدال استيراد ملف بأمان. المخزن الإضافي الذي لا يطبّق إحدى القدرتين لا يدعم مسارها في CLI/API.
- تُسجَّل المخازن عبر `register_memory_backend(name, builder)` في `cela_sh.memory.registry`، وهذا السجل هو مصدر الحقيقة لقيم `CELA_MEMORY_BACKEND` المقبولة؛ الخطأ في قيمة غير مدعومة يسمّي القيم الصالحة.
- `MockMemoryStore` مخزن مؤقت معجمي للاختبار، وليس بحثاً دلالياً.
- `QdrantMemoryStore` يستخدم REST. تنشأ المجموعة عند أول إضافة، بحجم embedding المكتشف ومسافة `Cosine`، ويرفض اختلاف الحجم. استيراد المصدر يستخدم point IDs حتمية مرتبطة بالجلسة والمسار ورقم المقطع، ثم يحذف المقاطع القديمة الزائدة بعد رفع النسخة الجديدة.
- الذاكرة معطلة افتراضياً في CLI/Python. السجلات `project` تستخدم `session_id` مستقلًا؛ `global` مخزنة في نطاق داخلي محجوز وتُدمج مع المشروع عند التخطيط. لا تستخدم `session_id` كهوية أو سر، والذاكرة العالمية مشتركة عن قصد على المخزن نفسه.
- `SqliteVecMemoryStore` يستخدم مكتبة sqlite-vec الرسمية، و`vec_memories_<dimension>` بمسافة cosine وعمود metadata لترشيح الجلسة. `MATCH` مع `k` ومرشح الجلسة يجري البحث داخل الامتداد قبل top-k؛ ترحيل القواعد القديمة وكتابة المصدر/متجهاته ذرّيان. جدول manifest يربط `(session_id, source_path)` ببصمة الملف ومقاطعها، فيسمح بتخطي الملف المطابق أو استبدال نسخته القديمة داخل معاملة واحدة.
- نتيجة الذاكرة تحفظ مفاتيح `session_id`, `content`, `metadata`, `score` فقط. cosine similarity هي `1 - distance` ضمن `[-1, 1]`؛ حد vec0 الأصلي 4096 نتيجة، وحد HTTP هو 100. الأبعاد المختلفة لا تختلط في البحث.
- `OllamaEmbeddingProvider.embed(text)` يستدعي `POST /api/embed` مع `model` و`input` كنص واحد.
- المخطط لا يكتب ذكريات تلقائياً. `learn` و`learn-file` و`POST /memory` كتابات صريحة؛ كل سجل يحمل `type` (`note` أو `correction`) ووسومًا وscope. تُقدّم corrections في سياق التخطيط لكنها تبقى بيانات غير موثوقة. لا يعيد ذلك تدريب أوزان النموذج.
- Ollama على loopback أو الخدمة الداخلية المسموح بها صراحة. Qdrant البعيد يتطلب HTTPS ومفتاح API، ولا تحفظ المفتاح في المستودع.

## مخطط الخطة

يرجع النموذج أولاً خطة خاماً:

```json
{
  "analysis": "Short summary",
  "steps": [
    {
      "action": "SEARCH_WEB",
      "target": "browser search field",
      "value": "agent architectures",
      "reason": "Find a relevant article"
    }
  ]
}
```

الأفعال المسموحة: `OPEN_APP`, `SEARCH_WEB`, `NAVIGATE`, `OBSERVE`, `CLICK`, `TYPE`, `SCROLL`, `WAIT`, `ASK_USER`, `STOP`. لا حقول إضافية؛ `analysis` من 1 إلى 1,500 حرف؛ الخطوات من 1 إلى 20؛ `target` حتى 500؛ `value` حتى 2,000؛ و`reason` من 1 إلى 500 حرف. يمكن أن يظهر `STOP` مرة واحدة في نهاية الخطة فقط.

`target` هو التطبيق أو الوجهة أو عنصر الواجهة، وليس محتوى البحث أو النص المكتوب. `value` يحمل استعلام `SEARCH_WEB` أو نص `TYPE` أو عنوان `NAVIGATE` عند معرفته؛ وقد يكون فارغاً إذا لم يحتج الفعل قيمة. يطبع النظام هدف `SEARCH_WEB` دائماً كحقل بحث المتصفح، وينقل النص الذي وضعه النموذج في `target` إلى `value` إذا ترك الاستعلام فارغاً.

بعد التحقق، يضيف النظام لكل خطوة `risk`, `requires_confirmation`, و`policy_reasons`. وعلى مستوى الخطة يضيف `status`, `requires_confirmation`, `dry_run=true`, و`execution_enabled=false`. `ready` ليس إذناً بالتنفيذ.

## الأخطاء ورموز الخروج

عند التخطيط، stdout يحتوي JSON الخطة فقط. أوامر `learn` و`learn-file` و`search` تكتب تأكيدًا أو نتائج JSON إلى stdout؛ عند الفشل، stderr يحتوي JSON خطأ، ولا توجد خطة بديلة.

| الرمز | المعنى                                 |
| ----: | -------------------------------------- |
|   `0` | نجح التخطيط أو أمر الذاكرة             |
|   `1` | خطأ Planner معروف عام                  |
|   `2` | مدخل CLI أو إعداد غير صالح             |
|   `3` | تعذر الاتصال بـ Ollama أو انتهت المهلة |
|   `4` | استجابة HTTP أو JSON غير صالحة         |
|   `5` | مدخل أو خطة لا تطابق المخطط            |
|   `6` | خطأ تهيئة أو استرجاع أو اتصال الذاكرة  |
| `130` | أوقف المستخدم العملية                  |

اختبارات SQLite تستخدم امتداد vec0 الحقيقي مع embeddings حتمية؛ بدائل HTTP للـAPI وQdrant وOllama تبقى offline. نجاحها لا يثبت جاهزية الخدمات والنماذج الحية. `tests/integration_plan_probe.py` يولد خطة عبر Ollama المحلي، و`tests/integration_live_probe.py` يفحص الذاكرة وQdrant اختيارياً.
