import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
  // Durable Objects run blockConcurrencyWhile callbacks one at a time. The stub
  // must do the same, or concurrency tests would prove nothing.
  let queue = Promise.resolve();
  return {
    values,
    storage: {
      get: async (key) => values.get(key),
      put: async (key, value) => values.set(key, structuredClone(value)),
      delete: async (key) => values.delete(key),
      list: async ({ prefix = "" } = {}) =>
        new Map([...values].filter(([key]) => key.startsWith(prefix))),
    },
    blockConcurrencyWhile(callback) {
      const run = queue.then(callback);
      queue = run.catch(() => {});
      return run;
    },
  };
}

function createEnvironment({
  plan = goodPlan,
  aiError,
  summaryError,
  browserError,
  budgetAllowed = true,
  budgetFailsOn,
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
          if (
            budgetFailsOn &&
            new URL(String(input)).pathname === budgetFailsOn
          )
            throw new Error("ledger unavailable");
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

/**
 * Sign an arbitrary payload with the test secret, exactly the way the Worker
 * does. Used to prove that a valid signature alone is not enough: the token
 * must also declare its own type.
 */
async function signToken(payload) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const body = Buffer.from(encoder.encode(JSON.stringify(payload))).toString(
    "base64url",
  );
  const signed = await crypto.subtle.sign("HMAC", key, encoder.encode(body));
  return `${body}.${Buffer.from(signed).toString("base64url")}`;
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

  it("rejects a plan token replayed as a session cookie", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const planResponse = await worker.fetch(
      post("/api/plan", { task: "open https://example.com" }, cookie),
      env,
    );
    const { planToken } = await planResponse.json();
    assert.ok(planToken, "the plan token is handed to the browser");

    // Log out, then replay the plan token where a session cookie belongs.
    // Both tokens are signed with SESSION_SECRET, so only the typ claim
    // separates them.
    await worker.fetch(post("/api/logout", {}), env);
    const forged = `ccelia_session=${planToken}`;
    const session = await worker.fetch(
      new Request("https://agent.test/api/session", {
        headers: { cookie: forged },
      }),
      env,
    );
    assert.deepEqual(await session.json(), { authenticated: false });

    const aiCallsBefore = calls.ai;
    const plan = await worker.fetch(
      post("/api/plan", { task: "open https://example.com" }, forged),
      env,
    );
    assert.equal(plan.status, 401);
    assert.equal(
      calls.ai,
      aiCallsBefore,
      "a replayed plan token must not reach the model",
    );
  });

  it("rejects a session cookie replayed as a plan token", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const sessionToken = cookie.split("=")[1];
    const response = await worker.fetch(
      post("/api/execute", { planToken: sessionToken }, cookie),
      env,
    );
    assert.equal(response.status, 400);
    assert.equal(calls.ai, 0);
    assert.equal(calls.browser, 0);
  });

  it("rejects a correctly signed token that declares no type", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const typeless = await signToken({ exp: Date.now() + 60_000 });

    const session = await worker.fetch(
      new Request("https://agent.test/api/session", {
        headers: { cookie: `ccelia_session=${typeless}` },
      }),
      env,
    );
    assert.deepEqual(await session.json(), { authenticated: false });

    const execute = await worker.fetch(
      post("/api/execute", { planToken: typeless }, cookie),
      env,
    );
    assert.equal(execute.status, 400);
    assert.equal(calls.browser, 0);
  });

  it("adds security headers to every JSON response", async () => {
    const { env } = createEnvironment();
    const requests = [
      new Request("https://agent.test/health"),
      new Request("https://agent.test/api/status"),
      post("/api/plan", { task: "open https://example.com" }),
    ];
    for (const request of requests) {
      const response = await worker.fetch(request, env);
      const policy = response.headers.get("content-security-policy") || "";
      assert.equal(
        response.headers.get("x-content-type-options"),
        "nosniff",
        request.url,
      );
      assert.equal(response.headers.get("x-frame-options"), "DENY");
      assert.equal(response.headers.get("referrer-policy"), "no-referrer");
      assert.match(policy, /default-src 'none'/);
      assert.match(policy, /frame-ancestors 'none'/);
    }
  });

  it("serves assets under a strict header policy with no inline script", () => {
    const raw = readFileSync(
      new URL("../docs/site/_headers", import.meta.url),
      "utf8",
    );
    // Only directives count; `#` comments may explain what is deliberately absent.
    const headers = raw
      .split("\n")
      .filter((line) => !line.trim().startsWith("#"))
      .join("\n");
    assert.match(headers, /script-src 'self'/);
    assert.match(headers, /connect-src 'self'/);
    assert.match(headers, /frame-ancestors 'none'/);
    assert.match(headers, /X-Content-Type-Options: nosniff/i);
    assert.ok(
      !headers.includes("unsafe-inline") && !headers.includes("unsafe-eval"),
      "the asset CSP must not weaken itself for inline code",
    );

    const html = readFileSync(
      new URL("../docs/site/index.html", import.meta.url),
      "utf8",
    );
    const scriptTags = html.match(/<script\b[^>]*>/gi) || [];
    assert.ok(scriptTags.length > 0, "the UI must still load its script");
    for (const tag of scriptTags) {
      assert.match(
        tag,
        /src="\/app\.js"/,
        `every script tag must be external, or script-src 'self' would not hold: ${tag}`,
      );
    }
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

  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const decodePlanPayload = (token) => {
    const part = token.split(".")[0].replaceAll("-", "+").replaceAll("_", "/");
    const padded = part + "=".repeat((4 - (part.length % 4)) % 4);
    return JSON.parse(Buffer.from(padded, "base64").toString("utf8"));
  };
  const planTask = "افتح https://example.com واقرأ الصفحة";
  const onPublicDns = (fn) =>
    withDnsAnswers(["93.184.216.34"], fn, ["2606:4700:4700::1111"]);

  it("issues every plan token with its own random jti", async () => {
    const { env } = createEnvironment();
    const cookie = await login(env);
    const first = await (
      await worker.fetch(post("/api/plan", { task: planTask }, cookie), env)
    ).json();
    const second = await (
      await worker.fetch(post("/api/plan", { task: planTask }, cookie), env)
    ).json();
    const firstJti = decodePlanPayload(first.planToken).jti;
    const secondJti = decodePlanPayload(second.planToken).jti;
    assert.match(firstJti, UUID);
    assert.match(secondJti, UUID);
    assert.notEqual(firstJti, secondJti);
  });

  it("rejects a second execution of the same plan token before any browser starts", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const { planToken } = await (
      await worker.fetch(post("/api/plan", { task: planTask }, cookie), env)
    ).json();
    const first = await onPublicDns(() =>
      worker.fetch(post("/api/execute", { planToken }, cookie), env),
    );
    assert.equal(first.status, 200);
    const second = await onPublicDns(() =>
      worker.fetch(post("/api/execute", { planToken }, cookie), env),
    );
    assert.equal(second.status, 409);
    assert.equal((await second.json()).code, "plan_already_used");
    assert.equal(calls.browser, 1);
  });

  it("does not consume a plan refused for missing confirmation", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    // "احذف" is a SENSITIVE_TERMS match, so the plan needs confirmation.
    const sensitiveTask =
      "احذف الكوكيز ثم افتح https://example.com واقرأ الصفحة";
    const planned = await (
      await worker.fetch(
        post("/api/plan", { task: sensitiveTask }, cookie),
        env,
      )
    ).json();
    assert.equal(planned.plan.requiresConfirmation, true);
    const { planToken } = planned;
    const refused = await worker.fetch(
      post("/api/execute", { planToken }, cookie),
      env,
    );
    assert.equal(refused.status, 409);
    const confirmed = await onPublicDns(() =>
      worker.fetch(
        post("/api/execute", { planToken, confirmed: true }, cookie),
        env,
      ),
    );
    assert.equal(confirmed.status, 200);
    assert.equal(calls.browser, 1);
  });

  it("keeps a plan token consumed when DNS refuses the destination", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const { planToken } = await (
      await worker.fetch(post("/api/plan", { task: planTask }, cookie), env)
    ).json();
    const refused = await withDnsAnswers(["10.0.0.5"], () =>
      worker.fetch(post("/api/execute", { planToken }, cookie), env),
    );
    assert.equal(refused.status, 400);
    const retry = await onPublicDns(() =>
      worker.fetch(post("/api/execute", { planToken }, cookie), env),
    );
    assert.equal(retry.status, 409);
    assert.equal((await retry.json()).code, "plan_already_used");
    assert.equal(calls.browser, 0);
  });

  it("fails closed with no browser when the plan ledger is unavailable", async () => {
    const { env, calls } = createEnvironment({
      budgetFailsOn: "/consume-plan",
    });
    const cookie = await login(env);
    const { planToken } = await (
      await worker.fetch(post("/api/plan", { task: planTask }, cookie), env)
    ).json();
    const response = await onPublicDns(() =>
      worker.fetch(post("/api/execute", { planToken }, cookie), env),
    );
    assert.equal(response.status, 503);
    assert.equal(calls.browser, 0);
  });

  it("rejects a correctly signed plan token that carries no jti", async () => {
    const { env, calls } = createEnvironment();
    const cookie = await login(env);
    const legacy = await signToken({
      typ: "plan",
      exp: Date.now() + 60_000,
      task: planTask,
      action: "browse",
      url: "https://example.com/",
      query: "",
      reason: "قراءة الصفحة.",
      needsConfirmation: false,
    });
    const response = await onPublicDns(() =>
      worker.fetch(post("/api/execute", { planToken: legacy }, cookie), env),
    );
    assert.equal(response.status, 400);
    assert.equal(calls.browser, 0);
  });

  it("consumes a plan id once when two requests race for it", async () => {
    const state = createState();
    const actor = new DailyBudget(state);
    const body = {
      jti: "11111111-1111-4111-8111-111111111111",
      exp: Date.now() + 300_000,
    };
    const results = await Promise.all([
      actor.fetch(post("/consume-plan", body)),
      actor.fetch(post("/consume-plan", body)),
    ]);
    assert.deepEqual(results.map((item) => item.status).sort(), [200, 409]);
  });

  it("rejects a malformed plan id at the ledger", async () => {
    const actor = new DailyBudget(createState());
    const response = await actor.fetch(
      post("/consume-plan", { jti: "not-a-uuid", exp: Date.now() + 1000 }),
    );
    assert.equal(response.status, 400);
  });

  it("drops expired plan ids when recording a new one", async () => {
    const state = createState();
    const actor = new DailyBudget(state);
    const old = "22222222-2222-4222-8222-222222222222";
    const fresh = "33333333-3333-4333-8333-333333333333";
    await actor.fetch(
      post("/consume-plan", { jti: old, exp: Date.now() + 300_000 }),
    );
    state.values.set(`plan:${old}`, Date.now() - 1);
    await actor.fetch(
      post("/consume-plan", { jti: fresh, exp: Date.now() + 300_000 }),
    );
    assert.equal(state.values.has(`plan:${old}`), false);
    assert.equal(state.values.has(`plan:${fresh}`), true);
  });
});
