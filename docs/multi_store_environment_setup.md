# 멀티 점포 환경 구축 완료

**작성일**: 2026-02-06
**상태**: ✅ 인프라 구축 완료
**다음 단계**: ML 모델 수정 (Phase 2)

---

## 구축된 환경

### 1. 디렉토리 구조

```
bgf_auto/
├── config/
│   ├── stores.json              # 점포 정보 (기존)
│   ├── eval_params.json         # 기본 파라미터 (기존)
│   │
│   └── stores/                  # 점포별 설정 (신규 ✨)
│       ├── 46513_eval_params.json   # 호반점 설정
│       └── 46704_eval_params.json   # 동양점 설정
│
└── src/
    └── utils/
        └── store_manager.py     # 점포 관리 유틸리티 (신규 ✨)
```

---

## 생성된 파일

### 1. 점포별 설정 파일

**호반점 (46513):**
- 경로: `config/stores/46513_eval_params.json`
- 상태: ✅ 생성됨
- 내용: 기본 eval_params 복사 (향후 점포별 튜닝 가능)

**동양점 (46704):**
- 경로: `config/stores/46704_eval_params.json`
- 상태: ✅ 생성됨
- 내용: 기본 eval_params 복사 (향후 점포별 튜닝 가능)

### 2. 점포 관리 유틸리티

**파일**: `src/utils/store_manager.py`

**주요 기능:**
- 점포 정보 조회 (stores.json 기반)
- 점포별 설정 파일 경로 자동 탐색
- 점포 유효성 검증
- 활성 점포 목록 조회

**사용 예시:**

```python
from src.utils.store_manager import (
    get_store_manager,
    get_active_stores,
    get_store_info,
    is_valid_store,
    get_store_config_path
)

# 1. 점포 관리자 인스턴스
manager = get_store_manager()

# 2. 모든 활성 점포 조회
stores = get_active_stores()
for store in stores:
    print(f"{store.store_id}: {store.store_name}")

# 3. 특정 점포 정보
store = get_store_info("46704")
print(store.store_name)  # "이천동양점"

# 4. 점포 유효성 확인
if is_valid_store("46704"):
    print("유효한 점포")

# 5. 점포별 설정 파일 경로
config_path = get_store_config_path("46513")
print(config_path)  # config/stores/46513_eval_params.json
```

---

## 현재 점포 현황

| 점포 ID | 점포명 | 위치 | 상태 | 설정 파일 |
|---------|--------|------|------|----------|
| 46513 | 이천호반베르디움점 | 경기 이천시 | ✅ 활성 | ✅ 존재 |
| 46704 | 이천동양점 | 경기 이천시 | ✅ 활성 | ✅ 존재 |

---

## 점포 정보 확인 방법

### CLI로 확인

```bash
cd bgf_auto
python -c "
import sys
sys.path.insert(0, '.')
from src.utils.store_manager import StoreManager

manager = StoreManager()
manager.print_stores_summary()
"
```

### Python으로 확인

```python
from src.utils.store_manager import get_store_manager

manager = get_store_manager()

# 활성 점포 ID 목록
store_ids = manager.get_active_store_ids()
print(store_ids)  # ['46513', '46704']

# 점포별 설정 로드
params_46513 = manager.load_store_eval_params('46513')
params_46704 = manager.load_store_eval_params('46704')

print(f"호반점 파라미터: {len(params_46513)}개")
print(f"동양점 파라미터: {len(params_46704)}개")
```

---

## 향후 작업 (Phase 2: ML 모델 수정)

### 1. ImprovedPredictor 수정

```python
# Before
class ImprovedPredictor:
    def __init__(self, db_path=None):
        self.db_path = db_path

# After
class ImprovedPredictor:
    def __init__(self, store_id: str, db_path=None):
        self.store_id = store_id
        self.db_path = db_path
        # 점포별 설정 로드
        from src.utils.store_manager import get_store_manager
        manager = get_store_manager()
        self.eval_params = manager.load_store_eval_params(store_id)
```

### 2. SQL 쿼리 수정

```python
# Before
cursor.execute("""
    SELECT * FROM daily_sales
    WHERE item_cd = ?
""", (item_cd,))

# After
cursor.execute("""
    SELECT * FROM daily_sales
    WHERE item_cd = ? AND store_id = ?
""", (item_cd, self.store_id))
```

### 3. AutoOrderSystem 수정

```python
# Before
predictor = ImprovedPredictor()

# After
predictor = ImprovedPredictor(store_id="46513")
```

### 4. DailyJob 수정

```python
# Before
def run_daily_job():
    # 단일 점포 처리
    pass

# After
def run_daily_job():
    from src.utils.store_manager import get_active_stores

    # 점포별 실행
    for store in get_active_stores():
        logger.info(f"점포 작업 시작: {store.store_name}")
        run_store_job(store.store_id)
```

---

## 점포별 설정 튜닝

### 호반점 (46513) - 대형점 특성

```json
{
  "daily_avg_days": {
    "value": 14.0,
    "description": "일평균 판매량 계산 기간"
  },
  "popularity_high_percentile": {
    "value": 70.0,
    "description": "고인기 상품 기준 (상위 30%)"
  }
}
```

### 동양점 (46704) - 중형점 특성

```json
{
  "daily_avg_days": {
    "value": 14.0,
    "description": "일평균 판매량 계산 기간"
  },
  "popularity_high_percentile": {
    "value": 65.0,
    "description": "고인기 상품 기준 (상위 35%, 좀 더 관대)"
  }
}
```

현재는 동일한 설정이지만, 점포별 데이터가 쌓이면 독립적으로 튜닝 가능합니다.

---

## 테스트 방법

### 1. 점포 관리자 테스트

```bash
cd bgf_auto
python -m pytest tests/test_store_manager.py -v
```

### 2. 점포별 설정 로드 테스트

```python
def test_store_specific_config():
    from src.utils.store_manager import get_store_config_path

    # 호반점 설정
    path_46513 = get_store_config_path("46513")
    assert path_46513.exists()
    assert "46513" in path_46513.name

    # 동양점 설정
    path_46704 = get_store_config_path("46704")
    assert path_46704.exists()
    assert "46704" in path_46704.name

    # 다른 설정 파일인지 확인
    assert path_46513 != path_46704
```

---

## 신규 점포 추가 방법

### 1. stores.json에 점포 추가

```json
{
  "stores": [
    ...
    {
      "store_id": "46705",
      "store_name": "이천신규점",
      "location": "경기 이천시",
      "type": "일반점",
      "is_active": true,
      "bgf_user_id": "46705",
      "bgf_password": "password",
      "description": "3호점",
      "added_date": "2026-03-01"
    }
  ]
}
```

### 2. 점포별 설정 파일 생성

```bash
cd bgf_auto
cp config/stores/46513_eval_params.json config/stores/46705_eval_params.json
```

### 3. 데이터 수집

```bash
python scripts/collect_dongyang_6months.py --store-id 46705 --store-name "이천신규점"
```

### 4. 자동 인식

StoreManager가 자동으로 신규 점포를 인식하고 설정을 로드합니다.

---

## 장점

### 1. 점포별 독립 설정
- 각 점포의 특성에 맞는 파라미터 튜닝 가능
- 한 점포의 설정 변경이 다른 점포에 영향 없음

### 2. 확장성
- 신규 점포 추가가 간단 (설정 파일만 추가)
- StoreManager가 자동으로 인식

### 3. 유지보수성
- 점포별 설정이 명확히 분리됨
- 디버깅 및 문제 추적 용이

### 4. 호환성
- 기존 코드와 호환 (기본 eval_params.json 유지)
- 점진적 마이그레이션 가능

---

## 다음 단계

### Phase 2: ML 모델 수정 (예정)

1. ImprovedPredictor store_id 파라미터 추가
2. 모든 SQL 쿼리에 store_id 필터 추가 (약 30개)
3. AutoOrderSystem 수정
4. DailyJob 멀티 점포 지원
5. 테스트 및 검증

**예상 작업 시간**: 2~3시간

---

## 참고 문서

- [멀티 점포 ML 아키텍처](./multi_store_ml_architecture.md)
- [점포 정보 설정](../config/stores.json)
- [기본 파라미터](../config/eval_params.json)

---

**작성자**: Claude
**업데이트**: 2026-02-06
**상태**: Phase 1 (환경 구축) 완료 ✅
