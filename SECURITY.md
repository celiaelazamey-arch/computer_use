# Security Policy

## Reporting a Vulnerability

Do not report security vulnerabilities in a public issue or pull request. Use the repository's **Report a vulnerability** action to submit a [private security advisory](https://github.com/celiaelazamey-arch/computer_use/security/advisories/new). If private vulnerability reporting is unavailable, contact the repository owner privately through GitHub before sharing technical details.

Include only the information needed to reproduce and assess the issue. Do not include real credentials, personal data, or production secrets. Do not test against systems or accounts you do not own.

## Scope

This repository ships two components with deliberately different attack surfaces. Reports on either are in scope.

**`cela.sh` (local-first Python planner with an optional cloud API)** — the safety promises are:

- Every plan carries `dry_run=true` and `execution_enabled=false`, enforced as `Literal` types rather than runtime flags.
- There is no execution layer: no screen capture, no clicking, no typing, no shell commands.
- Ollama defaults to loopback HTTP. An explicit `CELA_ALLOW_OLLAMA_CONTAINER_HOST=true` exception permits exactly the `ollama` service on a trusted Docker network, not arbitrary LAN/public hosts. Redirects are rejected and environment proxies are disabled (`trust_env = False`).
- The optional Python API is unauthenticated unless `CELA_API_KEY` is set. When set, `/plan`, `/memory`, and `/memory/search` require a constant-time-checked Bearer token; `/health` and documentation endpoints stay public. The cloud Compose overlay requires this key and the base Compose file still publishes API/Ollama only on host loopback. The shared key is single-operator access, not per-user Auth/Z; session IDs remain selectors, not credentials.
- Every API failure returns a code and a fixed sentence. Unexpected exceptions are answered with a sanitized `500` (`internal_error`); exception text, exception type, and tracebacks are logged server-side and never returned to the caller.
- SQLite memory uses the official sqlite-vec extension with native session-prefiltered cosine KNN and a second session check at the metadata join. Writes and legacy migration are atomic; unusable vectors fail closed. Extension loading is disabled immediately after loading the packaged extension.
- A remote Qdrant endpoint requires both HTTPS and an API key.
- Project memory requires a `session_id`, is filtered at the store and re-checked locally. The explicit global scope is deliberately shared across every project using that store; it is not a per-user isolation feature. Planning never writes to memory.
- Retrieved memories and `screen_context` are untrusted data. A `correction` record gets retrieval priority only; it cannot override the current request or the deterministic policy classifier.

**`ccelia2027` (Cloudflare Worker)** — the safety promises are:

- Read-only: navigate to a public HTTPS page or public search results, extract text, summarize. No login, click, type, form submission, send, delete, purchase, or data mutation.
- Session tokens and plan tokens are HMAC-signed and carry a `typ` claim; neither is accepted in the other's place, and a token without a type is rejected.
- The plan must match the task: a navigation URL the user did not write is refused before any browser session starts.
- SSRF defenses: hostname blocklists for local and reserved names, DNS-over-HTTPS resolution, and public-range checks for both IPv4 and IPv6.
- Browser sessions are short-lived and isolated: JavaScript disabled, service workers blocked, downloads disabled, same-origin GET/HEAD only, images/media/fonts/scripts filtered out, a hard deadline, and a guaranteed close.
- The service fails closed. Missing secrets, an unavailable rate limiter, an exhausted internal budget, or an unsupported model response stops the request; there is no paid fallback provider.
- Static assets are served under a strict Content-Security-Policy with no `unsafe-inline`, plus `nosniff`, `DENY` framing, and `no-referrer`.

A report that demonstrates any of the above being broken is a security bug, not a feature request.

## Out of Scope

- Prompt injection that produces an unwise **plan** while every listed boundary still holds. The plan is advice; `status: ready` is never an authorization to act, and no component executes it.
- Keyword gaps in the deterministic risk classifier. It is documented as an initial classifier, not a complete one. Report a gap as an improvement unless it also defeats a boundary above.
- Self-inflicted configuration, such as running the API without `CELA_API_KEY` on an untrusted network, bypassing the cloud Compose overlay, publishing a Compose port off loopback, trusting a hostile Docker network, or setting `CELA_QDRANT_URL` to a plaintext remote host. The adapter rejects the latter; deployment/network choices remain the operator's responsibility.
- Findings in third-party services (Ollama, Qdrant, Cloudflare Workers AI, Browser Run) that are not caused by this code.

## Disclosure

The maintainer will acknowledge reports and coordinate a reasonable fix and disclosure timeline with the reporter. Please allow time for a fix before public disclosure. No response-time guarantee is made.

## Supported Versions

| Version        | Supported |
| -------------- | --------- |
| 1.0.2 (`main`) | Yes       |

`main` is the only development line. There is no backport branch and no legacy compatibility window: a fix lands on `main` and superseded behavior is removed rather than deprecated.

## Secret Handling

- Deployment credentials live in GitHub Actions secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `APP_ACCESS_PASSWORD` (at least 16 characters), `SESSION_SECRET` (random, at least 32 characters). The deployment workflow validates them without printing values.
- Local Worker development uses `.dev.vars`, which is gitignored.
- The planner reads `CELA_*` from the process environment only; it never loads a `.env` file directly. Compose loads an ignored host `.env` file when used. `CELA_QDRANT_API_KEY` and `CELA_API_KEY` must not be committed or written into a public file; keep the API key in a cloud secret manager or a host `.env` with mode `0600`.
- Never send passwords, API tokens, account secrets, or payment details through this repository, its issues, or chat. If a secret is exposed, revoke it at its issuer immediately and report the incident privately.

## Operational Notes

- The production deployment workflow is manual (`workflow_dispatch`) and runs only from `main`. No push deploys anything.
- Its free-tier checkbox and repository variable are **operator attestations**, not an independent billing check. Confirm the account state in the Cloudflare dashboard before deploying.
- CI never runs Ollama, Browser Run, or a third-party model, and never deploys. The live probes in `tests/integration_*.py` are opt-in and must be run explicitly on the intended machine.
- The optional Python cloud path is a manually provisioned, single-operator Linux VM deployment. It is not a CI workflow; follow [`docs/cloud-deployment.md`](docs/cloud-deployment.md), terminate TLS at a trusted ingress, and keep ports 8000/11434 closed to the public Internet.
