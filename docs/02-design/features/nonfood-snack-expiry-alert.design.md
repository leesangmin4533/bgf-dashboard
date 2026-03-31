# Design: 비푸드 과자 유통기한 관리 전체 파이프라인

## 0. 전체 플로우

```
[Phase 1] 유통기한 7일 전 안내 알림 (구현 완료, 07:30)
    ↓
[Phase 2] PDA 전산등록 유도 메시지 (단톡방)
    "아래 상품의 유통기한을 PDA로 등록해주세요"
    ↓
[Phase 3] BGF "상품 유통기한 관리" 화면 수집
    매장별 병렬 진입 → STCM130_M0 → dsList 수집 → DB 저장
    ↓
[Phase 4] 철수예정일 기반 단톡방 알림
    DB에서 철수예정일 임박 상품 조회 → 매장 단톡방 발송
```

## 1. BGF 화면 구조 (크롬 확장으로 확인)

### 메뉴 경로
```
커뮤니케이션 > 기타 조회 > 상품 유통기한 관리
```

### 프레임
```
STCM130_M0
  └── div_workForm.form
      ├── div_search.form (조회 조건)
      │   ├── meSearchYm (조회년월: 2026-03)
      │   └── chk_nowQtyYn (현재고 있는 상품만 조회, value=0/1)
      ├── gdList (그리드, binds=dsList)
      └── datasets:
          ├── dsList (8컬럼, 메인 데이터)
          ├── dsList2 (14컬럼, 캘린더용 — DAY1~7, DAY1ITEM~7)
          └── dsList3 (8컬럼, dsList 복사본)
```

### API
```
POST https://store.bgfretail.com/stcm130/search
파라미터: SS_STORE_CD=46513, 조회년월 등 (넥사크로 SSV 형식)
```

### dsList 컬럼 (8개)

| 컬럼 | 설명 | 예시 |
|------|------|------|
| LARGE_NM | 대분류명 | 과자류 |
| MID_NM | 중분류명 | 캔디, 스낵류, 면류 |
| ITEM_NM | 상품명 | 앰지)홀스XS멘토립터스 |
| ITEM_CD | 상품코드 | 8850338019715 |
| **EXPIRE_YMD** | **철수예정일** | **20260302** (YYYYMMDD) |
| STORE_CD | 매장코드 | 46513 |
| DEL_FLAG | 삭제 플래그 | null |
| NOW_QTY | 현재고 | 1 (nexacro BigDecimal → .hi 사용) |

### 버튼
| ID | 텍스트 | 기능 |
|----|--------|------|
| F_10 | 조 회 | 데이터 조회 |
| F_9 | 신규등록 | 상품 등록 |
| F_8 | 삭 제 | 상품 삭제 |
| F_11 | 저 장 | 저장 |
| F_3 | 자동삭제조회 | 재고 없는 상품 자동 삭제 |

### 샘플 데이터 (2026-03 조회, 25건)

```
NO  상품코드         대분류        중분류       상품명                  현재고  철수예정일
1   8850338019715   과자류        캔디       앰지)홀스XS멘토립터스       1     2026-03-02
2   8801085880479   가공식사제품    면류       한일)얼큰유부우동컵         8     2026-03-03
3   8801019607912   과자류        스낵류      해태)자가비짭짤한맛         8     2026-03-03
...
```

## 2. Phase 2: PDA 전산등록 유도 메시지

### 발송 시점
- Phase 1(07:30)에서 7일 이내 만료 과자가 있을 때
- 같은 메시지에 "PDA로 유통기한 등록해주세요" 문구 추가

### 메시지 예시
```
[이천호반베르디움점] 과자류 유통기한 알림 (04/11)

D-7 이내 만료 3건:
  크라운)산도딸기       만료 04/18  2개
  해태)후렌치파이사과    만료 04/19  1개

총 3건 3개 — PDA로 유통기한 등록 후 폐기 처리해주세요
```

## 3. Phase 3: BGF "상품 유통기한 관리" 화면 수집

### 수집 시점
- 매일 07:00 메인 수집 후 (07:15~07:20 사이)
- 드라이버가 이미 로그인 상태이므로 메뉴 이동만 필요

### 수집 방식 — 2가지 옵션

#### 옵션 A: Selenium 메뉴 이동 + 데이터셋 추출 (안정적)
```python
# 1. 커뮤니케이션 > 상품 유통기한 관리 메뉴 이동
# 2. 조회년월 설정 (현재 월)
# 3. chk_nowQtyYn = 1 (현재고 있는 상품만)
# 4. 조회 버튼 클릭 (F_10)
# 5. dsList에서 데이터 추출 (JS 실행)
# 6. DB 저장
```

#### 옵션 B: Direct API 호출 (빠르지만 템플릿 필요)
```python
# POST https://store.bgfretail.com/stcm130/search
# 넥사크로 SSV 형식 body 구성
# 응답에서 dsList 파싱
```

### 추천: 옵션 A (Selenium)
- 기존 수집 플로우(Phase 1.0~1.35)에 Phase 1.17로 삽입
- 드라이버 재사용, 메뉴 이동만 추가
- Direct API보다 안정적 (SSV 형식 구성 불필요)

### DB 테이블 (신규: store DB)

```sql
CREATE TABLE IF NOT EXISTS expiry_management (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    large_nm TEXT,
    mid_nm TEXT,
    expire_ymd TEXT NOT NULL,     -- YYYYMMDD (철수예정일)
    now_qty INTEGER DEFAULT 0,
    collected_month TEXT NOT NULL, -- YYYY-MM (조회년월)
    created_at TEXT NOT NULL,
    updated_at TEXT,
    UNIQUE (store_id, item_cd, expire_ymd)
);
```

### 수집기 (신규)

```python
class ExpiryManagementCollector:
    """BGF 상품 유통기한 관리 화면 수집기"""

    FRAME_ID = "STCM130_M0"
    MENU_PATH = "커뮤니케이션 > 상품 유통기한 관리"

    def collect(self, driver, store_id, target_month=None):
        """
        1. 메뉴 이동 (STCM130_M0)
        2. 조회년월 설정
        3. 현재고 체크
        4. 조회 버튼 클릭
        5. dsList JS 추출
        6. DB 저장
        """
```

## 4. Phase 4: 철수예정일 기반 알림

### 조회 조건
```sql
SELECT * FROM expiry_management
WHERE store_id = ?
  AND expire_ymd BETWEEN date('now') AND date('now', '+7 days')
  AND now_qty > 0
ORDER BY expire_ymd ASC
```

### 메시지 포맷
```
[이천호반베르디움점] 철수예정 알림 (04/11)

7일 이내 철수예정 3건:
  크라운)산도딸기       철수 04/18  2개
  해태)후렌치파이사과    철수 04/19  1개

총 3건 — 할인/폐기 처리 필요
```

### 발송 채널
- 나에게 보내기: O (category="food_expiry")
- 단톡방: 추후 활성화

## 5. 구현 순서

| # | Phase | 작업 | 파일 | 난이도 |
|---|-------|------|------|--------|
| 1 | 3 | `expiry_management` 테이블 스키마 추가 | schema.py, models.py | 하 |
| 2 | 3 | `ExpiryManagementCollector` 수집기 | src/collectors/ | 중 |
| 3 | 3 | Phase 1.17 수집 플로우에 삽입 | phases/collection.py | 중 |
| 4 | 2 | generate_nonfood_expiry_message에 PDA 유도 문구 | expiry_checker.py | 하 |
| 5 | 4 | 철수예정일 기반 알림 메시지 생성 | expiry_checker.py | 하 |
| 6 | 4 | 알림 스케줄 (07:35) | run_scheduler.py | 하 |

## 6. 월별 조회 전략

- **현재 월**: 매일 수집 (최신 데이터)
- **다음 달**: 월말(25일 이후) 수집 시작
- 매일 새벽 02시 재고 없는 상품 자동 삭제됨 (BGF 시스템) → 수집은 07:00 이후

## 7. 데이터 흐름

```
07:00  메인 수집 (Phase 1.0~1.35)
  ↓
07:17  Phase 1.17: STCM130_M0 수집 → expiry_management DB 저장
  ↓
07:30  과자류 유통기한 7일 전 알림 (inventory_batches 기반, 기존)
  ↓
07:35  철수예정 알림 (expiry_management 기반, 신규)
       + PDA 전산등록 유도 메시지
```
