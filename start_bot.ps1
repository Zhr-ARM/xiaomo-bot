# 小源 QQ 机器人 - 启动脚本
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "============================================"
Write-Host "  小源 QQ 机器人 - 启动脚本"
Write-Host "============================================"
Write-Host ""

# 检测 Python
$pythonCmd = $null
foreach ($cmd in @("python3", "python")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        $pythonCmd = $cmd
        break
    }
}

if (-not $pythonCmd) {
    # 回退：检查常见安装路径
    $paths = @(
        "D:\Python312\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )
    foreach ($p in $paths) {
        $expanded = [Environment]::ExpandEnvironmentVariables($p)
        if (Test-Path $expanded) {
            $pythonCmd = $expanded
            break
        }
    }
}

if (-not $pythonCmd) {
    Write-Host "[错误] 未找到 Python。请安装 Python 3.10+ 并添加到 PATH。" -ForegroundColor Red
    Read-Host "按 Enter 退出"
    exit 1
}

Write-Host "[OK] Python: $pythonCmd"
& $pythonCmd --version

$env:PYTHONIOENCODING = "utf-8"

# 检测 LLBot（可选）
$llbotPaths = @(
    "$env:USERPROFILE\LLBot\llbot.exe",
    "C:\LLBot\llbot.exe"
)
$llbotExe = $null
foreach ($p in $llbotPaths) {
    if (Test-Path $p) {
        $llbotExe = $p
        break
    }
}

if ($llbotExe) {
    $llbotProc = Get-Process -Name "llbot" -ErrorAction SilentlyContinue
    if (-not $llbotProc) {
        Write-Host "[启动] LLBot..."
        Start-Process -FilePath $llbotExe -WorkingDirectory (Split-Path $llbotExe)
        Start-Sleep -Seconds 15
    } else {
        Write-Host "[OK] LLBot 已在运行 (PID: $($llbotProc.Id))"
    }
} else {
    Write-Host "[提示] 未找到 LLBot，跳过 QQ 桥接启动"
    Write-Host "       请手动安装 LLBot 或 NapCatQQ 来连接 QQ"
}

Write-Host "[启动] 小源机器人..."
Set-Location $scriptDir
& $pythonCmd -u bot.py
pause
