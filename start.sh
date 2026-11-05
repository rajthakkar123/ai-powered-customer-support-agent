#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
VENV_DIR="$BACKEND_DIR/.venv"
DB_FILE="$BACKEND_DIR/data/support_agent.sqlite3"
if [[ -x "$VENV_DIR/bin/python" ]]; then
  VENV_PYTHON="$VENV_DIR/bin/python"
elif [[ -x "$VENV_DIR/Scripts/python.exe" ]]; then
  VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
else
  VENV_PYTHON=""
fi

if command -v python3 >/dev/null 2>&1; then
  BASE_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  BASE_PYTHON="$(command -v python)"
else
  echo "Python 3.11+ is required. Install it and rerun ./start.sh." >&2
  exit 1
fi

if [[ -z "$VENV_PYTHON" ]]; then
  echo "Creating backend virtual environment..."
  "$BASE_PYTHON" -m venv "$VENV_DIR"
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    VENV_PYTHON="$VENV_DIR/bin/python"
  else
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
  fi
fi
PYTHON="$VENV_PYTHON"
# Checked on every start so a newly added dependency is installed even if the venv is older.
if ! (cd "$BACKEND_DIR" && "$PYTHON" -m scripts.ensure_requirements); then
  echo "Backend requirement installation failed." >&2
  exit 1
fi

if [[ -f "$BACKEND_DIR/.env" ]]; then
  for setting in SUPPORT_ADMIN_EMAIL SUPPORT_ADMIN_PASSWORD SUPPORT_HOST SUPPORT_PORT \
    SUPPORT_DATABASE_PATH SUPPORT_AS_OF_DATE SUPPORT_ENVIRONMENT OPENAI_API_KEY OPENAI_MODEL; do
    if [[ -z "${!setting:-}" ]]; then
      setting_value="$(CSA_DOTENV_PATH="$BACKEND_DIR/.env" CSA_DOTENV_KEY="$setting" \
        "$PYTHON" -c 'import os,sys; from dotenv import dotenv_values; print(dotenv_values(os.environ["CSA_DOTENV_PATH"]).get(os.environ["CSA_DOTENV_KEY"]) or "")')"
      if [[ -n "$setting_value" ]]; then export "$setting=$setting_value"; fi
    fi
  done
fi

PORT="${SUPPORT_PORT:-8000}"
export SUPPORT_DATABASE_PATH="${SUPPORT_DATABASE_PATH:-$DB_FILE}"

echo "Syncing SQLite schema and sample data..."
(cd "$BACKEND_DIR" && "$PYTHON" -m support_agent.infrastructure.database)

export SUPPORT_PORT="$PORT"
# Local launches are demos: development mode enables the simulated tool-failure switch.
export SUPPORT_ENVIRONMENT="${SUPPORT_ENVIRONMENT:-development}"
export SUPPORT_AS_OF_DATE="${SUPPORT_AS_OF_DATE:-$(date +%F)}"
URL="http://127.0.0.1:$PORT/"

echo "Starting Customer Support Agent at $URL"
echo "Sign in with a staff email and password. On first run the server prints the"
echo "generated administrator password once, unless SUPPORT_ADMIN_PASSWORD is set."

open_browser() {
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "$URL" >/dev/null 2>&1 || true
  elif command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "Start-Process '$URL'" >/dev/null 2>&1 || true
  else echo "Open $URL in your browser."; fi
}

(
  cd "$BACKEND_DIR"
  "$PYTHON" -m support_agent
) &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for _ in $(seq 1 40); do
  if command -v curl >/dev/null 2>&1 && curl --silent --fail "$URL" >/dev/null; then
    open_browser
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    wait "$SERVER_PID"
    exit $?
  fi
  sleep 0.25
done

wait "$SERVER_PID"
