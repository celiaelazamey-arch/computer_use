# Threat Model: ccelia2027 (Cloudflare Worker)

**Status:** Draft for review. This document describes the current code as read in the checkout. It does not change behavior. The separate threat model for `cela.sh` is in [../../docs/threat-model-cela.md](../../docs/threat-model-cela.md).

**Scope:** `src/index.js` (Worker and `DailyBudget` Durable Object), `docs/site/` (static UI and its headers), and the Cloudflare bindings in `wrangler.jsonc`. Live Cloudflare behavior is not verified by this document; see [deployment.md](deployment.md).

## Assets

- `APP_ACCESS_PASSWORD` and `SESSION_SECRET`: Worker secrets.
- Session cookie (`ccelia_session`, 8 hours) and plan tokens (5 minutes).
- Workers AI and Browser Run quota, which is shared by all users of the deployment.
- User task text: sent to Workers AI, not stored on the server. Kept in the browser's local storage.
- Extracted page text: sent to Workers AI for summarization, not stored on the server.

## Actors

| Actor                            | Trust          | Notes                                                                       |
| -------------------------------- | -------------- | --------------------------------------------------------------------------- |
| Anonymous internet visitor       | Untrusted      | Can reach `/`, `/health`, `/api/status`, `/api/session`, `/api/login`       |
| Authenticated user               | Partly trusted | Can plan and run public-page reads. Shares the daily budget with everyone   |
| Author of a public web page      | Untrusted      | Controls page text, redirects, and possibly DNS answers for their domain    |
| Attacker controlling DNS answers | Untrusted      | Can return different answers to the DNS-over-HTTPS check and to Browser Run |
| Operator                         | Trusted        | Sets secrets, deploys, and confirms the billing state                       |

## Trust boundaries

1. **Browser ↔ Worker:** same-origin requests. Cookies are `HttpOnly; Secure; SameSite=Strict`. `Origin` is checked when present.
2. **Worker ↔ Workers AI:** the task text (planner) and page text (summary) leave the Worker.
3. **Worker ↔ Cloudflare DNS-over-HTTPS** (`cloudflare-dns.com`): target hostname lookup, with `redirect: "error"` and a 3-second timeout.
4. **Worker ↔ Browser Run (Playwright):** the only component that opens a remote page. It runs on Cloudflare's infrastructure.
5. **Worker ↔ Durable Object:** daily counters and browser reservation IDs. No task text.

## Threats and controls

| #   | Threat                                            | Controls in code                                                                                                                                        | Residual risk                                                                                                                                                  |
| --- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T1  | Unauthenticated use of AI or browser quota        | `createPlan` and `executePlan` both require a valid session. Missing secrets or bindings fail closed (`freeServicesReady`)                              | None known                                                                                                                                                     |
| T2  | Password brute force                              | `LOGIN_LIMITER` allows 5 attempts per minute per `cf-connecting-ip`. Password must be at least 16 characters. Comparison is constant-time               | A distributed attacker with many IPs is not limited. The password length is the main defense                                                                   |
| T3  | Session cookie theft                              | `HttpOnly`, `Secure`, `SameSite=Strict`, 8-hour expiry, typed HMAC token                                                                                | **Logout does not revoke.** `/api/logout` only clears the cookie. A copied cookie stays valid until it expires (F2)                                            |
| T4  | CSRF                                              | `SameSite=Strict`, and `Origin` must match when the header is present                                                                                   | A request with no `Origin` header is accepted. Browsers send `Origin` on cross-site POST, so this is an accepted trade-off                                     |
| T5  | Token confusion (plan ↔ session)                  | Separate `typ` claims. A typeless token is rejected. Tests cover both directions                                                                        | None known                                                                                                                                                     |
| T6  | Plan tampering                                    | Plan token is HMAC-signed, expires in 5 minutes, and carries the validated URL and task. Client cannot change it                                        | **Plan tokens are reusable within their 5-minute window.** Nothing records use. Each replay starts a new browser reservation until the daily cap stops it (F1) |
| T7  | Plan token payload exposure                       | The payload is base64url JSON, readable by the client. It carries the task text and URL but no secret                                                   | Signed is not encrypted. Acceptable because the client already has this data                                                                                   |
| T8  | User-supplied URL the user did not request        | `planMatchesTask` requires the URL to appear in the task text. Tests cover trailing punctuation and Arabic punctuation                                  | None known                                                                                                                                                     |
| T9  | SSRF to private or reserved addresses             | Hostname blocklist, IP-literal rejection, DNS-over-HTTPS check for both A and AAAA, public-range checks for IPv4 and IPv6, fail closed on lookup errors | **DNS rebinding:** the check and Browser Run resolve separately. Documented in [security.md](security.md) (F3)                                                 |
| T10 | Redirect from a public page to a private host     | Route guard aborts any request whose origin differs from the target origin, and any non-GET/HEAD request                                                | Same-origin navigation to a host that rebinds to a private address is not checked again (see T9)                                                               |
| T11 | Browser executes page script or downloads         | JavaScript disabled, service workers blocked, downloads disabled, script/image/media/font requests aborted                                              | A page can still show misleading or harmful text. The summary is only as trustworthy as the page                                                               |
| T12 | Browser performs a state change through a GET URL | Only GET and HEAD are allowed. There is no click, type, or form action                                                                                  | A site whose GET requests change state would still change state. Documented in [security.md](security.md)                                                      |
| T13 | Prompt injection from the task into the planner   | Output must match a strict two-step schema. Unknown fields are rejected. The planner cannot choose a URL the user did not write                         | A plan can be wrong or unhelpful. The plan only leads to a read-only browser session                                                                           |
| T14 | Prompt injection from a page into the summary     | Page text is passed as untrusted data. The summary is text and triggers no further action                                                               | A misleading summary                                                                                                                                           |
| T15 | Quota exhaustion (availability)                   | Daily caps for AI calls, browser runs, browser minutes, interval, and concurrency. Fail closed on exhaustion                                            | **All users share one global budget.** One authenticated user can exhaust it for everyone (F4)                                                                 |
| T16 | Accidental paid billing                           | App-level caps and a manual deployment gate. Documented in [free-tier.md](free-tier.md)                                                                 | The counters cannot see billing state. The operator must confirm the dashboard (F5)                                                                            |
| T17 | XSS in the UI                                     | Strict CSP without `unsafe-inline`. `innerHTML` is only used with fixed strings. Rendering uses `textContent` and `createElement`                       | Full line-by-line review of `app.js` has not been done                                                                                                         |
| T18 | Data at rest in the browser                       | Conversation history stays in `localStorage`. Signed plan tokens are not persisted                                                                      | A shared computer exposes past tasks and page excerpts (F6)                                                                                                    |
| T19 | Detection of incidents                            | Worker observability is disabled (`observability.enabled: false`)                                                                                       | Limited logs for investigation (F7)                                                                                                                            |

## Gaps

| ID  | Priority | Gap                                                                                 | Evidence                                                                                |
| --- | -------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| F1  | P1       | Plan tokens are replayable within 5 minutes; no single-use record                   | `verifyPlanToken` is stateless; `executePlan` does not record use; no test covers reuse |
| F2  | P1       | Logout does not revoke server-side sessions                                         | `/api/logout` only sets `Max-Age=0`                                                     |
| F3  | P1       | DNS rebinding between the Worker's check and Browser Run's resolution is not closed | Documented in `security.md`; not mitigated in code                                      |
| F4  | P2       | Global budget can be exhausted by a single user                                     | `DailyBudget` is a single object named `global`                                         |
| F5  | P2       | No independent billing check                                                        | Documented as an operator attestation                                                   |
| F6  | P2       | Local storage keeps history on shared devices                                       | `app.js` uses `localStorage`                                                            |
| F7  | P2       | Observability is off                                                                | `wrangler.jsonc`                                                                        |
| F8  | P2       | Monorepo CI runs `npm audit` for the Worker, but no equivalent for the Python side  | See `cela.sh` threat model, F4                                                          |

## Out of scope

- Automation or execution of any kind. The Worker has no such path, and adding one changes this model.
- Security of Cloudflare services themselves (Workers AI, Browser Run, DNS-over-HTTPS).
- The manual NVIDIA smoke-test workflow in `.github/workflows/`. It is not a Worker runtime path, but it does send one prompt to an external provider using a repository secret. It must be reconciled with [free-tier.md](free-tier.md) and the monorepo `SECURITY.md` (see Open decisions).

## Conditions for reopening this model

Update this document before any of the following:

1. Any action that changes state on a remote site, or any user-controlled browser input beyond the URL.
2. Adding a provider outside Cloudflare, including any free-tier third-party model. Each provider is a new data boundary, and free tiers may use submitted data for training.
3. Storing task text or page text server-side.
4. Adding a second Worker, or any call from the Worker to the Python planner, or the reverse.
5. Changing the account from Workers Free.

## Open decisions

- **F1:** Add a single-use record (for example a `jti` stored in the Durable Object with expiry), with a test that a replayed token is rejected.
- **F2:** Either add a server-side revocation list, or document that logout is client-side only and reduce the cookie lifetime.
- **F3:** Accept and document, or constrain navigation to hostnames whose DNS answers are re-checked immediately before each navigation. The second option is not fully possible because Browser Run resolves names itself.
- **NVIDIA workflow:** Keep it as a documented exception, or remove it. Decide before any provider work starts.
