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

# Single source of truth for the repository-level documents that must stay
# formatted. Used by both `check-all` and `preflight` so the two gates cannot
# drift apart.
prettier_check_root_docs() {
  (
    cd "$ROOT/ccelia2027"
    npm exec -- prettier --check \
      ../README.md \
      ../CHANGELOG.md \
      ../docs/*.md \
      ../.github/dependabot.yml \
      ../.github/workflows/*.yml \
      ../CCELIA2027-DEPLOYMENT.md
  )
}

python_is_ready() {
  "$PYTHON" -c 'import cela_sh' >/dev/null 2>&1
}

worker_is_ready() {
  command -v npm >/dev/null 2>&1 && [[ -d "$ROOT/ccelia2027/node_modules" ]]
}

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
  preflight)
    # The offline gate to run before pushing. It never starts Docker, Ollama, or
    # a deployment, and it reports what it skipped instead of silently passing.
    status=0
    if python_is_ready; then
      "$PYTHON" -m unittest discover -s "$ROOT/tests" || status=1
      "$PYTHON" "$ROOT/tools/api_surface.py" --check || status=1
      "$PYTHON" -m pip wheel --no-deps --wheel-dir "${RUNNER_TEMP:-/tmp}/cela-sh-wheels" "$ROOT" >/dev/null || status=1
    else
      printf 'SKIPPED python checks: dependencies are missing. Run: bash ./cela.sh install\n' >&2
      status=1
    fi
    if worker_is_ready; then
      (
        cd "$ROOT/ccelia2027"
        npm run lint
        npm run format:check
        npm test
        npm run build
      ) || status=1
      prettier_check_root_docs || status=1
    else
      printf 'SKIPPED worker checks: npm or node_modules is missing. Run: bash ./cela.sh install-all\n' >&2
    fi
    if [[ "$status" -eq 0 ]]; then
      printf 'preflight passed.\n' >&2
    fi
    exit "$status"
    ;;
  api-surface)
    shift
    if ! python_is_ready; then
      printf 'CELA.sh is not installed. Run: bash ./cela.sh install\n' >&2
      exit 2
    fi
    exec "$PYTHON" "$ROOT/tools/api_surface.py" "$@"
    ;;
  check-all)
    if ! python_is_ready; then
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
    "$PYTHON" "$ROOT/tools/api_surface.py" --check
    "$PYTHON" -m pip wheel --no-deps --wheel-dir "${RUNNER_TEMP:-/tmp}/cela-sh-wheels" "$ROOT"
    (
      cd "$ROOT/ccelia2027"
      npm run lint
      npm run format:check
      npm test
      npm audit --audit-level=high
      npm run build
    )
    prettier_check_root_docs
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

if ! python_is_ready; then
  printf 'CELA.sh is not installed. Run: bash ./cela.sh install\n' >&2
  exit 2
fi

exec "$PYTHON" -m cela_sh "$@"
