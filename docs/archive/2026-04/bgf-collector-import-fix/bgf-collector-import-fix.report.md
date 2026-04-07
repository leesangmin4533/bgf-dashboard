# Report — bgf-collector-import-fix

**Status**: Completed ✅
**Match Rate**: 100%
**Date**: 2026-04-07

## 문제
`src/scheduler/daily_job.py:931` — `from src.collectors.bgf_collector import BGFCollector` 가 존재하지 않는 모듈을 import. 2026-04-06 14:00 D-1 부스트 발주가 47863/49965 매장에서 `No module named 'src.collectors.bgf_collector'` 로 실패.

## 원인
과거 collector 리팩토링에서 `BGFCollector` 클래스가 `SalesCollector`로 대체됐으나, `daily_job.py`의 D-1 Phase 2 블록 하나만 업데이트 누락. import 실패로 해당 블록 try/except가 "Selenium 실행 실패"로 처리됨.

## 수정
`src/scheduler/daily_job.py:928-956` (±13줄):
- `BGFCollector` → `SalesCollector` (`src/collectors/sales_collector.py`)
- `collector.login()` → `collector._ensure_login() + collector.get_driver()` (2단계)
- `try/except/finally` 로 재구성하여 로그인 실패/드라이버 획득 실패/예외를 독립 분기 처리
- `finally`에서 `collector.close()` 보장 → ChromeDriver 누수 방지

## 검증
- ✅ `ast.parse` 통과
- ✅ `from src.scheduler.daily_job import DailyCollectionJob` 성공
- ✅ `SalesCollector` API 확인 (_ensure_login / get_driver / close 전부 존재)
- 🕒 2026-04-08 14:00 D-1 트리거 시 실전 로그 확인 필요

## Gap 분석
| 항목 | 상태 |
|---|:---:|
| Plan §2 DoD — import 오류 해소 | ✅ |
| Plan §2 DoD — 부스트 발주 로그인 성공 | 🕒 실전 검증 대기 |
| Plan §2 DoD — 회귀 에러 0건 | 🕒 실전 검증 대기 |
| Design §1 API 매핑 | ✅ (3건 전부 적용) |
| Design §2 수정 블록 | ✅ (finally 추가로 개선) |

**Match Rate: 100%** (코드 변경 기준 — 실전 검증은 운영 관찰)

## 교훈
- **Legacy import 잔재**는 전체 grep으로 찾아야 한다 — 해당 블록만 동작 경로가 달라 테스트에 안 걸렸다
- **try 블록 안에서 변수 할당 후 다른 catch 분기**는 finally/별도 변수로 정리해야 안전
- **이번 job-health-monitor가 곧바로 이 에러를 즉시 알림**으로 잡게 될 것 — 본 수정으로 4/6 같은 사고가 4/8 D-1에서 재발해도 14:00에 바로 카톡 옴 (시너지)

## 후속
- 2026-04-08 14:00 D-1 실행 로그 관찰
- 다른 legacy import 잔재 수색은 별 작업 (scope out)

## 참조
- Plan: (archived below)
- Design: (archived below)
- 촉발 에러 로그: `logs/bgf_auto.log` 2026-04-06 14:00:24
