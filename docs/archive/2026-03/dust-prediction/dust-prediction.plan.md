# Plan: dust-prediction (미세먼지 예보 기반 예측 계수)

## 문제

미세먼지/초미세먼지가 나쁜 날은 외출이 줄어 편의점 방문 고객이 감소하지만,
현재 예측 파이프라인에 미세먼지 계수가 없어 과잉발주 → 폐기가 반복된다.
BGF 날씨정보(주간) 팝업에 3일치 미세먼지 예보가 있으나 수집하지 않고 있다.

## 데이터 소스 (라이브 확인 완료 2026-03-10)

### 접근 경로
```
nexacro.getApplication()
  .mainframe.HFrameSet00.VFrameSet00.TopFrame
  .STZZZ80_P0.form.dsList01Org
```
- 팝업 프레임: `STZZZ80_P0` (날씨 아이콘 클릭 시 생성)
- 바인드 그리드: `gdListPopup`

### dsList01Org 구조 (24컬럼, 1행)

| 컬럼 그룹 | 컬럼 | 설명 | 예시 |
|-----------|------|------|------|
| 날씨 코드 | WT_01~WT_06 | 날씨 아이콘 코드 | "03" |
| 날씨 텍스트 | WT_TXT_01~WT_TXT_06 | 날씨 설명 | "구름조금" |
| **미세먼지** | **DT_INFO_01~DT_INFO_06** | **미세먼지(초미세먼지)** | `"보통\n\r(나쁨\r)"` |
| 날짜 라벨 | DATE_TXT01~DATE_TXT03 | 날짜 표시 | "오늘 03/10 (화)" |
| 날짜 인덱스 | DATE_NUM01~DATE_NUM03 | 내부 인덱스 | "3" |

### 인덱스 매핑
| 인덱스 | 날짜 | 시간대 |
|--------|------|--------|
| 01, 02 | DATE_TXT01 (오늘) | 오전, 오후 |
| 03, 04 | DATE_TXT02 (내일) | 오전, 오후 |
| 05, 06 | DATE_TXT03 (모레) | 오전, 오후 |

### DT_INFO 파싱 규칙
```
"보통\n\r(나쁨\r)"
  ↑              ↑
  미세먼지=보통   초미세먼지=나쁨
```
- `\r` 제거 → `\n` split → 첫 파트 = dust_grade, 괄호 안 = fine_dust_grade
- 오전/오후 중 **더 나쁜 등급** 하나만 저장
- 등급: 좋음(1) < 보통(2) < 한때나쁨(3) < 나쁨(4) < 매우나쁨(5)

## 해결 방향

### 3단계 구현

1. **수집 (WeatherCollector)**: 팝업 열기 → dsList01Org 파싱 → 날짜별 dust/fine_dust 추출
2. **저장 (daily_job.py)**: external_factors에 dust_grade_forecast / fine_dust_grade_forecast 저장
3. **예측 (CoefficientAdjuster)**: get_dust_coefficient() → 카테고리별 차등 감소 계수 적용

### 미세먼지 예측 계수 설계

| 등급 | dust_grade | fine_dust_grade | 대상 카테고리 | 계수 |
|------|-----------|-----------------|-------------|------|
| 좋음 | 좋음 | 좋음 | 전체 | 1.00 |
| 보통 | 보통 | 보통 | 전체 | 1.00 |
| 나쁨 | 나쁨 또는 | 나쁨 이상 | 푸드(001~005,012) | 0.95 |
| 나쁨 | 〃 | 〃 | 음료(039~048) | 0.93 |
| 나쁨 | 〃 | 〃 | 아이스(027~030) | 0.90 |
| 매우나쁨 | 매우나쁨 | 매우나쁨 | 푸드 | 0.90 |
| 매우나쁨 | 〃 | 〃 | 음료 | 0.87 |
| 매우나쁨 | 〃 | 〃 | 아이스 | 0.83 |

- **판정 기준**: dust_grade와 fine_dust_grade 중 더 나쁜 등급 기준
- **합산 방식**: 기존 weather_coef에 곱셈 (`weather_coef *= dust_coef`)
- **적용 위치**: `coefficient_adjuster.py:get_all_coefficients()` 내부, precip_coef 뒤

## 수정 파일 (예상 6파일)

| 파일 | 변경 | 규모 |
|------|------|------|
| `src/collectors/weather_collector.py` | 팝업 오픈 + DT_INFO 파싱 로직 추가 | 중 |
| `src/scheduler/daily_job.py` | dust_grade/fine_dust_grade DB 저장 | 소 |
| `src/prediction/coefficient_adjuster.py` | get_dust_coefficient() 신규 메서드 | 중 |
| `src/settings/constants.py` | DUST_ENABLED 토글 + DUST_COEFFICIENTS 상수 | 소 |
| `tests/test_dust_prediction.py` | 파싱/계수/저장/통합 테스트 | 중 |
| `src/prediction/improved_predictor.py` | dust_coef 호출 (1줄) | 소 |

## 핵심 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| STZZZ80_P0 팝업이 안 열림 | 수집 100% 실패 | 팝업 여는 JS 코드 추가 + 로딩 대기 |
| gfn_transaction 비동기 경합 | ds_weatherTomorrow 0행 | 동기 호출 또는 폴링 대기 |
| 등급 텍스트 변경 | 파싱 실패 | 폴백 1.0 + 경고 로깅 |
| 미세먼지=좋음인데 초미세먼지=나쁨 | 복합 판정 필요 | 둘 중 나쁜 쪽 기준 |

## 토글

- `DUST_PREDICTION_ENABLED` (기본 True)
- 비활성 시 dust_coef = 1.0 (기존 동작 유지)

## 테스트 계획

| 테스트 그룹 | 건수 | 내용 |
|------------|------|------|
| DT_INFO 파싱 | 6 | 정상/빈값/한때나쁨/매우나쁨/오전오후비교 |
| 등급 점수 비교 | 4 | worseGrade 함수, 동점 처리 |
| 계수 계산 | 6 | 카테고리별 등급별 계수, 토글 OFF |
| DB 저장/조회 | 4 | external_factors UPSERT/조회 |
| 통합 (E2E) | 3 | 수집→저장→계수→예측 반영 |
| **합계** | **~23** | |
