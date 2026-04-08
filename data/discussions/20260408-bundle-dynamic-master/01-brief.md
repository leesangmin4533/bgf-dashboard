# Brief: bundle-suspect-dynamic-master 설계 결정 토론

## 토론 모드
**트레이드오프 + 설계** — Design 단계 진입 전 6개 결정 항목 조율

## 1. 현재 상황

`BUNDLE_SUSPECT_MID_CDS` (constants.py:262) 가 정적 set 으로 관리되어 04-06~04-08 5단계 패치(fa0e731 → 190b24f) 모두 사고가 난 카테고리만 추가하는 **반응형 패치 사이클**에 갇혀 있음.

매 사고마다 코드 수정 → 배포 → 검증 → 다음 사고 → 반복.
04-08 mid=023(햄/소시지) 누락도 동일 패턴.

## 2. 결정적 증거 (product_details 실측, 04-08)

### bundle_pct >= 70% 인데 BUNDLE_SUSPECT 미포함 (= 다음 사고 후보)
| mid | bundle_pct | total | bundle_n | unit=1 | 카테고리 |
|---|---|---|---|---|---|
| 021 | 88.5% | 130 | 115 | 0 | 냉동식품 |
| 605 | 86.2% | 65 | 56 | 3 | 하이볼 |
| 037 | 81.7% | 82 | 67 | 2 | 위생용품 |
| 044 | 78.8% | 132 | 104 | 18 | (확인필요) |
| 040 | 77.7% | 188 | 146 | 19 | (확인필요) |
| 064 | 75.9% | 29 | 22 | 0 | (확인필요) |
| 072 | 73.4% | 203 | 149 | 20 | **담배** ★ |
| 073 | 71.8% | 110 | 79 | 6 | **전자담배** ★ |

### 50~70% 약한 의심 (3개)
| 041 | 67.7% | 31 | 21 | 3 |
| 900 | 61.5% | 13 | 8 | 1 | 소모품 |
| 051 | 55.9% | 34 | 19 | 2 |

### 현재 BUNDLE_SUSPECT 인데 < 50% (오탐 후보)
| 010 | 34.6% | 26 | 5 | 음료 (null 12개로 통계 약함) |
| 048 | 46.7% | 15 | 1 | 음료 (샘플 적음) |
| 030 | 32.3% | 62 | 0 | 간식 (null 36개로 통계 약함) |

### 통계 정리
- 전체 mid: 72개
- bundle_pct >= 50%: 22개
- 현재 BUNDLE_SUSPECT: 23개 (190b24f 후)
- 다음 사고 후보: **11개 mid** (즉시 P1)

## 3. 이미 구현된 기능

| 모듈 | 기능 |
|---|---|
| `src/settings/constants.py:262` | `BUNDLE_SUSPECT_MID_CDS` 정적 set (23 mid) |
| `src/order/order_executor.py:_calc_order_result` | L1/L2 가드 (190b24f 이전) |
| `src/order/order_executor.py:input_product` | L3 Selenium 가드 (190b24f) |
| `src/notification/notification_dispatcher.py` | 카톡 알림 + fallback |
| `src/db/repository.py:product_details` | order_unit_qty 컬럼 |
| (없음) | **정기 점검 잡 — 미구현** |
| (없음) | **동적 resolver — 미구현** |

## 4. 토론 포인트 (6개 결정 항목)

### 결정 1: bundle_pct 임계값
- A안: **70% / 50% 이중 분류** — 강한 BLOCK + 약한 BLOCK + 디버그 로그
- B안: **60% 단일 임계값** — 단순, 명확
- C안: **카테고리별 ML 분류기** — 과잉 엔지니어링 가능

기준: 오탐/누락 균형, 운영 단순성, 비개발자 사용자 친화

### 결정 2: NULL 비율 처리 정책
NULL 비율이 높은 mid (030: 36/62=58%, 010: 12/26=46%) 의 경우:
- A안: NULL > 30% → 가드 미적용 (통계 신뢰도 낮음)
- B안: NULL > 30% → 보수적 BLOCK (안전 우선)
- C안: NULL 자체를 별도 카테고리로 — "수집 결함" 알림 분리

기준: BGF API 가 빈값 반환하는 카테고리는 그 자체가 본 사고의 근원이라는 점

### 결정 3: 캐시 만료 시간
- A안: **5분 메모리 캐시** — 발주 중 일관성, 다음 사이클 빠름
- B안: **1시간 메모리** — DB 부하 최소
- C안: **daily_job 1회만 (캐시 무한)** — 매일 갱신, 가장 단순

기준: product_details 가 일 1회 (07:00 BulkCollect) 갱신되는 점, 발주는 07:20~08:00 집중

### 결정 4: resolver 모듈 위치
- A안: `src/order/bundle_suspect_resolver.py` (order 도메인)
- B안: `src/infrastructure/database/repos/bundle_master_repo.py` (DB I/O)
- C안: `src/domain/order/bundle_classifier.py` (순수 도메인 + 입력 주입)

기준: CLAUDE.md 계층형 아키텍처 (Settings/Domain/Infrastructure/Application)

### 결정 5: 정적 fallback 운명
- A안: **영구 유지** — 동적 + 정적 항상 합집합
- B안: **첫 검증 후 제거** — 동적이 안정되면 삭제
- C안: **운영자 토글** — 환경변수 또는 app_settings 로 ON/OFF

기준: DB 장애 시 안전 보장, 코드 단순성, 회귀 보호

### 결정 6: 정기 점검 잡 형태
- A안: **daily_job 통합** — Phase 0 또는 종료 시 변화 리포트
- B안: **별도 스케줄** (예: 08:00 직후) — 발주 끝난 후 분석
- C안: **옵션** — 운영자가 필요시 수동 실행

기준: daily_job 부담, 알림 시점, 운영자 부담

## 5. 제약 조건

- **회귀 위험 0**: 현재 BLOCK 발사 중인 22 mid (51cd670 + 190b24f) 는 동적 set 에 반드시 포함
- **DB 장애 시 안전 보장**: 동적 산출 실패 시 fallback 필수
- **04-09 1차 수정 검증 진행 중**: 본 PDCA 의 Do 단계는 1차 수정 검증 후 진입
- **사용자는 비개발자**: 동작 변화는 카톡으로 자동 통지되어야 함
- **schema 변경 금지**: prediction_logs 와 달리 product_details 는 안정 컬럼만 사용

## 6. 출력 요구

순수 마크다운으로:

# bundle-suspect-dynamic-master 설계 토론 결과

## 1. 전체 진단 요약 (3~5줄)

## 2. 결정별 권고

### 2.1 결정 1: bundle_pct 임계값
| 기준 (가중) | A안 | B안 | C안 |
| ... |
**권고**: ?안
**근거**:

(결정 6까지 동일 형식)

## 3. 결정 간 의존 관계

## 4. 통합 구현 순서 체크리스트 (Design 단계 작성용)

## 5. 1차 수정 + 본 세션 PDCA 들과의 충돌/조화 분석

## 6. 예상 효과 / 리스크

운영 안정성 + 비개발자 친화성 + 회귀 0건 보장을 최우선 가치로 평가하세요.
각 결정에 반드시 '근거' 포함. 테이블/ASCII 다이어그램 적극 활용.
