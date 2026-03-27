# Design: dust-prediction (미세먼지 예보 기반 예측 계수)

> Plan 참조: `docs/01-plan/features/dust-prediction.plan.md`

## 1. 구현 순서

```
Step 1: constants.py         — 토글 + 상수 정의
Step 2: weather_collector.py — 팝업 오픈 + DT_INFO 파싱
Step 3: daily_job.py         — external_factors DB 저장
Step 4: coefficient_adjuster.py — get_dust_coefficient() 신규
Step 5: improved_predictor.py   — dust_coef 병합 (1줄)
Step 6: tests/test_dust_prediction.py — 전체 테스트
```

## 2. 파일별 상세 설계

---

### Step 1: `src/settings/constants.py`

**추가 위치**: `PAYDAY_ENABLED` (491행) 아래

```python
# ── 미세먼지 예보 계수 ──────────────────────────────────
DUST_PREDICTION_ENABLED = True    # 미세먼지 예보 기반 수요 감소 계수

# 미세먼지 등급 심각도 점수 (높을수록 나쁨)
DUST_GRADE_SCORE = {
    "좋음": 1,
    "보통": 2,
    "한때나쁨": 3,
    "나쁨": 4,
    "매우나쁨": 5,
}

# 미세먼지 등급별 × 카테고리별 수요 감소 계수
# 판정: dust_grade, fine_dust_grade 중 높은 점수 기준
# "나쁨" 이상(점수 ≥ 4)부터 적용, "한때나쁨"(3)은 영향 없음
DUST_COEFFICIENTS = {
    # 나쁨 (score 4): 외출 약간 감소
    "bad": {
        "food":      0.95,   # 001~005, 012
        "beverage":  0.93,   # 039~048
        "ice":       0.90,   # 027~030
        "default":   0.97,   # 기타
    },
    # 매우나쁨 (score 5): 외출 크게 감소
    "very_bad": {
        "food":      0.90,
        "beverage":  0.87,
        "ice":       0.83,
        "default":   0.93,
    },
}

# 미세먼지 대상 카테고리 매핑
DUST_CATEGORY_MAP = {
    "food": ["001", "002", "003", "004", "005", "012"],
    "beverage": [
        "039", "040", "041", "042", "043",
        "044", "045", "046", "047", "048",
    ],
    "ice": ["027", "028", "029", "030"],
}
```

---

### Step 2: `src/collectors/weather_collector.py`

**변경점 3가지:**

#### 2-A. 팝업 오픈 메서드 신규 (`_open_weather_popup`)

`_extract_weather_info()` 호출 **전에** 실행.

```python
def _open_weather_popup(self) -> bool:
    """날씨정보(주간) 팝업 오픈 (STZZZ80_P0)"""
    import time
    try:
        self.analyzer.driver.execute_script("""
            var topForm = nexacro.getApplication()
                .mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
            // pdiv_weather 영역 클릭 → STZZZ80_P0 팝업 생성
            if (topForm.pdiv_weather) {
                topForm.pdiv_weather.set_visible(true);
                // 실제 팝업 트리거는 img_weather 클릭 이벤트
                topForm.img_weather.click();
            }
        """)
        time.sleep(2)  # 팝업 + 트랜잭션 응답 대기

        # 팝업 존재 확인
        exists = self.analyzer.driver.execute_script("""
            try {
                var pf = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.TopFrame
                    .STZZZ80_P0;
                return pf && pf.form ? true : false;
            } catch(e) { return false; }
        """)
        if exists:
            logger.info("Weather popup (STZZZ80_P0) opened")
        else:
            logger.warning("Weather popup not found after click")
        return bool(exists)
    except Exception as e:
        logger.warning(f"Failed to open weather popup: {e}")
        return False
```

#### 2-B. collect() 메서드에 팝업 오픈 단계 추가

```python
def collect(self, target_date=None):
    ...
    # 3. 팝업 닫기 (광고)
    self.analyzer.close_popup()

    # 3.5 날씨 팝업 오픈 (미세먼지 수집용) ← 신규
    self._open_weather_popup()

    # 4. 날씨 정보 추출
    weather_data = self._extract_weather_info()
    ...
```

#### 2-C. `_extract_weather_info()` JS 내 B-2 섹션 버그 수정 3건

| # | 현재 (버그) | 수정 |
|---|------------|------|
| 1 | `gradeScore['한때나쁨'] = 3, '나쁨' = 3` | `'한때나쁨' = 3, '나쁨' = 4, '매우나쁨' = 5` |
| 2 | 오늘 날짜 dust 포함 (forecast 섹션은 오늘 스킵) | 오늘 제외: `di = 1` 부터 시작 (내일/모레만) |
| 3 | `var app2 = nexacro.getApplication()` 중복 | `topForm` 재사용 |

**수정된 dust 파싱 JS (B-2 섹션):**

```javascript
// ── B-2: 미세먼지 — dsList01Org ───────────────
try {
    var popupForm = null;
    try {
        popupForm = topForm  // ← app 재사용 (Fix #3)
            ? topForm.parent.STZZZ80_P0.form : null;
    } catch(e2) {}

    if (popupForm && popupForm.dsList01Org) {
        var ds = popupForm.dsList01Org;

        function parseDustInfo(raw) {
            if (!raw) return {dust: '', fine: ''};
            var clean = String(raw).replace(/\r/g, '');
            var parts = clean.split('\n');
            var dust = parts[0].trim();
            var fine = '';
            if (parts.length > 1) {
                var m = parts[1].match(/\(([^)]+)\)/);
                fine = m ? m[1].trim() : '';
            }
            return {dust: dust, fine: fine};
        }

        // Fix #1: 등급 점수 수정
        var gradeScore = {
            '좋음':1, '보통':2, '한때나쁨':3, '나쁨':4, '매우나쁨':5
        };
        function worseGrade(a, b) {
            return (gradeScore[a] || 0) >= (gradeScore[b] || 0) ? a : b;
        }

        // Fix #2: 내일/모레만 (오늘 제외)
        var today2 = new Date();
        var dustByDate = {};
        for (var di = 1; di <= 2; di++) {  // ← 1부터 시작
            var d = new Date(today2);
            d.setDate(today2.getDate() + di);
            var ymd2 = d.getFullYear() + '-'
                + String(d.getMonth()+1).padStart(2,'0') + '-'
                + String(d.getDate()).padStart(2,'0');

            var amIdx = String(di * 2 + 1).padStart(2, '0'); // 03, 05
            var pmIdx = String(di * 2 + 2).padStart(2, '0'); // 04, 06

            var amRaw = ds.getColumn(0, 'DT_INFO_' + amIdx);
            var pmRaw = ds.getColumn(0, 'DT_INFO_' + pmIdx);
            var am = parseDustInfo(amRaw);
            var pm = parseDustInfo(pmRaw);

            dustByDate[ymd2] = {
                dust: worseGrade(am.dust, pm.dust),
                fine: worseGrade(am.fine, pm.fine)
            };
        }

        // forecast_precipitation에 병합
        if (!result.forecast_precipitation) result.forecast_precipitation = {};
        for (var dk in dustByDate) {
            if (!result.forecast_precipitation[dk]) {
                result.forecast_precipitation[dk] = {
                    rain_rate: null, rain_qty: null,
                    rain_type_nm: '', weather_cd_nm: '',
                    is_snow: false
                };
            }
            result.forecast_precipitation[dk].dust_grade = dustByDate[dk].dust;
            result.forecast_precipitation[dk].fine_dust_grade = dustByDate[dk].fine;
        }
        result.dust_source = 'dsList01Org';
    } else {
        result.dust_source = 'unavailable';
    }
} catch(eDust) {
    result.dust_parse_error = eDust.message;
    result.dust_source = 'error';
}
```

---

### Step 3: `src/scheduler/daily_job.py`

**추가 위치**: `forecast_precipitation` 저장 루프 (1201행) 내부, `is_snow` 뒤에 추가

```python
# 미세먼지 등급 저장
if precip.get("dust_grade"):
    self.weather_repo.save_factor(
        factor_date=fdate,
        factor_type="weather",
        factor_key="dust_grade_forecast",
        factor_value=precip["dust_grade"]
    )
if precip.get("fine_dust_grade"):
    self.weather_repo.save_factor(
        factor_date=fdate,
        factor_type="weather",
        factor_key="fine_dust_grade_forecast",
        factor_value=precip["fine_dust_grade"]
    )
```

**저장 결과 로그에 dust 포함:**
```python
logger.info(
    f"Precipitation forecast saved: {list(forecast_precipitation.keys())} "
    f"(dust_src={weather.get('dust_source', '?')})"
)
```

---

### Step 4: `src/prediction/coefficient_adjuster.py`

**신규 메서드 2개:**

#### 4-A. `get_dust_data_for_date(date_str)` — DB 조회

```python
def get_dust_data_for_date(self, date_str: str) -> Dict[str, str]:
    """external_factors에서 미세먼지 예보 등급 조회

    Returns:
        {"dust_grade": "보통"|"나쁨"|..., "fine_dust_grade": "나쁨"|...}
    """
    result = {"dust_grade": "", "fine_dust_grade": ""}
    try:
        repo = ExternalFactorRepository()
        factors = repo.get_factors(date_str, factor_type='weather')
        if not factors:
            return result
        factor_map = {f['factor_key']: f['factor_value'] for f in factors}
        result["dust_grade"] = factor_map.get("dust_grade_forecast", "")
        result["fine_dust_grade"] = factor_map.get("fine_dust_grade_forecast", "")
        return result
    except Exception as e:
        logger.debug(f"미세먼지 데이터 조회 실패 ({date_str}): {e}")
        return result
```

#### 4-B. `get_dust_coefficient(date_str, mid_cd)` — 계수 계산

```python
def get_dust_coefficient(self, date_str: str, mid_cd: str) -> float:
    """미세먼지 예보 기반 수요 감소 계수

    판정: dust_grade, fine_dust_grade 중 높은 점수 기준
    - 점수 < 4 (좋음/보통/한때나쁨): 1.0 (영향 없음)
    - 점수 4 (나쁨): 카테고리별 감소
    - 점수 5 (매우나쁨): 카테고리별 큰 감소
    """
    from src.settings.constants import (
        DUST_PREDICTION_ENABLED, DUST_GRADE_SCORE,
        DUST_COEFFICIENTS, DUST_CATEGORY_MAP,
    )

    if not DUST_PREDICTION_ENABLED:
        return 1.0

    try:
        dust_data = self.get_dust_data_for_date(date_str)
        dust_grade = dust_data.get("dust_grade", "")
        fine_dust_grade = dust_data.get("fine_dust_grade", "")

        if not dust_grade and not fine_dust_grade:
            return 1.0

        # 둘 중 더 나쁜 등급의 점수
        score = max(
            DUST_GRADE_SCORE.get(dust_grade, 0),
            DUST_GRADE_SCORE.get(fine_dust_grade, 0),
        )

        if score < 4:  # 좋음/보통/한때나쁨
            return 1.0

        # 등급 결정
        level = "very_bad" if score >= 5 else "bad"

        # 카테고리 매핑
        cat_key = "default"
        for key, mids in DUST_CATEGORY_MAP.items():
            if mid_cd in mids:
                cat_key = key
                break

        coef = DUST_COEFFICIENTS.get(level, {}).get(cat_key, 1.0)

        if coef != 1.0:
            logger.debug(
                f"[PRED][Dust] {date_str} mid={mid_cd}: "
                f"dust={dust_grade} fine={fine_dust_grade} "
                f"score={score} → {coef:.2f}x"
            )

        return coef

    except Exception as e:
        logger.debug(f"미세먼지 계수 계산 실패 ({date_str}): {e}")
        return 1.0
```

#### 4-C. `apply()` 메서드에 dust_coef 병합

**추가 위치**: `sky_coef` 적용 뒤 (731행 이후), `food_wx_coef` 앞

```python
# Phase A-4: 미세먼지 계수
dust_coef = self.get_dust_coefficient(target_date_str, mid_cd)
if dust_coef != 1.0:
    weather_coef *= dust_coef
    logger.debug(
        f"[PRED][2-Dust] {product.get('item_nm', item_cd)}: "
        f"dust_coef={dust_coef}x → weather_coef={weather_coef:.3f}"
    )
```

---

### Step 5: `src/prediction/improved_predictor.py`

변경 없음. `CoefficientAdjuster.apply()`가 내부에서 `weather_coef`에 dust_coef를 병합하므로
`improved_predictor.py`는 이미 `self._coef.apply()`를 호출하고 있어 자동 반영됨.

---

### Step 6: `tests/test_dust_prediction.py`

```
tests/
  test_dust_prediction.py          ← 신규 (전체 테스트)
```

#### 테스트 목록

| # | 그룹 | 테스트명 | 검증 내용 |
|---|------|---------|----------|
| 1 | 파싱 | test_parse_dust_info_normal | `"보통\n\r(나쁨\r)"` → dust=보통, fine=나쁨 |
| 2 | 파싱 | test_parse_dust_info_empty | 빈값 → dust='', fine='' |
| 3 | 파싱 | test_parse_dust_info_hanttae | `"한때나쁨\n\r(보통\r)"` → dust=한때나쁨, fine=보통 |
| 4 | 파싱 | test_parse_dust_info_very_bad | `"매우나쁨\n\r(매우나쁨\r)"` → 양쪽 매우나쁨 |
| 5 | 파싱 | test_parse_dust_info_no_fine | `"보통"` (괄호 없음) → dust=보통, fine='' |
| 6 | 파싱 | test_parse_dust_info_with_extra_whitespace | 공백/탭 포함 |
| 7 | 등급 | test_worse_grade_bad_vs_hanttae | 나쁨(4) > 한때나쁨(3) |
| 8 | 등급 | test_worse_grade_same_score | 동점 시 첫 번째 반환 |
| 9 | 등급 | test_worse_grade_empty | 빈값 처리 |
| 10 | 등급 | test_grade_score_ordering | 좋음<보통<한때나쁨<나쁨<매우나쁨 |
| 11 | 계수 | test_dust_coef_good_returns_1 | 좋음 → 1.0 |
| 12 | 계수 | test_dust_coef_normal_returns_1 | 보통 → 1.0 |
| 13 | 계수 | test_dust_coef_hanttae_returns_1 | 한때나쁨 → 1.0 (영향 없음) |
| 14 | 계수 | test_dust_coef_bad_food | 나쁨 + 푸드 → 0.95 |
| 15 | 계수 | test_dust_coef_bad_beverage | 나쁨 + 음료 → 0.93 |
| 16 | 계수 | test_dust_coef_bad_ice | 나쁨 + 아이스 → 0.90 |
| 17 | 계수 | test_dust_coef_bad_default | 나쁨 + 담배 → 0.97 |
| 18 | 계수 | test_dust_coef_very_bad_food | 매우나쁨 + 푸드 → 0.90 |
| 19 | 계수 | test_dust_coef_very_bad_ice | 매우나쁨 + 아이스 → 0.83 |
| 20 | 계수 | test_dust_coef_toggle_off | DUST_PREDICTION_ENABLED=False → 1.0 |
| 21 | 계수 | test_dust_coef_mixed_grades | dust=보통, fine=나쁨 → score=4 (나쁨) |
| 22 | 저장 | test_save_dust_to_external_factors | DB UPSERT + 조회 일치 |
| 23 | 저장 | test_save_empty_dust_skipped | 빈값이면 저장 안 함 |
| 24 | 통합 | test_weather_coef_includes_dust | apply()에서 weather_coef에 dust 반영 |
| 25 | 통합 | test_dust_no_data_returns_1 | DB에 데이터 없으면 1.0 |

## 3. 데이터 흐름도

```
[BGF 날씨팝업]
  STZZZ80_P0.dsList01Org
    DT_INFO_03~06 (내일/모레 오전/오후)
        │
        ▼  파싱 (WeatherCollector)
  { "2026-03-11": {dust_grade:"나쁨", fine_dust_grade:"한때나쁨"},
    "2026-03-12": {dust_grade:"보통", fine_dust_grade:"보통"} }
        │
        ▼  저장 (daily_job.py)
  external_factors 테이블:
    (2026-03-11, weather, dust_grade_forecast, "나쁨")
    (2026-03-11, weather, fine_dust_grade_forecast, "한때나쁨")
        │
        ▼  조회 (CoefficientAdjuster)
  get_dust_data_for_date("2026-03-11")
    → score = max(4, 3) = 4 ("나쁨")
        │
        ▼  계수 (get_dust_coefficient)
  mid_cd="003"(김밥) → cat="food" → 0.95
        │
        ▼  병합 (apply)
  weather_coef *= 0.95
        │
        ▼  예측 결과
  adjusted_prediction = base × weather_coef × ...
```

## 4. 기존 코드 충돌 분석

| 기존 계수 | 충돌? | 이유 |
|----------|-------|------|
| 기온 계수 (weather_coef) | 곱셈 병합 | 미세먼지는 기온과 독립 신호 |
| 강수 계수 (precip_coef) | 곱셈 병합 | 비+미세먼지 동시 가능 |
| 하늘상태 (sky_coef) | 주의 | 황사 시 sky_coef에도 포함될 수 있음 |
| 푸드 교차 (food_wx_coef) | 없음 | 기온×푸드이므로 무관 |
| 급여일 (payday_coef) | 없음 | 후처리로 독립 |

**sky_coef와의 중복**: `WEATHER_CD_NM`에 "황사"가 포함되면 sky_coef도 감소할 수 있음.
→ 황사는 미세먼지의 원인이므로 **이중 적용이 맞음** (하늘 흐림 + 미세먼지 나쁨 = 외출 더 감소).

## 5. 제약 조건

1. **오늘 미세먼지 제외**: 예보 계수이므로 내일/모레만 수집 (오늘은 이미 발주 완료)
2. **폴백 1.0**: 팝업 미오픈/파싱 실패/DB 미조회 시 항상 1.0 (안전)
3. **DUST_PREDICTION_ENABLED**: 토글 OFF 시 전체 스킵
4. **DB 키**: `dust_grade_forecast`, `fine_dust_grade_forecast` (기존 rain 패턴과 일관)
