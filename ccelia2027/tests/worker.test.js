import assert from "node:assert/strict";
import { describe, it } from "node:test";
import productionWorker, { createWorker, DailyBudget } from "../src/index.js";

const secret = "test-only-session-secret-longer-than-32-characters";
const worker = createWorker((binding) => binding.launch());
const goodPlan = JSON.stringify({
  analysis: "قراءة صفحة عامة ثم تلخيص النص المتاح.",
  steps: [
    {
      action: "NAVIGATE",
      target: "صفحة Example العامة",
      value: "https://example.com/",
      reason: "افتح صفحة Example واقرأ محتواها.",
    },
    {
      action: "OBSERVE",
      target: "نص الصفحة",
      value: "",
      reason: "استخرج النص الظاهر للقراءة.",
    },
  ],
});

function createState() {
  const values = new Map();
  return {
    values,
    storage: {
      get: async (key) => values.get(key),
      put: async (key, value) => values.set(key, structuredClone(value)),
    },
    blockConcurrencyWhile: async (callback) => callback(),
  };
}

function createEnvironment({
  plan = goodPlan,
  aiError,
  summaryError,
  browserError,
  budgetAllowed = true,
  password = "local-test-password",
} = {}) {
  const state = createState();
  const actor = new DailyBudget(state);
  const calls = {
    ai: 0,
    browser: 0,
    browserClosed: 0,
    contextClosed: 0,
    routeDecisions: [],
  };
  const env = {
    APP_ACCESS_PASSWORD: password,
    SESSION_SECRET: secret,
    AI: {
      async run(_model, input) {
        calls.ai += 1;
        if (aiError) throw aiError;
        const isPlanner =
          input.messages[0].content.includes("planning component");
        if (isPlanner) calls.plannerInput = input.messages[1].content;
        else {
          calls.summaryInput = input.messages[1].content;
          if (summaryError) throw summaryError;
        }
        return {
          response: isPlanner ? plan : "تم التحقق من نص الصفحة العامة.",
        };
      },
    },
    BROWSER: {
      async launch() {
        calls.browser += 1;
        return {
          async newContext(options) {
            calls.contextOptions = options;
            let routeHandler;
            return {
              async route(pattern, handler) {
                calls.routePattern = pattern;
                routeHandler = handler;
              },
              async newPage() {
                return {
                  setDefaultNavigationTimeout(value) {
                    calls.navigationTimeout = value;
                  },
                  setDefaultTimeout(value) {
                    calls.actionTimeout = value;
                  },
                  async goto(url, options) {
                    calls.navigation = { url, options };
                    const requests = [
                      { url, method: "GET", type: "document" },
                      {
                        url: "https://example.com/private",
                        method: "POST",
                        type: "fetch",
                      },
                      {
                        url: "https://tracker.example/pixel",
                        method: "GET",
                        type: "image",
                      },
                      {
                        url: "https://example.com/image.png",
                        method: "GET",
                        type: "image",
                      },
                    ];
                    for (const item of requests) {
                      let decision = "pending";
                      await routeHandler(
                        {
                          continue: async () => {
                            decision = "continue";
                          },
                          abort: async () => {
                            decision = "abort";
                          },
                        },
                        {
                          url: () => item.url,
                          method: () => item.method,
                          resourceType: () => item.type,
                        },
                      );
                      calls.routeDecisions.push({ ...item, decision });
                    }
                    assert.equal(
                      calls.routeDecisions.at(-4).decision,
                      "continue",
                    );
                    if (browserError) throw browserError;
                  },
                  async title() {
                    return "Example Domain";
                  },
                  locator(selector) {
                    calls.locator = selector;
                    return {
                      innerText: async () =>
                        "Example Domain\n\nThis domain is for illustrative examples.",
                    };
                  },
                };
              },
              async close() {
                calls.contextClosed += 1;
              },
            };
          },
          async close() {
            calls.browserClosed += 1;
          },
        };
      },
    },
    LOGIN_LIMITER: { limit: async () => ({ success: true }) },
    DAILY_BUDGET: {
      idFromName: () => "global",
      get: () => ({
        fetch: async (input, init) => {
          if (!budgetAllowed) return new Response("blocked", { status: 429 });
          return actor.fetch(new Request(input, init));
        },
      }),
    },
    ASSETS: { fetch: async () => new Response("static app") },
  };
  return { env, calls, actor, state };
}

function post(path, payload, cookie = "", origin = "https://agent.test") {
  const headers = { origin, "content-type": "application/json" };
  if (cookie) headers.cookie = cookie;
  return new Request(`https://agent.test${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
}

async function login(env) {
  const response = await worker.fetch(
    post("/api/login", { password: "local-test-password" }),
    env,
  );
  assert.equal(response.status, 200);
  return response.headers.get("set-cookie").split(";", 1)[0];
}

async function withDnsAnswers(
  addresses,
  callback,
  ipv6Addresses = [],
  status = 0,
) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    assert.equal(url.origin, "https://cloudflare-dns.com");
    assert.equal(url.searchParams.get("name"), "example.com");
    const type = url.searchParams.get("type");
    assert.ok(["A", "AAAA"].includes(type));
    assert.equal(init.redirect, "error");
    if (addresses === null) throw new Error("DNS lookup unavailable");
    const answers = type === "A" ? addresses : ipv6Addresses;
    return new Response(
      JSON.stringify({
        Status: status,
        Answer: answers.map((data) => ({ type: type === "A" ? 1 : 28, data })),
      }),
      { headers: { "content-type": "application/dns-json" } },
    );
  };
  try {
    return await callback();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

describe("Ccelia read-only agent Worker", () => {
  it("keeps health independent of private credentials", async () => {
    const response = await productionWorker.fetch(
      new Request("https://agent.test/health"),
      {},
    );
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      ok: true,
      service: "ccelia2027",
      mode: "read-only-agent",
    });
  });

  it("fails closed until application secrets are available", async () => {
    const { env, calls } = createEnvironment();
    delete env.APP_ACCESS_PASSWORD;
    delete env.SESSION_SECRET;

    const loginResponse = await worker.fetch(
      post("/api/login", { password: "not-used" }),
      env,
    );
    const planResponse = await worker.fetch(
      post("/api/plan", { task: "open https://example.com" }),
      env,
    );
    const executeResponse = await worker.fetch(
      post("/api/execute", { planToken: "not-used" }),
      env,
    );

    assert.deepEqual(
      [loginResponse.status, planResponse.status, executeResponse.status],
      [503, 503, 503],
    );
    assert.equal(calls.ai, 0);
    assert.equal(calls.browser, 0);
  });

  it("rejects an application password below the minimum length", async () => {
    const { env, calls } = createEnvironment({ password: "too-short" });
    const status = await worker.fetch(
      new Request("https://agent.test/api/status"),
      env,
    );
    const loginResponse = await worker.fetch(
      post("/api/login", { password: "too-short" }),
      env,
    );
    const planResponse = await worker.fetch(
      post("/api/plan", { task: "open https://example.com" }),
      env,
    );

    assert.equal((await status.json()).configured, false);
    assert.equal(loginResponse.status, 503);
    assert.equal(planResponse.status, 503);
    assert.equal(calls.ai, 0);
    assert.equal(calls.browser, 0);
  });

  it("serves the static UI through the configured assets binding", async () => {
    const { env } = createEnvironment();
    const response = await worker.fetch(
      new Request("https://agent.test/"),
      env,
    );
    assert.equal(await response.text(), "static app");
  });

  it("requires a session before calling the planner", async () => {
    const { env, calls } = createEnvironment();
    const response = await worker.fetch(
      post("/api/plan", { task: "open https://example.com" }),
      env,
    );
    assert.equal(response.status, 401);
    assert.equal(calls.ai, 0);
  });

  it("rejects cross-origin planning requests", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const response = await worker.fetch(
      post(
        "/api/plan",
        { task: "open https://example.com" },
        cookie,
        "https://attacker.test",
      ),
      env,
    );
    assert.equal(response.status, 403);
    assert.equal(calls.ai, 0);
  });

  it("returns a validated plan from stubbed Workers AI using the CELA action contract", async () => {
    const { env, calls, state } = createEnvironment();
    const cookie = await login(env);
    const response = await worker.fetch(
      post(
        "/api/plan",
        { task: "افتح https://example.com واقرأ الصفحة" },
        cookie,
      ),
      env,
    );
    const value = await response.json();
    assert.equal(response.status, 200);
    assert.equal(value.plan.action, "browse");
    assert.equal(value.plan.url, "https://example.com/");
    assert.equal(value.plan.mode, "read-only");
    assert.deepEqual(
      value.plan.steps.map((step) => step.action),
      ["NAVIGATE", "OBSERVE"],
    );
    assert.deepEqual(JSON.parse(calls.plannerInput), {
      task: "افتح https://example.com واقرأ الصفحة",
    });
    assert.ok(value.planToken);
    assert.equal(value.model, "@cf/meta/llama-3.2-3b-instruct");
    assert.equal(calls.ai, 1);
    assert.equal((await state.storage.get("daily_usage")).aiCalls, 1);
  });

  it("rejects malformed or unsafe plans before execution", async () => {
    for (const plan of [
      "not json",
      goodPlan.replace("https://example.com/", "http://127.0.0.1/"),
    ]) {
      const { env } = createEnvironment({ plan });
      const cookie = await login(env);
      const response = await worker.fetch(
        post("/api/plan", { task: "افتح الصفحة" }, cookie),
        env,
      );
      assert.equal(response.status, 502);
      assert.equal((await response.json()).planToken, undefined);
    }
  });

  it("rejects a navigation URL that the user did not request", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const response = await worker.fetch(
      post("/api/plan", { task: "اقرأ صفحة عامة" }, cookie),
      env,
    );

    assert.equal(response.status, 422);
    assert.equal((await response.json()).planToken, undefined);
    assert.equal(calls.browser, 0);
  });

  it("matches URLs followed by Arabic punctuation", async () => {
    const { env } = createEnvironment();
    const cookie = await login(env);
    const response = await worker.fetch(
      post(
        "/api/plan",
        { task: "افتح https://example.com، واقرأ الصفحة" },
        cookie,
      ),
      env,
    );

    assert.equal(response.status, 200);
  });

  it("requires separate confirmation for sensitive requests", async () => {
    const searchPlan = JSON.stringify({
      analysis: "البحث عن معلومة عامة.",
      steps: [
        {
          action: "SEARCH_WEB",
          target: "محرك بحث عام",
          value: "إرسال بريد إلكتروني",
          reason: "قراءة نتائج عامة عن إرسال البريد.",
        },
        {
          action: "OBSERVE",
          target: "نتائج عامة",
          value: "",
          reason: "استخراج النص المقروء فقط.",
        },
      ],
    });
    const { env, state } = createEnvironment({ plan: searchPlan });
    const cookie = await login(env);
    const planned = await worker.fetch(
      post("/api/plan", { task: "ابحث عن طريقة إرسال بريد" }, cookie),
      env,
    );
    const value = await planned.json();
    assert.equal(value.plan.requiresConfirmation, true);
    const response = await worker.fetch(
      post("/api/execute", { planToken: value.planToken }, cookie),
      env,
    );
    assert.equal(response.status, 409);
    assert.equal((await state.storage.get("daily_usage")).browserRuns || 0, 0);
  });

  it("uses a short-lived Playwright session with same-origin read-only requests", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const task = "افتح https://example.com واقرأ الصفحة";
    const planned = await worker.fetch(
      post("/api/plan", { task }, cookie),
      env,
    );
    const plan = await planned.json();
    const response = await withDnsAnswers(
      ["93.184.216.34"],
      () =>
        worker.fetch(
          post("/api/execute", { planToken: plan.planToken }, cookie),
          env,
        ),
      ["2606:4700:4700::1111"],
    );
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.equal(calls.browser, 1);
    assert.equal(calls.navigation.url, "https://example.com/");
    assert.equal(calls.navigation.options.waitUntil, "domcontentloaded");
    assert.equal(calls.navigation.options.timeout, 12_000);
    assert.equal(calls.contextOptions.javaScriptEnabled, false);
    assert.equal(calls.contextOptions.serviceWorkers, "block");
    assert.equal(calls.routePattern, "**/*");
    assert.deepEqual(
      calls.routeDecisions.map((item) => item.decision),
      ["continue", "abort", "abort", "abort"],
    );
    assert.equal(calls.locator, "body");
    assert.equal(calls.contextClosed, 1);
    assert.equal(calls.browserClosed, 1);
    assert.match(result.answer, /تم التحقق/);
    assert.equal(result.evidence.title, "Example Domain");
    assert.equal(result.evidence.reservedBrowserMs, 120_000);
    assert.equal(JSON.parse(calls.summaryInput).goal, task);
    assert.equal(
      JSON.parse(calls.summaryInput).plan_reason,
      "افتح صفحة Example واقرأ محتواها.",
    );
    assert.ok(result.steps.some((step) => step.includes("Playwright")));
  });

  it("supports a public IPv6-only domain", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const planned = await worker.fetch(
      post(
        "/api/plan",
        { task: "افتح https://example.com واقرأ الصفحة" },
        cookie,
      ),
      env,
    );
    const plan = await planned.json();
    const response = await withDnsAnswers(
      [],
      () =>
        worker.fetch(
          post("/api/execute", { planToken: plan.planToken }, cookie),
          env,
        ),
      ["2606:4700:4700::1111"],
    );

    assert.equal(response.status, 200);
    assert.equal(calls.browser, 1);
  });

  it("returns extracted evidence when Workers AI cannot summarize it", async () => {
    const { env } = createEnvironment({
      summaryError: new Error("Workers AI unavailable"),
    });
    const cookie = await login(env);
    const planned = await worker.fetch(
      post(
        "/api/plan",
        { task: "افتح https://example.com واقرأ الصفحة" },
        cookie,
      ),
      env,
    );
    const plan = await planned.json();
    const response = await withDnsAnswers(["93.184.216.34"], () =>
      worker.fetch(
        post("/api/execute", { planToken: plan.planToken }, cookie),
        env,
      ),
    );
    const result = await response.json();

    assert.equal(response.status, 503);
    assert.equal(result.evidence.title, "Example Domain");
    assert.match(result.evidence.excerpt, /This domain is for illustrative/);
    assert.equal(result.steps.length, 3);
  });

  it("closes a failed browser session and keeps its full safety reservation", async () => {
    const { env, calls, state } = createEnvironment({
      browserError: new Error("navigation failed"),
    });
    const cookie = await login(env);
    const planned = await worker.fetch(
      post(
        "/api/plan",
        { task: "افتح https://example.com واقرأ الصفحة" },
        cookie,
      ),
      env,
    );
    const plan = await planned.json();
    const response = await withDnsAnswers(["93.184.216.34"], () =>
      worker.fetch(
        post("/api/execute", { planToken: plan.planToken }, cookie),
        env,
      ),
    );
    assert.equal(response.status, 502);
    assert.equal(calls.contextClosed, 1);
    assert.equal(calls.browserClosed, 1);
    const usage = await state.storage.get("daily_usage");
    assert.equal(usage.browserMs, 120_000);
    assert.deepEqual(usage.browserReservations, {});
  });

  it("fails closed on private, mixed, or unavailable DNS answers", async () => {
    for (const [addresses, ipv6Addresses, status, expectedCode] of [
      [["10.0.0.8"], [], 0, "non_public"],
      [["93.184.216.34", "169.254.169.254"], [], 0, "non_public"],
      [["93.184.216.34"], ["fd00::1"], 0, "non_public"],
      [["93.184.216.34"], ["2001:db8::1"], 0, "non_public"],
      [[], [], 0, "no_addresses"],
      [[], [], 3, "not_found"],
      [null, [], 0, "lookup_failed"],
    ]) {
      const { env, calls, state } = createEnvironment();
      const cookie = await login(env);
      const planned = await worker.fetch(
        post(
          "/api/plan",
          { task: "افتح https://example.com واقرأ الصفحة" },
          cookie,
        ),
        env,
      );
      const plan = await planned.json();
      const response = await withDnsAnswers(
        addresses,
        () =>
          worker.fetch(
            post("/api/execute", { planToken: plan.planToken }, cookie),
            env,
          ),
        ipv6Addresses,
        status,
      );
      const result = await response.json();
      assert.equal(response.status, 400);
      assert.equal(result.code, expectedCode);
      assert.equal(calls.browser, 0);
      assert.equal(
        (await state.storage.get("daily_usage")).browserRuns || 0,
        0,
      );
    }
  });

  it("stops at the daily internal budget before invoking AI", async () => {
    const { env, calls, actor } = createEnvironment();
    for (let index = 0; index < 28; index += 1) {
      const response = await actor.fetch(post("/reserve", { kind: "ai" }));
      assert.equal(response.status, 200);
    }
    const cookie = await login(env);
    const response = await worker.fetch(
      post("/api/plan", { task: "open https://example.com" }, cookie),
      env,
    );
    assert.equal(response.status, 429);
    assert.equal(calls.ai, 0);
  });

  it("maps provider quota errors to a closed 429 without fallback", async () => {
    const { env } = createEnvironment({
      aiError: new Error("429 neuron quota exceeded"),
    });
    const cookie = await login(env);
    const response = await worker.fetch(
      post("/api/plan", { task: "open https://example.com" }, cookie),
      env,
    );
    assert.equal(response.status, 429);
    assert.match((await response.json()).error, /بلا ترقية أو مزود بديل/);
  });

  it("enforces the 20-second Browser Run session interval", async () => {
    const actor = new DailyBudget(createState());
    assert.equal(
      (
        await actor.fetch(
          post("/reserve", { kind: "browser", reservationId: "run-1" }),
        )
      ).status,
      200,
    );
    assert.equal(
      (
        await actor.fetch(
          post("/reserve", { kind: "browser", reservationId: "run-2" }),
        )
      ).status,
      429,
    );
  });

  it("reserves two minutes and releases the concurrency slot after a session", async () => {
    const state = createState();
    const actor = new DailyBudget(state);
    assert.equal(
      (
        await actor.fetch(
          post("/reserve", { kind: "browser", reservationId: "run-1" }),
        )
      ).status,
      200,
    );
    const response = await actor.fetch(
      post("/release", { reservationId: "run-1" }),
    );
    assert.equal(response.status, 200);
    const usage = await state.storage.get("daily_usage");
    assert.equal(usage.browserMs, 120_000);
    assert.deepEqual(usage.browserReservations, {});
  });

  it("does not reserve more than three concurrent Browser Run sessions", async () => {
    const state = createState();
    const actor = new DailyBudget(state);
    for (let index = 0; index < 3; index += 1) {
      if (index > 0) {
        const usage = await state.storage.get("daily_usage");
        usage.lastBrowserAt = Date.now() - 20_000;
        await state.storage.put("daily_usage", usage);
      }
      assert.equal(
        (
          await actor.fetch(
            post("/reserve", {
              kind: "browser",
              reservationId: `run-${index}`,
            }),
          )
        ).status,
        200,
      );
    }
    const usage = await state.storage.get("daily_usage");
    usage.lastBrowserAt = Date.now() - 20_000;
    await state.storage.put("daily_usage", usage);
    assert.equal(
      (
        await actor.fetch(
          post("/reserve", { kind: "browser", reservationId: "run-4" }),
        )
      ).status,
      429,
    );
  });

  it("enforces the browser daily cap at Cloudflare's published free limit", async () => {
    const state = createState();
    const actor = new DailyBudget(state);
    for (let index = 0; index < 5; index += 1) {
      if (index > 0) {
        const usage = await state.storage.get("daily_usage");
        usage.lastBrowserAt = Date.now() - 20_000;
        await state.storage.put("daily_usage", usage);
      }
      const reservationId = `run-${index}`;
      assert.equal(
        (
          await actor.fetch(
            post("/reserve", { kind: "browser", reservationId }),
          )
        ).status,
        200,
      );
      assert.equal(
        (await actor.fetch(post("/release", { reservationId }))).status,
        200,
      );
    }
    const beforeEleventh = await state.storage.get("daily_usage");
    beforeEleventh.lastBrowserAt = Date.now() - 20_000;
    await state.storage.put("daily_usage", beforeEleventh);
    assert.equal(
      (
        await actor.fetch(
          post("/reserve", { kind: "browser", reservationId: "run-6" }),
        )
      ).status,
      429,
    );
    const usage = await state.storage.get("daily_usage");
    assert.equal(usage.browserRuns, 5);
    assert.equal(usage.browserMs, 600_000);
  });
});
