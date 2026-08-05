from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_linux_start_script_supports_bot_and_bridge_modes():
    text = read_text("start_bot.sh")

    assert "#!/usr/bin/env bash" in text
    assert "--no-llbot" in text
    assert "--kill-old" in text
    assert "python3" in text
    assert "bot.py" in text
    assert "ws://127.0.0.1:${PORT}/onebot/v11/ws" in text


def test_linux_autostart_installer_uses_systemd_user_service():
    text = read_text("scripts/install_linux_autostart.sh")

    assert "systemctl --user enable" in text
    assert "xiaomo-bot.service" in text
    assert 'ExecStart=/usr/bin/env bash "$ROOT_DIR/start_bot.sh"' in text
    assert "loginctl enable-linger" in text


def test_windows_autostart_uses_quoted_absolute_vbs_target():
    text = read_text("start_bot.vbs")

    assert 'fso.BuildPath(scriptDir, "start_bot.bat")' in text
    assert 'Chr(34) & batPath & Chr(34)' in text
    assert "ws.CurrentDirectory = scriptDir" in text


def test_windows_startup_installer_creates_startup_shortcut():
    text = read_text("scripts/install_windows_autostart.ps1")

    assert '[Environment]::GetFolderPath("Startup")' in text
    assert "CreateShortcut" in text
    assert "start_bot.vbs" in text
    assert "WorkingDirectory" in text


def test_windows_start_script_does_not_globally_kill_node_or_llbot():
    text = read_text("start_bot.ps1")

    assert 'Get-Process -Name "llbot", "node", "pmhq"' not in text
    assert "function Test-ProjectProcess" in text
    assert "Port 8080 is used by another process" in text
    assert "Read-Host" not in text


def test_windows_start_script_restarts_and_archives_bot_logs():
    text = read_text("start_bot.ps1")

    assert "function Start-BotProcess" in text
    assert "function Wait-BotReady" in text
    assert "data\\startup_history" in text
    assert "[Restart] Restarting bot" in text
    assert "Local\\XiaomoBotSupervisor" in text
    assert "[LLBot] Process missing, restarting" in text
    assert 'Remove-Item "$llbotDir\\bin\\llbot\\data\\config_*.json"' not in text


def test_plugin_starts_vector_store_in_background():
    text = read_text("src/plugins/xiaomo/__init__.py")

    assert "start_vector_store_init()" in text
    assert "await init_vector_store()" not in text
