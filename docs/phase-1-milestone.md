# cela.sh: قائمة تسليم المرحلة الأولى

**الحالة:** الاختبارات offline متاحة؛ فحص الخدمات الحي على الجهاز المقصود ما زال مطلوباً.

**النطاق:** CLI قابلة لإعادة الاستخدام، تخطيط محلي، وذاكرة جلسات اختيارية. لا تنفيذ.

## ما يتضمنه النظام

- حزمة Python بتخطيط `src`، إعداد موحد، factory لحقن الخدمات، وواجهة التشغيل `cela.sh`.
- Ollama للتخطيط وembeddings، وQdrant اختياري افتراضياً.
- اختبارات offline للـ CLI والمخطط والسياسة والذاكرة والإعداد والتركيب.
- `bash ./cela.sh test` يشغل الاختبارات offline. لا يبدأ Docker أو Ollama.
- الخطة دائماً `dry_run=true` و`execution_enabled=false`؛ لا التقاط شاشة ولا تنفيذ أوامر ولا حفظ ذاكرة تلقائي.

## فحص الجهاز المستهدف

نفذ الخطوات من مجلد `cela.sh/`. يلزم Python 3.11+ وBash. يحتاج التخطيط الحي إلى Ollama على `127.0.0.1:11434`؛ Qdrant مطلوب فقط إذا فُعّلت الذاكرة.

1. أنشئ البيئة وثبت الحزمة:

   ```bash
   bash ./cela.sh install
   ```

2. ابدأ الخدمات محلياً:

   ```bash
   bash ./cela.sh services-up
   ```

   يربط Compose المنافذ على loopback فقط. عند تثبيت Ollama مباشرة، يمكن تخطي Docker لخدمة التخطيط.

3. حمّل نموذج التخطيط إلى Ollama. عند تشغيل Compose:

   ```bash
   docker compose exec ollama ollama pull qwen2.5:3b
   docker compose exec ollama ollama list
   ```

   عند تشغيل Ollama مباشرة على الجهاز، استخدم `ollama pull qwen2.5:3b` بدلاً من `docker compose exec`.

4. أعد الاختبارات offline:

   ```bash
   bash ./cela.sh test
   ```

   المتوقع: نجاح جميع الاختبارات.

5. نفذ فحص التخطيط الحي بعد تثبيت نموذج Ollama:

   ```bash
   bash ./cela.sh probe
   ```

   يرسل الفحص هدفاً إلى نموذج Ollama الحقيقي ويطبع خطة JSON بعد التحقق من المخطط وكون التنفيذ معطلاً. لا يحتاج Qdrant. لا تعتبره ناجحاً إلا إذا خرج بالرمز صفر وطبع `PASS`.

6. اختبر إنشاء خطة بالهدف الذي تريده:

   ```bash
   bash ./cela.sh --goal "Find an article about agent architectures" --context "The browser is on its start page"
   ```

   افحص أن الناتج JSON صالح وأن العلمين `dry_run` و`execution_enabled` يعكسان حدود التخطيط فقط.

7. إن أردت اختبار الذاكرة الاختيارية، ابدأ Qdrant والنموذج المضمن ثم شغّل فحص الذاكرة:

   ```bash
   bash ./cela.sh services-up-memory
   docker compose exec ollama ollama pull nomic-embed-text
   bash ./cela.sh probe-memory
   ```

   ينشئ الفحص collection مؤقتة فريدة، يتحقق من عزل جلستين، ثم يحذفها. لا يلمس `agent_memory`.

8. افحص حالة الخدمات عند الحاجة:

   ```bash
   docker compose ps
   docker compose logs --tail=100 ollama qdrant
   ```

## بوابة القبول

- [ ] `bash ./cela.sh test` ينجح بالكامل.
- [ ] النموذج المختار مضبوط ومثبت في Ollama.
- [ ] `bash ./cela.sh probe` ينجح ويولد خطة صالحة بعلمَي التخطيط فقط.
- [ ] عند تفعيل الذاكرة: `bash ./cela.sh probe-memory` ينجح ويحذف مجموعة الاختبار المؤقتة.
- [ ] CLI تعيد خطة JSON صالحة، بلا أي فعل تنفيذي.
- [ ] منافذ Ollama وQdrant غير منشورة خارج loopback.

لا تفعّل تنفيذاً أو واجهة تحكم قبل مراجعة نموذج تهديد مستقل وsandbox وموافقة بشرية واختبارات رفض. أوقف الخدمات بـ `bash ./cela.sh services-stop`؛ تجنب `docker compose down -v` لأنه يحذف بيانات volumes.
