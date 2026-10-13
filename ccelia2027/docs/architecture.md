# Architecture

## Current MVP

```text
Browser (Manus Mini UI; history in localStorage)
  -> one Cloudflare Worker (static assets + API)
     -> Workers AI: plan -> strict CELA-shaped schema + deterministic policy
     -> signed, short-lived read-only plan token
     -> Durable Object: serialized daily safety-budget reservation
      -> Browser Run / Playwright: isolated session, public-page navigation + extraction
     -> Workers AI: summarize extracted page evidence
```

There is no R2, external model provider, user-supplied API key, or paid fallback. The SQLite-backed Durable Object keeps only one day's counters and the set of used plan IDs. Those IDs expire five seconds after their token does. All AI reservations, browser reservations, and plan consumptions go through this one object, so they are serialized. This is a known architectural limit; see [free-tier.md](free-tier.md). Each task gets a fresh browser context with no saved cookies; page JavaScript and service workers are disabled, and the session cannot click, type, submit forms, download files, or access authenticated accounts.

## Safety Boundaries

- Model output is strictly parsed; only NAVIGATE/SEARCH_WEB followed by OBSERVE is accepted.
- Navigation requires HTTPS and at least one public IPv4 or IPv6 DNS answer; every returned address must be public. A missing address family is allowed, while lookup failures, empty responses, and non-public answers stop before Browser Run. Playwright permits only same-origin HTTPS GET/HEAD requests and blocks downloads, scripts, images, media, fonts, and service workers.
- A navigation plan must target a URL explicitly present in the user's original task. The signed short-lived token retains that task so the summary model receives the actual request and constraints, not only the planner's paraphrase.
- Access passwords must contain at least 16 characters; session-signing secrets must contain at least 32.
- Sessions use signed, HttpOnly, Secure, same-site cookies. API routes fail closed when secrets or bindings are missing.
- Sensitive wording requires explicit confirmation, but the executor remains read-only even after confirmation.
- Internal daily limits stay under the documented Free ceiling: at most 5 Browser Run sessions (120,000 reserved ms each = a conservative 10-minute worst-case day), no more than 3 concurrent reservations, and a 20-second interval between starts; plus 28 AI calls (up to 9,800 estimated Neurons/day).
- Deployment is manual only. Billing status is not automatically queried; verify Workers Free and no billing before dispatch.

## Fault Isolation

- Every dependency call is bounded in wall-clock time: budget-ledger stub fetches 5 s, login-limiter 5 s, planner AI 60 s, summary AI 90 s. Workers Free has no wall-clock limit per request, so without these a hung Durable Object or model call would hang a user request indefinitely. An abandoned AI call may still complete in the background and consume quota; the request no longer waits for it.
- The ledger distinguishes "daily limit reached" (429) from "ledger unavailable" (503 with an honest message). No browser session starts and no model call is made without a confirmed reservation; a ledger error is never presented as an exhausted limit.
- The Worker itself is request-stateless (signed session cookies and plan tokens), so it scales horizontally across Cloudflare's edge. The single global `DailyBudget` Durable Object remains the only shared state and serializes budget operations; at the free-tier volume (≤ 66 ledger calls/day) this is acceptable, and sharding across N objects is the documented path if the internal limits ever move.
- `/health` and `/api/status` answer while the ledger is down: liveness is separate from dependency availability.
- Fault and concurrency tests: `tests/worker.test.js`, suite "Ccelia fault isolation".
