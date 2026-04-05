# Completion Report: milestone (3단계 마일스톤 KPI 자동 측정)

> 완료일: 2026-04-05
> Match Rate: 100%

---

## 1. 요약

| 항목 | 내용 |
|------|------|
| 기능 | 이슈체인에 "나침반" 기능 추가 — 목표 달성 여부 자동 측정 + 주간 리포트 |
| 동기 | 이슈체인이 악화만 감지(소방), 프로젝트 방향성 판단 불가 |
| 결과 | K1~K4 KPI 주간 자동 판정 + 카카오 리포트 + milestone_snapshots 누적 |
| Match Rate | 100% (66/66 항목) |
| 테스트 | 27개 전부 통과 |

---

## 2. 구현 내용

### 신규 파일
- `src/analysis/milestone_tracker.py` — 핵심 모듈 (~270줄)
  - MilestoneTracker 클래스: evaluate → K1~K4 계산 → 판정 → DB 저장 → 리포트

### 변경 파일 (6개)
| 파일 | 변경 내용 |
|------|----------|
| constants.py | MILESTONE_TARGETS, APPROACHING_RATIO, COMPLETION_WEEKS + v73 |
| schema.py | milestone_snapshots DDL (COMMON_SCHEMA) |
| models.py | 마이그레이션 v73 |
| ops_metrics.py | total_order_7d 추가 (K3 계산용) |
| kakao_notifier.py | ALLOWED_CATEGORIES에 "milestone" 추가 |
| run_scheduler.py | milestone_report_wrapper + 매주 일요일 00:00 스케줄 |

### KPI 정의
| KPI | 지표 | 측정 | 목표 |
|-----|------|------|------|
| K1 | 예측 안정성 | mae_7d / mae_14d 평균 | < 1.05 |
| K2 | 폐기율 | waste / (waste + sales) | food < 3%, 전체 < 2% |
| K3 | 발주 실패율 | fail_7d / total_order_7d | < 5% |
| K4 | 데이터 무결성 | max(consecutive_days) | < 3일 |

### 완료 조건
K1~K4 전부 ACHIEVED 2주 연속 → 3단계 완료 선언 → 4단계 착수

---

## 3. PDCA 이력

| 단계 | 날짜 | 결과 |
|------|------|------|
| Plan | 04-05 | 문제 정의, KPI 4개, PLANNED 이슈 매핑, 4단계 로드맵 |
| Design | 04-05 | 파일 구조, 메서드 설계, DB 스키마, 스케줄러 연동 |
| Do | 04-05 | 7개 파일 구현, 21개 테스트 통과 |
| Check | 04-05 | 초기 93.9% (테스트 4건 Gap) → 즉시 해소 → 100% |

---

## 4. 설계 판단

| 판단 | 이유 |
|------|------|
| OpsMetrics 재사용 | 새 DB 쿼리 최소화 — K3의 total_order_7d 하나만 추가 |
| common.db에 저장 | 마일스톤은 매장 무관 전체 평균이므로 |
| ASCII 아이콘 (V/~/X) | 카카오 메시지 호환성 — 유니코드 깨짐 방지 |
| _judge에 lower_is_better 생략 | YAGNI — 4단계에서 필요할 때 추가 |

---

## 5. 후속 작업

| 작업 | 시기 | 내용 |
|------|------|------|
| 2주 실측 | 04-06 ~ 04-19 | 매일 dry-run으로 K1~K4 실측값 수집 |
| 목표 재설정 | 04-19 | 실측 중앙값 기반 MILESTONE_TARGETS 조정 |
| PLANNED 착수 | 04-06~ | 1순위: 행사 종료 감량 자동화 (K2 기여) |
| 4단계 계획 | 3단계 완료 시 | 수익성 최적화 Plan 문서 작성 |
