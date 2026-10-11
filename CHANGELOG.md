# Changelog

## Unreleased

- Bring the local Python planner and hosted read-only Worker together under one monorepo workflow while keeping runtime, data, provider, and credential boundaries separate.
- Add root install and full-check commands; extend CI, CodeQL, and Dependabot coverage to both applications.
- Add a memory backend registry so a new store is registered rather than wired into the factory, and validate `CELA_MEMORY_BACKEND` against that single source of truth.
- Record the public contract surface (exported names, signatures, the action enum, model fields, CLI flags, exit codes, and Worker routes) in `tests/snapshots/api_surface.json`, and fail the build on unreviewed drift.
- Add a shared memory-store contract suite plus offline doubles, so every backend is held to the same input-validation, session-isolation, and copy-on-read rules.
- Assert the safety invariants independently of the model: plans stay `dry_run=true` and `execution_enabled=false`, Ollama stays loopback-only, and planning never writes memory.
- Add `bash ./cela.sh preflight` as one offline gate and `bash ./cela.sh api-surface` to review or record contract changes.
- Add `docs/extension-guide.md` with step-by-step extension recipes and the limits that must not change.

## 0.1.0

- Deliver a local CLI that converts a goal and optional text context into a strict JSON plan through Ollama.
- Validate every model response against a Pydantic schema and add deterministic risk labels.
- Keep execution disabled in every returned plan; do not capture screens, click, type, run commands, or save memories automatically.
- Keep Qdrant memory optional, explicitly enabled, and scoped to a required session ID.
- Add a live `probe` that generates a real plan; keep the Qdrant integration check separate as `probe-memory`.
- Default to `qwen2.5:3b`, which was live-tested on Ollama 0.40.2 in an ARM64 Linux environment with 8 GB RAM.
- Verify 10/10 live plan generations and 37 offline tests. Qdrant live integration was not run.
