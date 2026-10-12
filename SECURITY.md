# Security Policy

## Reporting a Vulnerability

Do not report security vulnerabilities in a public issue or pull request. Use the repository's **Report a vulnerability** action to submit a [private security advisory](https://github.com/celiaelazamey-arch/computer_use/security/advisories/new). If private vulnerability reporting is unavailable, contact the repository owner privately through GitHub before sharing technical details.

Include only the information needed to reproduce and assess the issue. Do not include real credentials, personal data, or production secrets. Do not test against systems or accounts you do not own.

## Scope

This repository ships two components with deliberately different attack surfaces. Reports on either are in scope.

**`cela.sh` (local Python planner)** — the safety promises are:

- Every plan carries `dry_run=true` and `execution_enabled=false`, enforced as `Literal` types rather than runtime flags.
- There is no execution layer: no screen capture, no clicking, no typing, no shell commands.
- Ollama defaults to loopback HTTP. An explicit `CELA_ALLOW_OLLAMA_CONTAINER_HOST=true` exception permits exactly the `ollama` service on a trusted Docker network, not arbitrary LAN/public hosts. Redirects are rejected and environment proxies are disabled (`trust_env = False`).
- The optional Python API is unauthenticated and Compose publishes it on host loopback only. Session IDs are selectors, not credentials: do not expose it to untrusted users before adding Auth/Z.
- Every API failure returns a code and a fixed sentence. Unexpected exceptions are answered with a sanitized `500` (`internal_error`); exception text, exception type, and tracebacks are logged server-side and never returned to the caller.
- SQLite memory uses the official sqlite-vec extension with native session-prefiltered cosine KNN and a second session check at the metadata join. Writes and legacy migration are atomic; unusable vectors fail closed. Extension loading is disabled immediately after loading the packaged extension.
- A remote Qdrant endpoint requires both HTTPS and an API key.
- When memory is enabled, a `session_id` is mandatory, the filter is sent to the server **and** re-checked locally, and planning never writes to memory.
- Retrieved memories and `screen_context` are untrusted data. The policy classifier is deterministic and independent of the model.

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
- Self-inflicted local configuration, such as exposing the unauthenticated API or publishing a Compose port off loopback, trusting a hostile Docker network, or setting `CELA_QDRANT_URL` to a plaintext remote host. The adapter rejects the latter; the former is operator configuration.
- Findings in third-party services (Ollama, Qdrant, Cloudflare Workers AI, Browser Run) that are not caused by this code.

## Disclosure

The maintainer will acknowledge reports and coordinate a reasonable fix and disclosure timeline with the reporter. Please allow time for a fix before public disclosure. No response-time guarantee is made.

## Supported Versions

| Version        | Supported |
| -------------- | --------- |
| 1.0.0 (`main`) | Yes       |

`main` is the only development line. There is no backport branch and no legacy compatibility window: a fix lands on `main` and superseded behavior is removed rather than deprecated.

## Secret Handling

- Deployment credentials live in GitHub Actions secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `APP_ACCESS_PASSWORD` (at least 16 characters), `SESSION_SECRET` (random, at least 32 characters). The deployment workflow validates them without printing values.
- Local Worker development uses `.dev.vars`, which is gitignored.
- The planner reads `CELA_*` from the process environment only; it never loads a `.env` file. `CELA_QDRANT_API_KEY` must not be committed or written into a public file.
- Never send passwords, API tokens, account secrets, or payment details through this repository, its issues, or chat. If a secret is exposed, revoke it at its issuer immediately and report the incident privately.

## Operational Notes

- The production deployment workflow is manual (`workflow_dispatch`) and runs only from `main`. No push deploys anything.
- Its free-tier checkbox and repository variable are **operator attestations**, not an independent billing check. Confirm the account state in the Cloudflare dashboard before deploying.
- CI never runs Ollama, Browser Run, or a third-party model, and never deploys. The live probes in `tests/integration_*.py` are opt-in and must be run explicitly on the intended machine.
