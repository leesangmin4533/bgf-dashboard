"""
연관 패턴 효과 분석 서비스

prediction_logs.association_boost 데이터를 기반으로
연관 규칙 부스트의 효과를 분석한다.

- get_status(): 데이터 수집 현황 및 분석 준비 여부
- get_analysis(): 부스트 효과 비교 분석 (MAE, 분포, 트렌드)
"""

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.infrastructure.database.connection import DBRouter, DATA_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 분석 준비 조건
READY_MIN_DAYS = 7          # 최소 기록 일수
READY_MIN_BOOST = 100       # 부스트 적용 건수
READY_MIN_NO_BOOST = 500    # 비교군 건수


class AssociationStatsService:
    """연관 패턴 효과 분석 서비스"""

    def __init__(self, store_id: Optional[str] = None):
        self.store_id = store_id

    # -----------------------------------------------------------------
    # 공개 API
    # -----------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """데이터 수집 현황 및 분석 준비 여부"""
        store_paths = self._get_target_db_paths()
        store_names = self._load_store_names()

        total_boost = 0
        total_no_boost = 0
        all_dates = set()
        stores = []

        for db_path in store_paths:
            sid = Path(db_path).stem
            row = self._query_status_single(str(db_path))
            if row is None:
                continue

            total_boost += row["boost_count"]
            total_no_boost += row["no_boost_count"]
            all_dates.update(row["dates"])

            stores.append({
                "store_id": sid,
                "name": store_names.get(sid, sid),
                "records": row["boost_count"] + row["no_boost_count"],
                "boost_count": row["boost_count"],
            })

        days = len(all_dates)
        ready = (
            days >= READY_MIN_DAYS
            and total_boost >= READY_MIN_BOOST
            and total_no_boost >= READY_MIN_NO_BOOST
        )

        return {
            "ready": ready,
            "days_collected": days,
            "boost_applied_count": total_boost,
            "no_boost_count": total_no_boost,
            "required": {
                "days": READY_MIN_DAYS,
                "boost_applied": READY_MIN_BOOST,
                "no_boost": READY_MIN_NO_BOOST,
            },
            "stores": stores,
        }

    def get_analysis(self) -> Dict[str, Any]:
        """부스트 효과 분석 결과"""
        store_paths = self._get_target_db_paths()
        store_names = self._load_store_names()

        # 매장별 MAE 수집
        all_boosted = []     # [(error, boost_val), ...]
        all_non_boosted = [] # [error, ...]
        boost_values = []    # 부스트 계수 모음
        daily_agg = defaultdict(lambda: {"boost_count": 0, "boost_sum": 0.0})
        by_store = []

        for db_path in store_paths:
            sid = Path(db_path).stem
            result = self._query_analysis_single(str(db_path))
            if result is None:
                continue

            all_boosted.extend(result["boosted_errors"])
            all_non_boosted.extend(result["non_boosted_errors"])
            boost_values.extend(result["boost_values"])

            for d, v in result["daily"].items():
                daily_agg[d]["boost_count"] += v["count"]
                daily_agg[d]["boost_sum"] += v["sum"]

            # 매장별 요약
            b_mae = (
                sum(e for e, _ in result["boosted_errors"])
                / len(result["boosted_errors"])
                if result["boosted_errors"] else 0
            )
            nb_mae = (
                sum(result["non_boosted_errors"])
                / len(result["non_boosted_errors"])
                if result["non_boosted_errors"] else 0
            )
            improvement = (
                round((1 - b_mae / nb_mae) * 100, 1)
                if nb_mae > 0 else 0
            )

            by_store.append({
                "store_id": sid,
                "name": store_names.get(sid, sid),
                "boost_count": len(result["boosted_errors"]),
                "avg_boost": (
                    round(sum(result["boost_values"]) / len(result["boost_values"]), 3)
                    if result["boost_values"] else 1.0
                ),
                "boosted_mae": round(b_mae, 2),
                "non_boosted_mae": round(nb_mae, 2),
                "improvement_pct": improvement,
            })

        # --- 전체 요약 ---
        total_b_mae = (
            sum(e for e, _ in all_boosted) / len(all_boosted)
            if all_boosted else 0
        )
        total_nb_mae = (
            sum(all_non_boosted) / len(all_non_boosted)
            if all_non_boosted else 0
        )
        total_improvement = (
            round((1 - total_b_mae / total_nb_mae) * 100, 1)
            if total_nb_mae > 0 else 0
        )

        # --- 부스트 분포 ---
        dist = self._compute_boost_distribution(boost_values)

        # --- 일별 트렌드 ---
        daily_trend = sorted([
            {
                "date": d,
                "boost_count": v["boost_count"],
                "avg_boost": round(v["boost_sum"] / v["boost_count"], 3)
                             if v["boost_count"] > 0 else 1.0,
            }
            for d, v in daily_agg.items()
        ], key=lambda x: x["date"])

        # --- 상위 연관 규칙 ---
        top_rules = self._load_top_rules(store_paths)

        return {
            "summary": {
                "boost_applied_count": len(all_boosted),
                "avg_boost_value": (
                    round(sum(boost_values) / len(boost_values), 3)
                    if boost_values else 1.0
                ),
                "boosted_mae": round(total_b_mae, 2),
                "non_boosted_mae": round(total_nb_mae, 2),
                "improvement_pct": total_improvement,
            },
            "by_store": by_store,
            "boost_distribution": dist,
            "top_rules": top_rules,
            "daily_trend": daily_trend[-30:],  # 최근 30일
        }

    # -----------------------------------------------------------------
    # 내부: 단일 DB 쿼리
    # -----------------------------------------------------------------

    def _query_status_single(self, db_path: str) -> Optional[Dict]:
        """단일 매장 DB에서 수집 현황 조회"""
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row

            # association_boost 컬럼 존재 확인
            cols = [c[1] for c in conn.execute(
                "PRAGMA table_info(prediction_logs)"
            ).fetchall()]
            if "association_boost" not in cols:
                conn.close()
                return None

            rows = conn.execute("""
                SELECT prediction_date,
                       association_boost
                FROM prediction_logs
                WHERE association_boost IS NOT NULL
            """).fetchall()
            conn.close()

            boost_count = sum(1 for r in rows if r["association_boost"] > 1.0)
            no_boost = sum(1 for r in rows if r["association_boost"] <= 1.0)
            dates = set(r["prediction_date"] for r in rows if r["prediction_date"])

            return {
                "boost_count": boost_count,
                "no_boost_count": no_boost,
                "dates": dates,
            }
        except Exception as e:
            logger.debug(f"[연관분석 대시보드] 상태 조회 실패 ({db_path}): {e}")
            return None

    def _query_analysis_single(self, db_path: str) -> Optional[Dict]:
        """단일 매장 DB에서 부스트 효과 분석 쿼리

        prediction_logs와 daily_sales를 조인하여
        부스트 적용/미적용 예측의 MAE를 비교한다.
        """
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row

            # 컬럼 확인
            cols = [c[1] for c in conn.execute(
                "PRAGMA table_info(prediction_logs)"
            ).fetchall()]
            if "association_boost" not in cols:
                conn.close()
                return None

            # order_qty 기반 MAE (실판매 대비)
            qty_col = "order_qty" if "order_qty" in cols else "adjusted_qty"

            rows = conn.execute(f"""
                SELECT
                    pl.prediction_date,
                    pl.association_boost,
                    pl.{qty_col} as pred_qty,
                    ds.sale_qty as actual_qty
                FROM prediction_logs pl
                JOIN daily_sales ds
                    ON pl.item_cd = ds.item_cd
                    AND pl.target_date = ds.sales_date
                WHERE pl.association_boost IS NOT NULL
                  AND pl.prediction_date >= date('now', '-30 days')
                  AND ds.sale_qty IS NOT NULL
            """).fetchall()
            conn.close()

            boosted_errors = []   # [(abs_error, boost_val), ...]
            non_boosted_errors = []
            boost_values = []
            daily = defaultdict(lambda: {"count": 0, "sum": 0.0})

            for r in rows:
                boost = r["association_boost"] or 1.0
                pred = r["pred_qty"] or 0
                actual = r["actual_qty"] or 0
                error = abs(pred - actual)

                if boost > 1.0:
                    boosted_errors.append((error, boost))
                    boost_values.append(boost)
                    d = r["prediction_date"]
                    if d:
                        daily[d]["count"] += 1
                        daily[d]["sum"] += boost
                else:
                    non_boosted_errors.append(error)

            return {
                "boosted_errors": boosted_errors,
                "non_boosted_errors": non_boosted_errors,
                "boost_values": boost_values,
                "daily": dict(daily),
            }
        except Exception as e:
            logger.debug(f"[연관분석 대시보드] 분석 조회 실패 ({db_path}): {e}")
            return None

    # -----------------------------------------------------------------
    # 내부: 부스트 분포 계산
    # -----------------------------------------------------------------

    @staticmethod
    def _compute_boost_distribution(values: List[float]) -> Dict[str, int]:
        """부스트 값을 구간별로 집계"""
        bins = [
            ("1.00~1.03", 1.00, 1.03),
            ("1.03~1.06", 1.03, 1.06),
            ("1.06~1.09", 1.06, 1.09),
            ("1.09~1.12", 1.09, 1.12),
            ("1.12~1.15", 1.12, 1.15),
        ]
        dist = {label: 0 for label, _, _ in bins}
        for v in values:
            for label, lo, hi in bins:
                if lo <= v < hi or (label == "1.12~1.15" and v >= 1.12):
                    dist[label] += 1
                    break
        return dist

    # -----------------------------------------------------------------
    # 내부: 상위 연관 규칙 조회
    # -----------------------------------------------------------------

    def _load_top_rules(self, db_paths: List[Path], limit: int = 10) -> List[Dict]:
        """상위 연관 규칙 (lift 기준) + 상품명 매핑"""
        # 1. 모든 매장 DB에서 규칙 수집
        all_rules = []
        for db_path in db_paths:
            try:
                conn = sqlite3.connect(str(db_path), timeout=10)

                # association_rules 테이블 확인
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()]
                if "association_rules" not in tables:
                    conn.close()
                    continue

                rows = conn.execute("""
                    SELECT item_a, item_b, rule_level, lift, confidence, support
                    FROM association_rules
                    ORDER BY lift DESC
                    LIMIT ?
                """, (limit * 2,)).fetchall()  # 중복 제거용으로 넉넉히

                for r in rows:
                    all_rules.append({
                        "item_a": r[0], "item_b": r[1],
                        "rule_level": r[2],
                        "lift": round(r[3], 2),
                        "confidence": round(r[4], 2),
                        "support": round(r[5], 3),
                    })
                conn.close()
            except Exception:
                continue

        if not all_rules:
            return []

        # 중복 제거 (item_a, item_b 기준), 상위 lift 유지
        seen = set()
        unique = []
        for r in sorted(all_rules, key=lambda x: x["lift"], reverse=True):
            key = (r["item_a"], r["item_b"])
            if key not in seen:
                seen.add(key)
                unique.append(r)
        unique = unique[:limit]

        # 2. 상품명 매핑 (common.db)
        item_codes = set()
        for r in unique:
            item_codes.add(r["item_a"])
            item_codes.add(r["item_b"])

        name_map = self._load_item_names(item_codes)

        # mid_cd 이름 (중분류)
        mid_codes = set()
        for r in unique:
            if r["rule_level"] == "mid":
                mid_codes.add(r["item_a"])
                mid_codes.add(r["item_b"])
        mid_map = self._load_mid_names(mid_codes) if mid_codes else {}

        result = []
        for r in unique:
            if r["rule_level"] == "mid":
                a_name = mid_map.get(r["item_a"], r["item_a"])
                b_name = mid_map.get(r["item_b"], r["item_b"])
            else:
                a_name = name_map.get(r["item_a"], r["item_a"])
                b_name = name_map.get(r["item_b"], r["item_b"])

            result.append({
                "antecedent": a_name,
                "consequent": b_name,
                "level": r["rule_level"],
                "lift": r["lift"],
                "confidence": r["confidence"],
                "support": r["support"],
            })

        return result

    # -----------------------------------------------------------------
    # 내부: 헬퍼
    # -----------------------------------------------------------------

    def _get_target_db_paths(self) -> List[Path]:
        """대상 매장 DB 경로 목록"""
        if self.store_id:
            p = DBRouter.get_store_db_path(self.store_id)
            return [p] if p.exists() else []
        return DBRouter.get_all_store_db_paths()

    @staticmethod
    def _load_store_names() -> Dict[str, str]:
        """common.db stores 테이블에서 매장명 조회"""
        try:
            common_path = DATA_DIR / "common.db"
            if not common_path.exists():
                return {}
            conn = sqlite3.connect(str(common_path), timeout=5)
            rows = conn.execute(
                "SELECT store_id, store_name FROM stores"
            ).fetchall()
            conn.close()
            return {str(r[0]): r[1] for r in rows}
        except Exception:
            return {}

    @staticmethod
    def _load_item_names(codes: set) -> Dict[str, str]:
        """common.db products 테이블에서 상품명 조회"""
        if not codes:
            return {}
        try:
            common_path = DATA_DIR / "common.db"
            if not common_path.exists():
                return {}
            conn = sqlite3.connect(str(common_path), timeout=5)
            placeholders = ",".join("?" for _ in codes)
            rows = conn.execute(
                f"SELECT item_cd, item_nm FROM products WHERE item_cd IN ({placeholders})",
                list(codes),
            ).fetchall()
            conn.close()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    @staticmethod
    def _load_mid_names(codes: set) -> Dict[str, str]:
        """common.db mid_categories 테이블에서 중분류명 조회"""
        if not codes:
            return {}
        try:
            common_path = DATA_DIR / "common.db"
            if not common_path.exists():
                return {}
            conn = sqlite3.connect(str(common_path), timeout=5)
            placeholders = ",".join("?" for _ in codes)
            rows = conn.execute(
                f"SELECT mid_cd, mid_nm FROM mid_categories WHERE mid_cd IN ({placeholders})",
                list(codes),
            ).fetchall()
            conn.close()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}
