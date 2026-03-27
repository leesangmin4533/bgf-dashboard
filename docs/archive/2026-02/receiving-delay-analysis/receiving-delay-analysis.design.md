# Design: 입고 지연 영향 분석 대시보드

## 1. API 설계

### GET /api/receiving/summary
매장별 입고 요약 통계.

**Response:**
```json
{
  "avg_lead_time": 1.5,
  "max_lead_time": 4.0,
  "short_delivery_rate": 0.12,
  "total_items_tracked": 150,
  "pending_items_count": 8,
  "pending_age_distribution": {
    "0-1": 3, "2-3": 2, "4-7": 2, "8+": 1
  }
}
```

### GET /api/receiving/trend?days=30
일별 리드타임 추이.

**Response:**
```json
{
  "dates": ["2026-02-01", ...],
  "avg_lead_times": [1.2, 1.5, ...],
  "delivery_counts": [25, 30, ...]
}
```

### GET /api/receiving/slow-items?limit=20
지연 상위 상품 목록.

**Response:**
```json
{
  "items": [
    {
      "item_cd": "8801234...",
      "item_nm": "상품명",
      "mid_cd": "001",
      "pending_age": 5,
      "lead_time_avg": 2.3,
      "short_delivery_rate": 0.25
    }
  ]
}
```

## 2. UI 설계

### 분석 탭 > 입고 서브탭
- **요약 카드 4개**: 평균 리드타임, 최대 리드타임, 숏배송율, 미입고 상품수
- **리드타임 추이 차트**: 라인차트 (30일, 일별 평균 + 건수 듀얼 Y축)
- **미입고 경과일 분포**: 바차트 (0-1일, 2-3일, 4-7일, 8일+)
- **지연 상품 테이블**: item_nm, pending_age, lead_time_avg, short_rate

## 3. 테스트 설계 (10개)

| 테스트 | 검증 내용 |
|--------|----------|
| test_summary_format | summary API 응답 형식 |
| test_summary_pending_distribution | pending_age 분포 계산 |
| test_trend_format | trend API 응답 형식 |
| test_trend_date_range | days 파라미터 동작 |
| test_slow_items_format | slow-items 응답 형식 |
| test_slow_items_sorted | pending_age 내림차순 정렬 |
| test_slow_items_limit | limit 파라미터 동작 |
| test_empty_data | 데이터 없을 때 빈 응답 |
| test_blueprint_registered | Blueprint 등록 확인 |
| test_js_file_exists | receiving.js 파일 존재 |
