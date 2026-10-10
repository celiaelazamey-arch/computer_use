# Ccelia 2027 / Manus Mini

A read-only public-web research agent served by one Cloudflare Worker.

## Project Status

This repository now contains an MVP implementation, but **no production deployment has been verified**. The Worker uses Workers AI for a strict CELA-shaped plan and summary, and Cloudflare Browser Run with Playwright to open and read public HTTPS pages in short-lived, isolated sessions. The Arabic Manus Mini interface keeps conversation history in this browser's local storage.

This is not unrestricted computer control. It cannot log into websites, click, type, submit forms, send, delete, purchase, or change data. It can only navigate to a public page or public search results, read extracted text, and summarize it. Unsupported or malformed model plans stop before browser execution.

## Vision

Build an automation platform with clear security boundaries, testable components, and deployment paths that can grow as requirements become concrete.

## Architecture

- `src/`: Worker API and daily-budget Durable Object.
- `tests/`: Node built-in tests for schema validation, access control, quotas, and the Playwright session contract.
- `docs/`: architecture, deployment, security notes, and Worker-served static assets.
- `.github/workflows/`: CI, CodeQL, Pages, Cloudflare deployment, and container workflows.
- `.devcontainer/`: reproducible Codespaces and VS Code environment.

The Worker serves the UI at `/`, health at `/health`, and secured `/api/*` routes. No paid API provider, R2, or user-supplied model key is used. A SQLite-backed Durable Object stores only rolling daily usage counters.

## Quick Start

Requirements: Node.js 22 and npm.

```sh
npm ci
npm run dev
```

In another terminal, check `http://localhost:8787/health`. A live Browser Run session requires a Cloudflare account and consumes its free allowance. Unit tests use stubs and are not a live-service check.

## Development

```sh
npm run lint
npm run format:check
npm test
npm run build
npm audit --audit-level=high
```

See [Development](docs/development.md) for workflow details.

## Deployment

- **Cloudflare Worker:** one manual-only workflow serves UI and API on `workers.dev`. It requires a verified Workers Free account, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `APP_ACCESS_PASSWORD`, and a random `SESSION_SECRET` of at least 32 characters. The workflow checkbox is operator attestation, not an independent billing check.
- **GitHub Pages:** no longer deploys this app because Pages would not provide its same-origin Worker API.
- **Container:** the GHCR workflow builds on demand and publishes only for a published GitHub Release. The image is a local Worker development environment, not a Cloudflare production runtime.

See [Deployment](docs/deployment.md).

Read [Free-tier limits](docs/free-tier.md) before using or deploying. Do not deploy if the Cloudflare account is Paid or has billing enabled.

## Security

Read [SECURITY.md](SECURITY.md). Never commit credentials, API keys, `.dev.vars`, or production data. Security vulnerabilities must be reported privately.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests should include tests and pass the required checks.

## License

No open-source license has been selected. `LICENSE` is only a decision-pending notice, not a license grant.

## Roadmap

1. Confirm product requirements and licensing.
2. Define the agent/runtime boundary and threat model.
3. Add one focused capability at a time with tests and documented deployment behavior.
