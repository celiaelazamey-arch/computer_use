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
  test)
    exec "$PYTHON" -m unittest discover -s tests -v
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
