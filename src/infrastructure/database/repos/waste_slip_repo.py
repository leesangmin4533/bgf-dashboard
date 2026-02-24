"""
폐기 전표 저장소 (WasteSlipRepository)

waste_slips 테이블 CRUD
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WasteSlipRepository(BaseRepository):
    """폐기 전표 저장소"""

    db_type = "store"

    def save_waste_slips(
        self,
        slips: List[Dict[str, Any]],
        store_id: Optional[str] = None,
    ) -> int:
        """폐기 전표 목록 일괄 저장

        Args:
            slips: 폐기 전표 딕셔너리 리스트
            store_id: 매장 코드

        Returns:
            저장/업데이트된 레코드 수
        """
        if not slips:
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        now = self._now()
        saved = 0

        sid = store_id or self.store_id

        try:
            for slip in slips:
                chit_ymd = slip.get("CHIT_YMD", "")
                # YYYYMMDD -> YYYY-MM-DD
                if len(chit_ymd) == 8:
                    chit_date = f"{chit_ymd[:4]}-{chit_ymd[4:6]}-{chit_ymd[6:8]}"
                else:
                    chit_date = chit_ymd

                chit_no = slip.get("CHIT_NO") or slip.get("CHIT_ID_NO", "")
                item_cnt = slip.get("ITEM_CNT") or 0
                wonga_amt = slip.get("WONGA_AMT") or 0
                maega_amt = slip.get("MAEGA_AMT") or 0

                # Decimal 타입 처리
                if isinstance(wonga_amt, dict):
                    wonga_amt = wonga_amt.get("hi", 0)
                if isinstance(maega_amt, dict):
                    maega_amt = maega_amt.get("hi", 0)

                try:
                    wonga_amt = float(wonga_amt) if wonga_amt else 0
                    maega_amt = float(maega_amt) if maega_amt else 0
                    item_cnt = int(item_cnt) if item_cnt else 0
                except (ValueError, TypeError):
                    pass

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO waste_slips
                    (store_id, chit_date, chit_no, chit_flag, chit_id,
                     chit_id_nm, item_cnt, center_cd, center_nm,
                     wonga_amt, maega_amt, nap_plan_ymd, conf_id,
                     cre_ymdhms, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        chit_date,
                        chit_no,
                        slip.get("CHIT_FLAG", ""),
                        slip.get("CHIT_ID", ""),
                        slip.get("CHIT_ID_NM", ""),
                        item_cnt,
                        slip.get("CENTER_CD", ""),
                        slip.get("CENTER_NM", ""),
                        wonga_amt,
                        maega_amt,
                        slip.get("NAP_PLAN_YMD", ""),
                        slip.get("CONF_ID", ""),
                        slip.get("CRE_YMDHMS", ""),
                        now,
                        now,
                    ),
                )
                saved += 1

            conn.commit()
            logger.info(f"[WasteSlipRepo] 저장 완료: {saved}건")
            return saved

        except Exception as e:
            conn.rollback()
            logger.error(f"[WasteSlipRepo] 저장 오류: {e}")
            raise

    def get_waste_slips(
        self,
        from_date: str,
        to_date: str,
        store_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """기간별 폐기 전표 조회

        Args:
            from_date: 시작일 (YYYY-MM-DD)
            to_date: 종료일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            폐기 전표 목록
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        sid = store_id or self.store_id

        cursor.execute(
            """
            SELECT * FROM waste_slips
            WHERE store_id = ?
              AND chit_date BETWEEN ? AND ?
            ORDER BY chit_date DESC, chit_no
            """,
            (sid, from_date, to_date),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_daily_waste_summary(
        self,
        from_date: str,
        to_date: str,
        store_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """날짜별 폐기 요약

        Returns:
            [{chit_date, slip_count, item_count, wonga_total, maega_total}, ...]
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        sid = store_id or self.store_id

        cursor.execute(
            """
            SELECT
                chit_date,
                COUNT(*) as slip_count,
                SUM(item_cnt) as item_count,
                SUM(wonga_amt) as wonga_total,
                SUM(maega_amt) as maega_total
            FROM waste_slips
            WHERE store_id = ?
              AND chit_date BETWEEN ? AND ?
            GROUP BY chit_date
            ORDER BY chit_date DESC
            """,
            (sid, from_date, to_date),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_latest_collection_date(
        self, store_id: Optional[str] = None
    ) -> Optional[str]:
        """가장 최근 수집된 전표 날짜"""
        conn = self._get_conn()
        cursor = conn.cursor()
        sid = store_id or self.store_id

        cursor.execute(
            "SELECT MAX(chit_date) FROM waste_slips WHERE store_id = ?",
            (sid,),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def get_waste_verification_data(
        self,
        target_date: str,
        store_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """특정 날짜의 검증용 폐기 데이터

        Returns:
            {date, slip_count, item_count, wonga_total, maega_total}
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        sid = store_id or self.store_id

        cursor.execute(
            """
            SELECT
                chit_date,
                COUNT(*) as slip_count,
                SUM(item_cnt) as item_count,
                SUM(wonga_amt) as wonga_total,
                SUM(maega_amt) as maega_total
            FROM waste_slips
            WHERE store_id = ?
              AND chit_date = ?
            """,
            (sid, target_date),
        )
        row = cursor.fetchone()
        if row and row["slip_count"]:
            return dict(row)
        return {
            "chit_date": target_date,
            "slip_count": 0,
            "item_count": 0,
            "wonga_total": 0,
            "maega_total": 0,
        }

    def save_verification_result(
        self,
        verification_date: str,
        slip_count: int,
        slip_item_count: int,
        daily_sales_disuse_count: int,
        gap: int,
        gap_percentage: float,
        status: str,
        details: Optional[str] = None,
        store_id: Optional[str] = None,
    ) -> int:
        """검증 결과 저장

        Args:
            verification_date: 검증 대상 날짜
            slip_count: 폐기 전표 건수
            slip_item_count: 전표 기반 폐기 품목 수
            daily_sales_disuse_count: daily_sales 기반 폐기 건수
            gap: 차이 (전표 - 매출분석)
            gap_percentage: 차이 비율 (%)
            status: 검증 상태 (OK/MISMATCH/NO_DATA)
            details: 추가 상세 정보 (JSON)
            store_id: 매장 코드

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = self._now()
        sid = store_id or self.store_id

        cursor.execute(
            """
            INSERT OR REPLACE INTO waste_verification_log
            (store_id, verification_date, slip_count, slip_item_count,
             daily_sales_disuse_count, gap, gap_percentage, status,
             details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                verification_date,
                slip_count,
                slip_item_count,
                daily_sales_disuse_count,
                gap,
                gap_percentage,
                status,
                details,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid

    def get_verification_history(
        self,
        days: int = 30,
        store_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """검증 이력 조회"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        sid = store_id or self.store_id

        cursor.execute(
            """
            SELECT * FROM waste_verification_log
            WHERE store_id = ?
            ORDER BY verification_date DESC
            LIMIT ?
            """,
            (sid, days),
        )
        return [dict(r) for r in cursor.fetchall()]

    # ================================================================
    # waste_slip_items (상세 품목) CRUD
    # ================================================================

    def _lookup_item_names(
        self, item_cds: List[str], store_id: Optional[str] = None
    ) -> Dict[str, str]:
        """상품코드 목록에 대한 정확한 상품명 일괄 조회

        조회 우선순위:
        1. common.db products 마스터
        2. order_tracking / inventory_batches (products에 없는 상품 대비)

        Args:
            item_cds: 조회할 상품코드 목록
            store_id: 매장 코드

        Returns:
            {item_cd: item_nm} 매핑
        """
        if not item_cds:
            return {}

        result = {}
        sid = store_id or self.store_id

        try:
            conn = self._get_conn_with_common()
            conn.row_factory = sqlite3.Row

            # 1) products 마스터에서 조회
            placeholders = ",".join("?" * len(item_cds))
            for row in conn.execute(
                f"SELECT item_cd, item_nm FROM common.products WHERE item_cd IN ({placeholders})",
                item_cds,
            ):
                if row["item_nm"]:
                    result[row["item_cd"]] = row["item_nm"]

            # 2) products에 없는 상품은 order_tracking/inventory_batches에서 보완
            missing = [cd for cd in item_cds if cd not in result]
            if missing and sid:
                placeholders2 = ",".join("?" * len(missing))
                for tbl in ["order_tracking", "inventory_batches"]:
                    still_missing = [cd for cd in missing if cd not in result]
                    if not still_missing:
                        break
                    ph = ",".join("?" * len(still_missing))
                    try:
                        for row in conn.execute(
                            f"""SELECT item_cd, item_nm FROM {tbl}
                                WHERE store_id = ? AND item_cd IN ({ph})
                                  AND item_nm IS NOT NULL AND item_nm != ''
                                GROUP BY item_cd""",
                            [sid] + still_missing,
                        ):
                            if row["item_nm"] and row["item_cd"] not in result:
                                result[row["item_cd"]] = row["item_nm"]
                    except Exception:
                        pass  # 테이블 미존재 등 무시

        except Exception as e:
            logger.debug(f"[WasteSlipRepo] item_nm 조회 실패 (무시): {e}")

        return result

    def save_waste_slip_items(
        self,
        items: List[Dict[str, Any]],
        store_id: Optional[str] = None,
    ) -> int:
        """폐기 전표 상세 품목 일괄 저장

        Args:
            items: 상세 품목 딕셔너리 리스트
                   (ITEM_CD, ITEM_NM, QTY, WONGA_AMT, ... from dsListType1)
            store_id: 매장 코드

        Returns:
            저장/업데이트된 레코드 수
        """
        if not items:
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        now = self._now()
        saved = 0
        sid = store_id or self.store_id

        # 일괄 item_nm 보정: 팝업 데이터보다 마스터 데이터 우선
        all_item_cds = list(set(
            (item.get("ITEM_CD") or item.get("item_cd", ""))
            for item in items
            if item.get("ITEM_CD") or item.get("item_cd")
        ))
        correct_names = self._lookup_item_names(all_item_cds, sid)

        try:
            for item in items:
                # 날짜 변환: YYYYMMDD -> YYYY-MM-DD
                chit_ymd = item.get("CHIT_YMD", "") or item.get("chit_date", "")
                if len(chit_ymd) == 8 and "-" not in chit_ymd:
                    chit_date = f"{chit_ymd[:4]}-{chit_ymd[4:6]}-{chit_ymd[6:8]}"
                else:
                    chit_date = chit_ymd

                chit_no = item.get("CHIT_NO", "") or item.get("chit_no", "")
                chit_seq = item.get("CHIT_SEQ") or item.get("chit_seq")
                item_cd = item.get("ITEM_CD", "") or item.get("item_cd", "")
                # 마스터에서 정확한 이름 우선 사용, 없으면 팝업 데이터 사용
                item_nm = correct_names.get(item_cd) or item.get("ITEM_NM") or item.get("item_nm", "")

                if not item_cd:
                    continue

                # 수량/금액 추출 + Decimal dict (hi) 처리
                qty = item.get("QTY") or item.get("qty", 0)
                wonga_price = item.get("WONGA_PRICE") or item.get("wonga_price", 0)
                wonga_amt = item.get("WONGA_AMT") or item.get("wonga_amt", 0)
                maega_price = item.get("MAEGA_PRICE") or item.get("maega_price", 0)
                maega_amt = item.get("MAEGA_AMT") or item.get("maega_amt", 0)

                # Decimal dict {hi: value} -> value 변환
                qty = qty.get("hi", 0) if isinstance(qty, dict) else qty
                wonga_price = wonga_price.get("hi", 0) if isinstance(wonga_price, dict) else wonga_price
                wonga_amt = wonga_amt.get("hi", 0) if isinstance(wonga_amt, dict) else wonga_amt
                maega_price = maega_price.get("hi", 0) if isinstance(maega_price, dict) else maega_price
                maega_amt = maega_amt.get("hi", 0) if isinstance(maega_amt, dict) else maega_amt

                try:
                    qty = int(qty) if qty else 0
                    wonga_price = float(wonga_price) if wonga_price else 0
                    wonga_amt = float(wonga_amt) if wonga_amt else 0
                    maega_price = float(maega_price) if maega_price else 0
                    maega_amt = float(maega_amt) if maega_amt else 0
                except (ValueError, TypeError):
                    pass

                large_cd = item.get("LARGE_CD") or item.get("large_cd", "")
                large_nm = item.get("LARGE_NM") or item.get("large_nm", "")
                cust_nm = item.get("CUST_NM") or item.get("cust_nm", "")
                center_nm = item.get("CENTER_NM") or item.get("center_nm", "")

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO waste_slip_items
                    (store_id, chit_date, chit_no, chit_seq, item_cd,
                     item_nm, large_cd, large_nm, qty,
                     wonga_price, wonga_amt, maega_price, maega_amt,
                     cust_nm, center_nm, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        chit_date,
                        chit_no,
                        chit_seq,
                        item_cd,
                        item_nm,
                        large_cd,
                        large_nm,
                        qty,
                        wonga_price,
                        wonga_amt,
                        maega_price,
                        maega_amt,
                        cust_nm,
                        center_nm,
                        now,
                        now,
                    ),
                )
                saved += 1

            conn.commit()
            logger.info(f"[WasteSlipRepo] 상세 품목 저장: {saved}건")
            return saved

        except Exception as e:
            conn.rollback()
            logger.error(f"[WasteSlipRepo] 상세 품목 저장 오류: {e}")
            raise

    def get_waste_slip_items(
        self,
        target_date: str,
        store_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """날짜별 상세 품목 조회

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            상세 품목 목록
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        sid = store_id or self.store_id

        cursor.execute(
            """
            SELECT * FROM waste_slip_items
            WHERE store_id = ?
              AND chit_date = ?
            ORDER BY chit_no, chit_seq
            """,
            (sid, target_date),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_waste_slip_items_summary(
        self,
        target_date: str,
        store_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """날짜별 상세 품목 요약 (상품코드별 합산, 검증용)

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            [{item_cd, item_nm, total_qty, total_wonga, total_maega, large_nm, slip_count}, ...]
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        sid = store_id or self.store_id

        cursor.execute(
            """
            SELECT
                item_cd,
                item_nm,
                SUM(qty) as total_qty,
                SUM(wonga_amt) as total_wonga,
                SUM(maega_amt) as total_maega,
                large_nm,
                COUNT(DISTINCT chit_no) as slip_count
            FROM waste_slip_items
            WHERE store_id = ?
              AND chit_date = ?
            GROUP BY item_cd
            ORDER BY item_nm
            """,
            (sid, target_date),
        )
        return [dict(r) for r in cursor.fetchall()]
