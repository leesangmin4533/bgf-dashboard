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
        """매장별 지표 전부 수집 -> dict 반환

        2026-04-08: false_consumed (#6) 추가 — BatchSync 가드 우회 감지
        """
        return {
            "prediction_accuracy": self._prediction_accuracy(),
            "order_failure": self._order_failure(),
            "waste_rate": self._waste_rate(),
            "collection_failure": self._collection_failure(),
            "integrity_unresolved": self._integrity_unresolved(),
            "false_consumed": self._false_consumed_post_guard(),
        }

    @staticmethod
    def collect_system() -> dict:
        """시스템 전역 지표 (매장 무관) -> dict 반환

        2026-04-08: verification_log_files (#7) 추가
        — 매장별 검증 로그 파일 분리 로직 회귀 감지
        """
        from datetime import date, timedelta
        from pathlib import Path
        from src.settings.store_context import StoreContext

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        log_dir = Path("data/logs")
        try:
            active_store_ids = [c.store_id for c in StoreContext.get_all_active()]
        except Exception as e:
            logger.warning(f"[OpsMetrics] active stores 조회 실패: {e}")
            return {"verification_log_files": {"insufficient_data": True}}

        expected = len(active_store_ids)
        missing: list[str] = []
        for sid in active_store_ids:
            fname = f"waste_verification_{sid}_{yesterday}.txt"
            if not (log_dir / fname).exists():
                missing.append(sid)

        return {
            "verification_log_files": {
                "expected_count": expected,
                "missing_count": len(missing),
                "missing_stores": missing,
                "yesterday": yesterday,
            }
        }

    def _false_consumed_post_guard(self) -> dict:
        """가드 우회 감지: 만료 24h 이내 시점에 consumed 마킹된 단기유통기한 배치

        2026-04-08 도입 (BatchSync FR-02 우회 사건 후속).
        - 슬라이딩 윈도우: 최근 24h 내 updated_at
        - expiration_days <= 7 (백필 노이즈 차단)
        - julianday(expiry_date) - julianday(updated_at) < 1.0 (가드 위반의 SQL 정의 그대로)
        """
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) AS cnt,
                       MAX(updated_at) AS latest_at,
                       GROUP_CONCAT(item_cd, ',') AS sample_items
                FROM inventory_batches
                WHERE store_id = ?
                  AND status = 'consumed'
                  AND COALESCE(expiration_days, 999) <= 7
                  AND updated_at >= datetime('now', '-24 hours')
                  AND expiry_date IS NOT NULL
                  AND julianday(expiry_date) - julianday(updated_at) < 1.0
                """,
                (self.store_id,),
            )
            row = cursor.fetchone()
            if not row or (row[0] or 0) == 0:
                return {"cnt": 0}
            return {
                "cnt": int(row[0]),
                "latest_at": row[1],
                "sample_items": row[2] or "",
            }
        except Exception as e:
            logger.warning(
                f"[OpsMetrics] {self.store_id} false_consumed_post_guard 실패: {e}"
            )
            return {"cnt": 0}  # 실패는 정상 취급(과알림 방지)
        finally:
            conn.close()

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
        """waste_slip_items + daily_sales에서 카테고리별 폐기율

        waste_slip_items에는 mid_cd 컬럼이 없으므로 common.products JOIN으로 도출.
        (ops-metrics-waste-query-fix, 2026-04-07)
        """
        conn = DBRouter.get_store_connection_with_common(self.store_id)
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

            # 7d/30d 폐기 집계: waste_slip_items + products JOIN으로 mid_cd 도출
            cursor.execute("""
                SELECT p.mid_cd,
                       SUM(CASE WHEN wsi.chit_date >= date('now', '-7 days') THEN wsi.qty ELSE 0 END) as waste_7d,
                       SUM(wsi.qty) as waste_30d
                FROM waste_slip_items wsi
                JOIN common.products p ON wsi.item_cd = p.item_cd
                WHERE wsi.chit_date >= date('now', '-30 days')
                GROUP BY p.mid_cd
            """)
            waste_map = {}
            for row in cursor.fetchall():
                waste_map[row["mid_cd"]] = {
                    "waste_7d": row["waste_7d"] or 0,
                    "waste_30d": row["waste_30d"] or 0,
                }

            # 매칭률 경고: products에 없는 item_cd 비율 (신제품 동기화 모니터링)
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN p.item_cd IS NULL THEN wsi.qty ELSE 0 END) as unmatched_qty,
                    SUM(wsi.qty) as total_qty
                FROM waste_slip_items wsi
                LEFT JOIN common.products p ON wsi.item_cd = p.item_cd
                WHERE wsi.chit_date >= date('now', '-30 days')
            """)
            row = cursor.fetchone()
            total_qty = row["total_qty"] or 0
            unmatched_qty = row["unmatched_qty"] or 0
            if total_qty > 0 and unmatched_qty / total_qty > 0.05:
                logger.warning(
                    f"[OpsMetrics] {self.store_id} waste_rate products 미매칭 "
                    f"{unmatched_qty}/{total_qty} ({100*unmatched_qty/total_qty:.1f}%) "
                    f"— 신제품 products 동기화 확인 필요"
                )

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
