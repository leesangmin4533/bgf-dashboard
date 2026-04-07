"""Fix BGF_AutoScheduler Task Scheduler entry.

Issues found:
1. Command path was split at the space in '바탕 화면', pointing to non-existent file
   → ERROR_FILE_NOT_FOUND (-2147020576) every day at 06:30
2. Action pointed to legacy start_scheduler.bat (no auto-reload wrapper)
3. Trigger only on daily 06:30, no onlogon → if process dies, no recovery until next day

Fix:
- Command: cmd.exe with /c "wrapper" (quoted) — handles spaces properly
- Action target: scripts\start_scheduler_loop.bat (auto-reload wrapper)
- Triggers: keep daily 06:30 + add LogonTrigger (start on user logon)
- MultipleInstancesPolicy: IgnoreNew (preserve)
"""
import subprocess
import sys
import os
import tempfile

PROJECT = r'C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto'
WRAPPER = PROJECT + r'\scripts\start_scheduler_loop.bat'

# UTF-16 XML — schtasks expects this encoding for /xml import
xml_content = '''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>2026-02-25T03:38:07</Date>
    <Author>BGF Auto Scheduler</Author>
    <URI>\\BGF_AutoScheduler</URI>
    <Description>BGF Auto Scheduler with auto-reload wrapper. Triggers: daily 06:30 + on user logon. Action: cmd.exe /c "scripts\\start_scheduler_loop.bat" (quoted to handle Korean path with space).</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-02-25T06:30:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>S-1-5-21-1549172084-2412735648-116597480-1001</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-21-1549172084-2412735648-116597480-1001</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c "''' + WRAPPER + '''"</Arguments>
      <WorkingDirectory>''' + PROJECT + '''</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''

# Write XML as UTF-16 LE with BOM (schtasks requirement)
xml_path = os.path.join(tempfile.gettempdir(), 'bgf_task_fixed.xml')
with open(xml_path, 'wb') as f:
    f.write('\ufeff'.encode('utf-16-le'))  # BOM
    f.write(xml_content.encode('utf-16-le'))

print(f'XML written to: {xml_path}')

# Delete existing task first
print('=== delete existing ===')
r1 = subprocess.run(
    ['schtasks', '/delete', '/tn', 'BGF_AutoScheduler', '/f'],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
print('rc:', r1.returncode)
print('out:', (r1.stdout or '').strip())
print('err:', (r1.stderr or '').strip())

# Create from XML
print('=== create from XML ===')
r2 = subprocess.run(
    ['schtasks', '/create', '/tn', 'BGF_AutoScheduler', '/xml', xml_path, '/f'],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
print('rc:', r2.returncode)
print('out:', (r2.stdout or '').strip())
print('err:', (r2.stderr or '').strip())

if r2.returncode != 0:
    sys.exit(r2.returncode)

# Verify
print('=== verify ===')
r3 = subprocess.run(
    ['schtasks', '/query', '/tn', 'BGF_AutoScheduler', '/fo', 'LIST'],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
print((r3.stdout or '').strip()[:600])
