# PDCA Completion Report: automation-phase2

> 자동화 2단계 — integrity 이상 → 원인 분석 → 액션 제안 → 카카오 발송

## 1. 개요

| 항목 | 내용 |
|------|------|
| Feature | automation-phase2 |
| 날짜 | 2026-03-28 |
| Commit | `cce8f42` |
| Match Rate | **97.3%** |
| 테스트 | 13개 PASS |
| 설계서 | v1.1 (코드 대조 완료, 불일치 0건) |

## 2. 해결한 문제

| Before | After |
|--------|-------|
| integrity "유령재고 3건" 숫자만 카카오 발송 | 원인 + 구체적 조치 제안 포함 |
| 상민님이 직접 원인 판단 | AI가 check_name별 원인 분석 |
| 어떤 상품인지 모름 | item_cd + 판매량 + 재고 포함 |

## 3. 구현 내역

| 파일 | 내용 |
|------|------|
| `action_type.py` (NEW) | 7개 ActionType: RESTORE_IS_AVAILABLE, CLEAR_GHOST_STOCK, FIX_EXPIRY_TIME, CLEAR_EXPIRED_BATCH, CHECK_DELIVERY_TYPE, DEACTIVATE_EXPIRED, MANUAL_CHECK_REQUIRED |
| `action_proposal_repo.py` (NEW) | save/get_pending/mark_resolved (store DB) |
| `action_proposal_service.py` (NEW) | 6개 check_name 전체 분석 → 제안 생성 |
| `kakao_proposal_formatter.py` (NEW) | 1000자 제한, 최대 5건, "외 N건" 표시 |
| `schema.py` | _STORE_COLUMN_PATCHES에 action_proposals (11컬럼) |
| `data_integrity_service.py` | _run_action_proposals() → return 직전 |

## 4. 실행 흐름

```
Phase 1.67 → 6개 체크 → _send_alert → _run_ai_summary → _run_action_proposals
  → ActionProposalService.generate(check_results)
    → 6개 check_name별 분석 + get_dangerous_skips()
    → action_proposals 저장 (PENDING)
  → KakaoProposalFormatter.format()
  → KakaoNotifier.send_message()
```

## 5. 카카오 메시지 예시

```
[46513] 조치 필요 2건

1. item_cd:0000080879190 | RESTORE_IS_AVAILABLE
   원인: is_available=0인데 최근 7일 판매 3개 (재고:0)
   제안: is_available → 1 복원 권장

2. CLEAR_GHOST_STOCK
   원인: 입고 기록 없는 재고 5건 감지
   제안: 실물 확인 후 재고 0 보정

내일 발주 전 확인 부탁드립니다
```

## 6. 에러 처리

모든 제안 로직은 발주 파이프라인을 절대 중단하지 않음:
- ActionProposalService.generate(): try/except → 빈 리스트
- _run_action_proposals(): try/except → logger.error
- _get_dangerous_skips(): try/except → 빈 리스트

## 7. 테스트 (13개)

| 클래스 | 건수 | 내용 |
|--------|:----:|------|
| TestActionType | 1 | 7코드 존재 + 중복 없음 |
| TestActionProposalService | 7 | 4개 check_name 제안, OK 스킵, PENDING 저장, 예외 안전 |
| TestKakaoProposalFormatter | 3 | 빈 제안, 1000자, 5건 초과 |
| TestDataIntegrityServiceProposals | 2 | 호출 확인, 실패 무해 |

## 8. 3단계 전제 조건

2단계 2주 안정 운영 후:
- 카카오 '승인' 입력 → action_proposals.status='APPROVED' → 자동 실행
- RESTORE_IS_AVAILABLE: inventory_repo 자동 복원
- CLEAR_GHOST_STOCK: stock_qty=0 자동 보정
