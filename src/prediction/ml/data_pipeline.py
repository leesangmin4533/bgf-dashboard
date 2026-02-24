"""
ML 데이터 파이프라인
- daily_sales + realtime_inventory + external_factors에서 학습 데이터 추출
- 매장별 DB (stores/{id}.db)와 공통 DB (common.db) 분리 접근
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.db.store_query import store_filter
from src.utils.logger import get_logger

logger = get_logger(__name__)

# DB 경로
_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
_LEGACY_DB_PATH = str(_DATA_DIR / "bgf_sales.db")
_COMMON_DB_PATH = str(_DATA_DIR / "common.db")


class MLDataPipeline:
    """ML 학습용 데이터 파이프라인

    매장별 DB 분리 지원:
    - store_id 지정 시: 매장별 DB (daily_sales 등) + common.db (products 등)
    - db_path 명시 시: 단일 DB (테스트/레거시)
    - 둘 다 없으면: 레거시 bgf_sales.db 폴백
    """

    def __init__(self, db_path: Optional[str] = None, store_id: Optional[str] = None) -> None:
        if db_path:
            self.db_path = db_path
        elif store_id:
            try:
                from src.infrastructure.database.connection import DBRouter
                self.db_path = str(DBRouter.get_store_db_path(store_id))
            except ImportError:
                self.db_path = str(_DATA_DIR / "stores" / f"{store_id}.db")
        else:
            self.db_path = _LEGACY_DB_PATH
        self.store_id = store_id
        self._use_split_db = store_id is not None and db_path is None

    def _get_conn(self) -> sqlite3.Connection:
        """매장 DB 연결 (daily_sales, promotions 등)"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_common_conn(self) -> sqlite3.Connection:
        """공통 DB 연결 (products, product_details, external_factors)"""
        if self._use_split_db:
            try:
                from src.infrastructure.database.connection import DBRouter
                common_path = str(DBRouter.get_common_db_path())
            except ImportError:
                common_path = _COMMON_DB_PATH
        else:
            # 레거시/테스트: 매장 DB와 같은 DB (통합 DB)
            common_path = self.db_path
        conn = sqlite3.connect(common_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def extract_training_data(self, days: int = 90) -> List[Dict[str, Any]]:
        """
        학습 데이터 추출

        daily_sales 기반으로 상품별 일별 판매 데이터를 추출.
        각 행에 feature 계산에 필요한 원시 데이터를 포함.

        Args:
            days: 과거 몇 일분 데이터 추출

        Returns:
            학습 데이터 리스트
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if self._use_split_db:
                # 매장별 DB: common.db ATTACH로 products JOIN
                try:
                    from src.infrastructure.database.connection import DBRouter
                    common_path = str(DBRouter.get_common_db_path())
                except ImportError:
                    common_path = _COMMON_DB_PATH
                conn.execute(f"ATTACH DATABASE '{common_path}' AS common")
                cursor.execute("""
                    SELECT
                        ds.item_cd,
                        ds.mid_cd,
                        ds.sales_date,
                        ds.sale_qty,
                        ds.stock_qty,
                        ds.buy_qty,
                        p.item_nm
                    FROM daily_sales ds
                    JOIN common.products p ON ds.item_cd = p.item_cd
                    WHERE ds.sales_date >= date('now', ?)
                    ORDER BY ds.item_cd, ds.sales_date
                """, (f'-{days} days',))
            else:
                # 레거시 통합 DB: store_filter 적용
                sf, sp = store_filter("ds", self.store_id)
                cursor.execute(f"""
                    SELECT
                        ds.item_cd,
                        ds.mid_cd,
                        ds.sales_date,
                        ds.sale_qty,
                        ds.stock_qty,
                        ds.buy_qty,
                        p.item_nm
                    FROM daily_sales ds
                    JOIN products p ON ds.item_cd = p.item_cd
                    WHERE ds.sales_date >= date('now', ?)
                    {sf}
                    ORDER BY ds.item_cd, ds.sales_date
                """, (f'-{days} days',) + sp)

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.warning(f"학습 데이터 추출 실패: {e}")
            return []
        finally:
            conn.close()

    def get_item_daily_stats(self, item_cd: str, days: int = 90) -> List[Dict[str, Any]]:
        """
        특정 상품의 일별 통계 조회

        Args:
            item_cd: 상품코드
            days: 기간

        Returns:
            일별 판매 데이터
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if self._use_split_db:
                # 매장별 DB: store_filter 불필요
                cursor.execute("""
                    SELECT sales_date, sale_qty, stock_qty, buy_qty, mid_cd
                    FROM daily_sales
                    WHERE item_cd = ? AND sales_date >= date('now', ?)
                    ORDER BY sales_date
                """, (item_cd, f'-{days} days'))
            else:
                sf, sp = store_filter("", self.store_id)
                cursor.execute(f"""
                    SELECT sales_date, sale_qty, stock_qty, buy_qty, mid_cd
                    FROM daily_sales
                    WHERE item_cd = ? AND sales_date >= date('now', ?)
                    {sf}
                    ORDER BY sales_date
                """, (item_cd, f'-{days} days') + sp)

            raw = [dict(row) for row in cursor.fetchall()]
            return self._fill_missing_dates(raw)
        except Exception as e:
            logger.warning(f"상품 일별 통계 조회 실패 ({item_cd}): {e}")
            return []
        finally:
            conn.close()

    def get_external_factors(self, start_date: str, end_date: str) -> Dict[str, Dict[str, Any]]:
        """
        외부 요인 데이터 조회 (날짜별) — common.db에서 조회

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)

        Returns:
            {날짜: {factor_key: factor_value, ...}}
            예: {"2026-01-29": {"temperature": "5.2", "is_holiday": "true", ...}}
        """
        conn = self._get_common_conn()
        factors = {}
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT factor_date, factor_key, factor_value
                FROM external_factors
                WHERE factor_date BETWEEN ? AND ?
            """, (start_date, end_date))

            for row in cursor.fetchall():
                d = row['factor_date']
                if d not in factors:
                    factors[d] = {}
                factors[d][row['factor_key']] = row['factor_value']
        except Exception as e:
            logger.debug(f"외부 요인 조회 실패: {e}")
        finally:
            conn.close()

        return factors

    def get_promo_status(self, item_cd: str, target_date: str) -> bool:
        """
        해당 날짜에 행사 진행 중인지 확인

        Args:
            item_cd: 상품코드
            target_date: 대상 날짜 (YYYY-MM-DD)

        Returns:
            행사 여부
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if self._use_split_db:
                cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM promotions
                    WHERE item_cd = ?
                    AND start_date <= ? AND end_date >= ?
                    AND is_active = 1
                """, (item_cd, target_date, target_date))
            else:
                sf, sp = store_filter(None, self.store_id)
                cursor.execute(f"""
                    SELECT COUNT(*) as cnt
                    FROM promotions
                    WHERE item_cd = ?
                    AND start_date <= ? AND end_date >= ?
                    AND is_active = 1
                    {sf}
                """, (item_cd, target_date, target_date) + sp)

            row = cursor.fetchone()
            return row['cnt'] > 0 if row else False
        except Exception as e:
            logger.debug(f"행사 상태 조회 실패: {e}")
            return False
        finally:
            conn.close()

    def get_data_days_count(self, item_cd: str) -> int:
        """
        상품의 데이터 축적 일수

        Args:
            item_cd: 상품코드

        Returns:
            데이터 일수
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if self._use_split_db:
                cursor.execute("""
                    SELECT COUNT(DISTINCT sales_date) as cnt
                    FROM daily_sales WHERE item_cd = ?
                """, (item_cd,))
            else:
                sf, sp = store_filter("", self.store_id)
                cursor.execute(f"""
                    SELECT COUNT(DISTINCT sales_date) as cnt
                    FROM daily_sales WHERE item_cd = ?
                    {sf}
                """, (item_cd,) + sp)

            row = cursor.fetchone()
            return row['cnt'] if row else 0
        except Exception as e:
            logger.debug(f"데이터 일수 조회 실패: {e}")
            return 0
        finally:
            conn.close()

    def get_items_meta(self, item_codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        상품별 메타정보 일괄 조회 (유통기한, 이익률, 폐기율)

        Args:
            item_codes: 상품코드 리스트

        Returns:
            {item_cd: {expiration_days, margin_rate, disuse_rate}, ...}
        """
        if not item_codes:
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        placeholders = ",".join("?" for _ in item_codes)

        # 1. product_details → common.db에서 유통기한, 이익률
        common_conn = self._get_common_conn()
        try:
            cursor = common_conn.cursor()
            cursor.execute(f"""
                SELECT item_cd, expiration_days, margin_rate
                FROM product_details
                WHERE item_cd IN ({placeholders})
            """, item_codes)

            for row in cursor.fetchall():
                result[row["item_cd"]] = {
                    "expiration_days": row["expiration_days"] or 0,
                    "margin_rate": row["margin_rate"] or 0.0,
                    "disuse_rate": 0.0,
                }
        except Exception as e:
            logger.warning(f"상품 메타정보(product_details) 조회 실패: {e}")
        finally:
            common_conn.close()

        # 2. inventory_batches → 매장 DB에서 폐기율 (유통기한 역추적 기반, 정확도 높음)
        #    폐기율 = expired 배치의 remaining_qty / 전체 배치의 initial_qty
        #    fallback: inventory_batches 데이터 없으면 daily_sales.disuse_qty 사용
        store_conn = self._get_conn()
        try:
            cursor = store_conn.cursor()

            # 2-1. inventory_batches 기반 폐기율 (완료된 배치: consumed + expired)
            ib_items = set()
            try:
                if self._use_split_db:
                    cursor.execute(f"""
                        SELECT item_cd,
                               SUM(initial_qty) as total_initial,
                               SUM(CASE WHEN status = 'expired' THEN remaining_qty ELSE 0 END) as total_waste
                        FROM inventory_batches
                        WHERE item_cd IN ({placeholders})
                        AND status IN ('consumed', 'expired')
                        GROUP BY item_cd
                    """, item_codes)
                else:
                    sf, sp = store_filter("", self.store_id)
                    cursor.execute(f"""
                        SELECT item_cd,
                               SUM(initial_qty) as total_initial,
                               SUM(CASE WHEN status = 'expired' THEN remaining_qty ELSE 0 END) as total_waste
                        FROM inventory_batches
                        WHERE item_cd IN ({placeholders})
                        AND status IN ('consumed', 'expired')
                        {sf}
                        GROUP BY item_cd
                    """, item_codes + list(sp))

                for row in cursor.fetchall():
                    item_cd = row["item_cd"]
                    total_initial = row["total_initial"] or 0
                    total_waste = row["total_waste"] or 0
                    if item_cd not in result:
                        result[item_cd] = {"expiration_days": 0, "margin_rate": 0.0, "disuse_rate": 0.0}
                    result[item_cd]["disuse_rate"] = total_waste / total_initial if total_initial > 0 else 0.0
                    ib_items.add(item_cd)
            except Exception:
                # inventory_batches 테이블 미존재 시 전체 fallback
                pass

            # 2-2. fallback: inventory_batches 데이터 없는 상품은 daily_sales.disuse_qty 사용
            fallback_codes = [cd for cd in item_codes if cd not in ib_items]
            if fallback_codes:
                fb_placeholders = ",".join("?" for _ in fallback_codes)
                if self._use_split_db:
                    cursor.execute(f"""
                        SELECT item_cd,
                               SUM(sale_qty) as total_sales,
                               SUM(disuse_qty) as total_disuse
                        FROM daily_sales
                        WHERE item_cd IN ({fb_placeholders})
                        AND sales_date >= date('now', '-30 days')
                        GROUP BY item_cd
                    """, fallback_codes)
                else:
                    cursor.execute(f"""
                        SELECT item_cd,
                               SUM(sale_qty) as total_sales,
                               SUM(disuse_qty) as total_disuse
                        FROM daily_sales
                        WHERE item_cd IN ({fb_placeholders})
                        AND sales_date >= date('now', '-30 days')
                        {sf}
                        GROUP BY item_cd
                    """, fallback_codes + list(sp))

                for row in cursor.fetchall():
                    item_cd = row["item_cd"]
                    total_sales = row["total_sales"] or 0
                    total_disuse = row["total_disuse"] or 0
                    total = total_sales + total_disuse
                    if item_cd not in result:
                        result[item_cd] = {"expiration_days": 0, "margin_rate": 0.0, "disuse_rate": 0.0}
                    result[item_cd]["disuse_rate"] = total_disuse / total if total > 0 else 0.0

        except Exception as e:
            logger.warning(f"상품 메타정보(disuse_rate) 조회 실패: {e}")
        finally:
            store_conn.close()

        return result

    def get_active_items(self, min_days: int = 7) -> List[Dict[str, str]]:
        """
        활성 상품 목록 (최소 데이터 일수 충족)

        Args:
            min_days: 최소 데이터 일수

        Returns:
            [{item_cd, mid_cd, item_nm, data_days}, ...]
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if self._use_split_db:
                # 매장별 DB: common.db ATTACH로 products JOIN
                try:
                    from src.infrastructure.database.connection import DBRouter
                    common_path = str(DBRouter.get_common_db_path())
                except ImportError:
                    common_path = _COMMON_DB_PATH
                conn.execute(f"ATTACH DATABASE '{common_path}' AS common")
                cursor.execute("""
                    SELECT
                        ds.item_cd,
                        ds.mid_cd,
                        p.item_nm,
                        COUNT(DISTINCT ds.sales_date) as data_days
                    FROM daily_sales ds
                    JOIN common.products p ON ds.item_cd = p.item_cd
                    GROUP BY ds.item_cd
                    HAVING data_days >= ?
                    ORDER BY ds.mid_cd, ds.item_cd
                """, (min_days,))
            else:
                sf, sp = store_filter("ds", self.store_id)
                cursor.execute(f"""
                    SELECT
                        ds.item_cd,
                        ds.mid_cd,
                        p.item_nm,
                        COUNT(DISTINCT ds.sales_date) as data_days
                    FROM daily_sales ds
                    JOIN products p ON ds.item_cd = p.item_cd
                    WHERE 1=1
                    {sf}
                    GROUP BY ds.item_cd
                    HAVING data_days >= ?
                    ORDER BY ds.mid_cd, ds.item_cd
                """, sp + (min_days,))

            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"활성 상품 목록 조회 실패: {e}")
            return []
        finally:
            conn.close()

    def _fill_missing_dates(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        일별 판매 데이터에서 빠진 날짜(0판매일 또는 수집 실패일)를 보간.

        채움 규칙:
        - sale_qty = 0
        - stock_qty = 직전 행의 stock_qty (전일 재고 유지)
        - buy_qty = 0
        - mid_cd = 첫 행에서 상속

        Args:
            rows: sales_date 기준 오름차순 정렬된 일별 판매 데이터

        Returns:
            연속 날짜로 보간된 데이터
        """
        if len(rows) < 2:
            return rows

        existing = {r["sales_date"]: r for r in rows}
        mid_cd = rows[0].get("mid_cd", "")

        start = datetime.strptime(rows[0]["sales_date"], "%Y-%m-%d")
        end = datetime.strptime(rows[-1]["sales_date"], "%Y-%m-%d")

        filled = []
        prev_stock = 0
        cur = start
        while cur <= end:
            ds = cur.strftime("%Y-%m-%d")
            if ds in existing:
                row = existing[ds]
                prev_stock = row.get("stock_qty") or prev_stock
                filled.append(row)
            else:
                filled.append({
                    "sales_date": ds,
                    "sale_qty": 0,
                    "stock_qty": prev_stock,
                    "buy_qty": 0,
                    "mid_cd": mid_cd,
                })
            cur += timedelta(days=1)

        return filled
