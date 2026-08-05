param(
    [switch]$NoInstall,
    [switch]$SkipLLBot
)

# Xiaoyuan QQ Bot - Startup Script
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
New-Item -ItemType Directory -Force -Path (Join-Path $scriptDir "data") | Out-Null

function Get-EnvValue($key, $defaultValue) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if ($value) { return $value }
    $envFile = Join-Path $scriptDir ".env"
    if (Test-Path $envFile) {
        $line = Get-Content -LiteralPath $envFile | Where-Object {
            $_ -match "^\s*$([regex]::Escape($key))\s*="
        } | Select-Object -First 1
        if ($line) {
            $parsed = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
            if ($parsed) { return $parsed }
        }
    }
    return $defaultValue
}

$botHost = Get-EnvValue "HOST" "127.0.0.1"
$botPort = [int](Get-EnvValue "PORT" "8080")
$checkHost = if ($botHost -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $botHost }
$healthUrl = "http://${checkHost}:${botPort}/healthz"
$readyUrl = "http://${checkHost}:${botPort}/readyz"
$botPidFile = Join-Path $scriptDir "data\bot.pid"

# Keep autostart and manual launches from creating competing supervisors.
$sha = New-Object System.Security.Cryptography.SHA256Managed
$pathBytes = [System.Text.Encoding]::UTF8.GetBytes($scriptDir.ToLowerInvariant())
$mutexSuffix = ([System.BitConverter]::ToString($sha.ComputeHash($pathBytes))).Replace("-", "").Substring(0, 12)
$sha.Dispose()
$supervisorCreated = $false
$supervisorMutex = New-Object System.Threading.Mutex(
    $true,
    "Local\XiaomoBotSupervisor-$mutexSuffix",
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
    return $false
}

function Stop-ProcessTree($pidValue) {
    if (-not $pidValue -or [int]$pidValue -eq $PID) { return }
    taskkill /T /F /PID $pidValue 2>&1 | Out-Null
}

Write-Host "[Clean] Checking old project processes..."
$knownBotPid = 0
if (Test-Path $botPidFile) {
    [void][int]::TryParse(
        (Get-Content $botPidFile -ErrorAction SilentlyContinue),
        [ref]$knownBotPid
    )
}
$portListeners = netstat -ano | Select-String ":$botPort .*LISTENING"
foreach ($line in $portListeners) {
    $pidMatch = [regex]::Match($line.Line, '\s+(\d+)\s*$')
    if (-not $pidMatch.Success) { continue }
    $oldPid = [int]$pidMatch.Groups[1].Value
    $oldProc = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue
    if ($oldProc -and ((Test-ProjectProcess $oldProc) -or $oldPid -eq $knownBotPid)) {
        Write-Host "[Clean] Killing old bot on port ${botPort}: $oldPid"
        Stop-ProcessTree $oldPid
    } elseif ($oldProc) {
        Write-Host "[Error] Port $botPort is used by another process: $($oldProc.Name) PID=$oldPid" -ForegroundColor Red
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
        "C:\Python311\python.exe"
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
$pythonVersionOk = & $pythonCmd -c "import sys; print(int(sys.version_info >= (3, 11)))"
if (($pythonVersionOk | Select-Object -Last 1) -ne "1") {
    Write-Host "[Error] Python 3.11+ is required" -ForegroundColor Red
    exit 1
}
$env:PYTHONIOENCODING = "utf-8"

# === Install deps (first run) ===
if (-not $NoInstall) {
    Write-Host "[Install] Installing dependencies..."
    & $pythonCmd -m pip install -e . -c constraints.txt --quiet
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
    Get-ChildItem -LiteralPath $botLogArchive -Filter "*.log" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $botLogArchive -Filter "*.log" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 60 |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Start-BotProcess {
    Archive-BotLogs
    Write-Host "[Start] Launching bot (NoneBot2)..."
    $botEntry = Join-Path $scriptDir "bot.py"
    $proc = Start-Process -FilePath $pythonCmd -ArgumentList "-u", $botEntry -WorkingDirectory $scriptDir -PassThru -NoNewWindow -RedirectStandardOutput $botStdout -RedirectStandardError $botStderr
    Set-Content -LiteralPath $botPidFile -Value $proc.Id -Encoding ASCII
    Write-Host "[Start] Bot PID: $($proc.Id), waiting for $healthUrl..."
    return $proc
}

function Wait-BotReady($proc) {
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        try {
            $health = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        } catch {
            $health = $null
        }
        if ($health -and $health.StatusCode -eq 200) {
            Write-Host "[Start] Bot HTTP health check passed"
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

function Install-LLBotConfig($sourcePath, $destinationPath) {
    if (-not (Test-Path $sourcePath)) { return }
    try {
        $destinationDir = Split-Path -Parent $destinationPath
        if ($destinationDir) {
            New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
        }
        $configObject = Get-Content -LiteralPath $sourcePath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($connection in $configObject.ob11.connect) {
            if ($connection.type -eq "ws-reverse") {
                $connection.url = "ws://127.0.0.1:${botPort}/onebot/v11/ws"
            }
        }
        $configObject | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $destinationPath -Encoding UTF8
    } catch {
        Write-Host "[LLBot] Failed to prepare config $sourcePath`: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

function Start-LLBotProcess {
    Write-Host "[LLBot] Starting..."
    return Start-Process -FilePath $llbotExe -WorkingDirectory $llbotDir -PassThru -WindowStyle Hidden
}

if ($SkipLLBot) {
    Write-Host "[LLBot] Skipped by -SkipLLBot"
} elseif ($llbotExe -and -not $llbotRunning) {
    Write-Host "[LLBot] Found at $llbotDir"
    # Patch configs to enable ws-reverse
    if (Test-Path "$scriptDir\llbot.config.json") {
        Install-LLBotConfig "$scriptDir\llbot.config.json" "$llbotDir\config.json"
    }
    if (Test-Path "$scriptDir\llbot.default_config.json") {
        Install-LLBotConfig "$scriptDir\llbot.default_config.json" "$llbotDir\bin\llbot\default_config.json"
    }
    # Keep the generated per-account config; it contains the working login and ws-reverse settings.
    $llbotProc = Start-LLBotProcess
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
$bridgeNotReadySince = 0
$supervisorStartedAt = [DateTimeOffset]::Now.ToUnixTimeSeconds()
$botHealthFailures = 0
while ($true) {
    while (-not $botProc.HasExited) {
        Start-Sleep -Seconds 10
        try {
            $healthCheck = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            $botHealthFailures = 0
        } catch {
            $botHealthFailures++
            if ($botHealthFailures -ge 3) {
                Write-Host "[Health] Bot became unresponsive; restarting..." -ForegroundColor Yellow
                Stop-ProcessTree $botProc.Id
                break
            }
        }
        if ((-not $SkipLLBot) -and $llbotExe) {
            $llbotAlive = [bool](Get-CimInstance Win32_Process | Where-Object {
                $_.Name -eq "llbot.exe" -and $_.CommandLine -like "*$llbotDir*"
            })
            $nowSeconds = [DateTimeOffset]::Now.ToUnixTimeSeconds()
            if ((-not $llbotAlive) -and ($nowSeconds - $lastLLBotRestart -ge 30)) {
                Write-Host "[LLBot] Process missing, restarting..." -ForegroundColor Yellow
                $llbotProc = Start-LLBotProcess
                $lastLLBotRestart = $nowSeconds
                $bridgeNotReadySince = $nowSeconds
            } elseif ($llbotAlive) {
                try {
                    $readyCheck = Invoke-WebRequest -Uri $readyUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                    $bridgeNotReadySince = 0
                } catch {
                    if ($bridgeNotReadySince -eq 0) { $bridgeNotReadySince = $nowSeconds }
                    $pastStartupGrace = ($nowSeconds - $supervisorStartedAt) -ge 120
                    $disconnectedTooLong = ($nowSeconds - $bridgeNotReadySince) -ge 45
                    $restartCooledDown = ($nowSeconds - $lastLLBotRestart) -ge 180
                    if ($pastStartupGrace -and $disconnectedTooLong -and $restartCooledDown) {
                        Write-Host "[LLBot] Process alive but QQ bridge is disconnected; restarting..." -ForegroundColor Yellow
                        Get-CimInstance Win32_Process | Where-Object {
                            $_.Name -eq "llbot.exe" -and $_.CommandLine -like "*$llbotDir*"
                        } | ForEach-Object { Stop-ProcessTree $_.ProcessId }
                        Start-Sleep -Seconds 2
                        $llbotProc = Start-LLBotProcess
                        $lastLLBotRestart = $nowSeconds
                        $bridgeNotReadySince = $nowSeconds
                    }
                }
            }
        }
    }

    Write-Host ""
    Remove-Item -LiteralPath $botPidFile -Force -ErrorAction SilentlyContinue
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
