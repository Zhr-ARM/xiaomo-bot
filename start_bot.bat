@echo off
cd /d "%~dp0"

echo ============================================
echo   Xiaoyuan QQ Bot
echo ============================================
echo.

REM Kill old bot on port 8080
echo [Clean] Checking old processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080 " ^| findstr "LISTENING"') do (
    echo [Clean] Killing port 8080 process %%a...
    taskkill /F /PID %%a >nul 2>&1
)
tasklist /FI "IMAGENAME eq llbot.exe" 2>NUL | find /I "llbot.exe" >NUL
if %ERRORLEVEL% EQU 0 (
    echo [Clean] Killing old LLBot...
    taskkill /F /IM llbot.exe >nul 2>&1
    taskkill /F /IM node.exe >nul 2>&1
    taskkill /F /IM pmhq.exe >nul 2>&1
)

REM Find Python
set PYTHON_CMD=
for %%p in (python python3) do (
    where %%p >nul 2>&1
    if not errorlevel 1 (
        set PYTHON_CMD=%%p
        goto :python_found
    )
)
if exist "D:\Python312\python.exe" set PYTHON_CMD=D:\Python312\python.exe && goto :python_found
if exist "C:\Python312\python.exe" set PYTHON_CMD=C:\Python312\python.exe && goto :python_found
if exist "C:\Python311\python.exe" set PYTHON_CMD=C:\Python311\python.exe && goto :python_found
if exist "C:\Python310\python.exe" set PYTHON_CMD=C:\Python310\python.exe && goto :python_found

echo [Error] Python not found
echo Install Python 3.10+ from https://www.python.org/downloads/
pause
exit /b 1

:python_found
echo [Python] %PYTHON_CMD%

REM Install deps on first run
if not exist "src\xiaomo_bot.egg-info" (
    echo [Install] Installing dependencies...
    %PYTHON_CMD% -m pip install -e . --quiet
    if errorlevel 1 (
        echo [Error] Dependency install failed
        pause
        exit /b 1
    )
    echo [Done] Dependencies installed
)

REM First run init
if not exist ".env" (
    echo [Init] Creating .env, please edit and add your API key
    copy .env.example .env >nul
    echo [Hint] Edit .env and set DEEPSEEK_API_KEY, then re-run
    start notepad .env
    pause
    exit /b 0
)

if not exist "data\persona.md" (
    echo [Init] Creating data\persona.md
    copy data\persona.example.md data\persona.md >nul
)

REM Start LLBot
tasklist /FI "IMAGENAME eq llbot.exe" 2>NUL | find /I "llbot.exe" >NUL
if errorlevel 1 (
    set LLBOT_EXE=
    set LLBOT_DIR=
    if exist "%~dp0llbot\llbot.exe" (
        set LLBOT_EXE=%~dp0llbot\llbot.exe
        set LLBOT_DIR=%~dp0llbot
        goto :llbot_found
    )
    if exist "C:\Users\%USERNAME%\LLBot\llbot.exe" (
        set LLBOT_EXE=C:\Users\%USERNAME%\LLBot\llbot.exe
        set LLBOT_DIR=C:\Users\%USERNAME%\LLBot
        goto :llbot_found
    )
    if exist "C:\LLBot\llbot.exe" (
        set LLBOT_EXE=C:\LLBot\llbot.exe
        set LLBOT_DIR=C:\LLBot
        goto :llbot_found
    )
    echo [Hint] LLBot not found, skipping QQ bridge
    echo        Download: https://github.com/LLOneBot/LuckyLilliaBot/releases
    goto :skip_llbot

    :llbot_found
    echo [LLBot] Found at %LLBOT_DIR%
    REM Patch configs to enable ws-reverse
    if exist "%~dp0llbot.config.json" (
        copy /Y "%~dp0llbot.config.json" "%LLBOT_DIR%\config.json" >nul 2>&1
    )
    if exist "%~dp0llbot.default_config.json" (
        copy /Y "%~dp0llbot.default_config.json" "%LLBOT_DIR%\bin\llbot\default_config.json" >nul 2>&1
    )
    REM Remove old per-QQ config so it regenerates from patched template
    if exist "%LLBOT_DIR%\bin\llbot\data\config_*.json" (
        echo [LLBot] Updating QQ config...
        del /Q "%LLBOT_DIR%\bin\llbot\data\config_*.json" >nul 2>&1
    )
    echo [LLBot] Starting...
    start "" /D "%LLBOT_DIR%" "%LLBOT_EXE%"
    echo [LLBot] Waiting for injection and login (15s)...
    timeout /t 15 /nobreak >NUL
) else (
    echo [LLBot] Already running
)

:skip_llbot

REM Start the bot
echo [Start] Launching bot...
echo ============================================
echo   Bot running - DO NOT CLOSE this window
echo ============================================
echo.
%PYTHON_CMD% -u bot.py
if errorlevel 1 (
    echo.
    echo [Error] Bot exited with code %ERRORLEVEL%
    echo Common causes:
    echo   1. Port 8080 in use - close other programs
    echo   2. API key not set or invalid in .env
    echo   3. Missing dependencies - run: pip install -e .
    echo.
)
pause
