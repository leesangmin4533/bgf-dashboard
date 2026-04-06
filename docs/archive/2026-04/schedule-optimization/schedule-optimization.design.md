# Design: schedule-optimization

## OPT-0: 정밀폐기 경량화

### daily_job.py
- `run_optimized()` 시그니처에 `collect_only: Optional[List[str]] = None` 추가
- ctx에 `"collect_only": collect_only` 전달
- `collect_only` 설정 시 calibration/preparation/execution Phase 호출 스킵

### phases/collection.py
- `_full = collect_only is None` 가드 변수 (Phase 1.04 앞에 정의)
- `_SkipPhase` 내부 예외 클래스
- Phase 1.04~1.36: `_full and collection_success` 또는 `_SkipPhase` 가드
- Phase 1.15+1.16: 가드 없음 (waste_slip 포함 시 항상 실행)
- `collect_only`에 `"sales"` 없으면 로그인만 (`_ensure_login()`) 수행

### run_scheduler.py
- `expiry_confirm_wrapper`: `collect_only=["waste_slip"]`
- `expiry_pre_collect_wrapper`: `collect_only=["sales", "waste_slip"]`

## OPT-1: 00:00+01:00 통합

### run_scheduler.py
- `consolidated_nightly_collect_wrapper()` 신규: 단일 SalesAnalyzer 로그인 → Phase A(STBJ070 발주단위) → Phase B(CallItemDetailPopup 상품상세)
- 00:00 스케줄 → 통합 wrapper, 01:00 스케줄 제거
- 기존 `order_unit_collect_wrapper`, `detail_fetch_wrapper`는 CLI용으로 보존

## OPT-2: 11:00 조건부

### run_scheduler.py
- `bulk_collect_wrapper` 내부: `get_target_items(force=False)` 사전 체크
- 대상 0건 → Selenium 생략 + 로그
- 대상 있으면 기존 `BatchCollectFlow.run()` 실행

## OPT-3: 03:00(수) 병렬화

### run_scheduler.py
- `inventory_verify_wrapper`: `_run_task` 패턴으로 전환
- `run_verification_single` (기존 함수) 매장별 병렬 호출
- 병렬 완료 후 `_write_excel` 통합 리포트 생성
