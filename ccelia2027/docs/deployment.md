# Deployment

## Hard Cost Gate

The app is a single Worker with static assets, AI and Browser Run bindings, and a SQLite-backed Durable Object. The public origin will be `https://ccelia2027.<workers.dev-subdomain>.workers.dev`; GitHub Pages is intentionally not used because it cannot serve the same-origin API.

Do not deploy unless the account dashboard shows **Workers Free**, no Workers Paid plan, no billing profile/payment method, and no automatic billing. Both attestations below (`free_tier_verified` and `CLOUDFLARE_FREE_TIER_VERIFIED`) are operator statements, not a billing API check. If Cloudflare asks to add a card, activate billing, or upgrade, stop.

## Deploy Workflows

Two workflows can deploy:

- `.github/workflows/deploy.yml` — automatic. Runs the tests, then deploys on every push to `main` (and on manual dispatch). It refuses to deploy unless all four secrets exist and the repository variable `CLOUDFLARE_FREE_TIER_VERIFIED` is exactly `true`. Set that variable only after confirming the account is Workers Free with no billing.
- `.github/workflows/cloudflare-deploy.yml` — manual dispatch; requires ticking the Free-plan attestation box.

## Required Secrets

Configure these as GitHub Actions repository secrets; do not put values in source, issues, or chat:

- `CLOUDFLARE_API_TOKEN` with only the permissions needed to deploy the Worker and set its secrets.
- `CLOUDFLARE_ACCOUNT_ID`.
- `APP_ACCESS_PASSWORD` (a unique, strong shared access password).
- `SESSION_SECRET` (random, at least 32 characters).

Both workflows check for all four secret values without printing them. They pass `APP_ACCESS_PASSWORD` and `SESSION_SECRET` to the Worker through the official `cloudflare/wrangler-action` `secrets:` input, where each name must match an environment variable supplied in the same step (the action runs `wrangler secret put` for them). `APP_ACCESS_PASSWORD` is not enough on its own: the two application values must reach the Worker as Cloudflare Worker Secrets, not just exist in GitHub. API routes fail closed without the app secrets.

Repository root must be the `ccelia2027` folder itself (the `npm ci` and `wrangler` commands run from the checkout root). If the sources live in a subdirectory, set the action's `workingDirectory` and adjust the `npm` steps instead.

## Verify After Deployment

1. Open the `workers.dev` URL printed by Wrangler from a separate browser session.
2. Confirm `/health` returns `ok: true` and `/api/status` shows the selected model and Playwright integration.
3. Sign in and run the included `https://example.com` read-only task.
4. Verify the result includes the page title, extracted excerpt, conservative 120-second session-budget reservation, and model summary.
5. Verify the task stops with a clear error when the free cap is reached. Never switch the account to Paid to test this.

Until those live checks succeed, deployment and live model/browser behavior are **not verified**.
