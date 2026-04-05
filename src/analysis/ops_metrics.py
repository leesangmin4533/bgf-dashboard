"""운영 지표 수집 -- 5개 지표 DB 조회

DB 조회만 수행, 판정 없음. dict 반환.
각 메서드에서 데이터 일수가 7일 미만이면 {"insufficient_data": True} 반환.
"""

from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)

_MIN_DATA_DAYS = 7  # 최소 데이터 일수


class OpsMetrics:
    """매장별 운영 지표 수집"""

    def __init__(self, store_id: str):
        self.store_id = store_id

    def collect_all(self) -> dict:
        """5개 지표 전부 수집 -> dict 반환"""
        return {
            "prediction_accuracy": self._prediction_accuracy(),
            "order_failure": self._order_failure(),
            "waste_rate": self._waste_rate(),
            "collection_failure": self._collection_failure(),
            "integrity_unresolved": self._integrity_unresolved(),
        }

    def _prediction_accuracy(self) -> dict:
        """eval_outcomes에서 카테고리별 7d/14d MAE 집계"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()

            # 데이터 일수 확인
            cursor.execute(
                "SELECT COUNT(DISTINCT eval_date) FROM eval_outcomes "
                "WHERE eval_date >= date('now', '-14 days')"
            )
            data_days = cursor.fetchone()[0]
            if data_days < _MIN_DATA_DAYS:
                return {"insufficient_data": True}

            # 카테고리별 7d MAE
            cursor.execute("""
                SELECT mid_cd,
                       AVG(ABS(COALESCE(predicted_qty, 0) - COALESCE(actual_sold_qty, 0))) as mae_7d
                FROM eval_outcomes
                WHERE eval_date >= date('now', '-7 days')
                  AND predicted_qty IS NOT NULL
                  AND actual_sold_qty IS NOT NULL
                GROUP BY mid_cd
            """)
            mae_7d_map = {row["mid_cd"]: row["mae_7d"] for row in cursor.fetchall()}

            # 카테고리별 14d MAE
            cursor.execute("""
                SELECT mid_cd,
                       AVG(ABS(COALESCE(predicted_qty, 0) - COALESCE(actual_sold_qty, 0))) as mae_14d
                FROM eval_outcomes
                WHERE eval_date >= date('now', '-14 days')
                  AND predicted_qty IS NOT NULL
                  AND actual_sold_qty IS NOT NULL
                GROUP BY mid_cd
            """)
            mae_14d_map = {row["mid_cd"]: row["mae_14d"] for row in cursor.fetchall()}

            # 합치기
            all_mids = set(mae_7d_map) | set(mae_14d_map)
            categories = []
            for mid_cd in sorted(all_mids):
                categories.append({
                    "mid_cd": mid_cd,
                    "mae_7d": mae_7d_map.get(mid_cd, 0),
                    "mae_14d": mae_14d_map.get(mid_cd, 0),
                })

            return {"categories": categories}
        except Exception as e:
            logger.warning(f"[OpsMetrics] {self.store_id} prediction_accuracy 실패: {e}")
            return {"insufficient_data": True}
        finally:
            conn.close()

    def _order_failure(self) -> dict:
        """order_fail_reasons에서 최근 7d vs 이전 7d 실패건수"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()

            # 데이터 일수 확인
            cursor.execute(
                "SELECT COUNT(DISTINCT eval_date) FROM order_fail_reasons "
                "WHERE eval_date >= date('now', '-14 days')"
            )
            data_days = cursor.fetchone()[0]
            if data_days < _MIN_DATA_DAYS:
                return {"insufficient_data": True}

            # 최근 7일 실패 건수
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM order_fail_reasons
                WHERE eval_date >= date('now', '-7 days')
            """)
            recent_7d = cursor.fetchone()["cnt"]

            # 이전 7일 실패 건수 (7~14일 전)
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM order_fail_reasons
                WHERE eval_date >= date('now', '-14 days')
                  AND eval_date < date('now', '-7 days')
            """)
            prev_7d = cursor.fetchone()["cnt"]

            # 최근 7일 총 발주 건수 (마일스톤 K3 계산용)
            cursor.execute("""
                SELECT COUNT(DISTINCT item_cd) as cnt
                FROM order_history
                WHERE order_date >= date('now', '-7 days')
            """)
            total_7d = cursor.fetchone()["cnt"]

            return {"recent_7d": recent_7d, "prev_7d": prev_7d, "total_order_7d": total_7d}
        except Exception as e:
            logger.warning(f"[OpsMetrics] {self.store_id} order_failure 실패: {e}")
            return {"insufficient_data": True}
        finally:
            conn.close()

    def _waste_rate(self) -> dict:
        """waste_slip_items + daily_sales에서 카테고리별 폐기율"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()

            # 데이터 일수 확인
            cursor.execute(
                "SELECT COUNT(DISTINCT sales_date) FROM daily_sales "
                "WHERE sales_date >= date('now', '-30 days')"
            )
            data_days = cursor.fetchone()[0]
            if data_days < _MIN_DATA_DAYS:
                return {"insufficient_data": True}

            # 7d 폐기율: waste_slip_items 합계 / daily_sales 합계
            cursor.execute("""
                SELECT mid_cd,
                       SUM(CASE WHEN chit_date >= date('now', '-7 days') THEN qty ELSE 0 END) as waste_7d,
                       SUM(qty) as waste_30d
                FROM waste_slip_items
                WHERE chit_date >= date('now', '-30 days')
                GROUP BY mid_cd
            """)
            waste_map = {}
            for row in cursor.fetchall():
                waste_map[row["mid_cd"]] = {
                    "waste_7d": row["waste_7d"] or 0,
                    "waste_30d": row["waste_30d"] or 0,
                }

            # 카테고리별 판매량
            cursor.execute("""
                SELECT mid_cd,
                       SUM(CASE WHEN sales_date >= date('now', '-7 days') THEN sale_qty ELSE 0 END) as sales_7d,
                       SUM(sale_qty) as sales_30d
                FROM daily_sales
                WHERE sales_date >= date('now', '-30 days')
                GROUP BY mid_cd
            """)
            sales_map = {}
            for row in cursor.fetchall():
                sales_map[row["mid_cd"]] = {
                    "sales_7d": row["sales_7d"] or 0,
                    "sales_30d": row["sales_30d"] or 0,
                }

            # 폐기율 계산: 폐기수량 / (판매수량 + 폐기수량)
            all_mids = set(waste_map) & set(sales_map)
            categories = []
            for mid_cd in sorted(all_mids):
                w = waste_map[mid_cd]
                s = sales_map[mid_cd]

                total_7d = s["sales_7d"] + w["waste_7d"]
                total_30d = s["sales_30d"] + w["waste_30d"]

                rate_7d = w["waste_7d"] / total_7d if total_7d > 0 else 0
                rate_30d = w["waste_30d"] / total_30d if total_30d > 0 else 0

                if rate_30d > 0:  # 30일 폐기율이 0이면 비교 의미 없음
                    categories.append({
                        "mid_cd": mid_cd,
                        "rate_7d": rate_7d,
                        "rate_30d": rate_30d,
                    })

            return {"categories": categories}
        except Exception as e:
            logger.warning(f"[OpsMetrics] {self.store_id} waste_rate 실패: {e}")
            return {"insufficient_data": True}
        finally:
            conn.close()

    def _collection_failure(self) -> dict:
        """collection_logs에서 수집 유형별 연속 실패일수"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()

            # 최근 7일 수집 로그 조회 (날짜별 + 상태)
            cursor.execute("""
                SELECT date(collected_at) as collect_date, status
                FROM collection_logs
                WHERE collected_at >= date('now', '-7 days')
                ORDER BY collected_at DESC
            """)
            rows = cursor.fetchall()

            if not rows:
                return {"insufficient_data": True}

            # 연속 실패일수 계산 (최근부터 역순)
            # collection_logs에는 collect_type이 없으므로 전체를 하나의 유형으로 취급
            consecutive_fails = 0
            seen_dates = set()
            for row in rows:
                d = row["collect_date"]
                if d in seen_dates:
                    continue
                seen_dates.add(d)
                if row["status"] != "success":
                    consecutive_fails += 1
                else:
                    break  # 성공 발견 시 연속 카운트 중단

            return {
                "types": [{"type": "sales", "consecutive_fails": consecutive_fails}]
            }
        except Exception as e:
            logger.warning(f"[OpsMetrics] {self.store_id} collection_failure 실패: {e}")
            return {"insufficient_data": True}
        finally:
            conn.close()

    def _integrity_unresolved(self) -> dict:
        """integrity_checks에서 check_name별 연속 anomaly일수"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()

            # check_name별 최근 데이터 조회
            cursor.execute("""
                SELECT check_name, check_date, anomaly_count
                FROM integrity_checks
                WHERE check_date >= date('now', '-30 days')
                ORDER BY check_name, check_date DESC
            """)
            rows = cursor.fetchall()

            if not rows:
                return {"insufficient_data": True}

            # check_name별 연속 anomaly 일수 계산
            from collections import defaultdict
            by_name = defaultdict(list)
            for row in rows:
                by_name[row["check_name"]].append({
                    "date": row["check_date"],
                    "anomaly_count": row["anomaly_count"],
                })

            checks = []
            for name, entries in by_name.items():
                # 최근 날짜부터 연속 anomaly > 0 카운트
                consecutive = 0
                for entry in entries:  # 이미 DESC 정렬
                    if entry["anomaly_count"] > 0:
                        consecutive += 1
                    else:
                        break
                checks.append({
                    "name": name,
                    "consecutive_days": consecutive,
                })

            return {"checks": checks}
        except Exception as e:
            logger.warning(f"[OpsMetrics] {self.store_id} integrity_unresolved 실패: {e}")
            return {"insufficient_data": True}
        finally:
            conn.close()
