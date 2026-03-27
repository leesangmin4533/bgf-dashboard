# Plan: 입고 지연 영향 분석 대시보드

## 1. 목적
- 입고 지연(리드타임, pending_age)이 발주 예측에 미치는 영향을 시각화
- 센터별 배송 성과, 상품별 지연 현황 파악
- 기존 order_tracking_repo, receiving_repo의 배치 메서드 활용 (DB 쿼리 신규 없음)

## 2. 기존 인프라
- `OrderTrackingRepository.get_pending_age_batch()` — 미입고 경과일
- `ReceivingRepository.get_receiving_pattern_stats_batch()` — 리드타임 통계
- `receiving_history` 테이블: lead_time, receiving_qty, order_qty
- ML Feature 36개 중 5개가 입고 패턴 피처

## 3. 구현 범위
- API 3개: /api/receiving/summary, /api/receiving/trend, /api/receiving/slow-items
- 대시보드 UI: 분석 탭 > 입고 서브탭
- 차트 3개: 리드타임 추이(라인), pending_age 분포(바), 지연 상품 테이블

## 4. 수정 파일
- `src/web/routes/api_receiving.py` (신규)
- `src/web/routes/__init__.py` (Blueprint 등록)
- `src/web/templates/index.html` (서브탭 추가)
- `src/web/static/js/receiving.js` (신규)
- `src/web/static/js/app.js` (탭 트리거)
- `tests/test_receiving_delay_analysis.py` (신규)
