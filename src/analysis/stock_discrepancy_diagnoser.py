"""
재고 불일치 진단기 (Stock Discrepancy Diagnoser)

예측 시점 재고 vs 발주 시점 재고의 불일치를 진단하고 분류한다.
순수 도메인 로직 — I/O 없음.

불일치 유형:
  GHOST_STOCK     — stale RI에 재고 표시, 실재고=0 → 과소발주
  STALE_FALLBACK  — RI stale→ds 폴백(24h 이전) → 실재고와 차이
  PENDING_MISMATCH — pending stale→0 무시 → 중복발주
  OVER_ORDER      — 예측재고 < 실재고 → 과대발주(필요이상)
  UNDER_ORDER     — 예측재고 > 실재고 → 과소발주(부족)
  NONE            — 재고 변동 무시 가능 범위
"""

from typing import Dict, Optional


class StockDiscrepancyDiagnoser:
    """예측-발주 간 재고 불일치 진단"""

    # ── 임계값 ──
    STOCK_DIFF_THRESHOLD = 2       # stock 차이 이 이상이면 유의미
    PENDING_DIFF_THRESHOLD = 3     # pending 차이 이 이상이면 유의미
    HIGH_SEVERITY_THRESHOLD = 5    # 차이 >= 5이면 HIGH
    MEDIUM_SEVERITY_THRESHOLD = 2  # 차이 >= 2이면 MEDIUM, 아래는 LOW

    # ── 불일치 유형 ──
    TYPE_GHOST_STOCK = "GHOST_STOCK"
    TYPE_STALE_FALLBACK = "STALE_FALLBACK"
    TYPE_PENDING_MISMATCH = "PENDING_MISMATCH"
    TYPE_OVER_ORDER = "OVER_ORDER"
    TYPE_UNDER_ORDER = "UNDER_ORDER"
    TYPE_NONE = "NONE"

    # ── 심각도 ──
    SEVERITY_HIGH = "HIGH"
    SEVERITY_MEDIUM = "MEDIUM"
    SEVERITY_LOW = "LOW"

    @staticmethod
    def diagnose(
        stock_at_prediction: int,
        pending_at_prediction: int,
        stock_at_order: int,
        pending_at_order: int,
        stock_source: str = "",
        is_stock_stale: bool = False,
        original_order_qty: int = 0,
        recalculated_order_qty: int = 0,
    ) -> Dict[str, object]:
        """재고 불일치를 진단한다.

        Args:
            stock_at_prediction:    예측 시점 재고
            pending_at_prediction:  예측 시점 발주잔량(pending)
            stock_at_order:         발주 실행 시점 재고
            pending_at_order:       발주 실행 시점 발주잔량
            stock_source:           예측 재고 소스 ("cache"|"ri"|"ri_stale_ds"|"ri_stale_ri"|"ds")
            is_stock_stale:         예측 시점 재고가 stale 이었는지
            original_order_qty:     예측 모델 원래 발주량
            recalculated_order_qty: 재계산된 발주량

        Returns:
            {
                "discrepancy_type": str,   # TYPE_* 상수
                "severity": str,           # HIGH|MEDIUM|LOW
                "stock_diff": int,         # 발주시점 - 예측시점 재고 차이
                "pending_diff": int,       # 발주시점 - 예측시점 pending 차이
                "order_impact": int,       # 발주량 변동 (recalc - original)
                "description": str,        # 사람이 읽을 수 있는 설명
            }
        """
        stock_diff = stock_at_order - stock_at_prediction
        pending_diff = pending_at_order - pending_at_prediction
        order_impact = recalculated_order_qty - original_order_qty

        abs_stock_diff = abs(stock_diff)
        abs_pending_diff = abs(pending_diff)

        # ── 유형 분류 ──
        discrepancy_type = StockDiscrepancyDiagnoser.TYPE_NONE
        description = ""

        # 1. GHOST_STOCK: stale 소스 + 예측 재고 > 0 + 실재고 = 0
        if (is_stock_stale and stock_at_prediction > 0 and stock_at_order == 0):
            discrepancy_type = StockDiscrepancyDiagnoser.TYPE_GHOST_STOCK
            description = (
                f"유령재고: 예측시점 재고 {stock_at_prediction}"
                f"(소스:{stock_source}) → 실재고 0"
            )

        # 2. STALE_FALLBACK: stale 소스에서 폴백했으나 실재고와 차이
        elif (is_stock_stale
              and stock_source in ("ri_stale_ds", "ri_stale_ri")
              and abs_stock_diff >= StockDiscrepancyDiagnoser.STOCK_DIFF_THRESHOLD):
            discrepancy_type = StockDiscrepancyDiagnoser.TYPE_STALE_FALLBACK
            description = (
                f"stale 폴백: 소스={stock_source}, "
                f"예측재고={stock_at_prediction} → 실재고={stock_at_order} "
                f"(차이:{stock_diff:+d})"
            )

        # 3. PENDING_MISMATCH: pending 차이가 유의미
        elif abs_pending_diff >= StockDiscrepancyDiagnoser.PENDING_DIFF_THRESHOLD:
            discrepancy_type = StockDiscrepancyDiagnoser.TYPE_PENDING_MISMATCH
            description = (
                f"발주잔량 불일치: 예측시점={pending_at_prediction} → "
                f"발주시점={pending_at_order} (차이:{pending_diff:+d})"
            )

        # 4. OVER_ORDER: 예측재고 < 실재고 → 필요 이상 발주될 수 있음
        elif (stock_diff > 0
              and abs_stock_diff >= StockDiscrepancyDiagnoser.STOCK_DIFF_THRESHOLD):
            discrepancy_type = StockDiscrepancyDiagnoser.TYPE_OVER_ORDER
            description = (
                f"과대발주 위험: 실재고({stock_at_order}) > "
                f"예측재고({stock_at_prediction}), 차이:{stock_diff:+d}"
            )

        # 5. UNDER_ORDER: 예측재고 > 실재고 → 부족 발주 위험
        elif (stock_diff < 0
              and abs_stock_diff >= StockDiscrepancyDiagnoser.STOCK_DIFF_THRESHOLD):
            discrepancy_type = StockDiscrepancyDiagnoser.TYPE_UNDER_ORDER
            description = (
                f"과소발주 위험: 실재고({stock_at_order}) < "
                f"예측재고({stock_at_prediction}), 차이:{stock_diff:+d}"
            )

        # ── 심각도 ──
        max_diff = max(abs_stock_diff, abs_pending_diff)
        if discrepancy_type == StockDiscrepancyDiagnoser.TYPE_NONE:
            severity = StockDiscrepancyDiagnoser.SEVERITY_LOW
        elif max_diff >= StockDiscrepancyDiagnoser.HIGH_SEVERITY_THRESHOLD:
            severity = StockDiscrepancyDiagnoser.SEVERITY_HIGH
        elif max_diff >= StockDiscrepancyDiagnoser.MEDIUM_SEVERITY_THRESHOLD:
            severity = StockDiscrepancyDiagnoser.SEVERITY_MEDIUM
        else:
            severity = StockDiscrepancyDiagnoser.SEVERITY_LOW

        return {
            "discrepancy_type": discrepancy_type,
            "severity": severity,
            "stock_diff": stock_diff,
            "pending_diff": pending_diff,
            "order_impact": order_impact,
            "stock_at_prediction": stock_at_prediction,
            "pending_at_prediction": pending_at_prediction,
            "stock_at_order": stock_at_order,
            "pending_at_order": pending_at_order,
            "stock_source": stock_source,
            "is_stock_stale": is_stock_stale,
            "original_order_qty": original_order_qty,
            "recalculated_order_qty": recalculated_order_qty,
            "description": description,
        }

    @staticmethod
    def is_significant(diagnosis: Dict) -> bool:
        """진단 결과가 유의미한 불일치인지 판별"""
        return diagnosis.get("discrepancy_type", "NONE") != StockDiscrepancyDiagnoser.TYPE_NONE

    @staticmethod
    def summarize_discrepancies(discrepancies: list) -> Dict[str, object]:
        """여러 진단 결과를 요약

        Args:
            discrepancies: diagnose() 결과 리스트

        Returns:
            {
                "total": int,
                "significant": int,
                "by_type": {"GHOST_STOCK": N, ...},
                "by_severity": {"HIGH": N, "MEDIUM": N, "LOW": N},
                "avg_stock_diff": float,
                "avg_order_impact": float,
            }
        """
        if not discrepancies:
            return {
                "total": 0,
                "significant": 0,
                "by_type": {},
                "by_severity": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
                "avg_stock_diff": 0.0,
                "avg_order_impact": 0.0,
            }

        significant = [d for d in discrepancies
                        if d.get("discrepancy_type", "NONE") != StockDiscrepancyDiagnoser.TYPE_NONE]

        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        total_stock_diff = 0
        total_order_impact = 0

        for d in discrepancies:
            dtype = d.get("discrepancy_type", "NONE")
            sev = d.get("severity", "LOW")
            by_type[dtype] = by_type.get(dtype, 0) + 1
            by_severity[sev] = by_severity.get(sev, 0) + 1
            total_stock_diff += abs(d.get("stock_diff", 0))
            total_order_impact += abs(d.get("order_impact", 0))

        n = len(discrepancies)
        return {
            "total": n,
            "significant": len(significant),
            "by_type": by_type,
            "by_severity": by_severity,
            "avg_stock_diff": round(total_stock_diff / n, 2) if n else 0.0,
            "avg_order_impact": round(total_order_impact / n, 2) if n else 0.0,
        }
