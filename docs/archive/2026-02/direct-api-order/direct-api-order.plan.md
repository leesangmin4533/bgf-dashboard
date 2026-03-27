# Plan: Direct API 발주 입력 최적화

## 1. 개요

| 항목 | 내용 |
|------|------|
| Feature | direct-api-order |
| 우선순위 | High |
| 예상 소요 | 7일 |
| 관련 PDCA | direct-api-prefetch (#17, archived) |

## 2. 문제 정의

### 현재 상황
OrderExecutor가 Selenium으로 상품별 개별 입력 수행:
- 상품당 ~3.3초 (셀 활성화 0.3s + 코드 입력 0.1s + Enter 대기 1.0s + 배수 입력 0.5s + 행간 대기 0.5s)
- 50개 상품 = **~170초 (2.8분)**
- 최대 병목: `ORDER_AFTER_ENTER = 1.0s` (서버 상품정보 조회 대기)

### 5가지 문제점
1. **API 미확인**: Save API body/endpoint 캡처 이력 없음
2. **Selenium 병목**: 상품당 3.3초, 선형 증가
3. **에러 복구 취약**: 개별 실패 시 전체 재시도
4. **검증 부재**: 서버 반영 여부 미확인
5. **확장 한계**: 상품 수 증가 = 시간 선형 증가

## 3. 목표

| 항목 | 현재 | 목표 | 단축률 |
|------|------|------|--------|
| Direct API (최적) | ~170초 | ~10초 | **94%** |
| Hybrid Batch (대안) | ~170초 | ~25초 | **85%** |
| 타이밍 최적화 (안전망) | ~170초 | ~100초 | **41%** |

## 4. 접근 전략 (5각도)

### 각도 1: Save API 캡처 (전제조건)
gfn_transaction + XHR 이중 인터셉터로 저장 버튼 클릭 시 API body/response 캡처

### 각도 2: Direct API 발주 저장
캡처된 endpoint로 fetch() 직접 호출 - 50개 상품 1회 API 요청

### 각도 3: Hybrid 배치 그리드 입력
넥사크로 dataset.setColumn()으로 그리드 직접 조작 후 UI 저장 버튼

### 각도 4: Selenium 타이밍 최적화
ORDER_AFTER_ENTER 동적 감소 + 불필요 대기 제거

### 각도 5: 3단계 실행 전략
Direct API -> Hybrid -> Selenium 자동 폴백 체인

## 5. 구현 범위

### 신규 파일 (5)
- `scripts/capture_save_api.py` - API 캡처 스크립트
- `src/order/direct_api_saver.py` - Direct API 저장 모듈
- `src/order/batch_grid_input.py` - Hybrid 배치 입력 모듈
- `tests/test_direct_api_saver.py` - 15개 테스트
- `tests/test_batch_grid_input.py` - 10개 테스트
- `tests/test_order_executor_direct_api.py` - 10개 테스트

### 수정 파일 (3)
- `src/order/order_executor.py` - 3단계 실행 전략 통합
- `src/settings/constants.py` - 피처 플래그
- `src/settings/timing.py` - 타이밍 상수

## 6. 핵심 분기점

Step 1 (API 캡처) 결과에 따라:
- **캡처 성공** -> Direct API + Hybrid + Selenium (L1/L2/L3)
- **부분 캡처** -> SSV body 역구성 시도 -> Direct API
- **캡처 실패** -> Hybrid + Selenium (L1/L2)

## 7. 재사용 코드

| 모듈 | 함수 | 용도 |
|------|------|------|
| `direct_api_fetcher.py` | `parse_ssv_dataset()` | SSV 파싱 |
| `direct_api_fetcher.py` | `ssv_row_to_dict()` | 행->딕셔너리 |
| `direct_api_fetcher.py` | JS fetch() 패턴 | API 호출 |
| `order_executor.py` | `confirm_order()` | 저장 버튼 (Hybrid용) |
| `order_executor.py` | `_FIND_ORDER_FORM_JS` | 넥사크로 폼 탐색 |

## 8. 리스크

| 리스크 | 대응 |
|--------|------|
| Save API 캡처 불가 | Hybrid 경로 전환 |
| 넥사크로 dataset 접근 차단 | Selenium 타이밍 최적화 |
| 서버 인증 실패 | driver 쿠키 공유 |
| SSV body 형식 오류 | dry-run 검증 |

## 9. 검증 계획

1. 캡처 스크립트 -> JSON 저장 확인
2. dry-run 모드 SSV body 비교
3. 테스트 매장 1건 실제 발주
4. 기존 테스트 전체 통과 + 신규 35개
