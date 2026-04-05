#!/bin/bash
# Hook: 작업 완료 시 Windows 네이티브 toast 알림 + 시스템 알림음
# Claude Code 앱 내장 알림과 동일한 Windows ToastNotification API 사용

powershell.exe -NoProfile -Command "
# Windows Runtime toast API (Windows 10/11 내장)
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null

\$template = @'
<toast duration='short'>
  <visual>
    <binding template='ToastGeneric'>
      <text>Claude Code</text>
      <text>작업이 완료되었습니다.</text>
    </binding>
  </visual>
  <audio src='ms-winsoundevent:Notification.Default'/>
</toast>
'@

\$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
\$xml.LoadXml(\$template)

\$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
\$toast = [Windows.UI.Notifications.ToastNotification]::new(\$xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(\$appId).Show(\$toast)
" &
exit 0
