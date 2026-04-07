"""Launch start_scheduler_loop.bat detached via powershell Start-Process."""
import base64
import subprocess
import sys
import io

# Force UTF-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT = r'C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto'
BAT = PROJECT + r'\scripts\start_scheduler_loop.bat'

ps = (
    'Start-Process -FilePath "' + BAT + '" '
    '-WorkingDirectory "' + PROJECT + '" '
    '-WindowStyle Minimized\n'
    'Write-Host "launched"\n'
)

b64 = base64.b64encode(ps.encode('utf-16-le')).decode('ascii')
r = subprocess.run(
    ['powershell', '-NoProfile', '-EncodedCommand', b64],
    capture_output=True, text=True, encoding='utf-8', errors='replace',
)
print('rc=', r.returncode)
print('out=', r.stdout)
print('err=', r.stderr)
