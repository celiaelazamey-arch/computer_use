#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

SYSTEM_PYTHON="${CELA_PYTHON:-python3}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$SYSTEM_PYTHON"
fi

case "${1:-}" in
  install)
    if ! command -v "$SYSTEM_PYTHON" >/dev/null 2>&1; then
      printf 'Python 3 is required.\n' >&2
      exit 2
    fi
    "$SYSTEM_PYTHON" -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/python" -m pip install -e '.[dev]'
    exit 0
    ;;
  install-all)
    if ! command -v "$SYSTEM_PYTHON" >/dev/null 2>&1; then
      printf 'Python 3 is required.\n' >&2
      exit 2
    fi
    if ! command -v npm >/dev/null 2>&1; then
      printf 'Node.js and npm are required to install the full monorepo.\n' >&2
      exit 2
    fi
    "$SYSTEM_PYTHON" -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/python" -m pip install -e '.[dev]'
    npm --prefix "$ROOT/ccelia2027" ci
    exit 0
    ;;
  test)
    exec "$PYTHON" -m unittest discover -s tests -v
    ;;
  check-all)
    if ! "$PYTHON" -c 'import cela_sh' >/dev/null 2>&1; then
      printf 'Python dependencies are missing. Run: bash ./cela.sh install-all\n' >&2
      exit 2
    fi
    if ! command -v npm >/dev/null 2>&1; then
      printf 'Node.js and npm are required to check the full monorepo.\n' >&2
      exit 2
    fi
    if [[ ! -d "$ROOT/ccelia2027/node_modules" ]]; then
      printf 'Worker dependencies are missing. Run: bash ./cela.sh install-all\n' >&2
      exit 2
    fi
    "$PYTHON" -m unittest discover -s "$ROOT/tests" -v
    "$PYTHON" -m pip wheel --no-deps --wheel-dir "${RUNNER_TEMP:-/tmp}/cela-sh-wheels" "$ROOT"
    (
      cd "$ROOT/ccelia2027"
      npm run lint
      npm run format:check
      npm exec -- prettier --check \
        ../README.md \
        ../CHANGELOG.md \
        ../docs/system-architecture.md \
        ../.github/dependabot.yml \
        ../.github/workflows/*.yml \
        ../CCELIA2027-DEPLOYMENT.md
      npm test
      npm audit --audit-level=high
      npm run build
    )
    exit 0
    ;;
  worker-dev)
    if ! command -v npm >/dev/null 2>&1; then
      printf 'Node.js and npm are required to run the Worker.\n' >&2
      exit 2
    fi
    if [[ ! -d "$ROOT/ccelia2027/node_modules" ]]; then
      printf 'Worker dependencies are missing. Run: bash ./cela.sh install-all\n' >&2
      exit 2
    fi
    exec npm --prefix "$ROOT/ccelia2027" run dev
    ;;
  services-up)
    if ! command -v docker >/dev/null 2>&1; then
      printf 'Docker Compose is required for local services.\n' >&2
      exit 2
    fi
    docker compose up -d ollama
    exit 0
    ;;
  services-up-memory)
    if ! command -v docker >/dev/null 2>&1; then
      printf 'Docker Compose is required for local memory services.\n' >&2
      exit 2
    fi
    docker compose --profile memory up -d ollama qdrant
    exit 0
    ;;
  services-stop)
    if ! command -v docker >/dev/null 2>&1; then
      printf 'Docker Compose is required to stop local services.\n' >&2
      exit 2
    fi
    docker compose stop ollama
    docker compose --profile memory stop qdrant
    exit 0
    ;;
  probe)
    exec "$PYTHON" tests/integration_plan_probe.py
    ;;
  probe-memory)
    exec "$PYTHON" tests/integration_live_probe.py
    ;;
esac

if ! "$PYTHON" -c 'import cela_sh' >/dev/null 2>&1; then
  printf 'CELA.sh is not installed. Run: bash ./cela.sh install\n' >&2
  exit 2
fi

exec "$PYTHON" -m cela_sh "$@"
