"""
폐기 위험 상품 감지기
- 푸드류: 시간 기반 유통기한 추적 (차수별 폐기 시간)
- 비-푸드류: FIFO 배치 기반 유통기한 추적 (입고일 + 유통기한)
- 알림 레벨 판정 및 카카오톡 알림 발송
"""

import math
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.infrastructure.database.connection import get_connection, DBRouter
from src.infrastructure.database.repos import (
    OrderTrackingRepository,
    InventoryBatchRepository,
    ReceivingRepository,
    RealtimeInventoryRepository,
)
from src.alert.config import ALERT_CATEGORIES, ALERT_LEVELS, EXPIRY_ALERT_SCHEDULE, DELIVERY_CONFIG
from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
from src.alert.delivery_utils import (
    get_delivery_type,
    get_arrival_time,
    get_next_expiry_time,
    get_all_expiry_hours,
    get_expiry_time_for_delivery,
    calculate_remaining_hours,
    format_time_remaining
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ExpiryChecker:
    """폐기 위험 상품 감지기"""

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.conn = None
        self.tracking_repo = OrderTrackingRepository(store_id=self.store_id)
        self.batch_repo = InventoryBatchRepository(store_id=self.store_id)
        self.receiving_repo = ReceivingRepository(store_id=self.store_id)

    def _get_connection(self) -> Any:
        if self.conn is None:
            if self.store_id:
                # store DB + common DB ATTACH (product_details 등 common 테이블 접근용)
                self.conn = DBRouter.get_store_connection_with_common(self.store_id)
            else:
                self.conn = get_connection()
        return self.conn

    def close(self) -> None:
        """DB 연결 해제"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_food_stock_status(self) -> List[Dict[str, Any]]:
        """
        푸드류 재고 현황 조회 (시간 기반)

        Returns:
            [{item_cd, item_nm, mid_cd, current_stock, avg_hourly_sales,
              remaining_hours, expiry_time, delivery_type, alert_level}, ...]
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 대상 카테고리
            category_codes = tuple(ALERT_CATEGORIES.keys())
            current_time = datetime.now()

            # 최근 재고가 있는 푸드 상품 조회
            store_filter = "AND ds.store_id = ?" if self.store_id else ""
            store_filter_sub = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT
                    ds.item_cd,
                    p.item_nm,
                    p.mid_cd,
                    ds.stock_qty as current_stock,
                    ds.sales_date as last_date
                FROM daily_sales ds
                JOIN products p ON ds.item_cd = p.item_cd
                WHERE p.mid_cd IN ({','.join('?' * len(category_codes))})
                {store_filter}
                AND ds.sales_date = (
                    SELECT MAX(sales_date) FROM daily_sales
                    WHERE item_cd = ds.item_cd {store_filter_sub}
                )
                AND ds.stock_qty > 0
                ORDER BY p.mid_cd, p.item_nm
            """, category_codes + store_params + store_params)

            items = []
            for row in cursor.fetchall():
                item_cd = row['item_cd']
                item_nm = row['item_nm']
                mid_cd = row['mid_cd']
                current_stock = row['current_stock']

                # 배송 차수 판별 (상품명 끝자리: 1=1차, 2=2차)
                delivery_type = get_delivery_type(item_nm) or "1차"

                # 실제 입고 기반 유통기한 조회 (order_tracking 또는 receiving_history)
                actual_expiry = self._get_actual_expiry_time(item_cd, mid_cd)

                if actual_expiry:
                    # 실제 입고 데이터 있음 - 실제 유통기한 사용
                    expiry_time = actual_expiry
                    remaining_hours = (expiry_time - current_time).total_seconds() / 3600
                else:
                    # 입고 데이터 없음 - 계산 기반 유통시간 사용
                    remaining_hours, expiry_time, _ = calculate_remaining_hours(
                        item_nm, mid_cd, current_time
                    )

                # 시간당 평균 판매량 계산 (일평균 / 24)
                avg_daily_sales = self._get_avg_daily_sales(item_cd, days=7)
                avg_hourly_sales = avg_daily_sales / 24 if avg_daily_sales > 0 else 0

                # 예상 소진 시간 계산
                if avg_hourly_sales > 0:
                    hours_to_sell = current_stock / avg_hourly_sales
                else:
                    hours_to_sell = float('inf')  # 판매 없음 = 무한대

                # 알림 레벨 판정 (시간 기반)
                alert_level = self._determine_alert_level_hours(hours_to_sell, remaining_hours)

                items.append({
                    'item_cd': item_cd,
                    'item_nm': item_nm,
                    'mid_cd': mid_cd,
                    'category_name': ALERT_CATEGORIES.get(mid_cd, {}).get('name', '기타'),
                    'current_stock': current_stock,
                    'avg_daily_sales': round(avg_daily_sales, 1),
                    'avg_hourly_sales': round(avg_hourly_sales, 2),
                    'delivery_type': delivery_type,
                    'remaining_hours': remaining_hours,
                    'expiry_time': expiry_time,
                    'hours_to_sell': round(hours_to_sell, 1) if not math.isinf(hours_to_sell) else None,
                    'alert_level': alert_level,
                    'last_date': row['last_date']
                })

            return items
        finally:
            cursor.close()

    def _get_actual_expiry_time(self, item_cd: str, mid_cd: str) -> Optional[datetime]:
        """
        실제 입고 기반 유통기한 조회

        1. order_tracking에서 최근 입고건 조회 (arrival_time이 있는 경우)
        2. receiving_history에서 최근 입고 조회 후 유통기한 계산

        Returns:
            유통기한 datetime 또는 None
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 1. order_tracking에서 최근 입고건 조회 (잔량이 있는 경우)
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT expiry_time, arrival_time, remaining_qty
                FROM order_tracking
                WHERE item_cd = ?
                {store_filter}
                AND remaining_qty > 0
                ORDER BY arrival_time DESC
                LIMIT 1
            """, (item_cd,) + store_params)

            tracking_row = cursor.fetchone()
            if tracking_row and tracking_row['expiry_time']:
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
                    try:
                        parsed = datetime.strptime(tracking_row['expiry_time'], fmt)
                        if fmt == '%Y-%m-%d':
                            parsed = parsed.replace(hour=23, minute=59, second=59)
                        return parsed
                    except (ValueError, TypeError):
                        continue
                return None

            # 2. receiving_history에서 최근 입고 조회
            cursor.execute(f"""
                SELECT receiving_date, receiving_time
                FROM receiving_history
                WHERE item_cd = ?
                {store_filter}
                ORDER BY receiving_date DESC, receiving_time DESC
                LIMIT 1
            """, (item_cd,) + store_params)

            recv_row = cursor.fetchone()
            if recv_row:
                recv_date = recv_row['receiving_date']
                recv_time = recv_row['receiving_time'] or '00:00'

                try:
                    recv_dt = datetime.strptime(f"{recv_date} {recv_time}", '%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    recv_dt = datetime.strptime(recv_date, '%Y-%m-%d')

                # 푸드류 유통기한 계산 (1일)
                if mid_cd in ALERT_CATEGORIES:
                    expiry_dt = recv_dt + timedelta(days=1)
                    return expiry_dt

            return None

        except Exception as e:
            logger.warning(f"실제 유통기한 조회 실패 ({item_cd}): {e}")
            return None
        finally:
            cursor.close()

    def _get_avg_daily_sales(self, item_cd: str, days: int = 7) -> float:
        """일평균 판매량 조회 (전체 기간 기준, 판매 0일 포함)"""
        conn = self._get_connection()
        cursor = conn.cursor()
        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()
        try:
            cursor.execute(f"""
                SELECT COALESCE(SUM(sale_qty), 0) as total_sales
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', ?)
                {store_filter}
            """, (item_cd, f'-{days} days') + store_params)

            row = cursor.fetchone()
            total_sales = row['total_sales'] or 0.0
            return total_sales / days
        finally:
            cursor.close()

    def _get_shelf_life(self, item_cd: str, mid_cd: str) -> int:
        """유통기한 조회 (DB 또는 카테고리 기본값)"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            # product_details는 common DB에 있으므로 common. 접두사 필요
            pd_prefix = "common." if self.store_id else ""
            cursor.execute(f"""
                SELECT expiration_days
                FROM {pd_prefix}product_details
                WHERE item_cd = ?
            """, (item_cd,))

            row = cursor.fetchone()
            if row and row['expiration_days']:
                return row['expiration_days']

            # 카테고리 기본값
            return ALERT_CATEGORIES.get(mid_cd, {}).get('shelf_life_default', 1)
        finally:
            cursor.close()

    def _determine_alert_level(self, days_to_sell: float, remaining_days: int) -> Optional[str]:
        """
        알림 레벨 판정 (일 기반 - 레거시)

        Args:
            days_to_sell: 예상 소진일
            remaining_days: 남은 유통기한

        Returns:
            'expired', 'urgent', 'warning', or None
        """
        if remaining_days <= 0:
            return 'expired'

        if math.isinf(days_to_sell):
            # 판매 없음 = 폐기 위험 높음
            return 'urgent' if remaining_days <= 1 else 'warning'

        # 소진 비율 = 예상 소진일 / 남은 유통기한
        sell_ratio = days_to_sell / remaining_days

        if sell_ratio >= ALERT_LEVELS['expired']['threshold']:
            return 'expired'
        elif sell_ratio >= ALERT_LEVELS['urgent']['threshold']:
            return 'urgent'
        elif sell_ratio >= ALERT_LEVELS['warning']['threshold']:
            return 'warning'

        return None  # 정상

    def _determine_alert_level_hours(self, hours_to_sell: float, remaining_hours: float) -> Optional[str]:
        """
        알림 레벨 판정 (시간 기반)

        Args:
            hours_to_sell: 예상 소진 시간 (시간)
            remaining_hours: 남은 유통 시간 (시간)

        Returns:
            'expired', 'urgent', 'warning', or None
        """
        if remaining_hours <= 0:
            return 'expired'

        if math.isinf(hours_to_sell):
            # 판매 없음 = 폐기 위험 높음
            if remaining_hours <= 2:
                return 'expired'
            elif remaining_hours <= 6:
                return 'urgent'
            else:
                return 'warning'

        # 소진 비율 = 예상 소진 시간 / 남은 유통 시간
        sell_ratio = hours_to_sell / remaining_hours

        if sell_ratio >= ALERT_LEVELS['expired']['threshold']:
            return 'expired'
        elif sell_ratio >= ALERT_LEVELS['urgent']['threshold']:
            return 'urgent'
        elif sell_ratio >= ALERT_LEVELS['warning']['threshold']:
            return 'warning'

        return None  # 정상

    def get_items_expiring_at(self, expiry_hour: int) -> List[Dict[str, Any]]:
        """
        특정 폐기 시간에 해당하는 발주 상품 조회 (order_tracking 기반)

        Args:
            expiry_hour: 폐기 시간 (예: 14 = 14:00 폐기)

        Returns:
            해당 시간에 폐기되는 상품 목록
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # order_tracking에서 조회
        items = self.tracking_repo.get_items_expiring_at(expiry_hour, today, store_id=self.store_id)

        # 결과 포맷팅
        result = []
        for item in items:
            mid_cd = item.get('mid_cd', '')
            expiry_time_str = item.get('expiry_time', '')

            # 폐기 시간 파싱
            try:
                expiry_time = datetime.strptime(expiry_time_str, "%Y-%m-%d %H:%M")
            except Exception:
                expiry_time = None

            result.append({
                'id': item.get('id'),
                'item_cd': item.get('item_cd'),
                'item_nm': item.get('item_nm'),
                'mid_cd': mid_cd,
                'category_name': ALERT_CATEGORIES.get(mid_cd, {}).get('name', '기타'),
                'order_qty': item.get('order_qty', 0),
                'remaining_qty': item.get('remaining_qty', 0),
                'delivery_type': item.get('delivery_type'),
                'expiry_time': expiry_time,
                'order_date': item.get('order_date'),
                'arrival_time': item.get('arrival_time')
            })

        return result

    def get_bread_items_expiring_today(self) -> List[Dict[str, Any]]:
        """
        빵(012) 오늘 유통기한 만료 상품 조회

        B방식: arrival_date + expiration_days + 1 == today
        - product_details.expiration_days 개별 조회
        - stock_qty > 0 필터
        - 최근 15일 입고 이력 기반

        Returns:
            오늘 만료되는 빵 상품 목록
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        today = datetime.now().date()

        try:
            # 012 재고 있는 상품 조회 (최신 데이터)
            store_filter = "AND ds.store_id = ?" if self.store_id else ""
            store_filter_sub = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            # product_details는 common DB에 있으므로 common. 접두사 필요
            pd_prefix = "common." if self.store_id else ""
            cursor.execute(f"""
                SELECT ds.item_cd, pd.item_nm, ds.stock_qty, pd.expiration_days
                FROM daily_sales ds
                JOIN {pd_prefix}product_details pd ON ds.item_cd = pd.item_cd
                WHERE ds.mid_cd = '012' AND ds.stock_qty > 0
                  {store_filter}
                  AND ds.sales_date = (
                      SELECT MAX(sales_date) FROM daily_sales WHERE item_cd = ds.item_cd {store_filter_sub}
                  )
            """, store_params + store_params)

            candidates = cursor.fetchall()
            items = []

            for row in candidates:
                item_cd = row['item_cd']
                item_nm = row['item_nm']
                stock_qty = row['stock_qty']
                expiration_days = row['expiration_days']

                # expiration_days 없으면 기본값 사용
                if not expiration_days:
                    expiration_days = ALERT_CATEGORIES.get("012", {}).get("shelf_life_default", 3)

                # 최근 입고 이력 조회 (buy_qty > 0)
                store_filter_arrival = "AND store_id = ?" if self.store_id else ""
                store_params_arrival = (self.store_id,) if self.store_id else ()

                cursor.execute(f"""
                    SELECT sales_date FROM daily_sales
                    WHERE item_cd = ? AND buy_qty > 0
                      AND sales_date >= date('now', '-15 days')
                      {store_filter_arrival}
                    ORDER BY sales_date DESC
                """, (item_cd,) + store_params_arrival)

                arrivals = cursor.fetchall()
                is_expiring_today = False

                for arrival_row in arrivals:
                    try:
                        # sales_date = 발주일, 도착은 익일(D+1)
                        order_date = datetime.strptime(arrival_row['sales_date'], "%Y-%m-%d").date()
                        arrival_date = order_date + timedelta(days=1)
                        # B방식: arrival_date + expiration_days + 1
                        expiry_date = arrival_date + timedelta(days=expiration_days + 1)

                        if expiry_date == today:
                            is_expiring_today = True
                            break
                    except (ValueError, TypeError):
                        continue

                if is_expiring_today:
                    expiry_time = datetime.combine(today, datetime.min.time())  # 00:00
                    items.append({
                        'item_cd': item_cd,
                        'item_nm': item_nm,
                        'mid_cd': '012',
                        'category_name': '빵',
                        'remaining_qty': stock_qty,
                        'delivery_type': '',
                        'expiry_time': expiry_time,
                        'expiry_date': today.strftime('%m/%d'),
                    })

            return items
        finally:
            cursor.close()

    def get_items_expiring_at_legacy(self, expiry_hour: int) -> List[Dict[str, Any]]:
        """
        특정 폐기 시간에 해당하는 재고 상품 조회 (레거시: daily_sales 기반)
        order_tracking에 데이터가 없을 때 폴백용

        Args:
            expiry_hour: 폐기 시간 (예: 14 = 14:00 폐기, 0 = 자정 빵 만료)

        Returns:
            해당 시간에 폐기되는 상품 목록
        """
        # 빵(012) 자정 만료 처리
        if expiry_hour == 0:
            return self.get_bread_items_expiring_today()

        conn = self._get_connection()
        cursor = conn.cursor()

        current_time = datetime.now()
        today = current_time.date()

        # 차수별 폐기 시간 체계: 해당 시간이 유효한 폐기 시간인지 확인
        all_expiry_hours = get_all_expiry_hours()
        if expiry_hour not in all_expiry_hours:
            return []

        # 전체 푸드 카테고리 대상 (012 빵 제외 - 별도 처리)
        target_categories = [k for k in ALERT_CATEGORIES.keys()
                             if not ALERT_CATEGORIES[k].get("use_product_expiry")]

        if not target_categories:
            return []

        category_codes = tuple(target_categories)

        # 해당 카테고리의 재고 상품 조회
        store_filter = "AND ds.store_id = ?" if self.store_id else ""
        store_filter_sub = "AND store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()

        cursor.execute(f"""
            SELECT
                ds.item_cd,
                p.item_nm,
                p.mid_cd,
                ds.stock_qty as current_stock,
                ds.sales_date as last_date
            FROM daily_sales ds
            JOIN products p ON ds.item_cd = p.item_cd
            WHERE p.mid_cd IN ({','.join('?' * len(category_codes))})
            {store_filter}
            AND ds.sales_date = (
                SELECT MAX(sales_date) FROM daily_sales
                WHERE item_cd = ds.item_cd {store_filter_sub}
            )
            AND ds.stock_qty > 0
            ORDER BY p.mid_cd, p.item_nm
        """, category_codes + store_params + store_params)

        items = []
        for row in cursor.fetchall():
            item_nm = row['item_nm']
            mid_cd = row['mid_cd']
            last_date = row['last_date']

            # sales_date = 발주일(데이터 수집일), 도착은 익일(D+1)
            delivery_type = get_delivery_type(item_nm) or "1차"
            try:
                order_date = datetime.strptime(last_date, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue

            # 도착시간: 발주일 + 1일의 arrival_hour (DELIVERY_CONFIG 기반)
            arrival_time = get_arrival_time(delivery_type, order_date)
            expiry_time = get_expiry_time_for_delivery(delivery_type, mid_cd, arrival_time)

            # 데이터 신선도 체크: 폐기시간이 이미 지난 상품 제외
            if expiry_time < current_time:
                continue

            # 오늘 해당 시간에 폐기되는 상품만 필터링
            if expiry_time.date() == today and expiry_time.hour == expiry_hour:
                items.append({
                    'item_cd': row['item_cd'],
                    'item_nm': item_nm,
                    'mid_cd': mid_cd,
                    'category_name': ALERT_CATEGORIES.get(mid_cd, {}).get('name', '기타'),
                    'remaining_qty': row['current_stock'],
                    'delivery_type': delivery_type,
                    'expiry_time': expiry_time,
                })

        return items

    def generate_expiry_alert_message(self, expiry_hour: int) -> Optional[str]:
        """
        특정 폐기 시간 30분 전 알림 메시지 생성

        Args:
            expiry_hour: 폐기 시간 (예: 14 = 14:00 폐기)

        Returns:
            알림 메시지 (없으면 None)
        """
        # order_tracking에서 조회
        items = self.get_items_expiring_at(expiry_hour)

        # order_tracking에 없으면 레거시 방식으로 조회
        # 있더라도 레거시 경로에서 추가 항목 보충 (중복 제거)
        legacy_items = self.get_items_expiring_at_legacy(expiry_hour)
        if legacy_items:
            tracked_codes = {item['item_cd'] for item in items}
            for legacy_item in legacy_items:
                if legacy_item['item_cd'] not in tracked_codes:
                    items.append(legacy_item)
                    tracked_codes.add(legacy_item['item_cd'])

        if not items:
            return None

        # 알림 대상 상품 ID 저장 (나중에 alert_sent 업데이트용)
        self._pending_alert_ids = [item.get('id') for item in items if item.get('id')]

        lines = []

        # 빵(012) 자정 만료: 별도 메시지 형식
        if expiry_hour == 0:
            lines.append(f"🍞 빵 유통기한 만료 알림 ({datetime.now().strftime('%m/%d %H:%M')})")
            lines.append(f"1시간 후 유통기한 만료! 재고 확인 필요")
            lines.append("")

            lines.append(f"📦 빵 ({len(items)}개)")
            for item in items[:10]:
                qty = item.get('remaining_qty', 0)
                expiry_date = item.get('expiry_date', '')
                lines.append(f"  • {item['item_nm'][:15]} {qty}개 (만료: {expiry_date})")
            if len(items) > 10:
                lines.append(f"  ... 외 {len(items) - 10}개")
            lines.append("")

            lines.append(f"총 {len(items)}개 상품 유통기한 만료 임박")
            return "\n".join(lines)

        # 기존 001-005 폐기 알림
        lines.append(f"⏰ {expiry_hour:02d}:00 폐기 알림 ({datetime.now().strftime('%m/%d %H:%M')})")
        lines.append(f"30분 후 폐기 처리 필요!")
        lines.append("")

        # 카테고리별 그룹핑
        by_category = {}
        for item in items:
            cat = item['category_name']
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(item)

        for cat_name, cat_items in by_category.items():
            lines.append(f"📦 {cat_name} ({len(cat_items)}개)")
            for item in cat_items[:5]:
                qty = item.get('remaining_qty', 0)
                delivery = item.get('delivery_type', '')
                lines.append(f"  • {item['item_nm'][:15]} {qty}개 ({delivery})")
            if len(cat_items) > 5:
                lines.append(f"  ... 외 {len(cat_items) - 5}개")
            lines.append("")

        total = sum(len(cat_items) for cat_items in by_category.values())
        lines.append(f"총 {total}개 상품 폐기 처리 필요")

        return "\n".join(lines)

    def send_expiry_alert(self, expiry_hour: int) -> bool:
        """
        특정 폐기 시간 30분 전 알림 발송

        Args:
            expiry_hour: 폐기 시간 (예: 14 = 14:00 폐기)

        Returns:
            발송 성공 여부
        """
        self._pending_alert_ids = []  # 초기화
        msg = self.generate_expiry_alert_message(expiry_hour)

        if not msg:
            logger.info(f"{expiry_hour:02d}:00 폐기 대상 없음")
            return True

        try:
            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)

            if not notifier.access_token:
                logger.warning("카카오 토큰 없음. 알림 스킵.")
                return False

            result = notifier.send_message(msg)
            if result:
                logger.info(f"{expiry_hour:02d}:00 폐기 알림 발송 완료!")

                # 알림 발송 완료 표시 (order_tracking)
                for order_id in self._pending_alert_ids:
                    try:
                        self.tracking_repo.mark_alert_sent(order_id)
                    except Exception as e:
                        logger.warning(f"alert_sent 업데이트 실패 (id={order_id}): {e}")

            return result

        except Exception as e:
            logger.error(f"카카오톡 발송 실패: {e}")
            return False

    def get_alert_items(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        알림 필요 상품 조회 (레벨별 분류)

        Returns:
            {
                'expired': [...],
                'urgent': [...],
                'warning': [...]
            }
        """
        all_items = self.get_food_stock_status()

        result = {
            'expired': [],
            'urgent': [],
            'warning': []
        }

        for item in all_items:
            level = item.get('alert_level')
            if level and level in result:
                result[level].append(item)

        return result

    def get_batch_expiring_items(self, days_ahead: int = 1) -> List[Dict[str, Any]]:
        """
        비-푸드 배치 기반 폐기 임박 상품 조회

        Args:
            days_ahead: 몇 일 이내 만료 예정 (기본: 1일)

        Returns:
            만료 예정 배치 목록
        """
        return self.batch_repo.get_expiring_soon(days_ahead, store_id=self.store_id)

    def check_and_expire_batches(self) -> List[Dict[str, Any]]:
        """배치 만료 체크 및 폐기 확정 + realtime_inventory 재고 보정

        만료 확정 시:
        1. inventory_batches → expired 상태 변경 + realtime_inventory 차감
        2. receiving_history와 크로스체크하여 실제 입고 수량 첨부
        3. 배치 기반 realtime_inventory 전체 보정 (만료 누락 보완)

        Returns:
            폐기 확정된 배치 목록 (actual_receiving_qty 포함)
        """
        expired = self.batch_repo.check_and_expire_batches(store_id=self.store_id)

        # receiving_history 크로스체크: 실제 입고 수량 첨부
        for batch in expired:
            try:
                recv_date = batch.get('receiving_date', '')
                if not recv_date:
                    continue
                recv_list = self.receiving_repo.get_receiving_by_date(
                    recv_date, store_id=self.store_id
                )
                for recv in recv_list:
                    if recv.get('item_cd') == batch.get('item_cd'):
                        batch['actual_receiving_qty'] = recv.get('receiving_qty', 0)
                        break
            except Exception as e:
                logger.debug(f"입고 크로스체크 실패 ({batch.get('item_nm')}): {e}")

        # 배치 기반 realtime_inventory 전체 보정
        # (만료 처리 외에도 배치가 소비되었는데 stock_qty가 안 줄어든 경우 보정)
        try:
            ri_repo = RealtimeInventoryRepository(store_id=self.store_id)
            sync_stats = ri_repo.sync_stock_from_batches(store_id=self.store_id)
            if sync_stats.get('corrected', 0) > 0:
                logger.info(
                    f"배치 기반 재고 보정: {sync_stats['corrected']}건, "
                    f"-{sync_stats['total_reduced']}개"
                )
        except Exception as e:
            logger.warning(f"배치 기반 재고 보정 실패: {e}")

        return expired

    def generate_alert_message(self) -> Optional[str]:
        """
        카카오톡 발송용 알림 메시지 생성
        푸드류(시간 기반) + 비-푸드(배치 기반) 통합

        Returns:
            알림 메시지 (없으면 None)
        """
        alerts = self.get_alert_items()
        batch_expiring = self.get_batch_expiring_items(days_ahead=1)

        # 알림 대상 없으면 None
        food_total = sum(len(items) for items in alerts.values())
        batch_total = len(batch_expiring)
        if food_total == 0 and batch_total == 0:
            return None

        lines = []
        lines.append(f"📦 폐기 위험 알림 ({datetime.now().strftime('%m/%d %H:%M')})")
        lines.append("")

        # === 푸드류 (시간 기반) ===
        if food_total > 0:
            lines.append(f"[푸드류] {food_total}건")
            lines.append("")

            if alerts['expired']:
                lines.append(f"⚫ 폐기 처리 필요 ({len(alerts['expired'])}개)")
                for item in alerts['expired'][:5]:
                    expiry = item.get('expiry_time')
                    expiry_str = expiry.strftime('%H:%M') if expiry else ""
                    lines.append(f"  • {item['item_nm'][:12]} {item['current_stock']}개 (폐기:{expiry_str})")
                if len(alerts['expired']) > 5:
                    lines.append(f"  ... 외 {len(alerts['expired']) - 5}개")
                lines.append("")

            if alerts['urgent']:
                lines.append(f"🔴 긴급 ({len(alerts['urgent'])}개)")
                for item in alerts['urgent'][:5]:
                    remaining = item.get('remaining_hours', 0)
                    remaining_str = format_time_remaining(remaining)
                    delivery = item.get('delivery_type', '')
                    lines.append(f"  • {item['item_nm'][:12]} {item['current_stock']}개 ({delivery}, {remaining_str})")
                if len(alerts['urgent']) > 5:
                    lines.append(f"  ... 외 {len(alerts['urgent']) - 5}개")
                lines.append("")

            if alerts['warning']:
                lines.append(f"🟡 주의 ({len(alerts['warning'])}개)")
                for item in alerts['warning'][:3]:
                    remaining = item.get('remaining_hours', 0)
                    remaining_str = format_time_remaining(remaining)
                    lines.append(f"  • {item['item_nm'][:12]} {item['current_stock']}개 ({remaining_str})")
                if len(alerts['warning']) > 3:
                    lines.append(f"  ... 외 {len(alerts['warning']) - 3}개")
                lines.append("")

        # === 비-푸드류 (배치 기반) ===
        if batch_total > 0:
            lines.append(f"[비-푸드 유통기한 만료 임박] {batch_total}건")
            for batch in batch_expiring[:10]:
                item_nm = batch.get('item_nm', '')[:12]
                remaining = batch.get('remaining_qty', 0)
                expiry_date = batch.get('expiry_date', '')
                recv_date = batch.get('receiving_date', '')
                recv_tag = f" 입고:{recv_date}" if recv_date else ""
                lines.append(f"  • {item_nm} {remaining}개 (만료:{expiry_date}{recv_tag})")
            if batch_total > 10:
                lines.append(f"  ... 외 {batch_total - 10}건")

        return "\n".join(lines)

    def print_status(self) -> None:
        """푸드 재고 폐기 위험 현황을 콘솔에 출력 (시간 기반)"""
        alerts = self.get_alert_items()

        logger.info(f"푸드 재고 폐기 위험 현황 (시간 기반) - 현재 시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        for level in ['expired', 'urgent', 'warning']:
            items = alerts[level]
            if not items:
                continue

            config = ALERT_LEVELS[level]
            logger.info(f"{config['emoji']} {config['label']} ({len(items)}개)")

            for item in items:
                remaining = item.get('remaining_hours', 0)
                remaining_str = format_time_remaining(remaining)
                delivery = item.get('delivery_type', '??')
                expiry = item.get('expiry_time')
                expiry_str = expiry.strftime('%m/%d %H:%M') if expiry else ""

                logger.info(f"  {item['category_name']:6} | {item['item_nm'][:18]:<18} | "
                            f"{delivery} | 재고:{item['current_stock']:3}개 | "
                            f"남은:{remaining_str:>6} | 폐기:{expiry_str}")

        # 요약
        total = sum(len(alerts[l]) for l in ['expired', 'urgent', 'warning'])
        logger.info(f"총 {total}개 상품 알림 필요")


    def send_kakao_alert(self) -> bool:
        """
        폐기 위험 알림을 카카오톡으로 발송

        Returns:
            발송 성공 여부
        """
        msg = self.generate_alert_message()

        if not msg:
            logger.info("알림 대상 없음")
            return True  # 알림 대상 없으면 성공으로 처리

        try:
            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)

            if not notifier.access_token:
                logger.warning("카카오 토큰 없음. 알림 스킵.")
                return False

            result = notifier.send_message(msg)
            if result:
                logger.info("카카오톡 발송 완료!")
            return result

        except Exception as e:
            logger.error(f"카카오톡 발송 실패: {e}")
            return False


def run_expiry_check_and_alert(store_id: Optional[str] = None) -> Dict[str, Any]:
    """폐기 위험 체크 및 알림 발송 (스케줄러용)

    푸드류 + 비-푸드류 통합 폐기 관리:
    1. 비-푸드 배치 만료 체크 → 폐기 확정
    2. 푸드류 시간 기반 알림 + 비-푸드 배치 알림 통합 발송

    Returns:
        실행 결과 {success, total_alerts, expired, urgent, warning,
                   batch_expired, kakao_sent}
    """
    checker = ExpiryChecker(store_id=store_id)

    try:
        # 1. 비-푸드 배치 만료 체크
        expired_batches = checker.check_and_expire_batches()

        # 2. 푸드류 알림
        alerts = checker.get_alert_items()
        food_total = sum(len(items) for items in alerts.values())

        result = {
            "success": True,
            "total_alerts": food_total + len(expired_batches),
            "expired": len(alerts['expired']),
            "urgent": len(alerts['urgent']),
            "warning": len(alerts['warning']),
            "batch_expired": len(expired_batches),
            "kakao_sent": False
        }

        if result["total_alerts"] > 0:
            result["kakao_sent"] = checker.send_kakao_alert()

        return result

    except Exception as e:
        return {"success": False, "error": str(e)}

    finally:
        checker.close()


# 테스트용
if __name__ == "__main__":
    checker = ExpiryChecker()
    checker.print_status()

    print("\n--- 카카오톡 메시지 미리보기 ---")
    msg = checker.generate_alert_message()
    if msg:
        print(msg)
    else:
        print("(알림 대상 없음)")

    # 카카오톡 발송 테스트
    print("\n--- 카카오톡 발송 ---")
    checker.send_kakao_alert()

    checker.close()
