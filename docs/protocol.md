# بروتوكول cela.sh

**الإصدار:** 1.0.1

**النقل:** HTTP محلي إلى Ollama، ثم JSON إلى stdout أو API محلية اختيارية

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

## API محلية اختيارية

تُثبت بـ`pip install -e '.[server]'` أو عبر `Dockerfile`. مسارات `/plan` و`/memory` و`/memory/search` موثقة في [دليل Docker](docker-deployment.md) و`/openapi.json`. الخطة نفس `Plan` المقفلة على dry-run. الذاكرة تُكتب صراحة بـPOST فقط، والبحث معزول بالجلسة. `/health` فحص حياة، لا جاهزية نموذج. لا يوجد Auth/Z أو rate limiting بعد؛ لا تنشرها على شبكة غير موثوقة.

## الذاكرة

- `MemoryStore` يحدد `add_memory` و`search_memory`؛ يمكن حقن تطبيق بديل.
- تُسجَّل المخازن عبر `register_memory_backend(name, builder)` في `cela_sh.memory.registry`، وهذا السجل هو مصدر الحقيقة لقيم `CELA_MEMORY_BACKEND` المقبولة؛ الخطأ في قيمة غير مدعومة يسمّي القيم الصالحة.
- `MockMemoryStore` مخزن مؤقت معجمي للاختبار، وليس بحثاً دلالياً.
- `QdrantMemoryStore` يستخدم REST. تنشأ المجموعة عند أول إضافة، بحجم embedding المكتشف ومسافة `Cosine`، ويرفض اختلاف الحجم.
- الذاكرة معطلة افتراضياً في CLI/Python. عند تفعيلها، يفرض `session_id` في استعلام المخزن ويتحقق منه محلياً قبل إعادة النتائج. حاوية API تفعّل sqlite-vec صراحة.
- `SqliteVecMemoryStore` يستخدم مكتبة sqlite-vec الرسمية، و`vec_memories_<dimension>` بمسافة cosine وعمود metadata لترشيح الجلسة. `MATCH` مع `k` ومرشح الجلسة يجري البحث داخل الامتداد قبل top-k؛ ترحيل القواعد القديمة والكتابة ذريّان.
- نتيجة الذاكرة تحفظ مفاتيح `session_id`, `content`, `metadata`, `score` فقط. cosine similarity هي `1 - distance` ضمن `[-1, 1]`؛ حد vec0 الأصلي 4096 نتيجة، وحد HTTP هو 100. الأبعاد المختلفة لا تختلط في البحث.
- `OllamaEmbeddingProvider.embed(text)` يستدعي `POST /api/embed` مع `model` و`input` كنص واحد.
- المخطط لا يكتب ذكريات تلقائياً. يظل `add_memory` واجهة صريحة للمستدعي.
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

عند النجاح، stdout يحتوي JSON الخطة فقط. عند الفشل، لا توجد خطة بديلة؛ stderr يحتوي JSON خطأ.

| الرمز | المعنى                                 |
| ----: | -------------------------------------- |
|   `0` | أُنتجت خطة صحيحة                       |
|   `1` | خطأ Planner معروف عام                  |
|   `2` | مدخل CLI أو إعداد غير صالح             |
|   `3` | تعذر الاتصال بـ Ollama أو انتهت المهلة |
|   `4` | استجابة HTTP أو JSON غير صالحة         |
|   `5` | مدخل أو خطة لا تطابق المخطط            |
|   `6` | خطأ تهيئة أو استرجاع أو اتصال الذاكرة  |
| `130` | أوقف المستخدم العملية                  |

اختبارات SQLite تستخدم امتداد vec0 الحقيقي مع embeddings حتمية؛ بدائل HTTP للـAPI وQdrant وOllama تبقى offline. نجاحها لا يثبت جاهزية الخدمات والنماذج الحية. `tests/integration_plan_probe.py` يولد خطة عبر Ollama المحلي، و`tests/integration_live_probe.py` يفحص الذاكرة وQdrant اختيارياً.
