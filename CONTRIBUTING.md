# Contributing

This repository holds two components with separate runtimes, providers, and threat models. Discuss scope before introducing product behavior, external services, or new runtime dependencies.

| Component    | Path           | Runtime                                                | Capability                                                                   |
| ------------ | -------------- | ------------------------------------------------------ | ---------------------------------------------------------------------------- |
| `cela.sh`    | `src/cela_sh/` | Python 3.11+, local Ollama or single-operator cloud VM | Returns a validated JSON plan. Never executes.                               |
| `ccelia2027` | `ccelia2027/`  | Cloudflare Worker                                      | Reads public HTTPS pages. Never logs in, clicks, types, submits, or mutates. |

They do not call one another and share no credentials. Do not add a runtime link, a shared secret, or a shared data store without a design discussion first: either one changes the threat model of the whole repository.

## Setup

Requires Python 3.11+, Bash, Node.js 22.13+, and npm. Docker and Ollama are optional and only needed for the live probes.

```bash
bash ./cela.sh install-all
bash ./cela.sh preflight
```

`preflight` is the single offline gate and the one CI runs. It never starts Docker, Ollama, or a deployment, and it reports what it skipped instead of passing silently. Use `bash ./cela.sh check-all` for the same checks plus `npm audit`.

## Making a change

1. Start from a green baseline. If `preflight` fails before your change, fix that first.
2. Create a focused branch and make a small change.
3. Add or update tests. A behavior change needs a test that fails without it.
4. Run `bash ./cela.sh preflight`.
5. Open a pull request with the template and resolve review conversations.

For work handed between sessions, follow the [session contract](docs/development/session-contract.md); in particular, verify the fetched remote HEAD rather than relying only on a successful push.

## The rules that do not bend

Read [docs/extension-guide.md](docs/extension-guide.md) before extending anything. In short:

- **Extension is additive.** Register a memory backend with `register_memory_backend`; do not edit `factory.py` or `settings.py` to add one.
- **The public contract is frozen.** Exported names, signatures, `Action` members, model fields, CLI flags, exit codes, memory backends, and Worker routes are recorded in `tests/snapshots/api_surface.json`. Removing or renaming one fails CI. After an intentional change, regenerate with `bash ./cela.sh api-surface --update` and explain the diff in the pull request.
- **Every backend meets the same contract.** A new memory store inherits `MemoryStoreContract` in `tests/contract_memory_store.py` and is added to `COVERED_BACKENDS`. Session isolation is never optional.
- **Plans stay dry.** `dry_run=true` and `execution_enabled=false` are type-level locks, not conventions. `status: ready` is never an authorization to act.
- **Errors never become plans.** A timeout, a malformed response, or a failed memory lookup raises; nothing degrades into a substitute plan.
- **Tests stay offline.** Anything that needs a live service belongs in `tests/integration_*.py` and is run explicitly, never by discovery or CI.

## Standards

- Keep workflow permissions minimal and pin GitHub Actions to a commit SHA with a version comment.
- Pin npm dependencies through the lockfile; `.npmrc` sets `save-exact=true`.
- Do not commit secrets, generated output, model weights, or personal data.
- Document Cloudflare bindings, environment variables, and deployment requirements without including secret values.
- Keep the version string in sync across all eight versioned files: `pyproject.toml`, `ccelia2027/package.json`, the root metadata in `ccelia2027/package-lock.json`, `docs/architecture.md`, `docs/protocol.md`, `docs/api-errors.md`, `SECURITY.md`, and `src/cela_sh/server.py`. `tests/test_version_consistency.py` enforces this list.
- Do not claim a feature works until code, tests, and documentation support it. If something is unverified on the target machine, say so.

## License

Apache License 2.0. See [LICENSE](LICENSE). Contributions are submitted under the same license.
