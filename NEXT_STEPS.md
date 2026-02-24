# 점포별 독립 운영 - 다음 단계

## 🎉 구현 완료 (2026-02-07)

### Phase 1: 보안 긴급 수정 ✅
- [x] 환경변수 기반 인증 (BGF_USER_ID_{store_id}, BGF_PASSWORD_{store_id})
- [x] stores.json에서 평문 비밀번호 제거
- [x] StoreConfigLoader 구현
- [x] SalesAnalyzer 환경변수 연동
- [x] 보안 검증 스크립트 작성

### Phase 2: 데이터베이스 마이그레이션 ✅
- [x] DB 스키마 v23 마이그레이션
- [x] order_tracking에 store_id 추가
- [x] collection_logs에 store_id 추가
- [x] order_fail_reasons에 store_id 추가
- [x] 기존 데이터 마이그레이션 (46513 기본값)
- [x] UNIQUE 제약 조건 업데이트

### Phase 3: 멀티 스토어 활성화 ✅
- [x] StoreRepository StoreConfigLoader 통합
- [x] run_scheduler.py --multi-store, --store 옵션 확인
- [x] DailyCollectionJob 점포별 독립성 확인

---

## ⚠️ 즉시 실행 필요

### 1. 환경변수 설정 (필수)
```bash
# .env 파일에 동양점 인증 정보 추가
BGF_USER_ID_46704=<실제_사용자_ID>
BGF_PASSWORD_46704=<실제_비밀번호>
```

### 2. 동양점 초기 데이터 수집
```bash
# 동양점 첫 실행 (최소 7일 데이터 수집 권장)
python run_scheduler.py --store 46704 --now
```

### 3. 동양점 보정 시작 (데이터 누적 후)
```bash
# 주의: 현재 --calibrate-only 옵션 미구현
# 임시 방법: EvalCalibrator 직접 호출
python -c "
from src.prediction.eval_calibrator import EvalCalibrator
calibrator = EvalCalibrator(store_id='46704')
result = calibrator.run_daily_calibration()
calibrator.config.save(store_id='46704')
print(f'보정 완료: {result}')
"
```

---

## 📋 남은 작업 (Phase 3 보완)

### CLI 개선
- [ ] run_scheduler.py에 `--calibrate-only` 옵션 추가
- [ ] run_scheduler.py에 `--stores` (복수 점포) 옵션 추가

**예시 구현**:
```python
# run_scheduler.py
parser.add_argument(
    "--calibrate-only",
    action="store_true",
    help="Run calibration only (no collection or order)"
)

# job_wrapper 수정
def job_wrapper(calibrate_only=False):
    job = DailyCollectionJob()
    if calibrate_only:
        calibrator = EvalCalibrator(store_id=job.store_id)
        result = calibrator.run_daily_calibration()
        calibrator.config.save(store_id=job.store_id)
    else:
        result = job.run_optimized(run_auto_order=True)
```

### Repository 계층 점포 파라미터 전파
일부 Repository 메서드에서 store_id 파라미터가 누락되어 있을 수 있습니다.
전체 코드베이스 검토 필요:

```bash
# store_id 파라미터가 없는 메서드 찾기
grep -r "def save_" src/db/repository.py | grep -v "store_id"
```

---

## 🚀 Phase 4: 확장성 개선 (계획)

### 1주차: 자동화 도구
- [ ] `scripts/add_store.py` 구현
  - stores.json 자동 업데이트
  - 점포별 설정 파일 템플릿 생성
  - 환경변수 설정 안내

```bash
# 사용 예시
python scripts/add_store.py \
  --store-id 99999 \
  --name "신규점포" \
  --location "서울 강남구"
```

### 2주차: 성능 모니터링
- [ ] `src/monitoring/store_metrics.py` 구현
  - 점포별 실행 시간 추적
  - 단계별 성능 메트릭 수집
  - 실행 시간 요약 리포트

### 3-4주차: 점포간 비교
- [ ] `src/prediction/multi_store_comparator.py` 구현
  - 점포별 파라미터 비교 테이블
  - 파라미터 발산 감지 및 동기화 제안

- [ ] `src/web/routes/api_store.py` 구현
  - `GET /api/stores`: 활성 점포 목록
  - `GET /api/stores/<store_id>/metrics`: 점포별 메트릭

---

## 🔍 검증 체크리스트

### Phase 1 검증
- [x] stores.json에 bgf_password 없음
- [x] .env에 점포별 환경변수 존재
- [x] StoreConfigLoader 정상 작동
- [x] SalesAnalyzer 환경변수로 로그인 성공

### Phase 2 검증
- [x] DB 스키마 v23 마이그레이션 완료
- [x] order_tracking, collection_logs, order_fail_reasons에 store_id 컬럼
- [x] UNIQUE 제약 조건 재구성 완료
- [x] 기존 데이터 마이그레이션 검증 (46513 기본값)

### Phase 3 검증 (진행중)
- [ ] 동양점 초기 수집 성공
- [ ] 동양점 daily_sales 데이터 존재 (N건 > 0)
- [ ] 동양점 보정 이력 생성 (calibration_history N건 > 0)
- [ ] 멀티 스토어 병렬 실행 안정성 확인

---

## 💡 유용한 명령어

### 데이터 확인
```bash
# 점포별 데이터 수 확인
python -c "
import sqlite3
conn = sqlite3.connect('data/bgf_sales.db')
c = conn.cursor()

tables = ['daily_sales', 'order_tracking', 'collection_logs',
          'prediction_logs', 'eval_outcomes', 'calibration_history']

for table in tables:
    result = c.execute(f'SELECT store_id, COUNT(*) FROM {table} GROUP BY store_id').fetchall()
    print(f'{table}:')
    for store_id, count in result:
        print(f'  {store_id}: {count}건')

conn.close()
"
```

### 점포별 보정 파라미터 확인
```bash
# 호반점 파라미터
cat config/stores/46513_eval_params.json

# 동양점 파라미터 (데이터 수집 후 생성)
cat config/stores/46704_eval_params.json
```

### 로그 모니터링
```bash
# 점포별 로그 필터링
tail -f logs/daily_job.log | grep "\[46704\]"
```

---

## 🎯 목표 달성 지표

### 보안
- ✅ 모든 인증 정보 환경변수 관리
- ✅ stores.json에 민감 정보 없음
- ✅ .env.example 문서화 완료

### 데이터 독립성
- ✅ 11개 운영 테이블 점포별 분리
- ✅ 점포별 데이터 조회/저장 정상 작동
- ⏳ 크로스 점포 데이터 오염 없음 (검증 필요)

### 자동 보정
- ✅ 호반점(46513) 보정 지속 (누적 57+N건)
- ⏳ 동양점(46704) 보정 시작 (목표: 누적 N건 > 0)
- ⏳ 점포별 파라미터 발산 모니터링

### 확장성
- ⏳ 새 점포 추가 10분 이내 (자동화 스크립트 필요)
- ⏳ 5개 점포 병렬 실행 25분 이내
- ⏳ 점포별 성능 메트릭 수집

---

## 📞 지원

### 문제 발생 시
1. 로그 확인: `logs/daily_job.log`
2. 환경변수 확인: `.env` 파일
3. 데이터베이스 상태 확인: `data/bgf_sales.db`

### 롤백 필요 시
```bash
# stores.json 복원
git checkout config/stores.json

# DB 다운그레이드 (v22로)
python -c "
import sqlite3
conn = sqlite3.connect('data/bgf_sales.db')
conn.execute('UPDATE schema_version SET version = 22')
conn.commit()
conn.close()
"
```

---

**작성**: 2026-02-07
**문서 버전**: 1.0
