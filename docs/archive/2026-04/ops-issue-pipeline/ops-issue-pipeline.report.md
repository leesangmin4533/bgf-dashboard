# ops-issue-pipeline Completion Report

> **Feature**: 운영 지표 → 이슈 자동 등록 파이프라인
> **Date**: 2026-04-05
> **Match Rate**: 100% (85/85)
> **Iterations**: 0

---

## 1. Summary

운영 데이터에서 이상 패턴을 자동 감지하여 이슈 체인에 `[PLANNED]` 블록을 등록하고 카카오 알림을 발송하는 파이프라인을 구현했다. 매일 23:55 자동 실행되며, 5개 지표(예측 정확도, 발주 실패, 폐기율, 수집 실패, 자전 미해결)를 모니터링한다.

### 핵심 성과
- **4계층 분리 구현**: Domain → Analysis → Infrastructure → Application 아키텍처 준수
- **5개 지표 자동 감지**: eval_outcomes, order_fail_reasons, waste_slip_items, collection_logs, integrity_checks
- **중복/쿨다운 방지**: 키워드 매칭(2개+) + 14일 RESOLVED 쿨다운
- **38개 테스트 통과**: 경계값, 우선순위, 중복 방지 등 포괄적 검증

---

## 2. PDCA Cycle

| Phase | Date | Result |
|-------|------|--------|
| Plan | 2026-04-05 | Plan Plus 완료 (의도 발견 + YAGNI) |
| Design | 2026-04-05 | 4계층 상세 설계 (6단계 구현 순서) |
| Do | 2026-04-05 | 1,215줄 구현, 커밋 `2de38e2` |
| Check | 2026-04-05 | Match Rate 100% (85/85), 커밋 `b6633a6` |
| Act | - | 불필요 (100%) |

---

## 3. Implementation Details

### 3.1 파일 구조

| File | Layer | Lines | Role |
|------|-------|:-----:|------|
| `src/domain/ops_anomaly.py` | Domain | 249 | 5개 지표 이상 판정 순수 함수 |
| `src/analysis/ops_metrics.py` | Analysis | 287 | DB 조회 (5개 테이블) |
| `src/infrastructure/issue_chain_writer.py` | Infrastructure | 155 | .md 파일 안전 삽입 |
| `src/application/services/ops_issue_detector.py` | Application | 120 | 전매장 순회 오케스트레이션 |
| `src/settings/constants.py` | Settings | +9 | OPS_* 상수 7개 |
| `run_scheduler.py` | Scheduler | +25 | wrapper + 23:55 등록 + CLI |
| `tests/test_ops_anomaly.py` | Test | 214 | 28개 테스트 |
| `tests/test_issue_chain_writer.py` | Test | 156 | 10개 테스트 |
| **Total** | | **1,215** | |

### 3.2 모니터링 지표

| # | 지표 | 소스 테이블 | 감지 조건 | 기본 우선순위 |
|---|------|-----------|----------|:---:|
| 1 | 예측 정확도 하락 | eval_outcomes | 7d MAE > 14d MAE x 1.2 | P2 (3개+ P1) |
| 2 | 발주 실패 급증 | order_fail_reasons | recent > prev x 1.5 | P1 |
| 3 | 폐기율 상승 | waste_slip_items + daily_sales | 7d > 30d x 1.5 | P2 (food P1) |
| 4 | 수집 연속 실패 | collection_logs | 3일 연속 | P1 |
| 5 | 자전 미해결 | integrity_checks | 7일 연속 (14일 P1) | P2 |

### 3.3 안전장치

- **중복 방지**: 기존 [PLANNED]/[OPEN]/[WATCHING] 제목 키워드 2개+ 매칭 시 스킵
- **쿨다운**: 14일 내 [RESOLVED] 동일 패턴 재감지 방지
- **데이터 부족**: 7일 미만 데이터면 감지 스킵
- **매장 간 중복**: metric_name + file + title 기준 중복 제거
- **에러 격리**: 매장/지표/파일별 독립 try/except (발주 플로우 무영향)

---

## 4. Gap Analysis Summary

```
Match Rate: 100% (85/85)
- Domain:         20/20
- Analysis:       15/15
- Infrastructure: 15/15
- Application:    12/12
- Scheduler:       5/5
- Constants:       7/7
- Tests:           6/6
- Error Handling:  5/5
```

**Positive Additions (10건)**: store_id 필드, --ops-detect CLI, prev_7d=0 경계 처리, 불용어 제거, 최종 갱신일 자동 업데이트, issues_dir DI, unique_anomalies 반환 등

---

## 5. Usage

```bash
# 스케줄 자동 실행 (매일 23:55)
# run_scheduler.py 내부에서 자동 호출

# 수동 즉시 실행
python run_scheduler.py --ops-detect

# 프로그래밍 API
from src.application.services.ops_issue_detector import OpsIssueDetector
result = OpsIssueDetector().run_all_stores()
# → {"total_anomalies": N, "unique_anomalies": M, "registered": K}
```

---

## 6. Lessons Learned

| # | 교훈 | 적용 |
|---|------|------|
| 1 | DB 스키마 확인 필수 | collection_logs에 collect_type 없음 → 단일 유형으로 적응 |
| 2 | waste_slips vs waste_slip_items | 전표 헤더 vs 품목 상세 — 폐기율은 품목 테이블 사용 |
| 3 | 불용어 처리로 중복 판정 정확도 향상 | "조사","검토","확인" 등 범용 단어 제거 |
| 4 | DI로 테스트 용이성 확보 | IssueChainWriter(issues_dir=tmp_path) |

---

## 7. Related Documents

- Plan: `docs/01-plan/features/ops-issue-pipeline.plan.md`
- Design: `docs/02-design/features/ops-issue-pipeline.design.md`
- Analysis: `docs/03-analysis/ops-issue-pipeline.analysis.md`
