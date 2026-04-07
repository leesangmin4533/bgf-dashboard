"""Launch start_scheduler_loop.bat fully detached using subprocess flags.

DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE
ensures the child outlives the parent (this script).
"""
import subprocess
import sys
import os

PROJECT = r'C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto'
BAT = PROJECT + r'\scripts\start_scheduler_loop.bat'

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NEW_CONSOLE = 0x00000010

# CREATE_NEW_CONSOLE so the bat has its own cmd window where it can echo
# DETACHED_PROCESS would suppress the console, breaking bat output, so use NEW_CONSOLE
flags = CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP

p = subprocess.Popen(
    ['cmd', '/c', BAT],
    cwd=PROJECT,
    creationflags=flags,
    close_fds=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

print(f'launched pid={p.pid}')
# Do NOT wait — let it detach
sys.exit(0)
