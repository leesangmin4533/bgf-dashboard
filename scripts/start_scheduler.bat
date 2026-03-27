@echo off
title BGF Auto Scheduler
cd /d "C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto"
echo [%date% %time%] BGF Scheduler starting...
python run_scheduler.py
echo [%date% %time%] BGF Scheduler stopped (exit code: %ERRORLEVEL%)
pause
