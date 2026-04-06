# waste-batch-api Plan

## 개요

폐기전표 상세 품목 수집을 **날짜별 일괄 Direct API 조회**로 전환하여 30배 속도 향상 달성.

## 현재 상태 (AS-IS)

### Phase 1.15 폐기전표 수집 흐름

```
메뉴 이동 (Selenium)
  → 인터셉터 설치
  → 필터 설정 + F10 검색 (XHR 캡처)
  → 헤더 조회: Direct API /stgj020/search (1회) ✅ 빠름
  → 상세 품목: 전표당 1회 API 호출 (순차) ← ★ 병목
```

### 상세 품목 수집 병목

| 구간 | 현재 방식 | 소요 시간 |
|------|----------|----------|
| Direct API (현재) | `fetch_all_slip_details()` — 전표당 1회 + 0.3초 딜레이 | 29건 × 0.6초 = **~17초** |
| Selenium 폴백 | 팝업 open/close 반복 | 29건 × 7.8초 = **~226초** |

### 관련 파일

| 파일 | 역할 |
|------|------|
| `src/collectors/waste_slip_collector.py` | 메인 수집 오케스트레이터 |
| `src/collectors/direct_frame_fetcher.py` | Direct API 구현 (DirectWasteSlipDetailFetcher) |
| `src/scheduler/phases/collection.py` | Phase 1.15-1.16 호출부 |
| `src/infrastructure/database/repos/waste_slip_repo.py` | DB 저장 |
| `src/application/services/waste_disuse_sync_service.py` | Phase 1.16 동기화 |

## 목표 상태 (TO-BE)

### 일괄 Direct API 조회

```
헤더 조회: /stgj020/search (1회, 기존과 동일)
  → 결과를 날짜별 그룹핑
  → 상세 품목: 날짜당 1회 API 호출 (순차 또는 병렬)
     /stgj020/searchDetailType1
     strChitNoList = ('NO1','NO2','NO3')  ← 같은 날짜 전표 일괄
```

### 성능 목표

| 구간 | 개선 방식 | 소요 시간 |
|------|----------|----------|
| **일괄 API** | 날짜당 1회 호출 | 6일 × 0.3초 = **~1.8초** (순차) |
| **일괄 API (병렬)** | 날짜별 병렬 | **~0.3초** |

**실측 검증 완료** (2026-04-06, 46513 매장):
- 29건 전표, 64개 품목 → 6회 병렬 API, **0.29초** 완료

## 핵심 발견 사항 (2026-04-06 Playwright 검증)

### strChitNoList 복수 전표 지원

- `searchDetailType1` 엔드포인트의 `strChitNoList` 파라미터가 SQL IN절 형식 지원
- 형식: `('전표번호1','전표번호2','전표번호3')`
- 같은 날짜(strChitYmd)의 전표를 한번에 조회 가능

### 제약사항

1. **`strChitYmd` 필수** — 빈값이면 0건 반환
2. **같은 날짜만** 묶을 수 있음 — 날짜별 그룹핑 필수
3. **`strChitDiv` = `04`** — CHIT_ID 코드 (CHIT_FLAG `10`과 다름)
4. 응답의 `CHIT_NO` 컬럼으로 전표 구분 가능

### 검증 결과

```
날짜       | 전표수 | 품목수 | 응답시간
20260401  |   5건  |   5건  |  186ms
20260402  |   6건  |   8건  |  233ms
20260403  |   6건  |   9건  |  254ms
20260404  |   5건  |  16건  |  290ms
20260405  |   6건  |  21건  |  289ms
20260406  |   1건  |   5건  |  165ms
합계       |  29건  |  64건  | 292ms (병렬)
```

## 구현 범위

### 변경 대상

1. **`direct_frame_fetcher.py`** — `DirectWasteSlipDetailFetcher`에 `fetch_all_slip_details_batch()` 추가
2. **`waste_slip_collector.py`** — `_try_direct_api_details()`에서 일괄 API 우선 호출

### 변경하지 않는 것

- 헤더 조회 (`/stgj020/search`) — 이미 1회 호출로 충분
- Selenium 팝업 폴백 — 안전망으로 유지
- Phase 1.16 동기화 로직 — 입력 데이터 형태 동일
- DB 스키마 — 변경 없음
- 기존 `fetch_all_slip_details()` — 폴백으로 유지

## 구현 단계

### Step 1: `fetch_all_slip_details_batch()` 메서드 추가

`DirectWasteSlipDetailFetcher`에 날짜별 일괄 조회 메서드 추가:

```python
def fetch_all_slip_details_batch(
    self, slip_list: List[Dict], delay: float = 0.1
) -> List[Dict]:
    """날짜별 일괄 API 조회 (strChitNoList 복수 전표)"""
    # 1) 날짜별 그룹핑: {YYYYMMDD: [CHIT_NO, ...]}
    # 2) 날짜별 strChitNoList 조립: ('NO1','NO2',...)
    # 3) 템플릿 치환 + API 호출
    # 4) SSV 파싱 → 품목 리스트 (CHIT_NO로 전표 구분)
```

### Step 2: `_try_direct_api_details()` 수정

일괄 API를 우선 시도하고, 실패 시 기존 순차 API로 폴백:

```python
def _try_direct_api_details(self, slip_list):
    # 1) 일괄 API 시도 (fetch_all_slip_details_batch)
    # 2) 실패 시 기존 순차 API (fetch_all_slip_details)
    # 3) 둘 다 실패 시 None 반환 (Selenium 팝업 폴백)
```

### Step 3: 테스트

- 기존 `test_direct_frame_fetcher.py` 확장
- 일괄 조회 파싱 검증, 날짜별 그룹핑 검증

## 리스크

| 리스크 | 대응 |
|--------|------|
| 전표 수가 매우 많은 날 (50건+) | strChitNoList 최대 길이 테스트, 필요시 배치 분할 |
| BGF 서버 응답 변경 | 기존 순차 API 폴백 유지 |
| 세션 만료 시 일괄 API 실패 | 기존 3단계 폴백 체인 유지 (일괄→순차→팝업) |

## 영향 분석

- **Phase 1.15 소요시간**: ~17초 → ~2초 (순차) 또는 ~0.3초 (병렬)
- **전체 파이프라인**: Phase 1.15가 빨라지면 후속 Phase 시작도 앞당겨짐
- **기존 동작 보장**: 3단계 폴백 체인으로 안전성 유지
- **DB 영향**: 없음 (저장 데이터 형태 동일)
