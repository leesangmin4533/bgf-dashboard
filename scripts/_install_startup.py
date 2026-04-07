"""Create Windows startup shortcut to start_scheduler_loop.bat
and launch it immediately (detached)."""
import base64
import os
import subprocess
import sys

PROJECT = r'C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto'
BAT = PROJECT + r'\scripts\start_scheduler_loop.bat'

ps_create_shortcut = r'''
$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
$bat = "''' + BAT + r'''"
$wd  = "''' + PROJECT + r'''"
$lnk = Join-Path $startup 'BGF Auto Scheduler.lnk'
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $bat
$s.WorkingDirectory = $wd
$s.WindowStyle = 7
$s.Description = 'BGF Auto Scheduler (auto-reload wrapper)'
$s.Save()
Write-Host ("created=" + $lnk)
Write-Host ("exists=" + (Test-Path $lnk))
'''

# UTF-16-LE base64 for powershell -EncodedCommand
b64 = base64.b64encode(ps_create_shortcut.encode('utf-16-le')).decode('ascii')
result = subprocess.run(
    ['powershell', '-NoProfile', '-EncodedCommand', b64],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
print('=== shortcut creation ===')
print('stdout:', result.stdout)
print('stderr:', result.stderr)
print('returncode:', result.returncode)

if result.returncode != 0:
    sys.exit(result.returncode)

# Launch the bat detached using Start-Process
ps_launch = r'''
Start-Process -FilePath "''' + BAT + r'''" -WorkingDirectory "''' + PROJECT + r'''" -WindowStyle Minimized
Write-Host "launched"
'''
b64_launch = base64.b64encode(ps_launch.encode('utf-16-le')).decode('ascii')
result2 = subprocess.run(
    ['powershell', '-NoProfile', '-EncodedCommand', b64_launch],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
print('=== launch ===')
print('stdout:', result2.stdout)
print('stderr:', result2.stderr)
print('returncode:', result2.returncode)
