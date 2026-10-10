# Ccelia2027 Worker Overlay

This additive package places the Worker in `ccelia2027/` inside the existing `computer_use` repository and adds a manual-only root workflow. It does not replace the Python project or its README.

## Apply

Extract this ZIP at the root of `computer_use`. It adds:

- `ccelia2027/` — Worker source, tests, configuration, and project documentation.
- `.github/workflows/deploy-ccelia2027.yml` — the only active deployment workflow for this monorepo.
- `CCELIA2027-DEPLOYMENT.md` — these instructions.

If the repository already has a root `.github/workflows/` directory, merge in the new workflow file; do not replace the directory or other workflows.

The workflow is manual-only and restricted to `main`; pushing Python project changes will not deploy the Worker. The standalone workflows retained under `ccelia2027/.github/workflows/` are not discovered or run by GitHub Actions from a subdirectory.

## GitHub Settings

In the repository's Actions secrets, configure `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `APP_ACCESS_PASSWORD`, and `SESSION_SECRET` (at least 32 characters). Configure `CLOUDFLARE_FREE_TIER_VERIFIED` as a repository variable, not a secret; set it to `true` only after checking the account in Cloudflare.

The token must be authorized for the same Cloudflare account as `CLOUDFLARE_ACCOUNT_ID`. Cloudflare's current roles guide requires `Admin` at the Workers product scope to create a new Worker with Wrangler; deploying an existing Worker and managing its secrets requires `Editor` for that Worker. For least privilege, create the empty `ccelia2027` Worker once in the dashboard, then scope the GitHub token as `Editor` to that Worker. Wrangler checks the Worker's existing secret names during deployment; the secret-list API accepts `Workers Scripts Read` or `Workers Scripts Write`, and secret writes require `Workers Scripts Write`. See [Workers roles](https://developers.cloudflare.com/workers/authorization/workers/) and the [secret API permissions](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/subresources/secrets/methods/list/).

Before deploying, verify the account is on Workers Free with no Workers Paid plan, billing profile, payment method, or automatic billing. If Cloudflare asks for a card, billing activation, or upgrade, stop. The workflow's checkbox and repository variable are operator attestations, not an independent billing check.

Then open **Actions → Deploy Ccelia2027 Worker (manual) → Run workflow**, select `main`, and check the Free-tier attestation only after the dashboard verification. The workflow runs tests, lint, and a dry-run build before deployment. It stores the two application secrets in the Worker without printing their values.

On the first deployment, the workflow creates the Worker script before uploading secrets, then deploys it again. Since `workers.dev` is enabled, the static UI and health/status endpoints can briefly be public before the secrets are installed. Login, planning, and execution remain disabled during that interval; the Worker fails closed until the secrets are present.

## Credential Safety

A Google password was exposed in the chat and was not used; change it before signing in. A Cloudflare API token was also exposed and was reported as revoked, but that could not be independently confirmed. Before deploying, ensure that token is revoked and use a replacement entered directly in GitHub Settings. Never send secrets in chat.

The previous Cloudflare API request was blocked by an IP restriction. A GitHub-hosted runner may be blocked too. Do not broaden the token's IP allowlist just to make the workflow pass; use an already authorized runner or an approved deployment route.

No deployment has been performed by creating this package. Live deployment remains contingent on the Free/no-billing checks and authorized Cloudflare access.
