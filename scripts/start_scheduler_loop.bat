@echo off
chcp 65001 >nul
REM BGF Auto Scheduler - auto-reload wrapper (scheduler-auto-reload, 2026-04-07)
REM
REM Behavior:
REM   - python run_scheduler.py
REM   - exit 0: auto-reload triggered, restart immediately
REM   - exit 2: stop command, wrapper exits
REM   - other : error, retry after 5s backoff
REM
REM Usage:
REM   Use this file instead of start_scheduler.bat
REM
REM NOTE: chcp 65001 (UTF-8) is required because the bat is saved as UTF-8.
REM       Without it, Korean characters in echo/REM are parsed as commands
REM       under cp949 default codepage and cause spurious errors.

title BGF Auto Scheduler (auto-reload)
cd /d "C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto"

:loop
echo.
echo ============================================================
echo [%date% %time%] BGF Scheduler starting...
echo ============================================================
python run_scheduler.py
set CODE=%ERRORLEVEL%

if %CODE%==2 (
    echo [%date% %time%] exit=2 ^(정지 명령^) - wrapper 종료
    exit /b 0
)
if %CODE%==0 (
    echo [%date% %time%] exit=0 ^(auto-reload^) - 즉시 재시작
    goto loop
)

echo [%date% %time%] exit=%CODE% ^(오류^) - 5초 후 재시작
timeout /t 5 /nobreak >nul
goto loop
