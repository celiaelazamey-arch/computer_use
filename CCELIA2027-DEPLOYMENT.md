# Ccelia2027 Worker Overlay

This additive package places the Worker in `ccelia2027/` inside the existing `computer_use` repository. It does not replace the Python project or its README.

## Apply

Extract this ZIP at the root of `computer_use`. Merge its files into the repository; if a root `.github/workflows/` directory already exists, keep its unrelated workflows. The overlay adds:

- `ccelia2027/` — Worker source, tests, configuration, static UI, and project documentation.
- `.github/workflows/ci.yml` and `.github/workflows/codeql.yml` — active root CI and security scanning.
- `.github/workflows/deploy-ccelia2027.yml` — manual-only root deployment workflow.
- `.github/workflows/nvidia-nim-smoke-test.yml` — optional, separate NVIDIA probe; it is not a Worker provider or deployment step.
- `.github/dependabot.yml` — root dependency-update configuration.
- `CCELIA2027-DEPLOYMENT.md` — these instructions.

GitHub discovers workflows only in the repository-root `.github/workflows/`. Any workflow examples retained under `ccelia2027/.github/workflows/` are inactive in this monorepo. No push to `main` automatically deploys the Worker.

## GitHub Settings

In the repository's Actions secrets, configure `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `APP_ACCESS_PASSWORD`, and `SESSION_SECRET`. Use an access password of at least 16 characters and a random session secret of at least 32 characters. Configure `CLOUDFLARE_FREE_TIER_VERIFIED` as a repository variable, not a secret; set it to `true` only after checking the account in Cloudflare.

The token must be authorized for the same Cloudflare account as `CLOUDFLARE_ACCOUNT_ID`. For least privilege, create the empty Worker once in the dashboard, then scope the GitHub token as `Editor` to that Worker. Wrangler also needs permission to inspect and write Worker secrets. See [Workers roles](https://developers.cloudflare.com/workers/authorization/workers/) and [secret API permissions](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/subresources/secrets/methods/list/).

Before deploying, verify the account is on Workers Free with no Workers Paid plan, billing profile, payment method, or automatic billing. If Cloudflare asks for a card, billing activation, or upgrade, stop. The workflow's checkbox and repository variable are operator attestations, not an independent billing check.

The root CI workflow runs tests, formatting, lint, dependency audit, and a Wrangler dry-run build on pushes and pull requests to `main`. To deploy, open **Actions → Deploy Ccelia2027 Worker (manual) → Run workflow**, select `main`, and check the Free-tier attestation only after verifying the dashboard. The workflow runs the same checks before deployment. It creates the Worker script before storing the app secrets, then deploys again; the app fails closed until those secrets exist.

## Credential Safety

A Google password was exposed in the chat and was not used; change it before signing in. A Cloudflare API token was also exposed and was reported as revoked, but that could not be independently confirmed. Before deploying, ensure that token is revoked and use a replacement entered directly in GitHub Settings. Never send secrets in chat.

The previous Cloudflare API request was blocked by an IP restriction. A GitHub-hosted runner may be blocked too. Do not broaden the token's IP allowlist just to make the workflow pass; use an already authorized runner or an approved deployment route.

An earlier revision was deployed and its health/status endpoints were checked. This updated overlay has not been deployed. Live model/browser checks still require an authorized deployment, and only the account owner can verify the billing configuration.
