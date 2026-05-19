# Xiaoyuan QQ Bot - Startup Script
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "============================================"
Write-Host "  Xiaoyuan QQ Bot"
Write-Host "============================================"
Write-Host ""

# === Clean old processes ===
Write-Host "[Clean] Checking old processes..."
$port8080 = netstat -ano | Select-String ":8080 .*LISTENING"
if ($port8080) {
    $pidMatch = [regex]::Match($port8080, '\s+(\d+)\s*$')
    if ($pidMatch.Success) {
        $oldPid = $pidMatch.Groups[1].Value
        Write-Host "[Clean] Killing port 8080 process $oldPid..."
        taskkill /F /PID $oldPid 2>&1 | Out-Null
    }
}
Get-Process -Name "llbot", "node", "pmhq" -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "[Clean] Done"
Write-Host ""

# === Find Python ===
$pythonCmd = $null
foreach ($cmd in @("python", "python3")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { $pythonCmd = $cmd; break }
}
if (-not $pythonCmd) {
    $paths = @(
        "D:\Python312\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($p in $paths) {
        if (Test-Path $p) { $pythonCmd = $p; break }
    }
}
if (-not $pythonCmd) {
    Write-Host "[Error] Python not found" -ForegroundColor Red
    Write-Host "Install from https://www.python.org/downloads/"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[Python] $pythonCmd"
$env:PYTHONIOENCODING = "utf-8"

# === Install deps (first run) ===
if (-not (Test-Path "src\xiaomo_bot.egg-info")) {
    Write-Host "[Install] Installing dependencies..."
    & $pythonCmd -m pip install -e . --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[Error] Dependency install failed" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "[Done] Dependencies installed"
}

# === First run init ===
if (-not (Test-Path ".env")) {
    Write-Host "[Init] Creating .env - please add your API key"
    Copy-Item .env.example .env
    Write-Host "[Hint] Edit .env and set DEEPSEEK_API_KEY, then re-run" -ForegroundColor Yellow
    Start-Process notepad .env
    Read-Host "Press Enter to exit"
    exit 0
}

if (-not (Test-Path "data\persona.md")) {
    Write-Host "[Init] Creating data\persona.md"
    Copy-Item data\persona.example.md data\persona.md
}

# === Start LLBot ===
$llbotExe = $null
$llbotDir = $null
$searchPaths = @(
    "$scriptDir\llbot\llbot.exe",
    "$env:USERPROFILE\LLBot\llbot.exe",
    "C:\LLBot\llbot.exe"
)
foreach ($p in $searchPaths) {
    if (Test-Path $p) {
        $llbotExe = $p
        $llbotDir = Split-Path $p
        break
    }
}

$llbotRunning = Get-Process -Name "llbot" -ErrorAction SilentlyContinue
if ($llbotExe -and -not $llbotRunning) {
    Write-Host "[LLBot] Found at $llbotDir"
    # Patch configs to enable ws-reverse
    if (Test-Path "$scriptDir\llbot.config.json") {
        Copy-Item "$scriptDir\llbot.config.json" "$llbotDir\config.json" -Force
    }
    if (Test-Path "$scriptDir\llbot.default_config.json") {
        Copy-Item "$scriptDir\llbot.default_config.json" "$llbotDir\bin\llbot\default_config.json" -Force
    }
    # Remove old per-QQ config so it regenerates with ws-reverse enabled
    $oldConfigs = Get-ChildItem "$llbotDir\bin\llbot\data\config_*.json" -ErrorAction SilentlyContinue
    if ($oldConfigs) {
        Write-Host "[LLBot] Updating QQ config..."
        Remove-Item "$llbotDir\bin\llbot\data\config_*.json" -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[LLBot] Starting..."
    Start-Process -FilePath $llbotExe -WorkingDirectory $llbotDir
    Write-Host "[LLBot] Waiting for injection and login (15s)..."
    Start-Sleep -Seconds 15
} elseif ($llbotRunning) {
    Write-Host "[LLBot] Already running"
} else {
    Write-Host "[Hint] LLBot not found, skipping QQ bridge" -ForegroundColor Yellow
    Write-Host "       Download: https://github.com/LLOneBot/LuckyLilliaBot/releases"
}

# === Start bot ===
Write-Host "[Start] Launching bot..."
Write-Host "============================================"
Write-Host "  Bot running - DO NOT CLOSE this window"
Write-Host "============================================"
Write-Host ""
& $pythonCmd -u bot.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[Error] Bot exited with code $LASTEXITCODE" -ForegroundColor Red
    Write-Host "Common causes:"
    Write-Host "  1. Port 8080 in use - close other programs"
    Write-Host "  2. API key not set or invalid in .env"
    Write-Host "  3. Missing dependencies - run: pip install -e ."
    Write-Host ""
}
Read-Host "Press Enter to exit"
