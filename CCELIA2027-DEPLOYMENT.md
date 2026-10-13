# CELA Monorepo Operations and Worker Deployment

This repository is one product with two deliberately separate runtime components. Preserve both; do not replace the local planner with the hosted Worker or connect their credentials/data without a separate design review.

## System Map

- `cela.sh` is the local-first Python planner. It uses Ollama, optionally retrieves session-isolated Qdrant memories, validates and classifies a plan, and always returns `dry_run=true` with `execution_enabled=false`. A separate single-operator cloud API deployment on a Linux VM is documented in [`docs/cloud-deployment.md`](docs/cloud-deployment.md); it is not automatically deployed by CI.
- `ccelia2027/` is the Cloudflare Worker web app. It uses Workers AI and short-lived Browser Run sessions to read public HTTPS pages. It does not log in, click, type, submit forms, or mutate websites.
- The components have separate plan schemas, providers, storage, and secrets. They do not call one another. Shared root CI, CodeQL, dependency updates, docs, and repository commands make them one maintained monorepo.
- All workflows live in the repository-root `.github/workflows/`; the Worker package keeps none of its own. No push automatically deploys the Worker, and no Python VM credentials or deployment are configured in CI.

See [System Architecture](docs/system-architecture.md) for data flows and trust boundaries.

## Local Setup and Checks

Requirements for the complete monorepo are Python 3.11+, Bash, Node.js 22.13+, and npm. Ollama and Docker are optional for the Python live-planning probe; they are not required for offline tests. Cloudflare credentials are not needed for unit tests.

```bash
bash ./cela.sh install-all
bash ./cela.sh check-all
```

`check-all` is the local equivalent of the root CI gate: Python tests and wheel build, Worker lint/format/tests, high-severity npm audit, and a Wrangler dry-run build. It does not call Ollama, Browser Run, NVIDIA, or a production deployment. The Python-only commands and optional live probes are documented in the root [README](README.md).

## Production Deployment Gate

The only active GitHub Actions production route is `.github/workflows/deploy-ccelia2027.yml`, manually dispatched from `main`. Python cloud deployment is operator-managed on a Linux VM as described in [`docs/cloud-deployment.md`](docs/cloud-deployment.md); this repository does not provision a VM, hold its secrets, or deploy it from CI. Before using the Worker route, the account owner must inspect Cloudflare directly and confirm all of the following:

- Workers Free is active.
- No Workers Paid plan, billing profile, payment method, or automatic billing is enabled.
- No card, billing activation, or upgrade is requested by Cloudflare.
- The previous Cloudflare token noted below is revoked; a replacement token is limited to the intended account/Worker and stored directly in GitHub Actions Secrets.

The workflow input `free_tier_verified` and repository variable `CLOUDFLARE_FREE_TIER_VERIFIED=true` are operator attestations, not an API check. Set them only after the account owner has verified the dashboard. If the account state is uncertain or paid, deployment is blocked.

Required GitHub Actions secrets are `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `APP_ACCESS_PASSWORD` (at least 16 characters), and `SESSION_SECRET` (random, at least 32 characters). Never place values in source, issues, reports, or chat. The workflow validates required values without printing them, reruns tests, formatting, lint, dependency audit, and a Wrangler dry run, then deploys and stores the application secrets.

The earlier Cloudflare API request was blocked by an IP restriction. A GitHub-hosted runner may also be blocked. Do not broaden the token IP allowlist to force the deploy; stop and use only an already authorized runner or another explicitly approved route.

## After an Authorized Deploy

1. Use the `workers.dev` origin reported by the successful deployment.
2. Confirm `/health` returns `ok: true` and `/api/status` returns `configured: true`.
3. Sign in with the application access password and run one low-risk public read, such as reading the title and a short excerpt from `https://example.com`.
4. Confirm the returned evidence and task-specific summary are visible. Stop if the service is unavailable, quota is exhausted, or the result is unexpected. Do not switch to a paid plan for testing.
5. Test the earlier DNS failure only after obtaining the exact public HTTPS URL. Remove credentials or secret query values before sharing it. The Worker must stop before Browser Run when lookup fails, no public address is returned, or any address is private/reserved.

An earlier revision had health/status checks, but commit `f09a850` and later fixes have not been deployed. No live model/Browser Run task or account billing inspection has been verified.

## Credential Incident Notes

- A Google password was exposed in an earlier chat and was not used. Change it before using that account to sign in.
- A Cloudflare API token was exposed and was reported as revoked, but revocation was not independently confirmed. Confirm revocation and use a replacement stored directly in GitHub Settings.
- Never send passwords, API tokens, account secrets, or payment details through this repository or chat.
