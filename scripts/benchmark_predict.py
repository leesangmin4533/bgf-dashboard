"""
예측 파이프라인 벤치마크 + cProfile 프로파일링

Usage:
    python scripts/benchmark_predict.py              # 프로파일링 (캐시 ON)
    python scripts/benchmark_predict.py --compare     # 캐시 ON/OFF 비교
"""
import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 로깅 레벨을 WARNING으로 올려서 출력 최소화
import logging
logging.getLogger().setLevel(logging.WARNING)

from src.prediction.improved_predictor import ImprovedPredictor

STORE_ID = "46513"


def get_item_codes(predictor):
    """예측 대상 상품코드 조회"""
    conn = predictor._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT item_cd
            FROM daily_sales
            WHERE store_id = ?
            AND sales_date >= date('now', '-14 days')
            AND sale_qty > 0
        """, (STORE_ID,))
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def run_profile():
    """cProfile로 predict_batch 프로파일링"""
    predictor = ImprovedPredictor(store_id=STORE_ID)
    item_codes = get_item_codes(predictor)
    print(f"상품 수: {len(item_codes)}개\n")

    pr = cProfile.Profile()
    pr.enable()

    t0 = time.perf_counter()
    results = predictor.predict_batch(item_codes)
    elapsed = time.perf_counter() - t0

    pr.disable()

    print(f"predict_batch 완료: {elapsed:.2f}초, {len(results)}개 예측\n")

    # cumulative 기준 상위 40개
    print("=" * 80)
    print("CUMULATIVE TIME (상위 40)")
    print("=" * 80)
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
    ps.print_stats(40)
    print(s.getvalue())

    # tottime 기준 상위 30개 (실제 CPU 사용)
    print("=" * 80)
    print("TOTAL TIME (실제 CPU 사용, 상위 30)")
    print("=" * 80)
    s2 = io.StringIO()
    ps2 = pstats.Stats(pr, stream=s2).sort_stats('tottime')
    ps2.print_stats(30)
    print(s2.getvalue())

    # 핵심 함수별 시간 요약
    print("=" * 80)
    print("핵심 함수 필터 (predict/ml/db/sql/feature)")
    print("=" * 80)
    s3 = io.StringIO()
    ps3 = pstats.Stats(pr, stream=s3).sort_stats('cumulative')
    ps3.print_stats(
        r'predict|ml|ensemble|feature|daily_stats|get_item|'
        r'batch|_get_conn|execute|fetchall|_fill_missing|'
        r'_compute_safety|_apply_ml|wma|build_features|'
        r'get_order_unit|_get_product|_get_connection|'
        r'coefficient|calibrat|disuse|weekday|weather'
    )
    print(s3.getvalue())


def run_compare():
    """캐시 ON/OFF 비교 (이전 벤치마크)"""
    predictor = ImprovedPredictor(store_id=STORE_ID)
    item_codes = get_item_codes(predictor)
    print(f"상품 수: {len(item_codes)}개\n")

    # 캐시 OFF
    print("[1] 캐시 OFF (개별 DB 쿼리) 실행중...")
    p1 = ImprovedPredictor(store_id=STORE_ID)
    p1._daily_stats_cache = {}
    p1._load_receiving_stats_cache()
    p1._load_ot_pending_cache()
    p1._load_demand_pattern_cache(item_codes)
    p1._load_food_coef_cache(item_codes)
    p1._load_group_context_caches()
    sub = p1._get_substitution_detector()
    if sub:
        try:
            sub.preload(item_codes)
        except Exception:
            pass

    t0 = time.perf_counter()
    r1 = [r for cd in item_codes if (r := p1.predict(cd))]
    e1 = time.perf_counter() - t0
    print(f"    완료: {e1:.2f}초, {len(r1)}개")

    # 캐시 ON
    print("[2] 캐시 ON (배치 DB 쿼리) 실행중...")
    p2 = ImprovedPredictor(store_id=STORE_ID)
    t0 = time.perf_counter()
    r2 = p2.predict_batch(item_codes)
    e2 = time.perf_counter() - t0
    print(f"    완료: {e2:.2f}초, {len(r2)}개")

    print(f"\n캐시 OFF: {e1:.2f}초 | 캐시 ON: {e2:.2f}초")
    print(f"속도 향상: {e1/e2:.1f}x ({e1-e2:.1f}초 절약)")


if __name__ == "__main__":
    if "--compare" in sys.argv:
        run_compare()
    else:
        run_profile()
