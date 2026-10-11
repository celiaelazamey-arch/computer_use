## Summary

<!-- What changed and why? Name the component: cela.sh, ccelia2027, or repo-wide. -->

## Changes

-

## Tests

Run the single offline gate, then tick what it covered:

- [ ] `bash ./cela.sh preflight` passed locally
- [ ] Python tests (`bash ./cela.sh test`)
- [ ] Contract snapshot reviewed (`bash ./cela.sh api-surface --check`)
- [ ] Worker lint, format, tests, and dry-run build
- [ ] Root docs formatting

## Contract impact

<!--
  Did this change an exported name, signature, Action member, model field, CLI
  flag, exit code, memory backend, or Worker route? If yes, the snapshot must be
  regenerated on purpose and the diff explained here.
-->

- [ ] No public contract change
- [ ] Snapshot regenerated with `bash ./cela.sh api-surface --update`; diff explained below

## Security impact

<!--
  Describe trust-boundary, data, dependency, token, or secret-handling changes.
  State explicitly whether any change adds execution ability, reaches a
  non-loopback network, moves data between the two components, or adds a secret.
-->

- [ ] None: no execution, no new network path, no new secret
- [ ] Described below

## Breaking changes

- [ ] None
- [ ] Described below

## Checklist

- [ ] No secrets, credentials, or personal data are included.
- [ ] Documentation is updated where behavior, settings, or operations changed.
- [ ] New behavior has a test that fails without the change.
- [ ] Version strings stay in sync (`pyproject.toml`, `ccelia2027/package.json`, `docs/architecture.md`, `docs/protocol.md`).
- [ ] Required review conversations are resolved.
