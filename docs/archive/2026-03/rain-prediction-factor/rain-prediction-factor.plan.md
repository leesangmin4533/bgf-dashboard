# Plan: 강수량/강수확률 예측 계수 추가 (rain-prediction-factor)

## Context
BGF 발주 사이트 TopFrame의 ds_weatherTomorrow 데이터셋에 강수확률(RAIN_RATE), 강수량(RAIN_QTY), 강수유형(RAIN_TY/RAIN_TY_NM)이 있지만, 현재는 HIGHEST_TMPT(최고기온)만 추출하여 예측에 반영 중. 비/눈은 편의점 유동인구에 직접적 영향(비 오면 방문객 감소)이 있으므로 예측 정확도 향상에 기여.

## ds_weatherTomorrow 컬럼 구조 (bgf_topframe_datasets.json L13712-13734 확인)
전체 16개 컬럼:
```
WEATHER_DATE, ADM_SECT_CD, ADM_SECT_CD_NM, WEATHER_YMD, WEATHER_CD,
WEATHER_CD_NM, VIEW_WEATHER_CD, VIEW_WEATHER_CD_NM, HIGHEST_TMPT,
LOWEST_TMPT, AVG_TMPT, RAIN_RATE, RAIN_QTY, RAIN_TY, RAIN_TY_NM, REMARKS
```

| 컬럼 | 설명 | 타입 | 현재 수집 | 추가 |
|------|------|------|:--------:|:----:|
| WEATHER_YMD | 날짜(YYYYMMDD) | STRING | O | - |
| HIGHEST_TMPT | 최고기온 | Decimal(.hi) | O | - |
| RAIN_RATE | 강수확률(%) | Decimal(.hi) | - | O |
| RAIN_QTY | 강수량(mm) | Decimal(.hi) | - | O |
| RAIN_TY_NM | 강수유형명 | STRING | - | O |
| WEATHER_CD_NM | 날씨설명 | STRING | - | O (눈 감지 보조) |

**넥사크로 Decimal 파싱**: `typeof val === 'object' && val.hi !== undefined` → `val.hi` (HIGHEST_TMPT와 동일 패턴)

### 라이브 검증 결과 (2026-03-02, 매장 46513 이천호반베르디움점)
```
Row 0: 03-02(오늘) RAIN_RATE=50(.hi) RAIN_QTY=3(.hi) RAIN_TY_NM="" WEATHER_CD_NM="오후한때 비온후 갬"
Row 1: 03-03(내일) RAIN_RATE=40(.hi) RAIN_QTY=0(.hi) RAIN_TY_NM="" WEATHER_CD_NM="흐림후 차차갬"
Row 2: 03-04(모레) RAIN_RATE=30(.hi) RAIN_QTY=0(.hi) RAIN_TY_NM="" WEATHER_CD_NM="구름많음"
```
**발견**: RAIN_TY_NM은 비 예보 시에도 빈 문자열 → **눈 감지 시 WEATHER_CD_NM에서 "눈" 키워드 매칭 보조 필요**

## 수정 대상 파일 (6개)

### 1. `src/sales_analyzer.py` L515-537 — JS 루프에 강수 추출 추가
현재 ds_weatherTomorrow 루프에서 WEATHER_YMD, HIGHEST_TMPT만 추출.
**추가 사항:**
- L511 이후: `result.forecast_precipitation = {};` 초기화
- L535-537 사이(temp 저장 직후): RAIN_RATE, RAIN_QTY, RAIN_TY_NM, WEATHER_CD_NM 추출
- Decimal 파싱: RAIN_RATE/RAIN_QTY는 `.hi` 속성 체크 (HIGHEST_TMPT와 동일 패턴, 라이브 확인됨)
- RAIN_TY_NM, WEATHER_CD_NM은 문자열 → 직접 사용
- 눈 감지: `RAIN_TY_NM.indexOf('눈') >= 0 || WEATHER_CD_NM.indexOf('눈') >= 0`
- `result.forecast_precipitation[ymdStr] = {rain_rate, rain_qty, rain_type_nm, weather_cd_nm, is_snow}` 저장
- L563-567 로깅에 강수 예보 정보 추가

### 2. `src/collectors/weather_collector.py` L134-161 — 동일 강수 추출 추가
독립 수집기에도 동일 패턴 적용 (sales_analyzer.py와 같은 JS 루프 구조).
- L112 이후: `result.forecast_precipitation = {};` 초기화
- L155-157 사이: RAIN_RATE, RAIN_QTY, RAIN_TY_NM, WEATHER_CD_NM 추가 추출 + is_snow 판정
- 로깅에 강수 정보 추가

### 3. `src/scheduler/daily_job.py` L1083-1093 — DB 저장 블록 추가
현재 `_save_weather_data()`에서 forecast_daily(기온)만 저장.
**추가 사항** (L1093 직후):
```python
forecast_precipitation = weather.get("forecast_precipitation", {})
if forecast_precipitation:
    for fdate, precip in forecast_precipitation.items():
        if precip.get("rain_rate") is not None:
            self.weather_repo.save_factor(
                factor_date=fdate, factor_type="weather",
                factor_key="rain_rate_forecast",
                factor_value=str(precip["rain_rate"])
            )
        if precip.get("rain_qty") is not None:
            self.weather_repo.save_factor(
                factor_date=fdate, factor_type="weather",
                factor_key="rain_qty_forecast",
                factor_value=str(precip["rain_qty"])
            )
        if precip.get("rain_type_nm"):
            self.weather_repo.save_factor(
                factor_date=fdate, factor_type="weather",
                factor_key="rain_type_nm_forecast",
                factor_value=precip["rain_type_nm"]
            )
        if precip.get("weather_cd_nm"):
            self.weather_repo.save_factor(
                factor_date=fdate, factor_type="weather",
                factor_key="weather_cd_nm_forecast",
                factor_value=precip["weather_cd_nm"]
            )
        if precip.get("is_snow"):
            self.weather_repo.save_factor(
                factor_date=fdate, factor_type="weather",
                factor_key="is_snow_forecast",
                factor_value="1"
            )
    logger.info(f"Precipitation forecast saved: {list(forecast_precipitation.keys())}")
```
**스키마 변경 없음** — 기존 external_factors 테이블 UPSERT 패턴 동일 사용

### 4. `src/prediction/coefficient_adjuster.py` — 강수 계수 로직
**신규 상수** (L90 부근, WEATHER_DELTA_COEFFICIENTS 뒤):
```python
PRECIPITATION_COEFFICIENTS = {
    "light_rain": {  # 30~60%
        "categories": ["001","002","003","004","005","012"],  # food
        "coefficient": 0.95,
    },
    "moderate_rain": {  # 60~80%
        "categories": ["001","002","003","004","005","012"],
        "coefficient": 0.90,
    },
    "moderate_rain_boost": {  # 60~80% 라면/핫푸드 +5%
        "categories": ["015","016","017","018"],
        "coefficient": 1.05,
    },
    "heavy_rain": {  # 80%+ 또는 10mm+
        "categories": ["001","002","003","004","005","012"],
        "coefficient": 0.85,
    },
    "heavy_rain_boost": {  # 80%+ 라면/핫푸드 +10%
        "categories": ["015","016","017","018"],
        "coefficient": 1.10,
    },
    "snow": {  # 눈
        "categories": ["001","002","003","004","005","012"],
        "coefficient": 0.82,
    },
    "snow_boost": {  # 눈 라면/핫푸드 +12%
        "categories": ["015","016","017","018"],
        "coefficient": 1.12,
    },
}
```

**신규 메서드 2개:**
- `get_precipitation_for_date(date_str)` → external_factors에서 rain_rate_forecast, rain_qty_forecast, rain_type_nm_forecast 조회, dict 반환
- `get_precipitation_coefficient(date_str, mid_cd)` → 강수확률/강수량/유형별 계수 반환
  - 눈(is_snow_forecast="1") → snow 계수
  - 80%+ 또는 10mm+ → heavy
  - 60~80% → moderate
  - 30~60% → light
  - <30% → 1.0

**`apply()` 수정** (L329 부근):
```python
# 기존
weather_coef = self.get_weather_coefficient(target_date_str, mid_cd)
# 추가
precip_coef = self.get_precipitation_coefficient(target_date_str, mid_cd)
weather_coef *= precip_coef  # 기온 계수에 강수 계수 곱하기 병합
```

**`_apply_multiplicative()` 로깅** (L401-406 뒤):
- `[PRED][2-Precip]` 로그 태그로 강수 계수 적용 내역 출력

**`_apply_additive()`**: weather_coef에 이미 precip_coef가 곱해져 있으므로 AdditiveAdjuster에 그대로 전달 (변경 불필요)

### 5. `src/prediction/categories/food.py` — 푸드x강수 교차 계수
기존 `FOOD_WEATHER_CROSS_COEFFICIENTS` (기온×mid_cd) 패턴과 동일하게:
```python
FOOD_PRECIPITATION_CROSS_COEFFICIENTS = {
    "light": {  # 30~60%
        "001": 0.97,  # 주먹밥/김밥 -3%
        "002": 0.97,  # 샌드위치 -3%
        "003": 0.95,  # 도시락 -5% (야외소비 영향 큼)
        "004": 1.00,  # 햄버거 (실내소비)
        "005": 1.00,  # 조리면
        "012": 0.98,  # 디저트
    },
    "moderate": {  # 60~80%
        "001": 0.93, "002": 0.93, "003": 0.90,
        "004": 0.97, "005": 1.00, "012": 0.95,
    },
    "heavy": {  # 80%+
        "001": 0.88, "002": 0.88, "003": 0.85,
        "004": 0.93, "005": 0.97, "012": 0.90,
    },
}
```
- `get_food_precipitation_cross_coefficient(mid_cd, rain_rate)` 함수 추가
- `apply()`에서 `food_wx_coef` 패턴과 동일하게 `food_precip_coef` 계산 → _apply_multiplicative에 전달

### 6. `tests/test_precipitation.py` (신규) — ~20개 테스트
- 강수 데이터 DB 조회 (get_precipitation_for_date)
- 계수 계산 (light/moderate/heavy/snow/boost)
- 푸드 교차 계수 (mid_cd별)
- DB 저장/UPSERT
- apply() 통합 (precip_coef가 weather_coef에 곱해지는지)
- 경계값 (30%, 60%, 80%, 10mm)
- None/missing 데이터 폴백 (1.0)

## 설계 결정사항
1. **weather_coef *= precip_coef 병합** — 파이프라인 단계 추가 없이 기존 흐름 유지, _apply_additive도 자동 반영
2. **ML 피처 추가는 별도 PDCA로 분리** — 모델 재학습 필요, 규칙 기반 먼저 검증
3. **눈 감지 2단계**: RAIN_TY_NM에서 "눈" 매칭 → 없으면 WEATHER_CD_NM에서 "눈" 매칭 (라이브에서 RAIN_TY_NM이 비 예보 시에도 빈 문자열 확인됨)
4. **계수 보수적 설정** — compound floor(15%)와 additive clamp으로 과도한 억제 방지
5. **Decimal 파싱 동일 적용** — RAIN_RATE/RAIN_QTY도 HIGHEST_TMPT와 같은 넥사크로 Decimal 객체일 수 있음

## 검증 방법
1. `python -m pytest tests/test_precipitation.py -v` (신규 테스트)
2. `python -m pytest tests/ --tb=short -q` (전체 회귀 테스트)
3. 라이브: 다음 7시 실행 후 prediction.log에서 `[PRED][2-Precip]` 로그 확인
4. DB 확인: `SELECT * FROM external_factors WHERE factor_key LIKE 'rain%'`
