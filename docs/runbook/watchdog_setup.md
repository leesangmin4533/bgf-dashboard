# Watchdog 설정 가이드 (job-health-monitor)

`scripts/watchdog.py`를 Windows 작업 스케줄러에 등록하여 스케줄러 본체가 죽어도 감지한다.

## 등록 (schtasks)

관리자 권한 PowerShell 또는 cmd에서:

```powershell
schtasks /Create /SC MINUTE /MO 5 /TN "BGF_Watchdog" ^
  /TR "python \"C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\scripts\watchdog.py\"" ^
  /ST 00:00 /RU "%USERNAME%" /RL LIMITED
```

옵션 설명:
- `/SC MINUTE /MO 5`: 5분마다
- `/TN`: 태스크 이름
- `/TR`: 실행 명령
- `/ST 00:00`: 시작 시각 (무관, 즉시 활성화)

## 확인

```powershell
schtasks /Query /TN "BGF_Watchdog" /V /FO LIST
```

## 수동 실행

```powershell
python scripts/watchdog.py
```

## 제거

```powershell
schtasks /Delete /TN "BGF_Watchdog" /F
```

## 동작

1. `data/runtime/scheduler_heartbeat.txt` mtime 확인 → 10분 초과 시 "Scheduler Dead" 알림
2. `common.db.job_runs`에서 최근 1시간 `failed/missed/timeout` + `alerted=0` 건 조회
3. 각 건에 대해 Kakao 1차 알림 시도 → 실패 시 2차 fallback (logs/job_health_alerts.log + winsound + stderr)
4. 알림 완료 건은 `alerted=1` 마킹

## 긴급 비활성화

Watchdog은 단일 스위치:
```powershell
schtasks /Change /TN "BGF_Watchdog" /DISABLE
```

In-process Tracker는 피처 플래그:
```python
# src/settings/constants.py
JOB_HEALTH_TRACKER_ENABLED = False
```
