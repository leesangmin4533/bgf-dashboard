"""daily_job.py Phase 모듈 분리

run_optimized()의 24개 Phase를 4개 모듈로 분리:
- collection.py: Phase 1.0~1.35 (데이터 수집)
- calibration.py: Phase 1.5~1.67 (보정/검증)
- preparation.py: Phase 1.68~1.95 (발주 준비)
- execution.py: Phase 2.0~3.0 (발주 실행)
"""
