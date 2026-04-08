# Design: bundle-suspect-dynamic-master

> 작성일: 2026-04-08
> 상태: Design
> Plan: docs/01-plan/features/bundle-suspect-dynamic-master.plan.md
> 토론: data/discussions/20260408-bundle-dynamic-master/03-최종-리포트.md
> 이슈체인: order-execution.md#bundle-suspect-dynamic-master
> 토론 권고: **A-C-C-C-A-A**

---

## 1. 설계 개요

5단계 반응형 패치 사이클(fa0e731 → 190b24f) 종결을 위해 `BUNDLE_SUSPECT_MID_CDS` 정적 set 을 **동적 마스터 + 정적 안전망 합집합** 으로 전환. food/dessert 카테고리는 `food-underprediction-secondary` 와 충돌 회피를 위해 STRONG 분류여도 WARN_ONLY 로 강등.

### 핵심 구조
```
[daily_job Phase 0]
       │
       ▼
BundleMasterService.build_master()  ← 단일 진입점, 일 1회
       │
       ├── BundleStatsRepo.fetch_bundle_stats()  ← Infrastructure (SQL)
       │       └─ {mid: (bundle_pct, null_ratio, total, unit1)}
       │
       ├── BundleClassifier.classify(stats)     ← Domain (순수 함수)
       │       └─ {STRONG, WEAK, UNKNOWN}
       │
       └── 합집합: STRONG ∪ WEAK ∪ STATIC_FALLBACK
              │
              ├─ JSON 스냅샷: config/cache/bundle_master_YYYYMMDD.json
              ├─ 메모리 dict 캐시
              ├─ 카톡 diff 알림 (전일 대비 추가/제거)
              └─ UNKNOWN 알림 (수집 결함 의심)
              │
              ▼
       order_executor 가드 (L1/L2/L3) 모두 동일 호출
              │
              ▼
       food/dessert mid → WARN_ONLY 강등 (BLOCK 미적용)
       그 외          → STRONG/WEAK 동일 BLOCK
```

### 차단점 (Step 0)
- 04-09 07:30 1차 수정(ab98bfc) 검증 통과 필수
- food-underprediction-secondary Phase A 배포(04-10) 후 본 작업 Do 진입 권고

---

## 2. 데이터 모델

### 2.1 BundleStats (도메인 값 객체)
```python
@dataclass(frozen=True)
class BundleStats:
    mid_cd: str          # 3자리 제로패딩
    total: int           # product_details row 수
    bundle_n: int        # order_unit_qty > 1 row 수
    null_n: int          # order_unit_qty IS NULL row 수
    unit1_n: int         # order_unit_qty = 1 row 수

    @property
    def bundle_pct(self) -> float:
        return 100.0 * self.bundle_n / self.total if self.total else 0.0

    @property
    def null_ratio(self) -> float:
        return 100.0 * self.null_n / self.total if self.total else 0.0
```

### 2.2 BundleClassification (도메인 enum)
```python
class BundleClassification(str, Enum):
    STRONG = "STRONG"      # 70%+, 즉시 BLOCK
    WEAK   = "WEAK"        # 50~69%, BLOCK + WARN 로그
    UNKNOWN = "UNKNOWN"    # NULL>30% or total<5, fallback 위임
    NORMAL = "NORMAL"      # < 50%, 가드 미적용
```

### 2.3 BundleMaster (애플리케이션 값 객체)
```python
@dataclass(frozen=True)
class BundleMaster:
    snapshot_date: str                           # YYYY-MM-DD
    strong: frozenset[str]                       # mid set
    weak: frozenset[str]
    unknown: frozenset[str]
    static_fallback: frozenset[str]              # constants.BUNDLE_SUSPECT_MID_CDS
    source_map: Dict[str, Set[str]]              # mid → {static, dynamic}
    food_warn_only: frozenset[str] = frozenset({"001","002","003","004","005","012"})

    def is_blocked(self, mid_cd: str) -> bool:
        """mid 가 BLOCK 대상인지 (food/dessert 강등 고려)"""
        m = mid_cd.zfill(3)
        if m in self.food_warn_only:
            return False  # WARN_ONLY 강등
        return m in (self.strong | self.weak | self.static_fallback)

    def is_warn_only(self, mid_cd: str) -> bool:
        """STRONG 분류이지만 food/dessert 라 강등된 경우"""
        m = mid_cd.zfill(3)
        return (m in self.food_warn_only) and (m in (self.strong | self.weak))
```

### 2.4 JSON 스냅샷 형식
**경로**: `config/cache/bundle_master_YYYYMMDD.json`
```json
{
  "v": 1,
  "snapshot_date": "2026-04-09",
  "built_at": "2026-04-09T07:00:35+09:00",
  "thresholds": {"strong": 70.0, "weak": 50.0, "null_max": 30.0, "min_total": 5},
  "strong": ["010","014","015","016","017","018","019","020","021","023","024","025","029","030","032","037","039","040","043","044","045","049","050","064","072","073","605"],
  "weak": ["006","041","051","053","900"],
  "unknown": ["030","048"],
  "static_fallback": ["006","010","014","015","016","017","018","019","020","023","024","025","029","030","032","039","043","045","048","049","050","052","053"],
  "stats_by_mid": {
    "021": {"total":130,"bundle_n":115,"null_n":3,"unit1_n":0,"bundle_pct":88.5,"null_ratio":2.3},
    "...": "..."
  }
}
```

### 2.5 schema 변경
- **DB schema 변경 없음** (제약 준수)
- product_details 기존 컬럼만 사용
- 캐시는 파일 시스템

---

## 3. 모듈 구조 (계층형 아키텍처)

| 레이어 | 파일 | 역할 |
|---|---|---|
| Infrastructure | `src/infrastructure/database/repos/bundle_stats_repo.py` | SQL — `fetch_bundle_stats() → Dict[str, BundleStats]` |
| Domain | `src/domain/order/bundle_classifier.py` | 순수 함수 — `classify(stats: BundleStats) → BundleClassification` |
| Application | `src/application/order/bundle_master_service.py` | 빌드/캐시/diff/알림 — `build_master() → BundleMaster`, `get() → BundleMaster` |
| Settings | `src/settings/constants.py` | `BUNDLE_SUSPECT_MID_CDS` 정적 set 보존 (fallback only 주석 추가) |
| Order | `src/order/order_executor.py` | 가드에서 `BundleMasterService.get()` 호출로 교체 |

### 3.1 BundleStatsRepo (Infrastructure)
```python
class BundleStatsRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def fetch_bundle_stats(self) -> Dict[str, BundleStats]:
        """common.db product_details + products 조인으로 mid_cd 별 집계"""
        rows = self.conn.execute("""
            SELECT
              COALESCE(p.mid_cd, '?') AS mid_cd,
              COUNT(*) AS total,
              SUM(CASE WHEN pd.order_unit_qty > 1 THEN 1 ELSE 0 END) AS bundle_n,
              SUM(CASE WHEN pd.order_unit_qty IS NULL THEN 1 ELSE 0 END) AS null_n,
              SUM(CASE WHEN pd.order_unit_qty = 1 THEN 1 ELSE 0 END) AS unit1_n
            FROM product_details pd
            LEFT JOIN products p USING(item_cd)
            GROUP BY COALESCE(p.mid_cd, '?')
        """).fetchall()
        return {
            r[0].zfill(3): BundleStats(
                mid_cd=r[0].zfill(3), total=r[1], bundle_n=r[2],
                null_n=r[3], unit1_n=r[4]
            )
            for r in rows if r[0] != '?'
        }
```

### 3.2 BundleClassifier (Domain, 순수)
```python
# 임계값 상수 (모듈 상수, 설계 명세 §4)
STRONG_THRESHOLD = 70.0
WEAK_THRESHOLD = 50.0
NULL_MAX = 30.0
MIN_TOTAL = 5

def classify(stats: BundleStats) -> BundleClassification:
    """순수 함수: stats → 분류 라벨"""
    if stats.total < MIN_TOTAL:
        return BundleClassification.UNKNOWN  # 샘플 부족
    if stats.null_ratio > NULL_MAX:
        return BundleClassification.UNKNOWN  # 통계 신뢰도 낮음
    if stats.bundle_pct >= STRONG_THRESHOLD:
        return BundleClassification.STRONG
    if stats.bundle_pct >= WEAK_THRESHOLD:
        return BundleClassification.WEAK
    return BundleClassification.NORMAL
```

### 3.3 BundleMasterService (Application)
```python
class BundleMasterService:
    _cache: Optional[BundleMaster] = None  # 클래스 레벨 메모리 캐시
    CACHE_DIR = Path("config/cache")

    @classmethod
    def build_master(cls, conn) -> BundleMaster:
        """daily_job Phase 0 에서 호출. 빌드 + 스냅샷 + diff 알림."""
        from src.settings.constants import BUNDLE_SUSPECT_MID_CDS

        try:
            stats_map = BundleStatsRepo(conn).fetch_bundle_stats()
        except Exception as e:
            logger.error(f"[BundleMaster] stats 조회 실패, fallback only: {e}")
            return cls._fallback_only_master()

        strong, weak, unknown = set(), set(), set()
        for mid, stats in stats_map.items():
            cls_label = classify(stats)
            if cls_label == BundleClassification.STRONG:
                strong.add(mid)
            elif cls_label == BundleClassification.WEAK:
                weak.add(mid)
            elif cls_label == BundleClassification.UNKNOWN:
                unknown.add(mid)

        master = BundleMaster(
            snapshot_date=date.today().isoformat(),
            strong=frozenset(strong),
            weak=frozenset(weak),
            unknown=frozenset(unknown),
            static_fallback=frozenset(BUNDLE_SUSPECT_MID_CDS),
            source_map=cls._build_source_map(strong, weak, BUNDLE_SUSPECT_MID_CDS),
        )

        cls._save_snapshot(master, stats_map)
        cls._cache = master
        cls._send_diff_alert(master)
        cls._send_unknown_alert(master, stats_map)
        return master

    @classmethod
    def get(cls) -> BundleMaster:
        """발주 가드에서 호출. 캐시 우선, 없으면 디스크 로드, 그것도 없으면 fallback."""
        if cls._cache:
            return cls._cache
        loaded = cls._load_today_snapshot()
        if loaded:
            cls._cache = loaded
            return loaded
        return cls._fallback_only_master()

    @classmethod
    def _fallback_only_master(cls) -> BundleMaster:
        """DB 장애·캐시 없음 시 정적 set 만으로 구성"""
        from src.settings.constants import BUNDLE_SUSPECT_MID_CDS
        return BundleMaster(
            snapshot_date=date.today().isoformat(),
            strong=frozenset(),
            weak=frozenset(),
            unknown=frozenset(),
            static_fallback=frozenset(BUNDLE_SUSPECT_MID_CDS),
            source_map={m: {"static"} for m in BUNDLE_SUSPECT_MID_CDS},
        )
```

### 3.4 order_executor 가드 통합
```python
# 변경 전 (190b24f 후)
from src.settings.constants import BUNDLE_SUSPECT_MID_CDS, ORDER_UNIT_QTY_GUARD_ENABLED
is_suspect = mid_cd in BUNDLE_SUSPECT_MID_CDS

# 변경 후
from src.application.order.bundle_master_service import BundleMasterService
master = BundleMasterService.get()
is_suspect = master.is_blocked(mid_cd)
is_warn_only = master.is_warn_only(mid_cd)

if is_warn_only:
    logger.warning(f"[BUNDLE WARN_ONLY] {item_cd} mid={mid_cd} — food/dessert 강등")
    # BLOCK 미적용, AUDIT 만 진행
elif is_suspect and (unit_missing or unit_is_one):
    # 기존 BLOCK 분기 유지
    ...
```

---

## 4. 임계값 / 정책 명세

| 항목 | 값 | 근거 |
|---|---|---|
| STRONG_THRESHOLD | 70.0 | 토론 결정 1 — 실측 11개 mid 가 자연스럽게 군집 |
| WEAK_THRESHOLD | 50.0 | 토론 결정 1 — 22개 mid 까지 포함 |
| NULL_MAX | 30.0 | 토론 결정 2 — 010(46%), 030(58%) 같은 약한 통계 분리 |
| MIN_TOTAL | 5 | 통계 유의성 최소 조건 |
| food_warn_only | {001,002,003,004,005,012} | food-underprediction-secondary 충돌 방지 (PREDICTION_PARAMS.category_floor.target_mid_cds 와 동일) |

### 4.1 food/dessert 강등 정책
- 본 정책은 **STEP 8.5** 로 명시됨
- food mid 가 STRONG 분류여도 BLOCK 미적용 → WARN_ONLY 로그만
- 이유: food-underprediction-secondary 가 발주 ↑ 방향이라 BLOCK 과 충돌
- 특히 mid=021 냉동식품(88.5%) 은 식품이지만 BLOCK 대상 — **명시 정책 필요**
  - 결정: 021 은 food_warn_only 에 포함하지 않음 (냉동식품은 food-underprediction-secondary 모집단 외)
  - 모집단: 001~005,012 (도시락/주먹밥/김밥/샌드위치/햄버거/빵)

---

## 5. 캐싱 전략

### 5.1 메모리 캐시
- `BundleMasterService._cache: Optional[BundleMaster]` (클래스 변수)
- 빌드 직후 self-set
- daily_job 이 다음날 다시 build_master() 호출 시 덮어씀

### 5.2 디스크 스냅샷
- 경로: `config/cache/bundle_master_YYYYMMDD.json`
- 빌드 직후 자동 저장
- get() 호출 시 메모리 캐시 → 디스크 → fallback 순서

### 5.3 무효화
- daily_job 시작 시 강제 재빌드 (07:00 BulkCollect 직후)
- product_details 가 일 1회만 갱신되므로 일 단위 캐시가 충분
- **5분/1시간 캐시 미사용** (토론 결정 3)

### 5.4 캐시 청소
- 30일 이전 스냅샷 자동 삭제 (cron: 매주 일요일 02:00)
- 운영 디버깅용 보존: 최근 7일

---

## 6. 알림

### 6.1 diff 알림 (전일 대비)
**트리거**: build_master() 종료 시
**조건**: 추가 또는 제거된 mid 가 있을 때만 (변화 없으면 알림 생략)
**채널**: NotificationDispatcher (카톡)
**형식**:
```
[BundleMaster] 04-09 갱신
  추가: 021 냉동식품 (88.5%, total=130)
       072 담배 (73.4%, total=203)
       073 전자담배 (71.8%, total=110)
  제거: 048 음료 (NULL>30%로 UNKNOWN 이동)
  STRONG=27 WEAK=5 UNKNOWN=2 (전일 STRONG=23 WEAK=4 UNKNOWN=3)
```

### 6.2 UNKNOWN 알림 (수집 결함)
**트리거**: build_master() 종료 시 + UNKNOWN set 변화 시 1회
**조건**: NULL>30% 인 신규 mid 발견
**형식**:
```
[BundleMaster 수집결함] 04-09
  다음 카테고리는 product_details NULL 비율이 30%를 초과합니다:
  - 030 간식 (NULL 36/62=58%)
  - 010 음료 (NULL 12/26=46%)
  → BGF API 가 ORD_UNIT_QTY 빈값 반환하는 상품 다수
  → 정적 fallback 으로 임시 보호 중
  → 수집기 점검 필요
```

### 6.3 빌드 실패 알림
**트리거**: build_master() 예외 발생 시
**형식**:
```
[BundleMaster ERROR] 04-09
  동적 빌드 실패, fallback only 모드로 진행:
  {예외 메시지}
  현재 BLOCK: 정적 set 22개 mid (회귀 없음)
```

---

## 7. 구현 체크리스트 (15+1 step)

- [ ] **Step 1** [Infra] `infrastructure/database/repos/bundle_stats_repo.py` — `fetch_bundle_stats()`
- [ ] **Step 2** [Domain] `domain/order/bundle_classifier.py` — `BundleStats` 값객체 + `classify()` + `BundleClassification` enum + 임계값 상수
- [ ] **Step 3** [Test] `tests/test_bundle_classifier.py` — 경계값(69.9/70.0/49.9/50.0), NULL 30/31%, total<5
- [ ] **Step 4** [Application] `application/order/bundle_master_service.py` — `BundleMaster` 값객체 + `build_master()` + `get()`
- [ ] **Step 5** [Application] 정적 fallback 합집합 + source_map
- [ ] **Step 6** [Cache] `config/cache/bundle_master_YYYYMMDD.json` 스냅샷 저장/로드
- [ ] **Step 7** [Constants] `BUNDLE_SUSPECT_MID_CDS` 보존 + 주석 추가 ("fallback only, dynamic master takes precedence")
- [ ] **Step 8** [Integration] order_executor `_calc_order_result` 와 `input_product` 가드를 `BundleMasterService.get()` 호출로 교체
- [ ] **Step 8.5** [Policy] food/dessert mid (001~005, 012) WARN_ONLY 강등 — `BundleMaster.is_blocked()` / `is_warn_only()` 메서드
- [ ] **Step 9** [Daily Job] `daily_job.phases.preparation` 또는 신규 phase 에 `BundleMasterService.build_master()` 추가
- [ ] **Step 10** [Alert] 전일 스냅샷 diff → 추가/제거 mid 카톡
- [ ] **Step 11** [Alert] UNKNOWN(NULL>30%) mid 목록 — "수집 결함 의심" 별도 카톡
- [ ] **Step 12** [Regression Test] 현재 22 BLOCK mid 모두 새 합집합에 포함되는지
- [ ] **Step 13** [Regression Test] DB 장애 시뮬레이션(repo 빈 결과) → 정적 fallback 만으로 22 BLOCK 유지
- [ ] **Step 14** [Observability] order_executor 알림에 `bundle_block_source` (static/dynamic/both) dict 메타데이터 포함
- [ ] **Step 15** [Validation] 04-10~04-12 3일간 STRONG/WEAK/UNKNOWN 분포 모니터링 → WEAK→STRONG 승격 가능 여부 판단

---

## 8. 검증 계획

### 8.1 단위 테스트
- `test_bundle_classifier.py`:
  - 경계값 (69.9/70.0/49.9/50.0)
  - NULL 30%/31% 경계
  - total<5 → UNKNOWN
  - 정상 케이스 6개
- `test_bundle_master_service.py`:
  - build_master() 정상 빌드
  - DB 장애 시 fallback only 모드
  - 디스크 스냅샷 저장/로드 일관성
  - food/dessert STRONG → WARN_ONLY 강등
  - source_map 정확성

### 8.2 회귀 테스트
- 현재 22 BLOCK mid 모두 새 master 에서 BLOCK 대상인지
- food mid 6개(001~005,012) 가 BLOCK 대상이 아닌지
- DB 장애 시뮬레이션 → 22 mid 모두 fallback 으로 유지

### 8.3 통합 테스트 (수동)
- daily_job dry-run → BundleMasterService.build_master() 호출 확인
- JSON 스냅샷 생성 확인
- 가드 로직이 새 호출 경로 사용 확인

### 8.4 운영 검증 (04-10 ~ 04-12)
- 매일 카톡 diff 알림 수신
- 신규 BLOCK 11개 mid 의 발주 변화 모니터링
- 회귀: food 카테고리 발주량 ±10% 이내 유지
- 알림: false positive 카톡 0건

### Match Rate 목표
- 90% (회귀 0 + 11 mid 정상 BLOCK + UNKNOWN 알림 1회 이상)

---

## 9. 본 세션 PDCA 들과의 충돌 매트릭스

| 변경 항목 | ab98bfc 1차 수정 | food-underprediction-secondary | 위험 |
|---|---|---|---|
| 동적 마스터 빌드 (Step 4~9) | 무관 (예측 코드 X) | 무관 | 🟢 |
| 정적 fallback 유지 (Step 5,7) | 023~025 자동 흡수 | — | 🟢 |
| food WARN_ONLY (Step 8.5) | — | 발주 ↑ 방향 보장 | 🟢 |
| daily_job Phase 0 통합 (Step 9) | 무관 | 무관 | 🟢 |
| order_executor 가드 교체 (Step 8) | 1차 수정과 같은 파일 | — | 🟡 (분리 커밋 필수) |

**원칙**:
- Do 단계 진입은 04-09 1차 수정 검증 통과 + 04-10 Phase A 배포 후
- Step 8 (가드 교체) 는 단독 커밋, 회귀 테스트 통과 후 푸시
- 모든 변경은 functionally additive (기존 BLOCK 동작 보존)

---

## 10. 롤백 계획

| Step | 롤백 트리거 | 절차 |
|---|---|---|
| Step 4~9 (서비스/잡) | build_master() 빌드 시 daily_job 30s+ 지연 | feature flag `BUNDLE_DYNAMIC_MASTER_ENABLED=False` 추가 + 즉시 비활성화 |
| Step 8 (가드 교체) | 회귀 카톡 알림 5건/일 초과 | git revert 단일 커밋 |
| Step 10~11 (알림) | 알림 폭주 | NotificationDispatcher 채널 throttle |
| Step 9 (Phase 0) | Phase 0 build 실패로 daily_job 차단 | try/except 로 fallback only 모드 자동 전환 (이미 §3.3 구현) |

**롤백 안전장치**:
- 모든 코드는 try/except 로 fallback 보장
- 정적 set 영구 유지 (Step 7) → 최악의 경우 기존 22 BLOCK 유지
- 신규 mid (021, 072 등) 가 추가되더라도 회귀 0

---

## 11. 산출물

| 파일 | 변경 유형 |
|---|---|
| `src/infrastructure/database/repos/bundle_stats_repo.py` | 신규 |
| `src/domain/order/bundle_classifier.py` | 신규 |
| `src/application/order/bundle_master_service.py` | 신규 |
| `src/order/order_executor.py` | 가드 호출 교체 (2곳) |
| `src/settings/constants.py` | 주석 추가 (기능 변경 없음) |
| `src/scheduler/phases/preparation.py` | build_master() 호출 추가 |
| `tests/test_bundle_classifier.py` | 신규 |
| `tests/test_bundle_master_service.py` | 신규 |
| `tests/test_bundle_guard_regression.py` | 신규 |
| `config/cache/bundle_master_YYYYMMDD.json` | 런타임 자동 생성 |
| `docs/05-issues/order-execution.md` | [PLANNED] → [OPEN] → [WATCHING] 전환 |
| `CLAUDE.md` 활성 이슈 | 동기화 |

---

## 12. Out of Scope

- ML 분류기 도입 (토론 결정 1 — 비용 대비 이득 낮음)
- 5분/1시간 메모리 캐시 (토론 결정 3 — 비결정성 위험)
- BundleClassifier 의 동적 임계값 자동 조정 (Phase B 후보)
- 수집기 측 stock_qty=-1 sentinel 근원 수정 (별도 PDCA, food-underprediction-secondary §10)
- 본부 발주 시스템과의 BUNDLE_SUSPECT 동기화 (별도 이슈, site-channel-attribution)

## Issue-Chain
order-execution#bundle-suspect-dynamic-master
