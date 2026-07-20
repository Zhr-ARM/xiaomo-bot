param(
    [switch]$NoInstall,
    [switch]$SkipLLBot
)

# Xiaoyuan QQ Bot - Startup Script
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
New-Item -ItemType Directory -Force -Path (Join-Path $scriptDir "data") | Out-Null

Write-Host "============================================"
Write-Host "  Xiaoyuan QQ Bot"
Write-Host "============================================"
Write-Host ""

# === Clean old project processes ===
function Test-ProjectProcess($proc) {
    $cmd = [string]$proc.CommandLine
    if (-not $cmd) { return $false }
    if ($cmd -like "*$scriptDir*") { return $true }
    if ($proc.Name -like "python*" -and $cmd -match '(^|[\\/\s])bot\.py($|\s)') { return $true }
    return $false
}

function Stop-ProcessTree($pidValue) {
    if (-not $pidValue -or [int]$pidValue -eq $PID) { return }
    taskkill /T /F /PID $pidValue 2>&1 | Out-Null
}

Write-Host "[Clean] Checking old project processes..."
$port8080 = netstat -ano | Select-String ":8080 .*LISTENING"
foreach ($line in $port8080) {
    $pidMatch = [regex]::Match($line.Line, '\s+(\d+)\s*$')
    if (-not $pidMatch.Success) { continue }
    $oldPid = [int]$pidMatch.Groups[1].Value
    $oldProc = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue
    if ($oldProc -and (Test-ProjectProcess $oldProc)) {
        Write-Host "[Clean] Killing old bot on port 8080: $oldPid"
        Stop-ProcessTree $oldPid
    } elseif ($oldProc) {
        Write-Host "[Error] Port 8080 is used by another process: $($oldProc.Name) PID=$oldPid" -ForegroundColor Red
        Write-Host "        CommandLine: $($oldProc.CommandLine)"
        exit 1
    }
}

$oldProjectProcesses = Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and
    $_.Name -match '^(python|python3|llbot|node|pmhq)\.exe$' -and
    (Test-ProjectProcess $_)
}
foreach ($proc in $oldProjectProcesses) {
    Write-Host "[Clean] Stopping $($proc.Name) PID=$($proc.ProcessId)"
    Stop-ProcessTree $proc.ProcessId
}
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
    exit 1
}
Write-Host "[Python] $pythonCmd"
$env:PYTHONIOENCODING = "utf-8"

# === Install deps (first run) ===
if ((-not $NoInstall) -and (-not (Test-Path "src\xiaomo_bot.egg-info"))) {
    Write-Host "[Install] Installing dependencies..."
    & $pythonCmd -m pip install -e . --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[Error] Dependency install failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "[Done] Dependencies installed"
}

# === First run init ===
if (-not (Test-Path ".env")) {
    Write-Host "[Init] Creating .env - please add your API key"
    Copy-Item .env.example .env
    Write-Host "[Hint] Edit .env and set LLM_API_KEY, then re-run" -ForegroundColor Yellow
    if ([Environment]::UserInteractive) {
        Start-Process notepad .env -ErrorAction SilentlyContinue
    }
    exit 1
}

if (-not (Test-Path "data\persona.md")) {
    Write-Host "[Init] Creating data\persona.md"
    Copy-Item data\persona.example.md data\persona.md
}

# === Start bot (Python) first, in background ===
Write-Host "[Start] Launching bot (NoneBot2)..."
$botProc = Start-Process -FilePath $pythonCmd -ArgumentList "-u", "bot.py" -WorkingDirectory $scriptDir -PassThru -NoNewWindow -RedirectStandardOutput "$scriptDir\data\_bot_stdout.log" -RedirectStandardError "$scriptDir\data\_bot_stderr.log"
Write-Host "[Start] Bot PID: $($botProc.Id), waiting for port 8080..."

# Wait for NoneBot2 to be ready
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    $portCheck = netstat -ano | Select-String ":8080 .*LISTENING"
    if ($portCheck) {
        $ready = $true
        Write-Host "[Start] Bot is ready (port 8080 listening)"
        break
    }
    if ($botProc.HasExited) {
        Write-Host "[Error] Bot process exited unexpectedly" -ForegroundColor Red
        Write-Host "--- stderr tail ---"
        Get-Content "$scriptDir\data\_bot_stderr.log" -Tail 20 -ErrorAction SilentlyContinue
        exit 1
    }
    Write-Host "." -NoNewline
}
if (-not $ready) {
    Write-Host ""
    Write-Host "[Error] Bot failed to start within 2 minutes" -ForegroundColor Red
    Stop-Process -Id $botProc.Id -Force -ErrorAction SilentlyContinue
    exit 1
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

$llbotRunning = $false
if ($llbotDir) {
    $llbotRunning = [bool](Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "llbot.exe" -and $_.CommandLine -like "*$llbotDir*"
    })
}
if ($SkipLLBot) {
    Write-Host "[LLBot] Skipped by -SkipLLBot"
} elseif ($llbotExe -and -not $llbotRunning) {
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

# === All services running ===
Write-Host "============================================"
Write-Host "  All services running - DO NOT CLOSE this window"
Write-Host "  Bot PID: $($botProc.Id)"
Write-Host "============================================"
Write-Host ""
Write-Host "Press Ctrl+C to stop all services"

# Monitor bot process and restart if it crashes
while (-not $botProc.HasExited) {
    Start-Sleep -Seconds 5
}
Write-Host ""
Write-Host "[Error] Bot process exited with code $($botProc.ExitCode)" -ForegroundColor Red
Get-Content "$scriptDir\data\_bot_stderr.log" -Tail 30 -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "Common causes:"
Write-Host "  1. Port 8080 in use - close other programs"
Write-Host "  2. API key not set or invalid in .env"
Write-Host "  3. Missing dependencies - run: pip install -e ."
exit $botProc.ExitCode
