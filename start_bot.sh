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

stop_old_project_processes() {
  echo "[Clean] Stopping old project processes..."
  pkill -f "$ROOT_DIR/.*/?bot.py" 2>/dev/null || true
  pkill -f "$ROOT_DIR/llbot" 2>/dev/null || true
  pkill -f "llbot.js.*--pmhq-port" 2>/dev/null || true
}

wait_for_port() {
  local waited=0
  while (( waited < 120 )); do
    if port_open; then
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
  echo "[Error] Bot did not listen on ${HOST}:${PORT} within 120 seconds" >&2
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

start_llbot_if_available() {
  (( SKIP_LLBOT == 0 )) || {
    echo "[LLBot] Skipped by --no-llbot"
    return 0
  }

  local llbot_exe
  if ! llbot_exe="$(find_llbot)"; then
    echo "[Hint] LLBot not found. Start NapCat/LLBot separately and point ws-reverse to ws://127.0.0.1:${PORT}/onebot/v11/ws"
    return 0
  fi

  local llbot_dir
  llbot_dir="$(cd "$(dirname "$llbot_exe")" && pwd)"
  chmod +x "$llbot_exe" 2>/dev/null || true

  if pgrep -f "$llbot_exe" >/dev/null 2>&1; then
    echo "[LLBot] Already running: $llbot_exe"
    return 0
  fi

  if [[ -f "$ROOT_DIR/llbot.config.json" ]]; then
    cp -f "$ROOT_DIR/llbot.config.json" "$llbot_dir/config.json" 2>/dev/null || true
  fi
  if [[ -f "$ROOT_DIR/llbot.default_config.json" && -d "$llbot_dir/bin/llbot" ]]; then
    cp -f "$ROOT_DIR/llbot.default_config.json" "$llbot_dir/bin/llbot/default_config.json" 2>/dev/null || true
  fi

  echo "[LLBot] Starting: $llbot_exe"
  (cd "$llbot_dir" && "$llbot_exe" >>"$ROOT_DIR/data/_llbot_stdout.log" 2>>"$ROOT_DIR/data/_llbot_stderr.log" &)
}

PYTHON_CMD="$(find_python || true)"
if [[ -z "$PYTHON_CMD" ]]; then
  echo "[Error] Python 3.10+ not found. Install python3 first." >&2
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

if (( SKIP_INSTALL == 0 )) && [[ ! -d "$ROOT_DIR/src/xiaomo_bot.egg-info" ]]; then
  echo "[Install] Installing dependencies..."
  "$PYTHON_CMD" -m pip install -e . --quiet
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
"$PYTHON_CMD" -u bot.py >>"$BOT_STDOUT" 2>>"$BOT_STDERR" &
BOT_PID=$!
echo "[Start] Bot PID: $BOT_PID, waiting for ${CHECK_HOST}:${PORT}..."

cleanup() {
  if [[ -n "${BOT_PID:-}" ]] && kill -0 "$BOT_PID" 2>/dev/null; then
    kill "$BOT_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM

wait_for_port
echo "[Start] Bot is ready"

start_llbot_if_available

echo "============================================"
echo "  Xiaoyuan bot is running"
echo "  Bot PID: $BOT_PID"
echo "  Logs: data/_bot_stdout.log / data/_bot_stderr.log"
echo "============================================"

wait "$BOT_PID"
