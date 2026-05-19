@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   小源 QQ 机器人
echo ============================================
echo.

rem === 清理旧进程 ===
echo [清理] 检查旧进程...
rem 先停旧 bot（占用 8080 端口的进程）
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080 " ^| findstr "LISTENING"') do (
    echo [清理] 关闭占用端口 8080 的进程 %%a...
    taskkill /F /PID %%a >nul 2>&1
)
rem 再停旧的 LLBot
tasklist /FI "IMAGENAME eq llbot.exe" 2>NUL | find /I "llbot.exe" >NUL
if !ERRORLEVEL! EQU 0 (
    echo [清理] 关闭旧 LLBot...
    taskkill /F /IM llbot.exe >nul 2>&1
    taskkill /F /IM node.exe >nul 2>&1
    taskkill /F /IM pmhq.exe >nul 2>&1
)

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
echo 请安装: https://www.python.org/downloads/
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

rem === 首次初始化 ===
if not exist ".env" (
    echo [初始化] 创建 .env，请编辑填入 API Key
    copy .env.example .env >nul
    echo [提示] 编辑 .env 填入 DEEPSEEK_API_KEY 后重新运行
    start notepad .env
    pause
    exit /b 0
)

if not exist "data\persona.md" (
    echo [初始化] 创建 data\persona.md
    copy data\persona.example.md data\persona.md >nul
)

rem === 启动 LLBot ===
tasklist /FI "IMAGENAME eq llbot.exe" 2>NUL | find /I "llbot.exe" >NUL
if !ERRORLEVEL! NEQ 0 (
    set LLBOT_EXE=
    set LLBOT_DIR=
    for %%d in (
        "%~dp0llbot\llbot.exe"
        "C:\Users\!USERNAME!\LLBot\llbot.exe"
        "C:\LLBot\llbot.exe"
    ) do (
        if exist %%d (
            set LLBOT_EXE=%%~d
            set LLBOT_DIR=%%~dpd
            goto :llbot_found
        )
    )
    echo [提示] 未找到 LLBot，跳过 QQ 桥接
    echo        下载: https://github.com/LLOneBot/LuckyLilliaBot/releases
    echo        解压到项目 llbot\ 目录后重新运行
    goto :skip_llbot

    :llbot_found
    echo [LLBot] 准备启动 QQ 桥接...
    rem 覆盖配置模板，确保 ws-reverse 默认启用
    if exist "%~dp0llbot.config.json" (
        copy /Y "%~dp0llbot.config.json" "!LLBOT_DIR!config.json" >nul 2>&1
    )
    if exist "%~dp0llbot.default_config.json" (
        copy /Y "%~dp0llbot.default_config.json" "!LLBOT_DIR!bin\llbot\default_config.json" >nul 2>&1
    )
    rem 删除旧的 per-QQ 配置，启动时会从模板重新生成
    if exist "!LLBOT_DIR!bin\llbot\data\config_*.json" (
        echo [LLBot] 更新 QQ 专属配置...
        del /Q "!LLBOT_DIR!bin\llbot\data\config_*.json" >nul 2>&1
    )
    echo [LLBot] 启动...
    start "" /D "!LLBOT_DIR!" "!LLBOT_EXE!"
    echo [LLBot] 等待注入并登录（15秒）...
    timeout /t 15 /nobreak >NUL
) else (
    echo [LLBot] 已在运行
)

:skip_llbot

rem === 启动机器人 ===
echo [启动] 小源机器人...
echo.
echo ============================================
echo   机器人正在运行，请勿关闭此窗口
echo ============================================
echo.
!PYTHON_CMD! -u bot.py
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo [错误] 机器人意外退出（错误码: !ERRORLEVEL!）
    echo 常见原因:
    echo   1. 端口 8080 被占用 — 关闭占用程序后重试
    echo   2. .env 中的 API Key 未配置或失效
    echo   3. 依赖缺失 — 尝试运行 pip install -e .
    echo.
)
pause
