# Plan: conftest-fix

## 개요
테스트 fixture의 DB 스키마가 프로덕션 스키마와 불일치하여 15개 테스트가 실패. conftest.py를 프로덕션 스키마에 맞추고 mock 누락을 수정한다.

## 근본 원인 3가지

### 1. conftest.py product_details 컬럼 누락 (9개 실패)
- **현상**: `no such column: pd.large_cd` (+ sell_price, margin_rate, lead_time_days)
- **원인**: conftest.py:123의 product_details CREATE TABLE에 v42 이후 추가된 컬럼 없음
- **영향**: test_improved_predictor.py 9개 테스트

### 2. _substitution_detector 속성 미초기화 (4개 실패)
- **현상**: `AttributeError: 'ImprovedPredictor' object has no attribute '_substitution_detector'`
- **원인**: test_prediction_redesign_integration.py, test_pending_cross_validation.py에서 ImprovedPredictor stub 생성 시 `_substitution_detector = None` 누락
- **영향**: 4개 테스트

### 3. daily_sales fixture sale_qty 컬럼 없음 (1개 실패)
- **현상**: `no such column: ds.sale_qty` (test_new_product_lifecycle.py)
- **원인**: 테스트 자체 DB fixture에 daily_sales 스키마가 프로덕션과 불일치
- **영향**: 1개 테스트

## 수정 대상

### 파일 1: `tests/conftest.py` (라인 123-131)
product_details CREATE TABLE에 컬럼 추가:
- `sell_price INTEGER`
- `margin_rate REAL`
- `lead_time_days INTEGER DEFAULT 1`
- `large_cd TEXT`
- `small_cd TEXT`
- `class_nm TEXT`

### 파일 2: `tests/test_prediction_redesign_integration.py`
ImprovedPredictor stub에 `_substitution_detector = None` 추가

### 파일 3: `tests/test_pending_cross_validation.py`
ImprovedPredictor stub에 `_substitution_detector = None` 추가

### 파일 4: `tests/test_new_product_lifecycle.py`
test_similar_avg_calculation fixture의 daily_sales 테이블에 `sale_qty` 컬럼 확인/추가

## 수정하지 않는 것
- 프로덕션 소스 코드 (data_provider.py, improved_predictor.py 등)
- 다른 정상 통과 테스트

## 검증
- 15개 기존 실패 테스트가 모두 통과
- 전체 테스트 suite 회귀 없음
