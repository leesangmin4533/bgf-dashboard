# Plan: automation-phase2

> 자동화 2단계 — integrity 이상 → 원인 분석 → 액션 제안 → 카카오 발송
> 설계서: v1.1 (코드 대조 완료, 불일치 0건)

## 1. 수정 범위

### 신규 4개 / 수정 2개

| 파일 | 변경 |
|------|------|
| `src/prediction/action_type.py` | NEW: 7개 ActionType 코드 |
| `src/infrastructure/database/repos/action_proposal_repo.py` | NEW: save/get_pending/mark_resolved |
| `src/application/services/action_proposal_service.py` | NEW: 6개 check_name 분석 → 제안 생성 |
| `src/application/services/kakao_proposal_formatter.py` | NEW: 카카오 메시지 포맷 |
| `src/infrastructure/database/schema.py` | 수정: _STORE_COLUMN_PATCHES에 action_proposals |
| `src/application/services/data_integrity_service.py` | 수정: _run_action_proposals() 추가 |

## 2. DB 마이그레이션

- `_STORE_COLUMN_PATCHES`에 CREATE TABLE IF NOT EXISTS action_proposals (v66 패턴)
- 9개 컬럼: id, proposed_at, proposal_date, store_id, item_cd, action_type, reason, suggestion, evidence, status, resolved_at

## 3. ActionType 7개

RESTORE_IS_AVAILABLE, CLEAR_GHOST_STOCK, FIX_EXPIRY_TIME, CLEAR_EXPIRED_BATCH, CHECK_DELIVERY_TYPE, DEACTIVATE_EXPIRED, MANUAL_CHECK_REQUIRED

## 4. 실행 흐름

```
Phase 1.67 run_all_checks(store_id)
  → 기존 6개 체크
  → _run_ai_summary() (하네스 v2.2)
  → _run_action_proposals() (NEW)
      → ActionProposalService.generate(check_results)
      → KakaoProposalFormatter.format()
      → KakaoNotifier.send_message()
```

## 5. 핵심 패턴

- Repository: try/finally + _get_conn()
- Service 생성자: store_id만
- Kakao: KakaoNotifier(DEFAULT_REST_API_KEY) 동적 생성
- 실패: try/except → logger.error (발주 무영향)
- DataIntegrityService: _run_single_check 디스패치 패턴, return 직전에 추가

## 6. 실제 CHECK_NAMES

expired_batch_remaining, food_ghost_stock, expiry_time_mismatch, missing_delivery_type, past_expiry_active, unavailable_with_sales

## 7. 테스트 (~12개)

- 6개 check_name별 제안 생성
- OK/RESTORED 제안 없음
- PENDING 저장
- 실패 발주 무영향
- 카카오 1000자 제한
- 5건 초과 표시
