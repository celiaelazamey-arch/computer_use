# Contributing

This repository is a small foundation. Discuss scope before introducing product behavior, external services, or new runtime dependencies.

## Development

1. Use Node.js 22 and run `npm ci`.
2. Create a focused branch and make a small change.
3. Add or update tests for behavior changes.
4. Run `npm run lint`, `npm run format:check`, `npm test`, `npm run build`, and `npm audit --audit-level=high`.
5. Open a pull request with the repository template and resolve review conversations.

## Standards

- Keep workflow token permissions minimal and pin dependencies through the lockfile.
- Do not commit secrets, generated output, or personal data.
- Document Cloudflare bindings and deployment requirements without including secret values.
- Avoid claiming a feature is implemented until code, tests, and documentation support it.
