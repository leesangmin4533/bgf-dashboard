# 드라이런 발주 상세 엑셀 내보내기

## Context
드라이런 실행 시 예측→조정→배수정렬→BGF 입력값까지 전 과정의 중간값을 엑셀로 한눈에 볼 수 없음.
현재는 로그 파싱이나 DB 조회를 별도로 해야 확인 가능. 상품별로 예측 파이프라인의 모든 단계를 엑셀 한 장에서 파악 가능하도록 설계.

## 엑셀 컬럼 설계 (5개 섹션)

### A. 기본정보
| 컬럼 | 소스 |
|------|------|
| No | 순번 |
| 상품코드 | item_cd |
| 상품명 | item_nm |
| 중분류 | mid_cd |
| 수요패턴 | demand_pattern |
| 데이터일수 | data_days |
| 판매일비율 | sell_day_ratio |

### B. 예측 단계
| 컬럼 | 소스 | 비고 |
|------|------|------|
| WMA(원본) | **신규** wma_raw | feature blend 전 |
| Feature예측 | feat_prediction | feature 모델 값 |
| 블렌딩결과 | predicted_qty | WMA+Feature 가중평균 |
| 요일계수 | weekday_coef | |
| 조정예측 | adjusted_qty | 모든 계수 적용 후 |

### C. 재고/필요량
| 컬럼 | 소스 |
|------|------|
| 현재재고 | current_stock |
| 미입고 | pending_qty |
| 안전재고 | safety_stock |
| 필요량 | **신규** need_qty |

### D. 조정 과정
| 컬럼 | 소스 | 비고 |
|------|------|------|
| Rule발주 | rule_order_qty | ML 전 |
| ML예측 | ml_order_qty | ML 단독 |
| ML가중치 | ml_weight_used | |
| ML후발주 | order_qty 중간 | ensemble 후 |
| 조정이력 | **신규** proposal_summary | 단계별 요약 |

### E. 배수정렬 + BGF 입력
| 컬럼 | 소스 | 비고 |
|------|------|------|
| 정렬전수량 | **신규** round_before | floor/ceil 전 |
| 내림후보 | **신규** round_floor | |
| 올림후보 | **신규** round_ceil | |
| 정렬결과 | final_order_qty | = order_qty |
| 발주단위(입수) | order_unit_qty | DB 값 |
| **PYUN_QTY(배수)** | 계산 | ceil(final/unit) |
| **TOT_QTY(발주량)** | 계산 | PYUN×unit |
| 모델타입 | model_type | |

## 수정 파일

### 1. `src/prediction/improved_predictor.py` — PredictionResult 확장
- PredictionResult dataclass에 5개 필드 추가:
  - `wma_raw: float = 0.0` — WMA 원본 (feature blend 전)
  - `need_qty: float = 0.0` — 필요량 (재고 차감 전)
  - `proposal_summary: str = ""` — 조정 이력 한줄 요약
  - `round_floor: int = 0` — 배수 내림 후보
  - `round_ceil: int = 0` — 배수 올림 후보
- `_compute_base_prediction()`: wma_raw 값 반환에 추가
- `_compute_safety_and_order()`: need_qty, proposal_summary, round_floor/ceil 세팅
- `predict()`: 새 필드를 PredictionResult에 전달

### 2. `src/order/auto_order.py` — order_list dict 확장
- `_convert_prediction_result_to_dict()` 에 신규 필드 추가:
  - `wma_raw`, `need_qty`, `proposal_summary`, `round_floor`, `round_ceil`
  - 기존 누락: `rule_order_qty`, `ml_order_qty`, `ml_weight_used`, `demand_pattern`, `sell_day_ratio`, `model_type`

### 3. `scripts/export_dryrun_excel.py` — 신규 엑셀 내보내기 스크립트
- `run_full_flow.py`의 dry-run 로직 재사용
- `get_recommendations()` → order_list 획득
- 각 item에 PYUN_QTY, TOT_QTY 계산 추가
- openpyxl로 5개 섹션 컬러 구분 엑셀 생성
- 출력: `data/exports/dryrun_detail_{날짜}.xlsx`

### 4. `scripts/run_full_flow.py` — `--export-excel` 옵션 추가
- dry_run 완료 후 order_list를 엑셀로 저장하는 기능
- export_dryrun_excel의 함수를 import해서 호출

## 엑셀 포맷
- 헤더: 5개 섹션별 색상 구분 (파랑/초록/주황/보라/빨강)
- PYUN_QTY, TOT_QTY 컬럼 강조 (빨간 배경)
- 조건부 서식: 발주량 10개 이상 빨간 글씨
- 합계행: 총 발주수량, 총 PYUN_QTY
- 시트명: `발주상세_{배송일}`

## 검증
1. `python scripts/run_full_flow.py --no-collect --max-items 999 --store-id 46513 --export-excel` 실행
2. 생성된 엑셀에서 카스캔500ml 확인: WMA→조정→need→72→PYUN_QTY=3 전체 추적 가능
3. 기존 테스트 회귀 없음 확인 (PredictionResult 필드 추가는 default값이라 안전)
4. PYUN_QTY × ORD_UNIT_QTY = TOT_QTY = final_order_qty 일치 검증
