#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
    printf "Missing project environment. Run: cd %q && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'\n" "$REPO_ROOT" >&2
    exit 1
fi

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m lg_tv_automation.cli.edid verify "$@"
