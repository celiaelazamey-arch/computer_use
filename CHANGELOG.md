# Changelog

This project keeps **one current version** and no historical release line. `main` is the only development branch: superseded behavior is removed rather than deprecated, and there are no backports. The current version is tagged on `main` at the merge commit of the release.

The version string is identical in **eight** files, and `tests/test_version_consistency.py` fails the build when they drift: `pyproject.toml`, `ccelia2027/package.json` and its root `package-lock.json` metadata, `docs/architecture.md`, `docs/protocol.md`, `docs/api-errors.md`, `SECURITY.md` (supported-versions table), and `src/cela_sh/server.py` (the FastAPI `version=`, which is published to callers through `/openapi.json` and `/docs`). Bumping only package metadata leaves the public OpenAPI contract advertising a stale version.

## Unreleased

### Project decision memory and brief

- `cela.sh`: three new engineering memory types on top of `note`/`correction` — `decision` (mandatory `--reason`, repeatable `--rejected` alternatives capped at 10, lifecycle status `active`/`superseded`/`deprecated`/`rejected` with `--superseded-by` required when superseded), `failure` (optional `--why` cause), and `debt`. All new explicit-learning records now carry a `recorded_at` timestamp, which becomes the base of the knowledge timeline.
- `cela.sh`: new `brief` command (`cela.sh brief --session-id <id> [--text]`) rebuilds the project brief from stored memory — goal, active decisions with rationale and rejected alternatives, closed decisions with what superseded them, past failures, technical debt, next steps, per-section counts, and the recorded-time range. It is deterministic, needs no model, no embedding, and no Ollama (works while Ollama is down), and reports an empty project honestly instead of inventing context. Project goal and next steps are notes tagged `goal`/`next`.
- `cela.sh`: approved-plan capture — `cela.sh learn --from-plan <plan.json>` validates the plan against the strict `Plan` model and stores its analysis as an active decision using replace-by-source on the plan file (unchanged re-import reports `unchanged`; an edited plan replaces the previous decision). The rationale is derived from the file's provenance, never fabricated.
- Stored decisions participate in planning retrieval automatically, like all session memory; the HTTP API (`/memory`, `/memory/search`) still accepts `note`/`correction` only in this stage.
- API surface snapshot updated for the new `learn` flags and the `brief` subcommand (additive; no flag, route, or exit code removed).

### Fault isolation and free-tier scaling

- `cela.sh`: removed the process-wide request lock. Planner, embedding, and Qdrant HTTP clients now use per-thread pooled sessions behind one stable handle (`cela_sh.thread_sessions`); the SQLite store already used short-lived connections, so concurrent requests run in parallel with bounded thread-pool backpressure (`CELA_MAX_CONCURRENT_REQUESTS`, default 40).
- `cela.sh`: per-dependency circuit breakers (`cela_sh.resilience.CircuitBreaker`) fail fast after 3 consecutive failures and re-probe automatically after a 10 s cooldown, exposing new stable 503 codes `planner_circuit_open` and `memory_circuit_open`. Input-validation errors do not trip a breaker, and the two dependencies have independent breakers.
- `cela.sh`: new public `GET /readyz` — a passive dependency view (no I/O, no probing) built from real request outcomes: `unknown` means no evidence yet, never assumed healthy. `/health` now runs on the event loop and keeps answering even when the thread pool is fully blocked behind slow planner calls.
- `cela.sh`: a memory write that is not confirmed, or that fails mid-transaction, is answered 503 and persists nothing (transaction rolls back text and vector together); verified by fault tests.
- `ccelia2027`: bounded wall-clock deadlines on every dependency call — ledger stub fetches 5 s, login limiter 5 s, planner AI 60 s, summary AI 90 s — so a hung Durable Object or model call can no longer hang a user request. The ledger now distinguishes a confirmed daily limit (429) from an unavailable ledger (honest 503), and no browser or model work starts without a confirmed reservation.
- Documentation: new `docs/fault-tolerance-and-scaling.md` records what survives each failure, the honest free-tier scaling limits (SQLite single-writer vs Qdrant shared store, one local Ollama, the single global budget Durable Object), and states explicitly that true HA (redundancy with automatic failover) for the local services is not available on the free tier.
- No live deployment, no billing change, no external AI provider; `dry_run=true` and `execution_enabled=false` remain fixed, and no conversations or plans are stored automatically.

## 1.0.2

### Security

- `cela.sh`: added optional constant-time Bearer authentication for planning and explicit memory routes using `CELA_API_KEY`; health and API documentation remain public. The configured key is validated and omitted from `Settings` representations.
- `cela.sh`: documented that the shared key is suitable only for a trusted single operator. It does not grant per-user memory authorization, and no application-level rate limiter is provided.

### Deployment

- Added a provider-neutral Docker Compose overlay for single-operator Linux VMs. It requires an API key and keeps API/Ollama host ports on loopback; TLS termination, firewall rules, persistent storage, and backups are operator responsibilities.
- Added `bash ./cela.sh services-up-cloud` and a deployment guide. The Cloudflare Worker remains independent; no cloud resource or live deployment is created by CI.

### Local knowledge

- Expanded the explicit local RAG flow with `learn`, `learn-file`, and `search`; records support `note`/`correction` types, multi-value tags, and project/global scopes. Corrections are prioritized for planning, global knowledge is combined with project memory, and local Markdown/text/PDF files are chunked with provenance metadata.
- `learn-file` now uses Replace-by-Source: per-chunk `content_hash` plus a file SHA-256 and safe relative source path make an unchanged import idempotent; changed content replaces the prior source chunks. SQLite applies the replacement in one transaction; Qdrant upserts deterministic point IDs then removes stale chunks.
- PDF parsing is an optional `pypdf` extra; no new always-installed runtime dependency or paid API is required. The feature stores user-selected knowledge and does not modify model weights or save conversations/plans automatically.
- Documented import replacement, source-path privacy, data retention, global sharing, and backend consistency boundaries in `docs/learning.md`.

### Documentation and checks

- Updated the API error contract for the stable `401 authentication_required` response and added offline auth/Compose tests.
- Added `docs/api-errors.md` and the npm lockfile metadata to the version consistency guard, bringing the complete version inventory to eight files.

## 1.0.1

### Security

- `cela.sh`: the local API answers every **application** failure with a code and a fixed sentence only. Unexpected exceptions now return `500` with `{"error":{"code":"internal_error","message":"Internal server error."}}` instead of the exception text, the planner and memory handlers keep their sanitized `502`/`503`/`422` messages, and the traceback is written to the server log rather than to the caller. Request-body validation and routing failures are answered by FastAPI itself with the standard `{"detail": ...}` envelope, so the API has two response shapes; `docs/api-errors.md` records both.
- `cela.sh`: `sudo`, privilege-escalation tools (`doas`, `pkexec`, `su -`), shell commands (`rm -rf`, `bash -c`, `powershell -`, `cmd /c`, `chmod`, `chown`), and shell targets (`terminal`, `PowerShell`, `الطرفية`, `سطر الأوامر`) are now blocked uniformly across all action types (`TYPE`, `SEARCH_WEB`, `NAVIGATE`, `CLICK`, `OPEN_APP`, `OBSERVE`, `SCROLL`, `WAIT`, `ASK_USER`, `STOP`) and in the goal text, returning `status: blocked` with explicit guidance to run intended system commands manually in the user's own terminal.
- `cela.sh`: extracted the abstract `Planner` interface (`cela_sh.planner.base`) and local planner provider registry (`register_planner_provider`, `build_planner`, `CELA_PLANNER_PROVIDER`) with shared `PlannerContract` enforcement, keeping `OllamaPlanner` as the default strictly local provider.
- `cela.sh`: consequential verbs (send, submit, post, publish, upload, email, reply, share, message, order, checkout, subscribe, and their Arabic equivalents) now require confirmation instead of returning `ready` with `low` risk. Classification applies to the goal and to each step.
- `ccelia2027`: each plan token can be executed once. Replays return 409 `plan_already_used` before any browser starts. Plan tokens issued before this change have no `jti` and are rejected. For a short window during deploy, users with unexpired old-format tokens get a 400 and must create a new plan.

### Documentation

- Added `docs/api-errors.md` as the **single reference** for the local API error contract: every application code with its status and fixed sentence, the framework's separate `{"detail": ...}` envelope, the input limits, what is never returned, and a re-verification appendix. Every row was confirmed against the running application rather than read from source. `README.md` and `docs/docker-deployment.md` now link to it instead of restating the tables, so the contract cannot drift between two copies.
- Corrected the version-string inventory above from four locations to six, and added `tests/test_version_consistency.py` to enforce it.

## 1.0.0

One product with two deliberately separate runtimes. They share maintenance, CI, documentation, and dependency governance, and nothing else: no runtime call, no shared secret, no shared data store.

### `cela.sh` — local planner (Python 3.11+, Ollama)

- Converts a goal and optional text context into a strict JSON plan through loopback Ollama by default; an explicit container-only option allows exactly the trusted `ollama` service.
- Validates every model response against a Pydantic schema (`extra="forbid"`, `strict=True`) and adds deterministic risk labels from a policy that is independent of the model.
- Every plan carries `dry_run=true` and `execution_enabled=false` as type-level `Literal` locks. There is no execution layer: no screen capture, clicking, typing, or shell commands.
- Errors never become plans. A typed exception hierarchy maps to stable JSON on stderr and a fixed exit-code table; stdout carries the plan only.
- Optional Qdrant or official sqlite-vec memory, disabled by default in CLI/Python, requiring an explicit `session_id`. Native vec0 cosine KNN filters sessions before top-k; the metadata join checks them again. Text/vector writes and legacy BLOB migration are atomic, without re-embedding.
- An optional local FastAPI API exposes dry-run plans and explicit memory writes/searches. It is not authenticated; session IDs are not authorization and host ports stay on loopback.
- A multi-stage, installed-wheel Docker image runs the API as a non-root user, with persistent SQLite storage, a read-only filesystem, liveness checks, and a separate Ollama service. The optional API profile preserves existing CLI service commands.
- Memory backends are registered, not wired in: `register_memory_backend` is the single source of truth for accepted `CELA_MEMORY_BACKEND` values, and every backend is held to the same behavioral contract suite.

### `ccelia2027` — read-only web agent (Cloudflare Worker)

- Plans and summarizes through Workers AI, and reads public HTTPS pages through short-lived, isolated Browser Run sessions.
- Read-only by construction: no login, click, type, form submission, send, delete, purchase, or data mutation. Unsupported or malformed plans stop before any browser session starts.
- Session and plan tokens are HMAC-signed and carry a `typ` claim, so neither is accepted in the other's place and a token without a type is rejected.
- A navigation target the user did not write is refused before execution. SSRF defenses combine hostname blocklists, DNS-over-HTTPS resolution, and public-range checks for IPv4 and IPv6.
- Browser sessions disable JavaScript and service workers, block downloads, and allow only same-origin HTTPS GET/HEAD, under a hard deadline with a guaranteed close.
- Static assets are served under a strict Content-Security-Policy with no `unsafe-inline`, plus `nosniff`, `DENY` framing, and `no-referrer`; the UI script is an external module.
- Fails closed on missing secrets, an unavailable rate limiter, an exhausted internal budget, or an unexpected model response. There is no paid fallback provider.

### Repository-wide

- One offline gate, `bash ./cela.sh preflight`, runs the same checks as CI and reports what it skipped instead of passing silently.
- The public contract surface — exported names, signatures, the action enum, model fields, CLI flags, exit codes, memory backends, and Worker routes — is recorded in `tests/snapshots/api_surface.json`. Unreviewed drift fails the build.
- CI runs both components in parallel jobs; CodeQL scans JavaScript and Python with `security-extended`; Dependabot covers pip, npm, and GitHub Actions. All actions are pinned to commit SHAs with least-privilege permissions.
- Production deployment is manual-only from `main`, behind explicit free-tier attestations and secret-length checks that never print values.
- Governance lives at the repository root: `LICENSE` (Apache-2.0), `CONTRIBUTING.md`, `SECURITY.md`, `CODEOWNERS`, and the issue and pull-request templates.

### Verification status

Offline tests cover the shared memory contracts, real sqlite-vec loading and KNN, session prefiltering, atomic migration/rollback, and the local HTTP API. CI also defines a Compose/image/runtime smoke check without starting Ollama or downloading models. The live Ollama and Qdrant probes remain opt-in; container deployment and model readiness must still be verified on the target machine. No Worker deployment or live Browser Run task is claimed for this revision.
