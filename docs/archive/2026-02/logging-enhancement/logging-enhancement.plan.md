# Plan: logging-enhancement

> 로깅 강화 및 예외 처리 품질 개선

## 1. Overview

### 1.1 Problem Statement

BGF Auto 프로젝트의 예외 처리 및 로깅에 다음 문제가 확인됨:

1. **Silent Exception (13곳)**: `except Exception: pass` 또는 `except Exception: return []`로 에러를 무시
2. **print() 남용 (40+곳)**: alert/, analysis/ 모듈에서 logger 대신 print() 사용
3. **Exception 컨텍스트 부족**: item_cd 포함 ~60%, store_id 포함 ~40%
4. **waste_cause_analyzer.py 5곳 silent pass**: 폐기 원인 분류의 핵심 컨텍스트(날씨/프로모/휴일) 조회 실패 시 무시

### 1.2 Goal

- Silent exception 0건 달성 (13건 → 0건)
- print() → logger 전환 (40+건 → 0건, __main__ 블록 제외)
- 핵심 모듈 exception에 item_cd + store_id 컨텍스트 포함
- 테스트 커버리지: 변경 모듈 100% 테스트

### 1.3 Scope

**In-Scope**:
- waste_cause_analyzer.py silent pass 5곳 수정
- waste_report.py silent return 5곳 수정
- src/alert/ print() → logger 전환 (3파일)
- src/analysis/ print() → logger 전환 (2파일)
- src/config/store_manager.py print() → logger 전환
- Exception 컨텍스트 강화 (핵심 모듈 우선)
- logger.py 유틸리티 확장 (structured context helper)

**Out-of-Scope**:
- Collector 모듈 (이미 로깅 양호)
- `__main__` 블록의 print() (개발용 유지)
- 579개 전체 exception 감사 (이번 PDCA에서는 핵심 모듈만)
- DB 리소스 관리 개선 (별도 PDCA)

### 1.4 Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| 로깅 추가로 성능 저하 | Low | logger.debug 레벨로 상세 로그, 기본 INFO 레벨 |
| 기존 테스트 영향 | Medium | mock 패치 경로 확인, conftest.py 업데이트 불필요 |
| 로그 파일 크기 증가 | Low | 기존 rotation 정책(20MB×10) 충분 |

---

## 2. Implementation Phases

### Phase 1: waste_cause_analyzer.py Silent Pass 수정 (핵심)

**대상**: 5곳 silent exception → logger.warning + 안전한 fallback

| Line | 메서드 | 현재 | 변경 |
|------|--------|------|------|
| 341 | _gather_context() | `except: pass` | `except: logger.warning(f"product_details 조회 실패: {item_cd}", exc_info=True)` |
| 394 | _get_weather_context() | `except: return None` | `except: logger.debug(...); return None` |
| 420 | _get_promo_context() | `except: return None` | `except: logger.debug(...); return None` |
| 435 | _get_holiday_context() | `except: return None` | `except: logger.debug(...); return None` |
| 637 | _load_params() | `except: pass` | `except: logger.warning("eval_params.json 로드 실패", exc_info=True)` |

**원칙**:
- 데이터 조회 실패는 `logger.debug` (빈번할 수 있음)
- 설정/파라미터 실패는 `logger.warning` (운영 영향)
- 모든 곳에 item_cd, store_id 컨텍스트 포함

### Phase 2: waste_report.py Silent Return 수정

**대상**: 5곳 `except Exception: return []` → 로그 추가

| Line | 메서드 | 변경 |
|------|--------|------|
| 250 | _fetch_inventory_tracking() | `logger.warning(f"inventory tracking 조회 실패 ({store_id}): {e}")` |
| 343 | _fetch_category_summary() | `logger.warning(f"category summary 조회 실패 ({store_id}): {e}")` |
| 433 | _fetch_all_tracking() | `logger.warning(f"all tracking 조회 실패 ({store_id}): {e}")` |
| 558 | _fetch_category_waste_ratio() | `logger.warning(f"waste ratio 조회 실패 ({store_id}): {e}")` |
| 626 | _fetch_related_details() | `logger.warning(f"related details 조회 실패 ({store_id}): {e}")` |

**원칙**: return 값은 유지 (`[]` 또는 `None`), 로그만 추가

### Phase 3: print() → logger 전환

**대상 파일 및 print() 수**:

| File | print() 수 | 변환 방식 |
|------|-----------|----------|
| src/alert/expiry_checker.py | 4 | `logger.info()` — 사용자 알림 출력 |
| src/alert/promotion_alert.py | 7 | `logger.info()` — 사용자 알림 출력 |
| src/alert/delivery_utils.py | 7 | `logger.debug()` — 테스트 함수 내 |
| src/analysis/daily_report.py | 12+ | `logger.info()` — 리포트 미리보기 |
| src/analysis/trend_report.py | 15+ | `logger.info()` — 리포트 미리보기 |
| src/config/store_manager.py | 18 | `logger.info()` — 점포 요약 |

**원칙**:
- 사용자 대면 출력 → `logger.info()`
- 디버그/테스트 출력 → `logger.debug()`
- `if __name__ == "__main__"` 블록은 유지 (개발 편의)

### Phase 4: logger.py 유틸리티 확장

**새 헬퍼 함수**:

```python
def log_with_context(logger, level, msg, **ctx):
    """컨텍스트 키워드를 자동 포맷하는 로깅 헬퍼.

    Usage:
        log_with_context(logger, "warning", "DB 조회 실패",
                        item_cd="123", store_id="S001", phase="1.55")
        # Output: "DB 조회 실패 | item_cd=123 | store_id=S001 | phase=1.55"
    """
```

- 기존 코드에 강제 적용하지 않음 (opt-in)
- 새로 작성하는 exception handler에서 사용 권장

### Phase 5: 테스트 작성

| 대상 | 테스트 내용 | 예상 수 |
|------|-----------|---------|
| waste_cause_analyzer.py | silent pass → 로깅 검증 (mock logger) | 5개 |
| waste_report.py | silent return → 로깅 검증 | 5개 |
| alert 모듈 | print() 제거 확인 (grep 기반) | 3개 |
| log_with_context() | 포맷 검증 | 3개 |
| 기존 테스트 회귀 | 전체 1564개 통과 확인 | - |

**예상 신규 테스트**: 16개

---

## 3. Success Criteria

| Metric | Before | Target |
|--------|--------|--------|
| Silent exception (pass/return) | 13곳 | 0곳 |
| print() in production (non-__main__) | 40+곳 | 0곳 |
| Exception with item_cd context | ~60% | 80%+ (핵심 모듈) |
| Exception with store_id context | ~40% | 70%+ (핵심 모듈) |
| New tests | 0 | 16개 |
| Existing tests | 1564 pass | 1564+ pass |

---

## 4. File Change Summary

| Phase | Files Modified | Files Created |
|-------|---------------|--------------|
| Phase 1 | waste_cause_analyzer.py | - |
| Phase 2 | waste_report.py | - |
| Phase 3 | expiry_checker.py, promotion_alert.py, delivery_utils.py, daily_report.py, trend_report.py, store_manager.py | - |
| Phase 4 | src/utils/logger.py | - |
| Phase 5 | - | tests/test_logging_enhancement.py |

**총 수정 파일**: 9개
**총 신규 파일**: 1개

---

## 5. Implementation Order

```
Phase 1 (waste_cause_analyzer.py)     ← 핵심, 최우선
  ↓
Phase 4 (logger.py utility)           ← Phase 2,3에서 활용
  ↓
Phase 2 (waste_report.py)             ← Phase 4 헬퍼 사용
  ↓
Phase 3 (print → logger)             ← 단순 치환, 병렬 가능
  ↓
Phase 5 (테스트)                      ← 전체 검증
```

---

## 6. Estimated Timeline

| Phase | 예상 시간 |
|-------|----------|
| Phase 1 | 30분 |
| Phase 4 | 20분 |
| Phase 2 | 20분 |
| Phase 3 | 40분 |
| Phase 5 | 30분 |
| **Total** | **~2.5시간** |
