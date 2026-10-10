# Development

## Requirements

- Node.js 22.13 or newer in the 22.x line.
- npm 10.9 or newer with support for lockfile version 3.
- Docker is optional; it is not required for the Worker tests.

## Setup

```sh
npm ci
npm run dev
```

Wrangler serves the Worker locally at `http://localhost:8787` by default. A real Browser Run Playwright session requires a Cloudflare account and consumes the account's free Browser Run allowance.

## Checks

```sh
npm run lint
npm run format:check
npm test
npm run build
npm audit --audit-level=high
```

The Node built-in test suite exercises request contracts using stubbed AI, budget, and Playwright browser sessions. These tests do not prove live service access. `npm run build` asks Wrangler to bundle the Worker without deploying it.

## Codespaces

The `.devcontainer/` configuration installs dependencies and starts Wrangler. A devcontainer file is provided, but no Codespace was created and no current account quota was visible during setup.
