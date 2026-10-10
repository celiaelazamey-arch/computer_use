# Security Overview

The runtime is a read-only public-web agent. It accepts tasks after shared-password login, sends the task and extracted public-page text to Workers AI, and does not retain task text on the server. Conversation history stays in browser local storage. The Durable Object retains only the current daily usage counters.

## Baseline

- CI workflows declare only the permissions they need.
- CodeQL scans JavaScript on pushes, pull requests, and a weekly schedule.
- Dependabot checks npm and GitHub Actions dependencies.
- Secret scanning and push protection are enabled only when GitHub confirms support for this public repository.
- `APP_ACCESS_PASSWORD` must contain at least 16 characters; `SESSION_SECRET` must contain at least 32. Both are Cloudflare Worker Secrets; CI values belong in GitHub Actions secrets. Local Wrangler values belong in ignored `.dev.vars` files.
- Authentication fails closed unless both secrets and AI, Browser Run, and budget bindings exist. Cookies are signed, Secure, HttpOnly, and SameSite=Strict.
- Login attempts are rate-limited. Plans are schema-validated and signed for five minutes; client-edited plans are rejected.
- Navigation plans are bound to an HTTPS URL explicitly included in the user's request. The signed plan retains the original task for the summary; planner reasoning alone is not substituted for the user's goal.
- Before Browser Run is reserved, the Worker checks A and AAAA records through Cloudflare DNS-over-HTTPS. It permits an empty record family (for example, IPv6-only hosts), but requires at least one answer and rejects any non-public address. Lookup failures, missing addresses, and non-public answers have distinct fail-closed diagnostics.
- Playwright uses a fresh context with no persisted cookies, JavaScript and service workers disabled, and a route guard allowing only same-origin HTTPS GET/HEAD requests; downloads, scripts, images, media, and fonts are blocked.
- Sensitive requests need a separate read-only confirmation. The browser executor has no form submission or state-change action.
- Conversation text and results remain in local storage. Signed plan tokens are not persisted; after reload the user can explicitly request a new plan from the original task. If the API returns extracted evidence with a summary error, the UI retains and displays it.
- GitHub discovers only workflows under the repository-root `.github/workflows/`. The root CI, CodeQL, and manual Cloudflare deployment workflows are active; similarly named workflow files nested under `ccelia2027/.github/workflows/` are inactive templates in this monorepo.

## Reporting

Read [SECURITY.md](../SECURITY.md). Do not put vulnerability details in public issues.

## Current Limitations

This is not a general-purpose network sandbox. The DNS check and Browser Run's later DNS resolution are separate, so DNS rebinding is not fully excluded; the route guard is defense-in-depth, not a guarantee that private-network access is impossible. The browser only makes GET/HEAD requests, but a poorly designed site can still perform a state change on a GET URL; only provide links intended for public reading. Browser tasks stop after 45 seconds and reserve 120 seconds of daily browser budget to cover the 60-second inactivity cleanup window after a failed close. Browser Run performs the remote navigation and website behavior can change. JavaScript-disabled mode may not show content rendered only by scripts. Do not provide account credentials or use private sites. App-level quota caps do not prove the Cloudflare account billing plan; deployment must be withheld unless the dashboard confirms Workers Free and no billing method/profile.
