# نشر API الخاصة بـ CELA.sh على VM سحابية

يوفر هذا الدليل مسارًا محايدًا لمزوّد السحابة: Linux VM يعمل عليه Docker Engine وDocker Compose v2، مع Ollama داخل شبكة Compose ومخزن SQLite دائم. يمكن تشغيله على VM لدى AWS أو Google Cloud أو مزوّد آخر؛ لا يفترض خدمة حاويات serverless بعينها، ولا ينشئ موارد أو ينشر تلقائيًا. اخترنا VM لأن Ollama يحتاج خدمة خاصة دائمة وقرصًا للنماذج والذاكرة.

**يبقى `ccelia2027/` على Cloudflare Worker دون تغيير.** API Python خدمة منفصلة على VM واسم مضيف مستقل مثل `api.example.com`؛ لا يستدعي Worker هذه API ولا يشاركها الأسرار أو البيانات. سير Cloudflare الحالي يدوي ومستقل. لا يوجد نشر حي موثّق في هذا المستودع.

## مسار الطلب

```text
العميل
  └─ HTTPS → Caddy أو ingress موثوق على VM
                └─ 127.0.0.1:8000 → API Python (Bearer token)
                                      ├─ شبكة Docker خاصة → Ollama
                                      └─ volume دائم → SQLite / sqlite-vec
```

يوفر الإعداد `docker-compose.cloud.yml` طبقة إعداد فوق `docker-compose.yml`: يتطلب مفتاح API، ويبقي API وOllama مربوطتين بـ`127.0.0.1` على المضيف كما في الإعداد الأساسي. استخدم proxy يعمل على الـVM لإنهاء TLS؛ لا تفتح المنفذ `8000` مباشرة للإنترنت. Ollama لا يحتاج منفذًا عامًا.

## المتطلبات قبل النشر

- VM Linux تدعم Docker Compose v2، بذاكرة ومساحة قرص مناسبتين لحجم نماذج Ollama التي ستختارها. التنزيل والتشغيل قد يستهلكان مساحة وموارد كبيرة؛ تحقق من التكلفة وحدود مزوّدك قبل البدء.
- اسم DNS منفصل للـAPI، مثل `api.example.com`. لا تغيّر سجلّ أو نطاق Worker الموجود لـ`ccelia2027`.
- جدار ناري يسمح بـSSH من عناوين الإدارة فقط، و80/443 للـTLS proxy. **لا** تسمح من الإنترنت بـ8000 أو 11434.
- قرص دائم ومشفّر للـVM أو volume مُدار ونسخ احتياطية مشفّرة. `/app/data` يحتوي الذاكرة؛ وvolume Ollama يحتوي النماذج.
- طريقة لحفظ الأسرار، مثل مدير أسرار المزوّد أو ملف `.env` على المضيف بصلاحية `0600`. لا تضع المفتاح في المستودع أو واجهة المتصفح أو سجل CI.

## التشغيل

من جذر المستودع على الـVM، أضف مفتاحًا عشوائيًا من 256 بت إلى `.env` (أو أنشئ الملف). هذا يحافظ على بقية الإعدادات، يستبدل قيمة فارغة فقط، ويرفض تدوير مفتاح قائم دون إجراء صريح. لا يطبع الأمر المفتاح:

```bash
umask 077
python3 - <<'PY'
from pathlib import Path
import secrets

path = Path(".env")
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
key_line = f"CELA_API_KEY={secrets.token_hex(32)}"
for index, line in enumerate(lines):
    if line.startswith("CELA_API_KEY="):
        if line.partition("=")[2].strip():
            raise SystemExit("CELA_API_KEY is already set; rotate it explicitly through your secret manager.")
        lines[index] = key_line
        break
else:
    lines.append(key_line)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
```

يمكنك ضبط أسماء النماذج أو صورة الأساس في `.env` أيضًا؛ لا تضع الأسرار في أوامر build args. إعداد الإنتاج ينبغي أن يثبت صور Python وOllama إلى digests معتمدة، بدل tags متحركة:

```dotenv
# أمثلة أسماء فقط — استبدل كل قيمة بوسم digest راجعه فريق التشغيل.
PYTHON_IMAGE=python:3.11-slim-bookworm@sha256:<approved-digest>
OLLAMA_IMAGE=ollama/ollama:0.40.3@sha256:<approved-digest>
```

تحقق من الإعداد ثم ابدأ API وOllama:

```bash
docker compose -f docker-compose.yml -f docker-compose.cloud.yml --profile api config --quiet
bash ./cela.sh services-up-cloud

docker compose -f docker-compose.yml -f docker-compose.cloud.yml exec ollama ollama pull qwen2.5:3b
docker compose -f docker-compose.yml -f docker-compose.cloud.yml exec ollama ollama pull nomic-embed-text
```

الأمر `services-up-cloud` يستخدم overlay نفسه، ويرفض Compose البدء إذا لم يكن `CELA_API_KEY` موجودًا. تتحقق Python أيضًا من أن المفتاح ASCII ولا يقل عن 32 محرفًا. `/health` فحص حياة فقط؛ لا يثبت أن النموذجين نُزّلا أو أن لديهما ذاكرة كافية. لا يعيد التطبيق خطة بديلة إذا تعذر الاتصال بـOllama.

## TLS وDNS

على VM، يمكن تشغيل Caddy مثبتًا على المضيف (خارج Compose) مع سجل DNS يشير إلى عنوان الـVM. Caddy يتولى HTTPS تلقائيًا، ويصل إلى API عبر loopback فقط:

```caddyfile
api.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

افتح 80/443 فقط حتى ينجح إصدار الشهادة والتجديد. إذا وضعت Cloudflare proxy أمام هذا السجل، استخدم وضع TLS `Full (strict)` واترك أصل الـVM غير متاح للعامة إلا عبر proxy/قواعد جدار ناري معتمدة. استخدم اسم API مستقلًا؛ لا تعدّل توجيه Worker أو أسرار Cloudflare لتشغيل Python.

بديلًا عن Caddy، يمكن استخدام ingress موثوق يعمل على نفس المضيف وينهي TLS ويرسل إلى `127.0.0.1:8000`. لا تستخدم إعدادًا يمرر بيانات الاعتماد عبر HTTP عام، ولا تثق بعناوين proxy عشوائية.

## التحقق من الخدمة

```bash
curl --fail https://api.example.com/health
```

`/health` عام عمدًا لفحوص الحياة ولا يتصل بـOllama. تحقّق أن المسارات الحساسة ترفض الطلب بلا Bearer token:

```bash
curl --include --request POST https://api.example.com/plan \
  --header 'Content-Type: application/json' \
  --data '{"goal":"Read a public page"}'
```

يجب أن تكون النتيجة `401` مع `authentication_required`. أرسل `Authorization: Bearer <API_KEY>` من مدير الأسرار لطلبات `/plan` و`/memory` و`/memory/search`؛ لا تضع قيمة حقيقية في سجل shell أو المستودع. `/docs` و`/openapi.json` متاحان دون مصادقة ولا يحتويان المفتاح. أمثلة عقد الخطأ والتحقق موجودة في [عقد أخطاء API](api-errors.md).

بعد تحميل النموذجين، نفّذ طلب تخطيط منخفض المخاطر عبر HTTPS وتحقق من أن الاستجابة تبقى `dry_run=true` و`execution_enabled=false`. لا يوجد `/execute`.

## حدود الأمان والبيانات

- المصادقة بمفتاح Bearer **مشترك لمشغّل واحد**. كل من يملكه يستطيع التخطيط وإضافة/قراءة الذاكرة باستخدام أي `session_id`؛ المعرّف ليس هوية. لا توزّع هذا المفتاح على مستخدمين أو تضعه داخل JavaScript بالمتصفح. النشر متعدد المستخدمين يحتاج هوية وتفويضًا لكل مستخدم، وعزل ملكية الذاكرة، وحدود معدل قبل إتاحته.
- لا يضيف التطبيق rate limiting. أضف حدًا عند ingress/مزود السحابة قبل السماح لعملاء غير موثوقين.
- `/health` و`/docs` و`/openapi.json` عامة؛ لا تضع معلومات تشغيلية أو أسرارًا فيها.
- الطلبات والذاكرة تعمل على Ollama المحلي داخل شبكة VM. بيانات الذاكرة المخزنة صراحة تبقى في `/app/data`؛ احمِ volume والنسخ الاحتياطية بالتشفير وصلاحيات محدودة. لا توسّع الخدمة إلى أكثر من نسخة API فوق ملف SQLite واحد.
- المفتاح في `CELA_API_KEY` غير ظاهر في `repr(Settings)`، لكنه يظل متغير بيئة داخل الحاوية. احمه بصلاحيات المضيف، ودوّره عبر مدير الأسرار ثم أعد إنشاء الخدمة.
- `ccelia2027` وAPI Python ليسا موصولين؛ لا تستخدم مفتاح API مشتركًا في Worker أو المتصفح دون تصميم ومراجعة أمنية مستقلة.

لأخذ نسخة SQLite متسقة، وإيقاف الخدمات وفحص الحاويات، راجع قسمي [التخزين والترحيل](docker-deployment.md#التخزين-والترحيل) و[التحقق والإيقاف](docker-deployment.md#التحقق-والإيقاف). لإيقاف الخدمات دون حذف البيانات:

```bash
bash ./cela.sh services-stop
```
