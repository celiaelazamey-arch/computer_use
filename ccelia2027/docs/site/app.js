const thread = document.querySelector("#thread");
const form = document.querySelector("#composer");
const taskBox = document.querySelector("#task");
const sendButton = document.querySelector("#send");
const emptyState = document.querySelector("#empty-state");
const historyList = document.querySelector("#history-list");
const activeTokens = new Map();
const STORAGE_KEY = "cela-sh-browser-history-v1";
const LEGACY_STORAGE_KEY = "manus-mini-browser-history-v1";
let busy = false;
let controller;
let messages = [];

try {
  const currentHistory = localStorage.getItem(STORAGE_KEY);
  const legacyHistory = currentHistory
    ? null
    : localStorage.getItem(LEGACY_STORAGE_KEY);
  const saved = JSON.parse(currentHistory || legacyHistory || "[]");
  if (Array.isArray(saved))
    messages = saved
      .filter(
        (item) =>
          item &&
          ["user", "assistant"].includes(item.role) &&
          typeof item.content === "string",
      )
      .slice(-40);
  if (!currentHistory && legacyHistory)
    localStorage.setItem(STORAGE_KEY, legacyHistory);
} catch {
  messages = [];
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-40)));
  } catch {
    // History is a convenience, not state: private browsing or a full quota
    // must not break the conversation, so the failure is deliberately ignored.
  }
}

function renderHistory() {
  const recent = messages
    .filter((item) => item.role === "user")
    .slice(-6)
    .reverse();
  historyList.replaceChildren();
  document.querySelector("#history-count").textContent = String(
    recent.length,
  ).padStart(2, "0");
  document.querySelector("#history-empty").hidden = recent.length > 0;
  for (const item of recent) {
    const button = el("button", "history-item");
    button.type = "button";
    button.title = item.content;
    const dot = el("span", "history-dot");
    dot.setAttribute("aria-hidden", "true");
    button.append(dot, el("span", "history-text", item.content));
    button.addEventListener("click", () => {
      taskBox.value = item.content;
      taskBox.focus();
    });
    historyList.append(button);
  }
}

function setTraceStep(id, status, stateClass = "") {
  const step = document.querySelector(`#${id}`);
  step.classList.toggle("is-done", stateClass === "done");
  step.classList.toggle("is-active", stateClass === "active");
  step.classList.toggle("is-blocked", stateClass === "blocked");
  step.classList.toggle("is-error", stateClass === "error");
  document.querySelector(`#${id}-state`).textContent = status;
}

function renderRuntime() {
  const latestUser = [...messages]
    .reverse()
    .find((item) => item.role === "user");
  const latestAssistant = [...messages]
    .reverse()
    .find((item) => item.role === "assistant");
  const hasPlan = Boolean(latestAssistant?.plan);
  const result = latestAssistant?.result;
  const hasResult = Boolean(result);
  const hasError = Boolean(latestAssistant?.error || result?.error);
  const requestBox = document.querySelector("#runtime-request");
  const runtimeState = document.querySelector("#runtime-state");
  const runtimeSummary = document.querySelector("#runtime-summary");
  const runtimeTarget = document.querySelector("#runtime-target");
  const runtimeOutcome = document.querySelector("#runtime-outcome");
  const outputBadge = document.querySelector("#runtime-output-badge");
  const method = document.querySelector("#runtime-method");

  requestBox.textContent = latestUser
    ? JSON.stringify({ task: latestUser.content }, null, 2)
    : '{\n  "task": "waiting for a public page request"\n}';
  method.textContent = latestUser ? "POST /api/plan" : "IDLE";
  runtimeTarget.textContent = "لا توجد مهمة حاليًا.";
  runtimeOutcome.textContent =
    "لن يبدأ Browser Run قبل مراجعة الخطة والموافقة عليها.";
  outputBadge.textContent = "EMPTY";
  document.querySelector("#runtime-output-label").textContent =
    "Plan target / result";

  if (!latestUser) {
    runtimeState.textContent = "جاهز لاستقبال مهمة";
    runtimeSummary.textContent =
      "تظهر هنا تفاصيل الطلب والخطة ونتيجة القراءة الفعلية.";
    setTraceStep("planner-step", "بانتظار مهمة", "");
    setTraceStep("safety-step", "لم تبدأ", "");
    setTraceStep("browser-step", "لم يبدأ", "");
    return;
  }

  if (busy && !hasPlan) {
    runtimeState.textContent = "جارٍ إنشاء الخطة";
    runtimeSummary.textContent =
      "لم يبدأ Browser Run؛ الطلب قيد المراجعة الآلية.";
    setTraceStep("planner-step", "قيد التنفيذ", "active");
    setTraceStep("safety-step", "بانتظار مخرجات الخطة", "");
    setTraceStep("browser-step", "لم يبدأ", "");
  } else if (hasError && !hasPlan) {
    runtimeState.textContent = "توقفت قبل التشغيل";
    runtimeSummary.textContent = "تعذر اعتماد خطة آمنة. لم يبدأ المتصفح.";
    setTraceStep("planner-step", "لم ينتج مخططًا صالحًا", "error");
    setTraceStep("safety-step", "محظور", "blocked");
    setTraceStep("browser-step", "لم يبدأ", "");
    runtimeOutcome.textContent =
      latestAssistant?.content || result?.error || "تعذر إكمال الطلب.";
    outputBadge.textContent = "BLOCKED";
  } else if (hasPlan && busy) {
    runtimeState.textContent = "جارٍ تنفيذ قراءة عامة";
    runtimeSummary.textContent = "بدأ التنفيذ بعد تأكيد الخطة في الواجهة.";
    setTraceStep("planner-step", "خطة جاهزة", "done");
    setTraceStep("safety-step", "تمت المراجعة", "done");
    setTraceStep("browser-step", "جارٍ التشغيل", "active");
  } else if (hasPlan && !hasResult) {
    runtimeState.textContent = "الخطة بانتظار موافقتك";
    runtimeSummary.textContent = "تحقق من عنوان الوجهة قبل بدء Browser Run.";
    setTraceStep("planner-step", "اكتملت الخطة", "done");
    setTraceStep("safety-step", "مراجعة المستخدم مطلوبة", "active");
    setTraceStep("browser-step", "لم يبدأ", "");
    runtimeTarget.textContent =
      latestAssistant.plan.action === "search"
        ? `بحث عام: ${latestAssistant.plan.query}`
        : latestAssistant.plan.url;
    runtimeOutcome.textContent =
      "لم يبدأ Browser Run. راجع الخطة في المحادثة قبل الموافقة.";
    outputBadge.textContent = "REVIEW";
  } else if (hasResult && hasError) {
    runtimeState.textContent = "توقفت المهمة بأمان";
    runtimeSummary.textContent = "أوقفت الحماية المهمة قبل إكمالها.";
    setTraceStep("planner-step", "اكتملت الخطة", "done");
    setTraceStep("safety-step", "تمت المراجعة", "done");
    setTraceStep("browser-step", "توقف مع خطأ", "error");
    runtimeOutcome.textContent = result.error || "تعذر تنفيذ القراءة بأمان.";
    outputBadge.textContent = "STOPPED";
  } else if (hasResult) {
    runtimeState.textContent = "اكتملت القراءة";
    runtimeSummary.textContent =
      "انتهى Browser Run؛ راجع الملخص والدليل في المحادثة.";
    setTraceStep("planner-step", "اكتملت الخطة", "done");
    setTraceStep("safety-step", "تمت المراجعة", "done");
    setTraceStep("browser-step", "اكتمل", "done");
    runtimeOutcome.textContent = result.answer || "اكتملت القراءة العامة.";
    outputBadge.textContent = "DONE";
  }

  if (hasPlan) {
    method.textContent = "POST /api/plan → /api/execute";
    document.querySelector("#runtime-output-label").textContent =
      "Plan target / result";
    runtimeTarget.textContent =
      latestAssistant.plan.action === "search"
        ? `بحث عام: ${latestAssistant.plan.query}`
        : latestAssistant.plan.url;
    outputBadge.textContent = hasResult
      ? hasError
        ? "STOPPED"
        : "DONE"
      : "REVIEW";
    if (hasResult)
      runtimeOutcome.textContent =
        result.answer || result.error || "انتهت المهمة.";
  }
}

function setBusy(value, stopped = false) {
  busy = value;
  sendButton.disabled = value;
  sendButton.setAttribute("aria-busy", String(value));
  sendButton.innerHTML = value
    ? '<span class="spinner" aria-hidden="true"></span>'
    : '<span class="icon icon-arrow-up" aria-hidden="true"></span>';
  const pill = document.querySelector("#service-pill");
  pill.textContent = value
    ? "المهمة قيد التنفيذ"
    : stopped
      ? "توقفت المهمة"
      : "الخدمات مهيأة";
  pill.className = `service-pill${stopped ? " offline" : ""}${value ? " working" : ""}`;
}

function render() {
  thread.replaceChildren();
  emptyState.hidden = messages.length > 0;
  renderHistory();
  for (const message of messages) {
    const card = el("article", `message ${message.role}`);
    const label = el(
      "div",
      "message-label",
      message.role === "user" ? "أنت" : "Cela.sh",
    );
    card.append(
      label,
      el("div", "content", message.content || "جارٍ العمل..."),
    );
    if (message.plan) {
      const plan = el("section", "plan");
      plan.append(el("h3", "", "خطة مقترحة للمراجعة"));
      plan.append(el("p", "", message.plan.analysis));
      plan.append(
        el(
          "p",
          "meta",
          message.plan.action === "search"
            ? `بحث على الويب: ${message.plan.query}`
            : `الموقع: ${message.plan.url}`,
        ),
      );
      plan.append(el("p", "meta", `السبب: ${message.plan.reason}`));
      plan.append(
        el("p", "meta", "التنفيذ المسموح: فتح صفحة عامة وقراءة النص فقط."),
      );
      const row = el("div", "action-row");
      let approved = false;
      if (message.plan.requiresConfirmation) {
        const confirmLabel = el("label", "confirm-line");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.addEventListener("change", () => {
          approved = checkbox.checked;
          button.disabled = !approved || busy;
        });
        confirmLabel.append(
          checkbox,
          el(
            "span",
            "",
            "أؤكد أن التنفيذ للقراءة العامة فقط، دون إرسال أو تغيير.",
          ),
        );
        plan.append(confirmLabel);
      }
      const button = el(
        "button",
        "primary",
        activeTokens.has(message.id)
          ? "راجع ثم نفّذ القراءة"
          : "انتهت صلاحية الخطة؛ أنشئ خطة جديدة",
      );
      button.type = "button";
      button.disabled =
        !activeTokens.has(message.id) ||
        busy ||
        (message.plan.requiresConfirmation && !approved);
      button.addEventListener("click", () =>
        runPlan(message.id, button, approved),
      );
      row.append(button);
      plan.append(row);
      card.append(plan);
    }
    if (message.result) {
      const resultCard = el("section", "result");
      resultCard.append(el("h3", "", "النتيجة"));
      resultCard.append(
        el(
          "p",
          "",
          message.result.answer ||
            message.result.error ||
            "انتهت المهمة دون ملخص.",
        ),
      );
      if (Array.isArray(message.result.steps)) {
        const list = el("ol", "steps");
        for (const step of message.result.steps)
          list.append(el("li", "", step));
        resultCard.append(list);
      }
      if (message.result.evidence) {
        const evidence = el("div", "evidence");
        evidence.append(
          el("b", "", message.result.evidence.title || "دليل الصفحة"),
        );
        const link = el("a", "", message.result.evidence.url);
        link.href = message.result.evidence.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        evidence.append(document.createElement("br"), link);
        if (message.result.evidence.reservedBrowserMs)
          evidence.append(
            el(
              "p",
              "meta",
              `ميزانية القراءة المحجوزة: حتى ${Math.round(message.result.evidence.reservedBrowserMs / 1000)} ثانية.`,
            ),
          );
        evidence.append(
          el("blockquote", "", message.result.evidence.excerpt || ""),
        );
        resultCard.append(evidence);
      }
      card.append(resultCard);
    }
    thread.append(card);
  }
  thread.scrollTop = thread.scrollHeight;
  renderRuntime();
  persist();
}

function setActiveNav(id) {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.id === id);
  });
}

async function requestJson(path, payload, signal) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal,
    credentials: "same-origin",
  });
  const result = await response.json().catch(() => ({}));
  if (response.status === 401)
    document.querySelector("#login-layer").hidden = false;
  if (!response.ok)
    throw new Error(result.error || `تعذر إكمال الطلب (${response.status}).`);
  return result;
}

async function makePlan(value) {
  if (busy) return;
  const task = value.trim();
  if (!task) return;
  messages.push({ id: crypto.randomUUID(), role: "user", content: task });
  const assistant = {
    id: crypto.randomUUID(),
    role: "assistant",
    content: "",
  };
  messages.push(assistant);
  taskBox.value = "";
  setBusy(true);
  render();
  controller = new AbortController();
  try {
    const data = await requestJson("/api/plan", { task }, controller.signal);
    assistant.content = "راجع الخطة أدناه. لن يبدأ المتصفح تلقائيًا.";
    assistant.plan = data.plan;
    activeTokens.set(assistant.id, data.planToken);
    document.querySelector("#model").textContent =
      data.planner ||
      (data.provider === "nvidia"
        ? `NVIDIA NIM · ${data.model}`
        : data.model || "غير متاح");
  } catch (error) {
    assistant.content = error.message || "تعذر إنشاء الخطة.";
    assistant.error = true;
  } finally {
    controller = null;
    setBusy(false, Boolean(assistant.error));
    render();
  }
}

async function runPlan(id, button, confirmed) {
  const token = activeTokens.get(id);
  if (!token || busy) return;
  const message = messages.find((item) => item.id === id);
  if (!message) return;
  setBusy(true);
  button.disabled = true;
  message.content = "المتصفح يفتح الصفحة العامة ويقرأها الآن.";
  render();
  controller = new AbortController();
  try {
    const result = await requestJson(
      "/api/execute",
      { planToken: token, confirmed },
      controller.signal,
    );
    message.result = result;
    activeTokens.delete(id);
    document.querySelector("#model").textContent =
      result.provider === "nvidia"
        ? `NVIDIA NIM · ${result.model}`
        : result.model || "Workers AI";
  } catch (error) {
    message.result = {
      error: error.message || "تعذر تنفيذ المهمة بأمان.",
    };
  } finally {
    controller = null;
    setBusy(false, Boolean(message.result?.error));
    render();
  }
}

function setActiveBrowserTab(index) {
  document.querySelectorAll(".chrome-tab[role='tab']").forEach((tab, i) => {
    const selected = i === index;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
}

function closeMobileNavigation() {
  document.querySelector("#app-shell").classList.remove("nav-open");
  document
    .querySelector("#mobile-nav-toggle")
    .setAttribute("aria-expanded", "false");
}

document.querySelector("#new-task-nav").addEventListener("click", () => {
  setActiveNav("new-task-nav");
  setActiveBrowserTab(0);
  closeMobileNavigation();
  document
    .querySelector("#composer")
    .scrollIntoView({ behavior: "smooth", block: "center" });
  taskBox.focus();
});
document.querySelector("#history-nav").addEventListener("click", () => {
  setActiveNav("history-nav");
  setActiveBrowserTab(1);
  closeMobileNavigation();
  document
    .querySelector("#recent-section")
    .scrollIntoView({ behavior: "smooth", block: "center" });
});
document.querySelector("#safety-nav").addEventListener("click", () => {
  setActiveNav("safety-nav");
  closeMobileNavigation();
  document
    .querySelector("#safety-note")
    .scrollIntoView({ behavior: "smooth", block: "center" });
});
document.querySelector("#activity-tab").addEventListener("click", () => {
  document.querySelector("#history-nav").click();
});
window.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    document.querySelector("#new-task-nav").click();
  }
});
document.querySelectorAll("[data-task]").forEach((button) => {
  button.addEventListener("click", () => {
    taskBox.value = button.dataset.task;
    taskBox.focus();
  });
});
form.addEventListener("submit", (event) => {
  event.preventDefault();
  makePlan(taskBox.value);
});
document
  .querySelector("#login-form")
  .addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.querySelector("#login-error");
    error.textContent = "";
    try {
      await requestJson("/api/login", {
        password: document.querySelector("#password").value,
      });
      document.querySelector("#password").value = "";
      document.querySelector("#login-layer").hidden = true;
      document.querySelector("#logout").hidden = false;
    } catch (failure) {
      error.textContent = failure.message;
    }
  });
document.querySelector("#logout").addEventListener("click", async () => {
  try {
    await requestJson("/api/logout", {});
  } finally {
    location.reload();
  }
});
document
  .querySelector("#browser-reload")
  .addEventListener("click", () => location.reload());
document.querySelector("#copy-origin").addEventListener("click", async () => {
  const button = document.querySelector("#copy-origin");
  try {
    await navigator.clipboard.writeText(location.origin);
    button.title = "تم نسخ عنوان التطبيق";
  } catch {
    button.title = location.origin;
  }
});
document
  .querySelector("#mobile-runtime-toggle")
  .addEventListener("click", (event) => {
    const shell = document.querySelector("#app-shell");
    const open = shell.classList.toggle("runtime-open");
    if (open) closeMobileNavigation();
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });
document
  .querySelector("#mobile-nav-toggle")
  .addEventListener("click", (event) => {
    const shell = document.querySelector("#app-shell");
    const open = shell.classList.toggle("nav-open");
    if (open) {
      shell.classList.remove("runtime-open");
      document
        .querySelector("#mobile-runtime-toggle")
        .setAttribute("aria-expanded", "false");
    }
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });
document.addEventListener("click", (event) => {
  const shell = document.querySelector("#app-shell");
  if (
    shell.classList.contains("nav-open") &&
    !event.target.closest("#sidebar") &&
    !event.target.closest("#mobile-nav-toggle")
  ) {
    closeMobileNavigation();
  }
});

let demoProgress = 24;
let demoTimer = null;
const progressFill = document.querySelector("#demo-progress-fill");
const progressBar = document.querySelector(".demo-progress");
const progressText = document.querySelector("#demo-progress-text");
const playButton = document.querySelector("#demo-play");
function updateDemoProgress(value) {
  demoProgress = Math.max(0, Math.min(100, value));
  progressFill.style.width = `${demoProgress}%`;
  progressBar.setAttribute("aria-valuenow", String(demoProgress));
  progressText.textContent = `${demoProgress}%`;
}
function stopDemo() {
  window.clearInterval(demoTimer);
  demoTimer = null;
  playButton.innerHTML =
    '<span class="icon icon-play" aria-hidden="true"></span>';
  playButton.setAttribute("aria-label", "تشغيل المثال");
}
document
  .querySelector("#demo-rewind")
  .addEventListener("click", () => updateDemoProgress(demoProgress - 20));
document
  .querySelector("#demo-forward")
  .addEventListener("click", () => updateDemoProgress(demoProgress + 20));
playButton.addEventListener("click", () => {
  if (demoTimer) {
    stopDemo();
    return;
  }
  playButton.innerHTML =
    '<span class="icon icon-pause" aria-hidden="true"></span>';
  playButton.setAttribute("aria-label", "إيقاف المثال");
  demoTimer = window.setInterval(() => {
    if (demoProgress >= 100) {
      updateDemoProgress(0);
    } else {
      updateDemoProgress(demoProgress + 4);
    }
  }, 450);
});

async function initialize() {
  render();
  document.querySelector("#origin-label").textContent = location.host;
  try {
    const status = await fetch("/api/status", { cache: "no-store" }).then(
      (response) => response.json(),
    );
    document.querySelector("#model").textContent =
      status.provider === "nvidia"
        ? `NVIDIA NIM · ${status.model}`
        : status.model || "غير متاح";
    const pill = document.querySelector("#service-pill");
    pill.textContent = status.configured ? "الخدمات مهيأة" : "الإعداد ناقص";
    pill.className = `service-pill${status.configured ? "" : " offline"}`;
    if (!status.configured) {
      const notice = document.querySelector("#config");
      notice.hidden = false;
      notice.textContent =
        "أضف إعدادات الأسرار والربط قبل الاستخدام. لا تُرسل أي مهمة الآن.";
    }
    const session = await fetch("/api/session", {
      cache: "no-store",
    }).then((response) => response.json());
    if (session.authenticated) document.querySelector("#logout").hidden = false;
    else document.querySelector("#login-layer").hidden = false;
  } catch {
    document.querySelector("#service-pill").textContent = "الخدمة غير متاحة";
    document.querySelector("#model").textContent = "غير متاح";
    document.querySelector("#config").hidden = false;
    document.querySelector("#config").textContent =
      "تعذر فحص الخدمات. لم يبدأ أي تنفيذ.";
  }
}

render();
initialize();
