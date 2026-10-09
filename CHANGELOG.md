# Changelog

## 0.1.0

- Deliver a local CLI that converts a goal and optional text context into a strict JSON plan through Ollama.
- Validate every model response against a Pydantic schema and add deterministic risk labels.
- Keep execution disabled in every returned plan; do not capture screens, click, type, run commands, or save memories automatically.
- Keep Qdrant memory optional, explicitly enabled, and scoped to a required session ID.
- Add a live `probe` that generates a real plan; keep the Qdrant integration check separate as `probe-memory`.
- Default to `qwen2.5:3b`, which was live-tested on Ollama 0.40.2 in an ARM64 Linux environment with 8 GB RAM.
- Verify 10/10 live plan generations and 37 offline tests. Qdrant live integration was not run.
