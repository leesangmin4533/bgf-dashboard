# Historical Data Collector - 과거 데이터 수집 모듈

## 📋 개요

점포별 과거 데이터를 자동으로 수집하는 범용 모듈입니다. 신규 점포 추가 시 초기 데이터 로딩이나 누락된 날짜를 보충할 때 사용합니다.

### 주요 기능

- ✅ **점포별 독립 수집**: 점포 ID만 지정하면 자동 수집
- ✅ **누락 날짜 자동 탐지**: DB에 없는 날짜만 자동으로 찾아 수집
- ✅ **배치 처리**: 30일 단위로 나누어 안정적으로 수집
- ✅ **진행 상황 추적**: 실시간 로그로 진행률 확인
- ✅ **재시도 가능**: 실패한 날짜는 목록으로 제공, 재실행 가능
- ✅ **강제 재수집**: 기존 데이터 덮어쓰기 옵션

## 🚀 사용법

### 1. Python 스크립트에서 사용

```python
from src.collectors.historical_data_collector import HistoricalDataCollector

# 1. Collector 초기화
collector = HistoricalDataCollector(
    store_id="46704",
    store_name="이천 동양점"  # 선택사항
)

# 2. 수집 계획 확인 (실제 수집 안 함)
plan = collector.get_collection_plan(months=6)
print(f"수집 대상: {plan['missing_count']}일")
print(f"예상 시간: {plan['estimated_time_minutes']}분")

# 3. 데이터 수집 실행
result = collector.collect_historical_data(
    months=6,           # 6개월 분량
    batch_size=30,      # 30일씩 배치 처리
    force=False,        # 기존 데이터 유지
    auto_confirm=True   # 자동 진행
)

# 4. 결과 확인
if result['success']:
    print(f"성공: {result['success_count']}일")
    print(f"소요 시간: {result['elapsed_minutes']:.1f}분")
else:
    print(f"실패한 날짜: {result['failed_dates']}")
```

### 2. CLI 명령어로 사용

#### 기본 사용 (6개월 데이터 수집)

```bash
cd bgf_auto
python -m src.collectors.historical_data_collector --store-id 46704 --yes
```

#### 신규 점포 추가 (12개월 데이터 수집)

```bash
python -m src.collectors.historical_data_collector \
  --store-id 46513 \
  --store-name "원주혁신점" \
  --months 12 \
  --yes
```

#### 강제 재수집 (기존 데이터 덮어쓰기)

```bash
python -m src.collectors.historical_data_collector \
  --store-id 46704 \
  --force \
  --yes
```

#### 현황만 확인 (수집하지 않음)

```bash
python -m src.collectors.historical_data_collector \
  --store-id 46704 \
  --status-only \
  --status-days 30
```

#### 배치 크기 조정 (메모리 부족 시)

```bash
python -m src.collectors.historical_data_collector \
  --store-id 46704 \
  --batch-size 15 \
  --yes
```

## 📊 CLI 파라미터

| 파라미터 | 필수 | 기본값 | 설명 |
|---------|------|--------|------|
| `--store-id` | ✓ | - | 점포 ID (예: 46704) |
| `--store-name` |  | store_id | 점포명 (로그 표시용) |
| `--months` |  | 6 | 수집할 개월 수 |
| `--batch-size` |  | 30 | 배치 크기 (일) |
| `--force`, `-f` |  | False | 기존 데이터도 재수집 |
| `--yes`, `-y` |  | False | 자동 진행 (확인 생략) |
| `--status-only` |  | False | 현황만 조회 |
| `--status-days` |  | 30 | 현황 조회 일수 |

## 🔄 작동 흐름

```
1. 초기화
   └─ HistoricalDataCollector(store_id, store_name)

2. 수집 계획 수립
   ├─ 날짜 범위 생성 (months 기준)
   ├─ DB에서 이미 수집된 날짜 확인
   └─ 미수집 날짜 필터링

3. 사용자 확인 (auto_confirm=False일 때)
   └─ 수집 대상 날짜, 예상 시간 표시

4. 배치 단위 수집
   ├─ 날짜를 batch_size 단위로 분할
   ├─ 각 배치별로:
   │   ├─ SalesCollector.collect_multiple_dates() 호출
   │   ├─ 수집된 데이터 DB 저장
   │   └─ 배치 간 30초 대기
   └─ 실패한 날짜 기록

5. 결과 요약
   ├─ 성공/실패 통계
   ├─ 소요 시간
   └─ 실패한 날짜 목록
```

## 📝 출력 예시

### 정상 실행

```
================================================================================
과거 데이터 수집 - 이천 동양점 (46704)
================================================================================

[수집 계획]
  점포: 이천 동양점 (46704)
  기간: 2025-08-09 ~ 2026-02-05
  전체: 181일
  이미 수집됨: 150일
  수집 대상: 31일
  예상 시간: 약 62.0분
  강제 재수집: 아니오
  누락 날짜 샘플: ['2025-08-15', '2025-08-20', ...] 외 26일

계속하시겠습니까? (y/n): y

[데이터 수집 시작]
총 2개 배치로 수집 (배치당 최대 30일)
================================================================================

[배치 1/2] 30일 수집 중...
날짜 범위: 2025-08-15 ~ 2025-09-13
배치 1 완료: 성공 30, 실패 0
다음 배치 전 30초 대기...

[배치 2/2] 1일 수집 중...
날짜 범위: 2025-09-14 ~ 2025-09-14
배치 2 완료: 성공 1, 실패 0

================================================================================
수집 완료
================================================================================
점포: 이천 동양점 (46704)
총 날짜: 31일
성공: 31일
실패: 0일
성공률: 100.0%
소요 시간: 58.3분
```

### 모두 수집된 경우

```
================================================================================
과거 데이터 수집 - 이천 동양점 (46704)
================================================================================

[수집 계획]
  점포: 이천 동양점 (46704)
  기간: 2025-08-09 ~ 2026-02-05
  전체: 181일
  이미 수집됨: 181일
  수집 대상: 0일

✓ 모든 데이터가 이미 수집되었습니다!
```

### 현황 조회

```bash
$ python -m src.collectors.historical_data_collector \
    --store-id 46704 \
    --status-only

================================================================================
수집 현황 - 이천 동양점 (46704)
================================================================================

기간: 2026-01-07 ~ 2026-02-05 (30일)
수집 완료: 28일 (93.3%)
미수집: 2일

미수집 날짜:
  - 2026-01-15
  - 2026-01-22
```

## ⚙️ 클래스 API

### HistoricalDataCollector

#### 초기화

```python
__init__(store_id: str, store_name: Optional[str] = None)
```

**Parameters:**
- `store_id` (str, 필수): 점포 ID
- `store_name` (str, 선택): 점포명 (로그 표시용, 기본값=store_id)

**Raises:**
- `ValueError`: store_id가 없을 때

#### get_collection_plan()

수집 계획 수립 (실제 수집하지 않음)

```python
get_collection_plan(
    months: int = 6,
    force: bool = False
) -> Dict[str, Any]
```

**Parameters:**
- `months` (int): 수집할 개월 수 (기본: 6)
- `force` (bool): True면 이미 수집된 날짜도 포함

**Returns:**
```python
{
    'total_dates': int,              # 전체 날짜 수
    'collected_dates': int,          # 이미 수집된 날짜 수
    'missing_dates': List[str],      # 미수집 날짜 목록
    'missing_count': int,            # 미수집 날짜 수
    'date_range': {
        'start': str,                # YYYY-MM-DD
        'end': str                   # YYYY-MM-DD
    },
    'estimated_time_minutes': float, # 예상 소요 시간(분)
    'force': bool                    # 강제 재수집 여부
}
```

#### collect_historical_data()

과거 데이터 수집 실행

```python
collect_historical_data(
    months: int = 6,
    batch_size: int = 30,
    force: bool = False,
    auto_confirm: bool = False
) -> Dict[str, Any]
```

**Parameters:**
- `months` (int): 수집할 개월 수 (기본: 6)
- `batch_size` (int): 배치 크기 (기본: 30일)
- `force` (bool): True면 이미 수집된 날짜도 재수집
- `auto_confirm` (bool): True면 사용자 확인 없이 자동 진행

**Returns:**
```python
{
    'success': bool,                 # 전체 성공 여부
    'total_dates': int,              # 전체 날짜 수
    'success_count': int,            # 성공한 날짜 수
    'failed_count': int,             # 실패한 날짜 수
    'success_rate': float,           # 성공률 (%)
    'elapsed_minutes': float,        # 소요 시간(분)
    'failed_dates': List[str]        # 실패한 날짜 목록
}
```

#### get_collection_status()

최근 N일 수집 현황 조회

```python
get_collection_status(days: int = 30) -> Dict[str, Any]
```

**Parameters:**
- `days` (int): 조회할 일수 (기본: 30)

**Returns:**
```python
{
    'store_id': str,
    'store_name': str,
    'period': {
        'start': str,                # YYYY-MM-DD
        'end': str,                  # YYYY-MM-DD
        'days': int
    },
    'collected_dates': List[str],    # 수집된 날짜 목록
    'collected_count': int,          # 수집된 날짜 수
    'missing_dates': List[str],      # 미수집 날짜 목록
    'missing_count': int,            # 미수집 날짜 수
    'completion_rate': float         # 완료율 (%)
}
```

## 🎯 사용 시나리오

### 시나리오 1: 신규 점포 추가

```python
# 1. 점포 정보 확인
collector = HistoricalDataCollector(
    store_id="46513",
    store_name="원주혁신점"
)

# 2. 현재 상태 확인
status = collector.get_collection_status(days=180)
print(f"현재 수집률: {status['completion_rate']:.1f}%")

# 3. 6개월 데이터 수집
result = collector.collect_historical_data(
    months=6,
    auto_confirm=True
)
```

### 시나리오 2: 누락 데이터 보충

```python
# 1. 최근 30일 현황 확인
collector = HistoricalDataCollector(store_id="46704")
status = collector.get_collection_status(days=30)

if status['missing_count'] > 0:
    print(f"누락: {status['missing_dates']}")

    # 2. 6개월 범위에서 누락된 날짜만 수집
    result = collector.collect_historical_data(
        months=6,
        force=False,  # 누락된 날짜만
        auto_confirm=True
    )
```

### 시나리오 3: 데이터 품질 검증 후 재수집

```python
collector = HistoricalDataCollector(store_id="46704")

# 1. 계획 확인
plan = collector.get_collection_plan(months=6, force=True)
print(f"재수집 대상: {plan['total_dates']}일")
print(f"예상 시간: {plan['estimated_time_minutes']}분")

# 2. 사용자 확인 후 강제 재수집
result = collector.collect_historical_data(
    months=6,
    force=True,          # 전체 재수집
    auto_confirm=False   # 사용자 확인 받기
)
```

## ⚠️ 주의사항

### 1. 실행 전 확인

- ✅ `.env` 파일에 BGF 로그인 정보 설정 필수
- ✅ 점포 ID 정확성 확인 (잘못된 ID는 오류 발생)
- ✅ 충분한 시간 확보 (6개월 = 약 6~12시간)
- ✅ 안정적인 네트워크 연결

### 2. 메모리 관리

- 배치 크기가 클수록 메모리 사용량 증가
- 메모리 부족 시 `--batch-size` 줄이기 (예: 15)
- 대량 수집 시 서버 리소스 모니터링 권장

### 3. 실패 처리

- 실패한 날짜는 `failed_dates`에 기록됨
- 재실행 시 누락된 날짜만 자동으로 수집
- 연속 실패 시 BGF 사이트 접속 확인

### 4. 데이터 무결성

- `force=False` (기본값): UPSERT 방식으로 중복 방지
- `force=True`: 기존 데이터 덮어쓰기 (최신 데이터로 갱신)
- DB 백업 후 재수집 권장

### 5. 성능 최적화

- 배치 간 30초 대기 (BGF 서버 부하 방지)
- 날짜당 평균 2분 소요 (네트워크 상황에 따라 변동)
- 대량 수집 시 야간/주말 실행 권장

## 🐛 트러블슈팅

### 문제 1: "Login failed"

**원인:** BGF 로그인 정보 오류

**해결:**
```bash
# .env 파일 확인
cat .env | grep BGF

# 정보 업데이트
BGF_USER_ID=올바른_아이디
BGF_PASSWORD=올바른_비밀번호
```

### 문제 2: 수집 중 멈춤

**원인:** 네트워크 타임아웃 또는 BGF 서버 문제

**해결:**
```bash
# 스크립트 종료 후 재실행 (누락 날짜만 자동으로 수집됨)
python -m src.collectors.historical_data_collector \
  --store-id 46704 \
  --yes
```

### 문제 3: 메모리 부족

**원인:** batch_size가 너무 큼

**해결:**
```bash
# 배치 크기 줄이기
python -m src.collectors.historical_data_collector \
  --store-id 46704 \
  --batch-size 15 \
  --yes
```

### 문제 4: 수집 속도가 느림

**원인:** 정상 동작 (날짜당 2분 소요)

**대안:**
- 야간에 백그라운드 실행
- 필요한 기간만 수집 (`--months` 조정)

## 📁 관련 파일

```
bgf_auto/
├── src/
│   └── collectors/
│       ├── historical_data_collector.py  # [메인 모듈]
│       ├── sales_collector.py            # 판매 데이터 수집
│       └── base.py                       # 베이스 클래스
├── docs/
│   └── collectors/
│       └── historical-data-collector.md  # [이 문서]
└── data/
    └── bgf_sales.db                      # SQLite DB
```

## 🔗 참고

- [SalesCollector](../src/collectors/sales_collector.py): 실제 데이터 수집 로직
- [SalesRepository](../src/db/repository.py): DB 저장 및 조회
- [run_full_flow.py](../../scripts/run_full_flow.py): 기존 로직 참조

## 📌 버전 정보

- **작성일**: 2026-02-06
- **버전**: 1.0.0
- **호환**: Python 3.12+, BGF 리테일 시스템
- **DB 스키마**: v19

---

**문의사항이나 버그 리포트는 프로젝트 관리자에게 문의하세요.**
