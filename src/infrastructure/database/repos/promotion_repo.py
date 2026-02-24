"""
PromotionRepository — 행사 정보 저장소

원본: src/db/repository.py PromotionRepository 클래스에서 1:1 추출
"""

import calendar
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PromotionRepository(BaseRepository):
    """행사 정보 저장소 (1+1, 2+1 등)"""

    db_type = "store"

    def save_monthly_promo(
        self,
        item_cd: str,
        item_nm: str,
        current_month_promo: str,
        next_month_promo: str,
        store_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        월별 행사 정보 저장

        당월/익월 행사 정보를 promotions 테이블에 저장하고,
        당월→익월 변경 시 promotion_changes에 기록

        Args:
            item_cd: 상품코드
            item_nm: 상품명
            current_month_promo: 당월 행사 ('1+1', '2+1', '')
            next_month_promo: 익월 행사 ('1+1', '2+1', '')
            store_id: 매장 코드

        Returns:
            {'saved': bool, 'change_detected': bool, 'change_type': str}
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = self._now()

        result = {'saved': False, 'change_detected': False, 'change_type': None}

        try:
            today = datetime.now()
            last_day = calendar.monthrange(today.year, today.month)[1]

            # 당월: 반월 분할 (1~15 / 16~말일)
            if today.day <= 15:
                current_month_start = today.strftime('%Y-%m-01')
                current_month_end = today.strftime('%Y-%m-15')
            else:
                current_month_start = today.strftime('%Y-%m-16')
                current_month_end = today.strftime(f'%Y-%m-{last_day:02d}')

            # 익월: 전체 월 (진입 시 반월로 세분화됨)
            if today.month == 12:
                next_month_first = today.replace(year=today.year + 1, month=1, day=1)
            else:
                next_month_first = today.replace(month=today.month + 1, day=1)
            next_month_start = next_month_first.strftime('%Y-%m-%d')
            next_last_day = calendar.monthrange(next_month_first.year, next_month_first.month)[1]
            next_month_end = next_month_first.strftime(f'%Y-%m-{next_last_day:02d}')

            # 당월 행사 저장
            if current_month_promo:
                cursor.execute(
                    """
                    INSERT INTO promotions
                    (item_cd, item_nm, promo_type, start_date, end_date, is_active, store_id, collected_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(store_id, item_cd, promo_type, start_date) DO UPDATE SET
                        item_nm = excluded.item_nm,
                        end_date = excluded.end_date,
                        is_active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (item_cd, item_nm, current_month_promo,
                     current_month_start, current_month_end, store_id, now, now)
                )
                result['saved'] = True

            # 익월 행사 저장
            if next_month_promo:
                cursor.execute(
                    """
                    INSERT INTO promotions
                    (item_cd, item_nm, promo_type, start_date, end_date, is_active, store_id, collected_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(store_id, item_cd, promo_type, start_date) DO UPDATE SET
                        item_nm = excluded.item_nm,
                        end_date = excluded.end_date,
                        is_active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (item_cd, item_nm, next_month_promo,
                     next_month_start, next_month_end, store_id, now, now)
                )
                result['saved'] = True

            # 당월→익월 행사 변경 감지
            if current_month_promo != next_month_promo:
                result['change_detected'] = True

                if current_month_promo and not next_month_promo:
                    change_type = 'end'
                elif not current_month_promo and next_month_promo:
                    change_type = 'start'
                else:
                    change_type = 'change'

                result['change_type'] = change_type

                # promotion_changes에 기록
                cursor.execute(
                    """
                    INSERT INTO promotion_changes
                    (item_cd, item_nm, change_type, change_date,
                     prev_promo_type, next_promo_type, store_id, detected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, item_cd, change_date, change_type) DO UPDATE SET
                        item_nm = excluded.item_nm,
                        prev_promo_type = excluded.prev_promo_type,
                        next_promo_type = excluded.next_promo_type,
                        detected_at = excluded.detected_at
                    """,
                    (item_cd, item_nm, change_type, next_month_start,
                     current_month_promo or None, next_month_promo or None, store_id, now)
                )

            conn.commit()

            # product_details에도 행사 정보 업데이트 (common DB에 별도 접근)
            promo_type = current_month_promo or next_month_promo or None
            if promo_type:
                common_conn = DBRouter.get_common_connection() if not self._db_path else conn
                try:
                    common_conn.execute(
                        """
                        UPDATE product_details
                        SET promo_type = ?,
                            promo_start = ?,
                            promo_end = ?,
                            promo_updated = ?
                        WHERE item_cd = ?
                        """,
                        (current_month_promo or None,
                         current_month_start if current_month_promo else next_month_start,
                         current_month_end if current_month_promo else next_month_end,
                         now, item_cd)
                    )
                    common_conn.commit()
                except Exception as e:
                    logger.warning(f"product_details 행사 업데이트 실패 (비치명적): {e}")
                finally:
                    if common_conn is not conn:
                        common_conn.close()

            return result

        except Exception as e:
            conn.rollback()
            logger.error(f"행사 정보 저장 실패: {e}")
            return result

        finally:
            conn.close()

    def get_active_promo(self, item_cd: str, store_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        상품의 현재 활성 행사 조회

        Args:
            item_cd: 상품코드
            store_id: 매장 코드

        Returns:
            {'promo_type': '1+1', 'start_date': '...', 'end_date': '...'} 또는 None
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            today = datetime.now().strftime('%Y-%m-%d')
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"""
                SELECT promo_type, start_date, end_date, item_nm
                FROM promotions
                WHERE item_cd = ?
                  AND start_date <= ?
                  AND end_date >= ?
                  AND is_active = 1 {sf}
                ORDER BY start_date DESC
                LIMIT 1
                """,
                (item_cd, today, today) + sp
            )

            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def get_next_promo(self, item_cd: str, store_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        상품의 다음 예정 행사 조회

        Args:
            item_cd: 상품코드
            store_id: 매장 코드

        Returns:
            {'promo_type': '2+1', 'start_date': '...', 'end_date': '...'} 또는 None
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            today = datetime.now().strftime('%Y-%m-%d')
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"""
                SELECT promo_type, start_date, end_date, item_nm
                FROM promotions
                WHERE item_cd = ?
                  AND start_date > ?
                  AND is_active = 1 {sf}
                ORDER BY start_date ASC
                LIMIT 1
                """,
                (item_cd, today) + sp
            )

            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def get_monthly_comparison(self, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        당월→익월 행사 변경 비교 조회

        Args:
            store_id: 매장 코드

        Returns:
            변경 예정 상품 리스트
            [{'item_cd': str, 'item_nm': str, 'change_type': str,
              'prev_promo_type': str, 'next_promo_type': str, ...}, ...]
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            today = datetime.now()
            if today.month == 12:
                next_month_start = today.replace(year=today.year + 1, month=1, day=1).strftime('%Y-%m-%d')
            else:
                next_month_start = today.replace(month=today.month + 1, day=1).strftime('%Y-%m-%d')

            cursor.execute(
                f"""
                SELECT item_cd, item_nm, change_type, change_date,
                       prev_promo_type, next_promo_type, detected_at
                FROM promotion_changes
                WHERE change_date = ? {sf}
                ORDER BY change_type, item_nm
                """,
                (next_month_start,) + sp
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_all_active_promotions(self, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        현재 진행 중인 모든 행사 조회

        Args:
            store_id: 매장 코드

        Returns:
            행사 목록
        """
        conn = self._get_conn_with_common()
        try:
            cursor = conn.cursor()
            today = datetime.now().strftime('%Y-%m-%d')
            sf, sp = self._store_filter("p", store_id)

            # products는 common DB에 있으므로 ATTACH된 common. 접두사 사용
            p_prefix = "common." if (self.db_type == "store" and self.store_id and not self._db_path) else ""

            cursor.execute(
                f"""
                SELECT p.item_cd, p.item_nm, p.promo_type, p.start_date, p.end_date,
                       pr.mid_cd
                FROM promotions p
                LEFT JOIN {p_prefix}products pr ON p.item_cd = pr.item_cd
                WHERE p.start_date <= ?
                  AND p.end_date >= ?
                  AND p.is_active = 1 {sf}
                ORDER BY p.promo_type, p.item_nm
                """,
                (today, today) + sp
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_promo_summary(self, store_id: Optional[str] = None) -> Dict[str, Any]:
        """
        행사 현황 요약

        Args:
            store_id: 매장 코드

        Returns:
            {'total_active': N, 'by_type': {'1+1': N, '2+1': N},
             'ending_soon': N, 'changes_next_month': N}
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            today = datetime.now().strftime('%Y-%m-%d')
            sf, sp = self._store_filter(None, store_id)

            # 현재 활성 행사 수
            cursor.execute(
                f"""
                SELECT promo_type, COUNT(*) as cnt
                FROM promotions
                WHERE start_date <= ? AND end_date >= ? AND is_active = 1 {sf}
                GROUP BY promo_type
                """,
                (today, today) + sp
            )
            by_type = {}
            total_active = 0
            for row in cursor.fetchall():
                by_type[row['promo_type']] = row['cnt']
                total_active += row['cnt']

            # 익월 변경 건수
            if datetime.now().month == 12:
                next_month_start = datetime.now().replace(year=datetime.now().year + 1, month=1, day=1).strftime('%Y-%m-%d')
            else:
                next_month_start = datetime.now().replace(month=datetime.now().month + 1, day=1).strftime('%Y-%m-%d')

            cursor.execute(
                f"SELECT COUNT(*) FROM promotion_changes WHERE change_date = ? {sf}",
                (next_month_start,) + sp
            )
            changes_next_month = cursor.fetchone()[0]

            return {
                'total_active': total_active,
                'by_type': by_type,
                'changes_next_month': changes_next_month
            }
        finally:
            conn.close()
