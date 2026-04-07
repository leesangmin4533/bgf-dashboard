@echo off
REM BGF Auto Scheduler — auto-reload wrapper (scheduler-auto-reload, 2026-04-07)
REM
REM 동작:
REM   - python run_scheduler.py 실행
REM   - exit code 0: auto-reload 트리거 → 즉시 재시작
REM   - exit code 2: 정지 명령 → wrapper 종료
REM   - exit code 기타: 오류 → 5초 후 재시작 (backoff)
REM
REM 사용:
REM   기존 start_scheduler.bat 대신 이 파일을 사용하세요.

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
