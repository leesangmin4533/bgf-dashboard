#!/bin/bash
# Hook: 작업 완료 시 Windows 데스크톱 알림
powershell.exe -NoProfile -Command "
[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
\$n = New-Object System.Windows.Forms.NotifyIcon
\$n.Icon = [System.Drawing.SystemIcons]::Information
\$n.Visible = \$true
\$n.BalloonTipTitle = 'Claude Code'
\$n.BalloonTipText = '작업이 완료되었습니다. 확인해주세요.'
\$n.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
\$n.Dispose()
" &
exit 0
