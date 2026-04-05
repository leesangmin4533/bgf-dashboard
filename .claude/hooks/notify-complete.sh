#!/bin/bash
# Hook: 작업 완료 시 Windows 데스크톱 알림 + 시스템 알림음
powershell.exe -NoProfile -Command "
[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
\$n = New-Object System.Windows.Forms.NotifyIcon
\$n.Icon = [System.Drawing.SystemIcons]::Information
\$n.Visible = \$true
\$n.BalloonTipTitle = 'Claude Code'
\$n.BalloonTipText = '작업이 완료되었습니다. 확인해주세요.'
\$n.ShowBalloonTip(5000)
(New-Object Media.SoundPlayer 'C:\Windows\Media\Windows Notify System Generic.wav').PlaySync()
Start-Sleep -Seconds 4
\$n.Dispose()
" &
exit 0
