# Deployment

## Hard Cost Gate

The app is a single Worker with static assets, Workers AI, Browser Run, and a SQLite-backed Durable Object. The public origin is `https://ccelia2027.<workers.dev-subdomain>.workers.dev`; GitHub Pages is intentionally not used because it cannot serve the same-origin API.

Do not deploy unless the Cloudflare dashboard confirms **Workers Free**, with no Workers Paid plan, billing profile, payment method, or automatic billing. The workflow input and `CLOUDFLARE_FREE_TIER_VERIFIED` repository variable are operator attestations, not a billing API check. If Cloudflare asks for a card, billing activation, or upgrade, stop.

## Active Monorepo Workflows

GitHub runs workflows only from the repository-root `.github/workflows/` directory. For this monorepo, the active workflows are:

- `ci.yml` — runs lint, formatting, tests, high-severity dependency audit, and a Wrangler dry-run build on pushes and pull requests to `main`.
- `codeql.yml` — scans JavaScript on pushes, pull requests, and a weekly schedule.
- `deploy-ccelia2027.yml` — manual-only production deploy, restricted to `main`, with CI-style checks and explicit Free-tier attestations.
- `nvidia-nim-smoke-test.yml` — optional manual test that sends one synthetic prompt to NVIDIA and verifies its exact sentinel response. It does not change or deploy the Worker.

The similarly named files retained under `ccelia2027/.github/workflows/` are inactive templates in this monorepo. In particular, there is no automatic deploy on push.

## Required Secrets

Configure these as GitHub Actions repository secrets; never put their values in source, issues, or chat:

- `CLOUDFLARE_API_TOKEN`, authorized for the same account as `CLOUDFLARE_ACCOUNT_ID`.
- `CLOUDFLARE_ACCOUNT_ID`.
- `APP_ACCESS_PASSWORD`, a unique access password of at least 16 characters.
- `SESSION_SECRET`, random and at least 32 characters.

For least privilege, create the empty Worker in the dashboard first, then scope the GitHub token as `Editor` to that Worker. Wrangler also needs permission to list and write Worker secrets; see [Workers roles](https://developers.cloudflare.com/workers/authorization/workers/) and [secret API permissions](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/subresources/secrets/methods/list/).

Set the repository variable `CLOUDFLARE_FREE_TIER_VERIFIED` to `true` only after confirming the billing conditions above. Then open **Actions → Deploy Ccelia2027 Worker (manual) → Run workflow**, select `main`, and check the Free-tier attestation. The workflow checks secret lengths without printing their values, runs tests, formatting, lint, dependency audit, and dry-run build, then stores the two application values as Worker Secrets.

On the first deployment, the workflow creates the Worker script before uploading secrets, then deploys it again. Static health/status assets can be reachable briefly before secrets are installed; login, planning, and execution fail closed during that interval.

## Verify After Deployment

1. Open the `workers.dev` URL printed by Wrangler from a separate browser session.
2. Confirm `/health` returns `ok: true` and `/api/status` shows `configured: true`.
3. Sign in with the configured password and try a low-risk public read such as `https://example.com`.
4. Confirm the result includes the page title, excerpt, task-specific summary, and a clear error if a service or quota is unavailable.
5. Do not switch the Cloudflare account to Paid to test quota handling.

An earlier revision was deployed and its health/status endpoints were checked. The fixes in this checkout have not been deployed, and a live model/Browser Run task still needs to be tested after an authorized deployment. Billing status must be checked by the account owner; app limits and workflow attestations do not prove it.
