# Plan: beer-zero-demand-overorder

## 문제 정의

맥주(049) 카테고리에서 저빈도 상품(삿포로, 필굿, 크로넨버그 등)이 수요 없는데 반복 발주됩니다.

### 피해 규모 (최근 30일)
| 매장 | 무수요 발주 횟수 | 총 불필요 발주량 |
|------|:-:|:-:|
| 47863 (마평로드) | 43회 (auto) | 260+개 |
| 49965 (원삼휴게소) | 20회 (auto) | 130+개 |

### 근본 원인 (라이브 추적 완료)

**beer.py `analyze_beer_pattern()`의 daily_avg 계산 버그:**
```
daily_avg = total_sales / data_days (판매일 기준)
삿포로캔350ml: 30일 중 1일 판매, 4개 → 4/1 = 4.0
→ safety_stock = 4.0 × 3일(금토) = 12.0
→ order = 12.0 - 4(재고) = 8개 발주 (실제 수요 월 4개)
```
달력일(30일) 기준이면 `4/30 = 0.13`, safety = 0.4 → 발주 0

**soju.py 동일 버그 존재** (197행)

## 수정 범위

### 변경 파일
1. `src/prediction/categories/beer.py` — daily_avg 분모 3곳 + safety 하한
2. `src/prediction/categories/soju.py` — daily_avg 분모 1곳 + safety 하한

### 변경 내용
1. `daily_avg = total_sales / data_days` → `daily_avg = total_sales / config["analysis_days"]` (30일)
2. `min_data_days` 미달 + data_days > 0 시 `safety_stock = max(safety_stock, 1.0)` 하한 추가

### 변경하지 않는 것
- DemandClassifier (BeerStrategy가 독립 경로로 daily_avg 계산하므로 직접 영향 없음)
- 다른 카테고리 Strategy (맥주/소주 외 과발주 보고 없음, food 파급 위험)

## 전문가 토론 결과

### 악마의 변호인 핵심 리스크
- 고빈도 맥주(카스500ml) 안전재고 ~17% 감소 → 허용 (2일분 여전히 충분)
- min_data_days 미달 시 safety=0은 위험 → 하한 1로 합의
- 전체 카테고리 수정은 food 대참사 위험 → 보류

### 실용주의자 핵심 판단
- beer.py 3곳 + soju.py 1곳 수정으로 80% 해결
- 고빈도 상품 영향: 카스500ml safety 24→20 (충분)
- 저빈도 상품 해결: 삿포로 safety 12→0.4 (과발주 해소)

## 검증 계획
1. 수정 전 기준값 기록 (완료)
2. 수정 후 동일 항목 비교
3. 4매장 맥주/소주 상위 상품 dry-run 시뮬레이션
4. 다음 07:00 자동발주 로그로 최종 확인

## 영향도
- **고영향**: 맥주(049), 소주(050) 카테고리 전체
- **예상 효과**: 47863/49965 무수요 반복발주 90% 이상 감소
- **위험**: 고빈도 상품 안전재고 소폭 감소 (허용 범위)
