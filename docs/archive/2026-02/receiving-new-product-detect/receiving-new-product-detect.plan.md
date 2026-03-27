# Plan: receiving-new-product-detect

> 센터매입조회 입고 시 DB 미등록 상품을 신제품으로 자동 감지하여 별도 관리

## 1. 배경 및 문제 정의

### 현재 상태
- **Phase 1.1** `ReceivingCollector.collect_and_save()`: 센터매입 조회에서 입고 데이터 수집
- `_get_mid_cd()`: `common.db.products` 테이블에서 `item_cd` 조회 → 실패 시 `_fallback_mid_cd()`로 상품명 패턴 추정
- `update_stock_from_receiving()`: `realtime_inventory`에 미등록 상품은 `skipped_no_data`로 카운트만 하고 **스킵**
- `_create_batches_from_receiving()`: `order_tracking`, `inventory_batches` 생성 시 미등록 상품 처리 없음

### 문제점
1. **신제품 누락**: 입고되었지만 `products`/`product_details`에 없는 상품은 예측/발주 파이프라인에서 완전히 제외
2. **감지 불가**: 어떤 상품이 신제품인지 운영자가 수동으로 확인해야 함
3. **초기 발주 공백**: 신제품 입고 후 판매 데이터 축적까지 자동 발주가 불가능
4. **재고 미추적**: `realtime_inventory`에 등록되지 않아 재고 파악 불가

### 관련 모듈
- 기존 **신상품 도입 현황 모듈** (`new_product_collector.py`): BGF STBJ460 화면에서 "도입률/달성률" 수집 — **월별 도입 지원금 점수 관리** 목적
- 본 기능: 실제 **입고 시점**에서 DB 미등록 상품을 자동 감지 — **운영 파이프라인 편입** 목적
- 두 모듈은 **목적이 다르므로 독립 운영** (겹치는 item_cd가 있을 수 있지만 관리 관점이 다름)

## 2. 목표

### 핵심 목표
1. Phase 1.1 입고 수집 시 `products` 테이블에 없는 상품을 **자동 감지**
2. 감지된 신제품을 **별도 테이블에 기록** (입고일, 상품코드, 상품명, 수량, mid_cd 추정값)
3. 신제품의 **상품 상세 정보 자동 수집** 트리거 (product_details에 등록)
4. 신제품을 `products` + `product_details` + `realtime_inventory`에 **자동 등록**하여 파이프라인 편입

### 부가 목표
5. 웹 대시보드에서 **신제품 감지 이력** 조회
6. 카카오 알림으로 **신제품 감지 알림** 발송 (선택)

## 3. 범위

### In Scope
- `ReceivingCollector` 내 신제품 감지 로직 추가
- 신제품 감지 테이블 (`detected_new_products`) 스키마 및 Repository
- `products` + `product_details` 자동 등록 (입고 데이터에서 확보 가능한 정보 기반)
- `realtime_inventory` 자동 등록 (입고 수량 기반)
- `daily_job.py` Phase 1.1 후처리 연동
- 웹 API 엔드포인트 (신제품 목록 조회)

### Out of Scope
- BGF 상품상세 화면 스크래핑 (별도 `batch_collect_flow` 활용 — 유통기한/마진 등은 기존 로직에 위임)
- 신상품 도입 현황 모듈과의 교차 연동 (Phase 2에서 고려)
- 신제품 자동 발주 전략 결정 (DefaultStrategy로 기본 예측)

## 4. 기술 접근

### 4.1 감지 시점 및 조건

**핵심 규칙**: 센터매입조회 메뉴에서 **입고 확정** 상품만 신제품으로 인식

```
BGF 센터매입조회 데이터 구조:
- NAP_QTY > 0 (receiving_qty): 검수 확정 → ★ 신제품 감지 대상
- NAP_PLAN_QTY > 0, NAP_QTY == 0 (plan_qty): 입고 예정/미확정 → 감지 제외

감지 조건 (AND):
  1. 센터매입조회 화면에서 수집한 데이터일 것
  2. receiving_qty > 0 (입고 확정)
  3. products 테이블에 해당 item_cd가 없을 것
```

```
Phase 1.1 ReceivingCollector.collect_and_save()
  └─ collect_receiving_data() 에서 상품별 반복
      └─ _get_mid_cd() 내부에서 products 테이블 조회 실패 시 신제품 후보 축적
  └─ ★ _detect_new_products(): recv_qty > 0인 후보만 필터 → 신제품 확정
  └─ ★ _register_new_products(): products + product_details + inventory 등록
  └─ 기존 로직 (save_bulk_receiving, batches, stock update) 계속 진행
```

### 4.2 DB 스키마 변경
- **새 테이블**: `detected_new_products` (store DB)
  - `item_cd`, `item_nm`, `mid_cd` (추정), `first_receiving_date`, `receiving_qty`, `center_cd`, `center_nm`
  - `registered_to_products` (bool), `registered_to_details` (bool)
  - `detected_at`, `store_id`

### 4.3 자동 등록 플로우
```
1. _get_mid_cd()에서 products 미조회 → 신제품 리스트에 추가
2. collect_and_save() 완료 후 _register_new_products() 호출
   a. products 테이블에 INSERT (item_cd, item_nm, mid_cd)
   b. product_details 테이블에 기본값 INSERT (expiration_days는 mid_cd 기반 추정)
   c. realtime_inventory에 입고 수량으로 INSERT
3. detected_new_products 테이블에 이력 기록
4. 로그 출력: "[Phase 1.1] 신제품 감지: N건 (자동 등록 완료)"
```

### 4.4 수정 대상 파일
| 파일 | 변경 내용 |
|------|----------|
| `src/collectors/receiving_collector.py` | `_get_mid_cd()` 신제품 플래그 + `_register_new_products()` |
| `src/infrastructure/database/schema.py` | `detected_new_products` 테이블 추가 (STORE_SCHEMA) |
| `src/infrastructure/database/repos/` | `DetectedNewProductRepository` 신규 |
| `src/infrastructure/database/repos/product_detail_repo.py` | `register_basic()` 메서드 추가 (기본값 등록) |
| `src/db/models.py` | 스키마 버전 v45 + 마이그레이션 |
| `src/settings/constants.py` | `DB_SCHEMA_VERSION = 45`, 관련 상수 |
| `src/scheduler/daily_job.py` | Phase 1.1 후처리 로그 |
| `src/web/routes/` | `api_new_product_detect.py` 신규 (웹 API) |

## 5. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 동일 상품 중복 감지 | 매일 입고될 때마다 감지 | `products` 등록 후에는 감지 안 됨 (UPSERT) |
| mid_cd 추정 오류 | 잘못된 카테고리로 예측 | fallback은 기존 로직 유지, 후속 batch_collect에서 교정 |
| 유통기한 미확보 | 푸드류 폐기 관리 부정확 | `CATEGORY_EXPIRY_DAYS` 기본값 사용 → batch_collect에서 실제값 갱신 |
| 기존 플로우 영향 | 입고 수집 성능 저하 | 신제품 등록은 collect_and_save() 말미에 배치 처리 |

## 6. 성공 기준

- [ ] 입고 상품 중 DB 미등록 상품 100% 감지
- [ ] 감지된 상품이 `products` + `product_details` + `realtime_inventory`에 자동 등록
- [ ] 기존 입고 수집 플로우에 영향 없음 (실패 시 warning 로그 후 계속)
- [ ] `detected_new_products` 테이블에 이력 기록
- [ ] 웹 API로 신제품 감지 이력 조회 가능
- [ ] 기존 테스트 전체 통과 + 신규 테스트 15개 이상

## 7. 구현 순서

1. **DB 스키마** — `detected_new_products` 테이블 + v45 마이그레이션
2. **Repository** — `DetectedNewProductRepository` CRUD
3. **감지 로직** — `ReceivingCollector._get_mid_cd()` 수정 + `_register_new_products()`
4. **자동 등록** — `products` + `product_details` + `realtime_inventory` INSERT
5. **daily_job.py** — Phase 1.1 후처리 통합
6. **웹 API** — 조회 엔드포인트
7. **테스트** — 단위 + 통합 테스트
