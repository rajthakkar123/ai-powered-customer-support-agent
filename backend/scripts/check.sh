#!/usr/bin/env bash
# Quality gate: the same steps as scripts/check.ps1, for Linux, macOS, and CI.
set -Eeuo pipefail

BACKEND_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BACKEND_ROOT"
if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python
elif [[ -x .venv/Scripts/python.exe ]]; then PYTHON=.venv/Scripts/python.exe
else PYTHON="${PYTHON:-python3}"; fi

step() { echo "==> $1"; shift; "$@" || { echo "FAILED: $1" >&2; exit 1; }; }

step "Dev tools are installed" "$PYTHON" -m scripts.ensure_requirements requirements-dev.txt
step "Python syntax" "$PYTHON" -m compileall -q support_agent tests scripts
step "Unit, workflow, and security tests with coverage" "$PYTHON" -m coverage run -m unittest discover
step "Coverage threshold" "$PYTHON" -m coverage report
step "Offline scenario evals" "$PYTHON" -m scripts.run_evals
step "Ruff lint" "$PYTHON" -m ruff check support_agent tests scripts
step "Ruff format" "$PYTHON" -m ruff format --check support_agent tests scripts
step "Mypy type check" "$PYTHON" -m mypy
step "Secret scan" "$PYTHON" -m scripts.scan_secrets
if command -v node >/dev/null 2>&1; then
  step "Frontend JavaScript syntax" node --check ../frontend/assets/support.js
else
  echo "==> Frontend JavaScript syntax: skipped (Node.js not installed)"
fi
echo "All checks passed."
