@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   小源 QQ 机器人 - 启动脚本
echo ============================================
echo.

rem === 检测 Python ===
set PYTHON_CMD=
for %%p in (python3 python) do (
    where %%p >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set PYTHON_CMD=%%p
        goto :python_found
    )
)

rem 回退：检查常见安装路径
for %%d in (
    "D:\Python312\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) do (
    if exist %%d (
        set PYTHON_CMD=%%~d
        goto :python_found
    )
)

echo [错误] 未找到 Python。请安装 Python 3.10+ 并添加到 PATH。
pause
exit /b 1

:python_found
echo [OK] Python: !PYTHON_CMD!
!PYTHON_CMD! --version

rem === 检测 LLBot（可选） ===
set LLBOT_DIR=
for %%d in (
    "C:\Users\%USERNAME%\LLBot"
    "C:\LLBot"
) do (
    if exist "%%d\llbot.exe" (
        set LLBOT_DIR=%%d
        goto :llbot_found
    )
)

echo [提示] 未找到 LLBot，跳过 QQ 桥接启动
echo        请手动安装 LLBot 或 NapCatQQ 来连接 QQ
goto :skip_llbot

:llbot_found
tasklist /FI "IMAGENAME eq llbot.exe" 2>NUL | find /I "llbot.exe" >NUL
if !ERRORLEVEL! NEQ 0 (
    echo [启动] LLBot...
    start "" /D "!LLBOT_DIR!" "!LLBOT_DIR!\llbot.exe"
    timeout /t 15 /nobreak >NUL
) else (
    echo [OK] LLBot 已在运行
)

:skip_llbot
echo [启动] 小源机器人...
cd /d "%~dp0"
!PYTHON_CMD! bot.py
pause
