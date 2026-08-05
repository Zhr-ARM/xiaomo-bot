#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SKIP_INSTALL=0
SKIP_LLBOT=0
KILL_OLD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-install) SKIP_INSTALL=1 ;;
    --no-llbot) SKIP_LLBOT=1 ;;
    --kill-old) KILL_OLD=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./start_bot.sh [--no-install] [--no-llbot] [--kill-old]

Starts the NoneBot service on Linux/macOS. If a Linux LLBot binary is found,
it is started after the bot is listening on port 8080.
EOF
      exit 0
      ;;
    *) echo "[Error] Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$ROOT_DIR/data"
BOT_STDOUT="$ROOT_DIR/data/_bot_stdout.log"
BOT_STDERR="$ROOT_DIR/data/_bot_stderr.log"
LLBOT_STDOUT="$ROOT_DIR/data/_llbot_stdout.log"
LLBOT_STDERR="$ROOT_DIR/data/_llbot_stderr.log"
LOG_ARCHIVE="$ROOT_DIR/data/startup_history"
mkdir -p "$LOG_ARCHIVE"

export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

read_env_value() {
  local key="$1"
  local file="$ROOT_DIR/.env"
  [[ -f "$file" ]] || return 0
  awk -F= -v key="$key" '
    $1 == key {
      value=$0
      sub("^[^=]*=", "", value)
      gsub(/^["'\'' ]+|["'\'' ]+$/, "", value)
      print value
      exit
    }
  ' "$file"
}

HOST="${HOST:-$(read_env_value HOST)}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-$(read_env_value PORT)}"
PORT="${PORT:-8080}"
CHECK_HOST="$HOST"
if [[ "$CHECK_HOST" == "0.0.0.0" || "$CHECK_HOST" == "::" ]]; then
  CHECK_HOST="127.0.0.1"
fi

find_python() {
  local candidates=(
    "$ROOT_DIR/.venv/bin/python"
    "$ROOT_DIR/venv/bin/python"
    "python3"
    "python"
  )
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

port_open() {
  timeout 1 bash -c ":</dev/tcp/${CHECK_HOST}/${PORT}" >/dev/null 2>&1
}

http_ok() {
  local path="$1"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --max-time 2 "http://${CHECK_HOST}:${PORT}${path}" >/dev/null 2>&1
    return
  fi
  "$PYTHON_CMD" -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2).read()' \
    "http://${CHECK_HOST}:${PORT}${path}" >/dev/null 2>&1
}

archive_logs() {
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  for entry in "$BOT_STDOUT:bot_stdout" "$BOT_STDERR:bot_stderr"; do
    local path="${entry%%:*}"
    local name="${entry##*:}"
    if [[ -s "$path" ]]; then
      cp -f "$path" "$LOG_ARCHIVE/${name}-${stamp}.log"
    fi
  done
  find "$LOG_ARCHIVE" -type f -name '*.log' -mtime +14 -delete 2>/dev/null || true
}

stop_old_project_processes() {
  echo "[Clean] Stopping old project processes..."
  pkill -f "$ROOT_DIR/.*/?bot.py" 2>/dev/null || true
  pkill -f "$ROOT_DIR/llbot" 2>/dev/null || true
  pkill -f "llbot.js.*--pmhq-port" 2>/dev/null || true
}

wait_for_port() {
  local waited=0
  while (( waited < 120 )); do
    if http_ok "/healthz"; then
      return 0
    fi
    if ! kill -0 "$BOT_PID" 2>/dev/null; then
      echo "[Error] Bot process exited before port ${PORT} became ready" >&2
      tail -n 80 "$BOT_STDERR" 2>/dev/null || true
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
    printf '.'
  done
  echo
  echo "[Error] Bot health check failed on ${HOST}:${PORT} within 120 seconds" >&2
  tail -n 80 "$BOT_STDERR" 2>/dev/null || true
  return 1
}

find_llbot() {
  local candidates=(
    "$ROOT_DIR/llbot/llbot"
    "$ROOT_DIR/llbot/llbot.AppImage"
    "$HOME/LLBot/llbot"
    "$HOME/LLBot/llbot.AppImage"
    "/opt/LLBot/llbot"
    "/opt/llbot/llbot"
  )
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  if command -v llbot >/dev/null 2>&1; then
    command -v llbot
    return 0
  fi
  return 1
}

LLBOT_EXE=""
LLBOT_DIR=""
LLBOT_PID=""
LLBOT_MANAGED=0

install_llbot_config() {
  local source="$1"
  local destination="$2"
  [[ -f "$source" ]] || return 0
  mkdir -p "$(dirname "$destination")"
  "$PYTHON_CMD" - "$source" "$destination" "$PORT" <<'PY'
import json
import pathlib
import sys

source, destination, port = sys.argv[1:]
data = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
for connection in data.get("ob11", {}).get("connect", []):
    if connection.get("type") == "ws-reverse":
        connection["url"] = f"ws://127.0.0.1:{port}/onebot/v11/ws"
pathlib.Path(destination).write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
}

launch_llbot() {
  [[ -n "$LLBOT_EXE" ]] || return 1
  echo "[LLBot] Starting: $LLBOT_EXE"
  pushd "$LLBOT_DIR" >/dev/null
  "$LLBOT_EXE" >>"$LLBOT_STDOUT" 2>>"$LLBOT_STDERR" &
  LLBOT_PID=$!
  popd >/dev/null
  LLBOT_MANAGED=1
}

start_llbot_if_available() {
  (( SKIP_LLBOT == 0 )) || {
    echo "[LLBot] Skipped by --no-llbot"
    return 0
  }

  if ! LLBOT_EXE="$(find_llbot)"; then
    echo "[Hint] LLBot not found. Start NapCat/LLBot separately and point ws-reverse to ws://127.0.0.1:${PORT}/onebot/v11/ws"
    return 0
  fi

  LLBOT_DIR="$(cd "$(dirname "$LLBOT_EXE")" && pwd)"
  chmod +x "$LLBOT_EXE" 2>/dev/null || true

  if pgrep -f "$LLBOT_EXE" >/dev/null 2>&1; then
    echo "[LLBot] Already running: $LLBOT_EXE"
    return 0
  fi

  install_llbot_config "$ROOT_DIR/llbot.config.json" "$LLBOT_DIR/config.json"
  install_llbot_config "$ROOT_DIR/llbot.default_config.json" "$LLBOT_DIR/bin/llbot/default_config.json"
  launch_llbot
}

PYTHON_CMD="$(find_python || true)"
if [[ -z "$PYTHON_CMD" ]]; then
  echo "[Error] Python 3.11+ not found. Install python3 first." >&2
  exit 1
fi
if ! "$PYTHON_CMD" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "[Error] Python 3.11+ is required." >&2
  exit 1
fi
echo "[Python] $PYTHON_CMD"

if (( KILL_OLD == 1 )); then
  stop_old_project_processes
  sleep 2
fi

if port_open; then
  echo "[Error] ${HOST}:${PORT} is already listening. Stop the old bot or run with --kill-old." >&2
  exit 1
fi

if (( SKIP_INSTALL == 0 )); then
  echo "[Install] Installing dependencies..."
  "$PYTHON_CMD" -m pip install -e . -c constraints.txt --quiet
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "[Init] Created .env. Fill LLM_API_KEY and rerun." >&2
  exit 1
fi

if [[ ! -f "$ROOT_DIR/data/persona.md" && -f "$ROOT_DIR/data/persona.example.md" ]]; then
  cp "$ROOT_DIR/data/persona.example.md" "$ROOT_DIR/data/persona.md"
fi

echo "[Start] Launching bot..."
archive_logs
"$PYTHON_CMD" -u bot.py >>"$BOT_STDOUT" 2>>"$BOT_STDERR" &
BOT_PID=$!
echo "[Start] Bot PID: $BOT_PID, waiting for ${CHECK_HOST}:${PORT}..."

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "${BOT_PID:-}" ]] && kill -0 "$BOT_PID" 2>/dev/null; then
    kill "$BOT_PID" 2>/dev/null || true
  fi
  if (( LLBOT_MANAGED == 1 )) && [[ -n "${LLBOT_PID:-}" ]] && kill -0 "$LLBOT_PID" 2>/dev/null; then
    kill "$LLBOT_PID" 2>/dev/null || true
  fi
  if (( LLBOT_MANAGED == 1 )) && [[ -n "${LLBOT_EXE:-}" ]]; then
    while read -r pid; do
      [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done < <(pgrep -f "$LLBOT_EXE" || true)
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

wait_for_port
echo "[Start] Bot is ready"

start_llbot_if_available

echo "============================================"
echo "  Xiaoyuan bot is running"
echo "  Bot PID: $BOT_PID"
echo "  Logs: data/_bot_stdout.log / data/_bot_stderr.log"
echo "============================================"

bridge_grace_until=$(( $(date +%s) + 120 ))
bridge_not_ready_since=0
last_llbot_restart=0
bot_health_failures=0

while kill -0 "$BOT_PID" 2>/dev/null; do
  sleep 10
  now=$(date +%s)
  if http_ok "/healthz"; then
    bot_health_failures=0
  else
    bot_health_failures=$((bot_health_failures + 1))
    if (( bot_health_failures >= 3 )); then
      echo "[Health] Bot became unresponsive; stopping for supervisor restart" >&2
      kill "$BOT_PID" 2>/dev/null || true
      break
    fi
  fi

  if (( SKIP_LLBOT == 0 )) && [[ -n "$LLBOT_EXE" ]]; then
    if ! pgrep -f "$LLBOT_EXE" >/dev/null 2>&1; then
      if (( now - last_llbot_restart >= 30 )); then
        echo "[LLBot] Process missing, restarting..."
        launch_llbot
        last_llbot_restart=$now
        bridge_not_ready_since=$now
      fi
    elif http_ok "/readyz"; then
      bridge_not_ready_since=0
    else
      if (( bridge_not_ready_since == 0 )); then
        bridge_not_ready_since=$now
      fi
      if (( now >= bridge_grace_until && now - bridge_not_ready_since >= 45 && now - last_llbot_restart >= 180 )); then
        echo "[LLBot] Process alive but QQ bridge is disconnected; restarting..."
        while read -r pid; do
          [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
        done < <(pgrep -f "$LLBOT_EXE" || true)
        sleep 2
        launch_llbot
        last_llbot_restart=$now
        bridge_not_ready_since=$now
      fi
    fi
  fi
done

set +e
wait "$BOT_PID"
status=$?
set -e
exit "$status"
