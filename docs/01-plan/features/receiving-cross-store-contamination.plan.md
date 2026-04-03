# Plan: receiving-cross-store-contamination

## 문제 정의

센터매입 수집기(receiving_collector)의 Direct API가 점포 컨텍스트 없이 전표번호만으로 조회하여, 46513의 입고 데이터가 46704/47863/49965에 복제 저장됨.

### 피해 규모
| 항목 | 수량 |
|------|------|
| 오염 전표 수 | 63개 (1254개 중 5%) |
| receiving_history 오염 | 756건 (46704:259 + 47863:259 + 49965:238) |
| inventory_batches 오염 | 75건 (25건 × 3매장) |
| order_tracking 고스트 | 166건 (46704:59 + 47863:84 + 49965:23) |
| 증상 | 존재하지 않는 상품에 대한 폐기 알림 오발송 |

### 근본 원인

```
Direct API `/stgj010/search` 요청 시 strChitNo(전표번호) + strNapYmd(출고일)만 전송
→ 점포코드 미포함 → BGF 서버가 전표번호 기준으로 응답
→ 46513 전표 데이터가 46704/47863/49965에도 반환
→ 4/1 11:42 성공률 50% 폴백 코드 커밋했으나, 스케줄러 미재시작으로 20:31 수집 시 미적용
```

### 발견 경로
샌)계란블루베리잼샌드2 폐기 알림 → 발주 이력 없음 → 입고 이력 없음 → daily_sales 0건
→ receiving_history에만 존재 → 전표번호 4매장 동일 → Direct API 점포 필터 미포함 확인

## 수정 범위

### Task 1: 오염 데이터 정리 (긴급)
- 46704/47863/49965의 receiving_history에서 46513 전표 레코드 삭제
- 관련 inventory_batches 삭제
- 관련 order_tracking (order_source='receiving' + daily_sales에 없는 것) 삭제

### Task 2: 재발 방지 — Direct API 성공률 폴백 강화
- 현재 폴백 코드(4/1 11:42 커밋)가 이미 적용됨
- 추가: Direct API 응답의 전표별 상품에서 점포 교차검증 추가
- 방법: 수집 후 daily_sales에 해당 상품이 있는지 확인하여 오염 필터링

### Task 3: 폐기 알림 방어
- `_get_receiving_items_expiring_at`에서 daily_sales 교차검증 강화
- 현재: stock_qty == 0이면 제외
- 추가: **daily_sales에 아예 기록 없는 상품도 제외** (매장에 물리적으로 없는 상품)

### 변경하지 않는 것
- Direct API에 점포코드 파라미터 추가 — BGF 서버 API 스펙을 변경할 수 없음
- receiving_collector의 Selenium 폴백 로직 — 이미 정상 동작

## 구현 순서
1. Task 1 — DB 오염 정리 스크립트
2. Task 3 — 폐기 알림 방어 (daily_sales 교차검증)
3. Task 2 — 수집 후 교차검증 필터

## 검증 계획
- 정리 전/후 receiving_history 레코드 수 비교
- 정리 후 다중 매장 전표 0건 확인
- 다음 자동발주에서 오발송 없음 확인
