#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="xiaomo-bot.service"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$SERVICE_DIR/$SERVICE_NAME"
START_ARGS="--no-install"
START_NOW=0
DISABLE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --now) START_NOW=1 ;;
    --no-llbot) START_ARGS="$START_ARGS --no-llbot" ;;
    --disable|--remove) DISABLE=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/install_linux_autostart.sh [--now] [--no-llbot] [--disable]

Installs a systemd user service for Xiaomo.
Use --now to start it immediately.
Use --no-llbot if the QQ bridge is managed separately.
EOF
      exit 0
      ;;
    *) echo "[Error] Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if ! command -v systemctl >/dev/null 2>&1; then
  echo "[Error] systemctl not found. This installer requires systemd user services." >&2
  exit 1
fi

mkdir -p "$SERVICE_DIR"
chmod +x "$ROOT_DIR/start_bot.sh" 2>/dev/null || true

if (( DISABLE == 1 )); then
  systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
  rm -f "$SERVICE_PATH"
  systemctl --user daemon-reload
  echo "[Done] Removed $SERVICE_NAME"
  exit 0
fi

cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=Xiaomo QQ group-chat bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory="$ROOT_DIR"
ExecStart=/usr/bin/env bash "$ROOT_DIR/start_bot.sh" $START_ARGS
Restart=on-failure
RestartSec=10
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"

if (( START_NOW == 1 )); then
  systemctl --user restart "$SERVICE_NAME"
fi

echo "[Done] Installed $SERVICE_PATH"
echo "[Hint] Check status: systemctl --user status $SERVICE_NAME"
echo "[Hint] View logs: journalctl --user -u $SERVICE_NAME -f"
echo "[Hint] To run before login on a headless box: sudo loginctl enable-linger $USER"
