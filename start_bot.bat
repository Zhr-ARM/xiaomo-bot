@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   小源 QQ 机器人
echo ============================================
echo.

rem === 检测 Python ===
set PYTHON_CMD=
for %%p in (python python3) do (
    where %%p >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set PYTHON_CMD=%%p
        goto :python_found
    )
)

for %%d in (
    "D:\Python312\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
    "!LOCALAPPDATA!\Programs\Python\Python312\python.exe"
    "!LOCALAPPDATA!\Programs\Python\Python311\python.exe"
) do (
    if exist %%d (
        set PYTHON_CMD=%%~d
        goto :python_found
    )
)

echo [错误] 未找到 Python 3.10+
echo 请安装后重试: https://www.python.org/downloads/
pause
exit /b 1

:python_found
echo [Python] !PYTHON_CMD!

rem === 安装依赖（首次运行） ===
if not exist "src\xiaomo_bot.egg-info" (
    echo [安装] 正在安装依赖...
    !PYTHON_CMD! -m pip install -e . --quiet
    if !ERRORLEVEL! NEQ 0 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
    echo [完成] 依赖安装完毕
)

rem === 复制 .env（首次运行） ===
if not exist ".env" (
    echo [初始化] 创建 .env，请编辑填入 API Key
    copy .env.example .env >nul
    echo [提示] 请编辑 .env 文件填入 DEEPSEEK_API_KEY 后重新运行
    start notepad .env
    pause
    exit /b 0
)

rem === 检查 persona.md ===
if not exist "data\persona.md" (
    echo [初始化] 创建 data\persona.md
    copy data\persona.example.md data\persona.md >nul
)

rem === 启动 LLBot ===
tasklist /FI "IMAGENAME eq llbot.exe" 2>NUL | find /I "llbot.exe" >NUL
if !ERRORLEVEL! NEQ 0 (
    if exist "llbot\llbot.exe" (
        echo [LLBot] 启动 QQ 桥接...
        start "" /D "%~dp0llbot" "%~dp0llbot\llbot.exe"
        echo [LLBot] 等待 QQ 登录（15秒）...
        timeout /t 15 /nobreak >NUL
    ) else (
        echo [提示] 未找到 llbot\llbot.exe，跳过 QQ 桥接
    )
) else (
    echo [LLBot] 已在运行
)

rem === 启动机器人 ===
echo [启动] 小源机器人...
!PYTHON_CMD! -u bot.py
pause
