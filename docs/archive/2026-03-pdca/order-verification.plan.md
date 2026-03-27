# order-verification Planning Document

> **Summary**: 발주 저장 검증 강화 + 발주현황 재수집을 통한 중복 발주 방지
>
> **Project**: BGF 자동 발주 시스템
> **Author**: AI
> **Date**: 2026-03-26
> **Status**: Draft

---

## 1. Overview

### 1.1 Purpose

Direct API 발주 저장 시 false positive (성공 응답이지만 실제 미저장) 를 감지하고,
다음날 발주 전 BGF 발주현황을 재수집하여 order_tracking 정합성을 보장한다.

### 1.2 Background

**발생한 문제 (2026-03-25~26, 46513 매장)**:
1. 첫 세션(a77fda73): Direct API/Batch Grid/Selenium 모두 실패 → BGF에 발주 미전송
2. 두 번째 세션(32513be7): 깨진 브라우저 상태에서 Direct API 전송 → BGF가 `errCd=99999` 성공 응답
3. 검증: `matched=0, missing=89` (89건 전부 실종) 인데 `grid_replaced` 추정으로 검증 스킵 → **false positive**
4. order_tracking에 허위 기록 (remaining_qty=1)
5. 3/26: prediction이 pending=1 반영 → BGF 실시간 조회 pending=0 → adjuster 재계산 → **중복 발주 (qty=2)**

**근본 원인**:
- Direct API 검증 로직이 `grid_replaced_cond=True`일 때 missing=100%여도 성공 처리
- 발주 폼 상태 검증 미흡 (`ordYn=''` 빈값을 가능으로 판단)
- order_tracking 기록과 BGF 실제 발주현황 간 정합성 검증 부재

### 1.3 Related Documents

- `src/order/direct_api_saver.py` — Direct API 저장 및 검증 로직
- `src/order/auto_order.py` — 자동 발주 메인 오케스트레이터
- `src/collectors/order_status_collector.py` — 발주현황 수집 (pending_clear)
- `src/order/order_adjuster.py` — 미입고/재고 조정
- `docs/04-report/changelog.md` — 변경 이력

---

## 2. Scope

### 2.1 In Scope

- [x] **Layer 1**: Direct API 검증 강화 — missing 비율 임계치 초과 시 실패 처리
- [x] **Layer 2**: 발주 전 상태 검증 — `ordYn` 빈값이면 발주 불가 처리
- [x] **Layer 3**: 발주현황 재수집 — 다음날 발주 전 BGF 발주현황과 order_tracking 대조

### 2.2 Out of Scope

- Layer 4 (세션 격리/드라이버 재생성) — 변경 범위가 크므로 별도 PDCA로 진행
- order_adjuster의 safety_stock 이중 적용 문제 — 주먹밥 Cap 재적용으로 임시 방어 완료, 별도 관찰

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | Direct API 저장 후 missing 비율 > 50%이면 실패 처리 (grid_replaced 무시) | High | Pending |
| FR-02 | Direct API 전 `ordYn` 값이 빈값이면 발주 불가로 판단, 다음 레벨 폴백 | High | Pending |
| FR-03 | 발주 실행 전 BGF 발주현황 화면에서 전날 발주 목록 재수집 | High | Pending |
| FR-04 | 재수집 결과와 order_tracking 대조, DB에만 있는 건은 무효화 (remaining_qty=0) | High | Pending |
| FR-05 | 무효화된 건은 로그에 경고 기록 + prediction의 pending에서 제외 | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 안정성 | false positive 발생률 0% | 1주간 발주 로그 모니터링 |
| 성능 | 발주현황 재수집 30초 이내 | 로그 타임스탬프 비교 |
| 호환성 | 기존 3개 매장 발주 흐름에 영향 없음 | 전 매장 테스트 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] FR-01~05 전체 구현
- [ ] 기존 테스트 전체 통과 (2195+)
- [ ] 3개 매장 dry_run 테스트 정상
- [ ] changelog.md + CLAUDE.md 변경 이력 업데이트

### 4.2 Quality Criteria

- [ ] Direct API false positive 재현 불가
- [ ] 발주현황 재수집 후 order_tracking 정합성 100%
- [ ] 정상 발주 흐름(ordYn=Y, matched>50%)에는 영향 없음

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| missing 임계치가 너무 낮으면 정상 케이스도 실패 처리 | Medium | Low | 50% 임계치 + 로그 모니터링, 필요 시 조정 |
| ordYn이 정상 케이스에서도 빈값일 수 있음 | Medium | Low | 1주간 로그 수집 후 판단, 빈값 시 경고만 + 진행도 옵션 |
| 발주현황 재수집 중 BGF 사이트 오류 | Low | Medium | 재수집 실패 시 기존 order_tracking 유지 (보수적) |
| 발주현황과 order_tracking 타이밍 차이 (입고 직후) | Low | Medium | 당일 발주분만 대조, 이전 날짜는 제외 |

---

## 6. Architecture Considerations

### 6.1 수정 대상 파일

| 파일 | 수정 내용 | FR |
|------|----------|-----|
| `src/order/direct_api_saver.py` | 검증 로직: missing > 50% → 실패 | FR-01 |
| `src/order/direct_api_saver.py` | ordYn 빈값 → available=false | FR-02 |
| `src/collectors/order_status_collector.py` | 발주현황 재수집 메서드 추가 | FR-03 |
| `src/order/auto_order.py` | 발주 전 재수집 호출 + order_tracking 대조 | FR-03, FR-04 |
| `src/infrastructure/database/repos/order_tracking_repo.py` | 무효화 메서드 추가 | FR-04 |

### 6.2 실행 순서 (발주 파이프라인 내 위치)

```
기존:
  pending_clear → prediction → Floor/CUT/Cap → 미입고조정 → 수동차감 → Cap재적용 → 발주실행

변경 후:
  pending_clear → [NEW: 발주현황 재수집 + order_tracking 대조]
  → prediction → Floor/CUT/Cap → 미입고조정 → 수동차감 → Cap재적용 → 발주실행
                                                                    ↓
                                                         [NEW: Direct API 검증 강화]
```

### 6.3 Layer별 구현 전략

**Layer 1 (Direct API 검증 강화)**:
```python
# direct_api_saver.py 검증 로직
if missing_count > len(orders) * 0.5:
    # 50% 초과 missing → 무조건 실패 (grid_replaced 무시)
    return SaveResult(success=False, message=f"검증 실패: {missing_count}/{len(orders)} missing")
```

**Layer 2 (ordYn 검증)**:
```python
# direct_api_saver.py 발주 가능 확인
if not ordYn or ordYn.strip() == '':
    available = False  # 빈값 = 폼 비정상
```

**Layer 3 (발주현황 재수집)**:
```
1. order_status_collector에서 BGF '발주현황조회' 화면 접근
2. 전날(어제) 발주 목록 수집 (item_cd, order_qty, status)
3. order_tracking DB의 어제 auto 발주와 대조
4. DB에만 있고 BGF에 없는 건 → remaining_qty=0, status='invalidated' 처리
5. 로그 경고: "[발주정합성] {item_nm}: order_tracking에 있으나 BGF에 없음 → 무효화"
```

---

## 7. Convention Prerequisites

### 7.1 기존 프로젝트 컨벤션

- [x] `CLAUDE.md` 코딩 규칙 섹션 존재
- [x] 발주 파이프라인 변경 이력 테이블 존재
- [x] 로깅: `from src.utils.logger import get_logger` 사용
- [x] DB: Repository 패턴 사용
- [x] 예외 처리: `except Exception as e: logger.warning(...)` (silent pass 금지)

---

## 8. Next Steps

1. [ ] Design 문서 작성 (`order-verification.design.md`)
2. [ ] 구현 (Layer 1 → Layer 2 → Layer 3 순서)
3. [ ] 테스트 및 Gap 분석
4. [ ] 1주 모니터링 후 임계치 조정

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-03-26 | Initial draft | AI |
