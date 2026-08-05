param(
    [switch]$NoInstall,
    [switch]$SkipLLBot
)

# Xiaoyuan QQ Bot - Startup Script
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
New-Item -ItemType Directory -Force -Path (Join-Path $scriptDir "data") | Out-Null

# Keep autostart and manual launches from creating competing supervisors.
$supervisorCreated = $false
$supervisorMutex = New-Object System.Threading.Mutex(
    $true,
    "Local\XiaomoBotSupervisor",
    [ref]$supervisorCreated
)
if (-not $supervisorCreated) {
    Write-Host "[Skip] Xiaomo bot supervisor is already running"
    exit 0
}

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
$botStdout = Join-Path $scriptDir "data\_bot_stdout.log"
$botStderr = Join-Path $scriptDir "data\_bot_stderr.log"
$botLogArchive = Join-Path $scriptDir "data\startup_history"
New-Item -ItemType Directory -Force -Path $botLogArchive | Out-Null

function Archive-BotLogs {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    foreach ($entry in @(
        @{ Path = $botStdout; Name = "bot_stdout" },
        @{ Path = $botStderr; Name = "bot_stderr" }
    )) {
        if (Test-Path $entry.Path) {
            $item = Get-Item -LiteralPath $entry.Path -ErrorAction SilentlyContinue
            if ($item -and $item.Length -gt 0) {
                Copy-Item -LiteralPath $entry.Path -Destination (Join-Path $botLogArchive "$($entry.Name)-$stamp.log") -Force
            }
        }
    }
}

function Start-BotProcess {
    Archive-BotLogs
    Write-Host "[Start] Launching bot (NoneBot2)..."
    $proc = Start-Process -FilePath $pythonCmd -ArgumentList "-u", "bot.py" -WorkingDirectory $scriptDir -PassThru -NoNewWindow -RedirectStandardOutput $botStdout -RedirectStandardError $botStderr
    Write-Host "[Start] Bot PID: $($proc.Id), waiting for port 8080..."
    return $proc
}

function Wait-BotReady($proc) {
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        $portCheck = netstat -ano | Select-String ":8080 .*LISTENING"
        if ($portCheck) {
            Write-Host "[Start] Bot is ready (port 8080 listening)"
            return $true
        }
        if ($proc.HasExited) {
            Write-Host "[Error] Bot process exited unexpectedly (code $($proc.ExitCode))" -ForegroundColor Red
            $stderrTail = Get-Content $botStderr -Tail 30 -ErrorAction SilentlyContinue
            foreach ($line in $stderrTail) {
                Write-Host $line
            }
            return $false
        }
        Write-Host "." -NoNewline
    }

    Write-Host ""
    Write-Host "[Error] Bot failed to start within 2 minutes" -ForegroundColor Red
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    return $false
}

$restartDelaySeconds = 5
$botProc = $null
while ($true) {
    $botProc = Start-BotProcess
    if (Wait-BotReady $botProc) {
        break
    }
    Write-Host "[Restart] Retrying bot startup in $restartDelaySeconds seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds $restartDelaySeconds
    $restartDelaySeconds = [Math]::Min(60, $restartDelaySeconds * 2)
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
    # Keep the generated per-account config; it contains the working login and ws-reverse settings.
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

# Monitor bot process and restart it after unexpected exits.
$restartDelaySeconds = 5
$lastLLBotRestart = 0
while ($true) {
    while (-not $botProc.HasExited) {
        Start-Sleep -Seconds 5
        if ((-not $SkipLLBot) -and $llbotExe) {
            $llbotAlive = [bool](Get-CimInstance Win32_Process | Where-Object {
                $_.Name -eq "llbot.exe" -and $_.CommandLine -like "*$llbotDir*"
            })
            $nowSeconds = [DateTimeOffset]::Now.ToUnixTimeSeconds()
            if ((-not $llbotAlive) -and ($nowSeconds - $lastLLBotRestart -ge 30)) {
                Write-Host "[LLBot] Process missing, restarting..." -ForegroundColor Yellow
                Start-Process -FilePath $llbotExe -WorkingDirectory $llbotDir
                $lastLLBotRestart = $nowSeconds
            }
        }
    }

    Write-Host ""
    Write-Host "[Error] Bot process exited with code $($botProc.ExitCode)" -ForegroundColor Red
    Get-Content $botStderr -Tail 30 -ErrorAction SilentlyContinue
    Write-Host "[Restart] Restarting bot in $restartDelaySeconds seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds $restartDelaySeconds

    $botProc = Start-BotProcess
    if (Wait-BotReady $botProc) {
        $restartDelaySeconds = 5
    } else {
        $restartDelaySeconds = [Math]::Min(60, $restartDelaySeconds * 2)
    }
}
