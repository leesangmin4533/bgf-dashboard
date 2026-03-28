"""
SkipReason — eval_outcomes.skip_reason 컬럼에 저장되는 코드 정의

실제 SKIP/PASS 흐름:
  사전 필터: is_available=0 → SKIP_UNAVAILABLE
  PreOrderEvaluator: 재고충분+저인기 → SKIP_LOW_POPULARITY
  PASS 판정: stock >= rop → PASS_STOCK_SUFFICIENT
"""


class SkipReason:
    """eval_outcomes.skip_reason 컬럼에 저장되는 코드"""

    # ── PreOrderEvaluator 내부 SKIP ─────────────────────
    SKIP_LOW_POPULARITY = "SKIP_LOW_POPULARITY"
    # 재고충분(exposure_days >= threshold) + 저인기(popularity_level == "low")
    # skip_detail: {"exposure_days", "threshold_sufficient", "popularity_level"}

    # ── 사전 필터 SKIP ──────────────────────────────────
    SKIP_UNAVAILABLE = "SKIP_UNAVAILABLE"
    # is_available=0 (BGF 사이트 취급안함)
    # skip_detail: {"is_available", "current_stock", "sales_7d", "last_sale_date"}
    # sales_7d > 0 이면 위험 케이스 (판매 중인데 발주 제외)

    # ── PASS 판정 (발주 불필요) ──────────────────────────
    PASS_STOCK_SUFFICIENT = "PASS_STOCK_SUFFICIENT"
    # 재고 충분으로 PASS
    # skip_detail: {"current_stock", "rop"}

    PASS_DATA_INSUFFICIENT = "PASS_DATA_INSUFFICIENT"
    # 판매 데이터 부족
    # skip_detail: {"sales_days", "required_days"}
