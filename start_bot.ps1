# 小源 QQ 机器人 - 一键启动脚本
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "============================================"
Write-Host "  小源 QQ 机器人"
Write-Host "============================================"
Write-Host ""

# ── 检测 Python ──
$pythonCmd = $null
foreach ($cmd in @("python3", "python")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { $pythonCmd = $cmd; break }
}
if (-not $pythonCmd) {
    $paths = @(
        "D:\Python312\python.exe", "C:\Python312\python.exe",
        "C:\Python311\python.exe", "C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )
    foreach ($p in $paths) {
        $expanded = [Environment]::ExpandEnvironmentVariables($p)
        if (Test-Path $expanded) { $pythonCmd = $expanded; break }
    }
}
if (-not $pythonCmd) {
    Write-Host "[错误] 未找到 Python 3.10+" -ForegroundColor Red
    Write-Host "请安装: https://www.python.org/downloads/"
    Read-Host "按 Enter 退出"
    exit 1
}
Write-Host "[Python] $pythonCmd"
$env:PYTHONIOENCODING = "utf-8"

# ── 安装依赖（首次） ──
if (-not (Test-Path "src\xiaomo_bot.egg-info")) {
    Write-Host "[安装] 正在安装依赖..."
    & $pythonCmd -m pip install -e . --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 依赖安装失败" -ForegroundColor Red
        Read-Host "按 Enter 退出"
        exit 1
    }
    Write-Host "[完成] 依赖安装完毕"
}

# ── 复制 .env（首次） ──
if (-not (Test-Path ".env")) {
    Write-Host "[初始化] 创建 .env，请编辑填入 API Key"
    Copy-Item .env.example .env
    Write-Host "[提示] 请编辑 .env 填入 DEEPSEEK_API_KEY 后重新运行" -ForegroundColor Yellow
    Start-Process notepad .env
    Read-Host "按 Enter 退出"
    exit 0
}

# ── 初始化 persona.md ──
if (-not (Test-Path "data\persona.md")) {
    Write-Host "[初始化] 创建 data\persona.md"
    Copy-Item data\persona.example.md data\persona.md
}

# ── 启动 LLBot ──
$llbotExe = Join-Path $scriptDir "llbot\llbot.exe"
$llbotProc = Get-Process -Name "llbot" -ErrorAction SilentlyContinue
if (-not $llbotProc) {
    if (Test-Path $llbotExe) {
        Write-Host "[LLBot] 启动 QQ 桥接..."
        Start-Process -FilePath $llbotExe -WorkingDirectory (Join-Path $scriptDir "llbot")
        Write-Host "[LLBot] 等待 QQ 登录（15秒）..."
        Start-Sleep -Seconds 15
    } else {
        Write-Host "[提示] 未找到 llbot\llbot.exe，跳过 QQ 桥接" -ForegroundColor Yellow
    }
} else {
    Write-Host "[LLBot] 已在运行 (PID: $($llbotProc.Id))"
}

# ── 启动机器人 ──
Write-Host "[启动] 小源机器人..."
& $pythonCmd -u bot.py
pause
