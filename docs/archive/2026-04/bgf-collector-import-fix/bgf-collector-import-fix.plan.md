# Plan — bgf-collector-import-fix

**Feature**: bgf-collector-import-fix
**Priority**: P2
**Created**: 2026-04-07
**Related Issue**: `docs/05-issues/scheduling.md`

## 1. 배경

2026-04-06 14:00 `[D-1] Selenium 실행 실패`가 매장 47863/49965에서 발생:

```
src.scheduler.daily_job | [D-1] Selenium 실행 실패 (store=47863):
  No module named 'src.collectors.bgf_collector'
```

`src/scheduler/daily_job.py:931` 에서 `from src.collectors.bgf_collector import BGFCollector` 를 import하는데 해당 모듈이 존재하지 않는다. 과거 리팩토링에서 collector 모듈명이 변경됐거나 누락된 legacy 참조. 결과적으로 D-1 부스트 발주가 해당 매장에서만 스킵되어 실물 영향 발생.

## 2. 목표

D-1 2차 배송 보정 플로우의 Selenium 실행 경로를 현재 존재하는 BGF 로그인 모듈로 복구한다.

### DoD
- [ ] `daily_job.py:931` import 오류 해소 — 실제 존재하는 클래스 사용
- [ ] D-1 부스트 발주 시 로그인 성공 (3매장 기준)
- [ ] 회귀 유닛/통합 테스트 통과
- [ ] 4/6 같은 `No module named` 에러 0건

## 3. 범위

### 포함
- `src/scheduler/daily_job.py:931` 수정
- 대체 클래스로 `SalesCollector` 또는 `SalesAnalyzer` 선택 (아래 §5)
- 회귀 테스트 1건 추가 (import 자체 + mock login)

### 제외
- D-1 로직 전반 리팩토링
- 다른 legacy import 잔재 수색 (별 작업)

## 4. 영향 범위

- 파일: `src/scheduler/daily_job.py` (~20줄 변경 예상)
- 잡: `delivery_d1_adjust` / `delivery_match` 라인의 부스트 실행 phase만
- 매장: 현재 전 매장 (error가 47863/49965에서만 보인 건 부스트 대상 유무 차이일 뿐)

## 5. 접근

### 조사 결과
- `src/collectors/bgf_collector.py` — **존재하지 않음**
- `src/collectors/sales_collector.py:26` `class SalesCollector(BaseCollector)` — `_ensure_login()`, `get_driver()` 제공
- `src/sales_analyzer.py:38` `class SalesAnalyzer` — 하위 레벨 로그인 엔진, `driver` 속성 제공

### 선택지
| 대안 | 장점 | 단점 | 채택 |
|---|---|---|:---:|
| A. **`SalesCollector` 사용** (_ensure_login + get_driver) | 다른 Phase가 이미 쓰는 표준 경로, 세션 재사용 | close() API 확인 필요 | ✅ |
| B. `SalesAnalyzer` 직접 사용 | 더 저수준, 제어 용이 | 기존 패턴과 불일치 | ❌ |
| C. `BGFCollector` 새로 생성 (wrapper) | 이름 유지 | 중복 클래스 생성, 의미 없음 | ❌ |

**A 채택**: 현재 `daily_job.py`의 다른 Phase는 전부 `SalesCollector`를 쓰고 있어 일관성 유지.

## 6. 리스크

| 리스크 | 대응 |
|---|---|
| `SalesCollector`의 로그인 메서드 시그니처가 `BGFCollector.login()`과 다름 | `_ensure_login()` + `get_driver()` 2단계로 대체 (이미 조사 완료) |
| close() 누락 시 ChromeDriver 누수 | `SalesCollector.close()` 존재 확인 후 적용 |
| 부스트 대상 있는 날에만 트리거되는 코드라 회귀 감지 어려움 | 유닛 테스트에 mock 추가 |

## 7. 마일스톤

| 단계 | 내용 |
|---|---|
| M1 | daily_job.py:931 수정 + close() 경로 정리 |
| M2 | 유닛 테스트: import 성공 + _ensure_login 호출 검증 (mock) |
| M3 | 수동 검증 — 다음 D-1 트리거 시 로그 확인 |

## 8. 검증

- [ ] `python -c "from src.scheduler.daily_job import *"` import 통과
- [ ] 신규 유닛 테스트 PASS
- [ ] 2026-04-08 14:00 D-1 실행 로그에 `No module named` 에러 0건
- [ ] 47863/49965 포함 부스트 발주 성공 로그 확인

## 9. 이슈 체인

`docs/05-issues/scheduling.md` — 시도 기록에 이번 수정 추가. 커밋 footer:
`Issue-Chain: scheduling#d1-bgf-collector-import`

## 10. 참조
- 4/6 로그: `logs/bgf_auto.log` 14:00:24 ERROR
- 해결 교훈: job-health-monitor 부수효과 — Tracker가 작동하면 이 에러도 `job_runs.failed`로 즉시 포착됨
