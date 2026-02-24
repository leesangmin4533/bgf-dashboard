"""
발주량 예측기
- DB 데이터 기반 예측
- 발주 목록 생성
"""

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.infrastructure.database.connection import get_connection
from src.prediction.prediction_config import get_category_config, get_weekday_factor, get_weekday_factor_from_db
from src.prediction.rules.base import (
    predict_sales, calculate_order_quantity, calculate_volatility,
    calculate_turnover_factor, evaluate_min_order_decision
)
from src.alert.config import ALERT_CATEGORIES
from src.alert.delivery_utils import (
    get_delivery_type,
    calculate_shelf_life_after_arrival,
    format_time_remaining
)
from src.infrastructure.database.repos import ReceivingRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderPredictor:
    """발주량 예측기"""

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.conn = None
        # 미입고 수량 캐시 (발주 전 사전 조회용)
        self._pending_qty_cache: Dict[str, int] = {}
        self._pending_cache_enabled = False

    def _get_connection(self) -> Any:
        if self.conn is None:
            self.conn = get_connection()
        return self.conn

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_sales_history(self, item_cd: str, days: int = 30) -> List[Dict[str, Any]]:
        """
        상품별 판매 이력 조회

        Args:
            item_cd: 상품 코드
            days: 조회 일수

        Returns:
            [{sales_date, sale_qty, stock_qty}, ...]
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        cursor.execute("""
            SELECT sales_date, sale_qty, stock_qty
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= ?
            ORDER BY sales_date DESC
        """, (item_cd, start_date.strftime("%Y-%m-%d")))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_current_stock(self, item_cd: str) -> int:
        """
        현재 재고 조회 (우선순위: realtime_inventory → daily_sales)

        realtime_inventory는 단품별 발주 화면에서 실시간 조회한 데이터이고,
        daily_sales는 일별 판매 데이터 수집 시 저장된 마감 재고입니다.
        더 최신 데이터를 반환합니다.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 1. realtime_inventory에서 조회 (더 정확한 실시간 데이터)
        cursor.execute("""
            SELECT stock_qty, queried_at
            FROM realtime_inventory
            WHERE item_cd = ?
        """, (item_cd,))
        realtime_row = cursor.fetchone()

        # 2. daily_sales에서 조회 (일별 마감 재고)
        cursor.execute("""
            SELECT stock_qty, sales_date
            FROM daily_sales
            WHERE item_cd = ?
            ORDER BY sales_date DESC
            LIMIT 1
        """, (item_cd,))
        daily_row = cursor.fetchone()

        # 둘 다 없으면 0 반환
        if not realtime_row and not daily_row:
            return 0

        # realtime만 있으면 realtime 반환
        if realtime_row and not daily_row:
            return realtime_row["stock_qty"]

        # daily만 있으면 daily 반환
        if daily_row and not realtime_row:
            return daily_row["stock_qty"]

        # 둘 다 있으면 더 최신 데이터 반환
        from datetime import datetime as dt

        realtime_time = dt.fromisoformat(realtime_row["queried_at"].replace("Z", "+00:00")) if realtime_row["queried_at"] else None
        # daily_sales의 sales_date는 날짜만 있으므로 해당 날짜의 끝으로 간주
        daily_time = dt.fromisoformat(daily_row["sales_date"] + "T23:59:59") if daily_row["sales_date"] else None

        if realtime_time and daily_time:
            if realtime_time >= daily_time:
                return realtime_row["stock_qty"]
            else:
                return daily_row["stock_qty"]

        # 시간 비교 불가 시 realtime 우선
        return realtime_row["stock_qty"] if realtime_row else daily_row["stock_qty"]

    def set_pending_qty_cache(self, pending_data: Dict[str, int]) -> None:
        """
        미입고 수량 캐시 설정 (발주 전 사전 조회 결과)

        Args:
            pending_data: {상품코드: 미입고수량, ...}
        """
        self._pending_qty_cache = pending_data
        self._pending_cache_enabled = True
        logger.info(f"미입고 수량 캐시 설정: {len(pending_data)}개 상품")

    def clear_pending_qty_cache(self) -> None:
        """미입고 수량 캐시 초기화"""
        self._pending_qty_cache = {}
        self._pending_cache_enabled = False

    def get_pending_receiving_qty(self, item_cd: str) -> int:
        """
        미입고 수량 조회 (발주됐으나 아직 입고되지 않은 수량)

        우선순위:
        1. 캐시 (단품별 발주 화면에서 사전 조회한 데이터)
        2. realtime_inventory 테이블 (단품별 발주 화면에서 조회한 데이터)
        3. order_tracking 테이블 (DB 기반)

        Args:
            item_cd: 상품 코드

        Returns:
            미입고 수량 합계
        """
        # 1. 캐시 사용 (단품별 발주 화면에서 사전 조회한 데이터)
        if self._pending_cache_enabled and item_cd in self._pending_qty_cache:
            return self._pending_qty_cache.get(item_cd, 0)

        # 2. realtime_inventory 테이블에서 조회
        conn = self._get_connection()
        cursor = conn.cursor()
        store_filter = "AND store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()
        cursor.execute(f"""
            SELECT pending_qty FROM realtime_inventory WHERE item_cd = ? {store_filter}
        """, (item_cd,) + store_params)
        row = cursor.fetchone()
        if row and row["pending_qty"] is not None:
            return row["pending_qty"]

        # 3. DB 기반 조회 (receiving_history vs order_tracking)
        try:
            repo = ReceivingRepository(store_id=self.store_id)
            pending_qty = repo.get_pending_qty_sum(item_cd, store_id=self.store_id)
            return pending_qty
        except Exception as e:
            logger.warning(f"미입고 수량 조회 실패: {e}")
            return 0

    def get_product_info(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """상품 정보 조회 (products + product_details + 행사 정보)"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.item_cd, p.item_nm, p.mid_cd,
                   pd.orderable_day, pd.order_unit_qty, pd.case_unit_qty,
                   pd.expiration_days, pd.promo_type, pd.promo_name,
                   pd.promo_start, pd.promo_end, pd.promo_updated
            FROM products p
            LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
            WHERE p.item_cd = ?
        """, (item_cd,))

        row = cursor.fetchone()
        return dict(row) if row else None

    def get_disuse_rate(self, item_cd: str, days: int = 30, weekday: Optional[int] = None) -> float:
        """
        상품별 폐기율 조회 (최근 N일)

        Args:
            item_cd: 상품 코드
            days: 조회 기간 (일)
            weekday: 특정 요일만 조회 (0=월, 6=일, None=전체)

        Returns:
            폐기율 (0.0 ~ 1.0, 예: 0.15 = 15%)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if weekday is not None:
            # Python weekday(0=월) → SQLite %w(0=일) 변환
            sqlite_weekday = (weekday + 1) % 7
            cursor.execute("""
                SELECT SUM(sale_qty) as total_sale,
                       SUM(disuse_qty) as total_disuse
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', ?)
                AND strftime('%w', sales_date) = ?
            """, (item_cd, f'-{days} days', str(sqlite_weekday)))
        else:
            cursor.execute("""
                SELECT SUM(sale_qty) as total_sale,
                       SUM(disuse_qty) as total_disuse
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', ?)
            """, (item_cd, f'-{days} days'))

        row = cursor.fetchone()
        if not row:
            return 0.0

        total_sale = row['total_sale'] or 0
        total_disuse = row['total_disuse'] or 0

        total = total_sale + total_disuse
        if total == 0:
            return 0.0

        return total_disuse / total

    def get_turnover_info(self, item_cd: str, days: int = 30) -> Dict[str, float]:
        """
        상품별 회전율 정보 조회

        Args:
            item_cd: 상품 코드
            days: 조회 기간 (일)

        Returns:
            {
                'turnover_rate': 회전율 (판매량/평균재고),
                'stock_days': 재고일수 (평균재고/일평균판매량),
                'avg_stock': 평균재고,
                'total_sale': 총판매량
            }
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT SUM(sale_qty) as total_sale,
                   AVG(stock_qty) as avg_stock,
                   COUNT(DISTINCT sales_date) as data_days
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= date('now', ?)
        """, (item_cd, f'-{days} days'))

        row = cursor.fetchone()
        if not row:
            return {'turnover_rate': 0.0, 'stock_days': 0.0, 'avg_stock': 0.0, 'total_sale': 0}

        total_sale = row['total_sale'] or 0
        avg_stock = row['avg_stock'] or 0
        data_days = row['data_days'] or 1

        # 일 평균 판매량
        daily_sale = total_sale / data_days if data_days > 0 else 0

        # 회전율 = 판매량 / 평균재고
        turnover_rate = total_sale / avg_stock if avg_stock > 0 else 0.0

        # 재고일수 = 평균재고 / 일평균판매량
        stock_days = avg_stock / daily_sale if daily_sale > 0 else 0.0

        return {
            'turnover_rate': round(turnover_rate, 2),
            'stock_days': round(stock_days, 2),
            'avg_stock': round(avg_stock, 2),
            'total_sale': total_sale
        }

    def predict_item(self, item_cd: str, target_date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        단일 상품 발주량 예측

        Args:
            item_cd: 상품 코드
            target_date: 발주 대상 날짜 (기본값: 내일)

        Returns:
            예측 결과 dict
        """
        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        # 1. 상품 정보 조회
        product = self.get_product_info(item_cd)
        if not product:
            return {"item_cd": item_cd, "error": "product not found"}

        # 2. 카테고리 설정
        mid_cd = product.get("mid_cd", "")
        config = get_category_config(mid_cd)

        # 3. 판매 이력 조회
        history = self.get_sales_history(item_cd, days=30)
        sales_list = [h["sale_qty"] for h in history]

        # 4. 현재 재고
        current_stock = self.get_current_stock(item_cd)

        # 4-1. 미입고 수량 (발주됐으나 아직 입고되지 않은 수량)
        pending_receiving_qty = self.get_pending_receiving_qty(item_cd)

        # 5. 요일 계수 (DB 기반)
        # Python weekday: 0=월, 6=일
        # SQLite strftime %w: 0=일, 6=토
        py_weekday = target_date.weekday()
        sqlite_weekday = (py_weekday + 1) % 7  # 변환: 월=1, 일=0
        weekday_factor = get_weekday_factor_from_db(sqlite_weekday)

        # 6. 유통기한 조회 (product_details에서)
        shelf_life_days = product.get("expiration_days")
        if not shelf_life_days:
            # 카테고리 기본값 사용
            shelf_life_days = config.get("shelf_life", 30)

        # 7. 폐기율 조회 (요일별 적용)
        disuse_rate = self.get_disuse_rate(item_cd, days=30, weekday=py_weekday)

        # 8. 회전율 조회 (최근 30일 기준)
        turnover_info = self.get_turnover_info(item_cd, days=30)
        turnover_rate = turnover_info.get('turnover_rate', 0)
        stock_days = turnover_info.get('stock_days', 0)

        # 9. 회전율 가중치 (단순화)
        # 회전율 > 10: 품절위험 → +10%
        # 회전율 3~10: 정상 → 0%
        # 회전율 < 3: 재고과다 → -10%
        if turnover_rate > 10:
            turnover_weight = 1.1
        elif turnover_rate < 3:
            turnover_weight = 0.9
        else:
            turnover_weight = 1.0

        # 10. 푸드류의 경우 배송 차수별 실제 유통 시간 계산
        delivery_type = None
        shelf_life_hours = None
        arrival_time = None
        expiry_time = None
        delivery_factor = 1.0

        if mid_cd in ALERT_CATEGORIES:
            item_nm = product.get("item_nm", "")
            delivery_type = get_delivery_type(item_nm)

            if delivery_type:
                # 도착~폐기 실제 유통 시간 계산
                shelf_hours, arrival, expiry = calculate_shelf_life_after_arrival(
                    item_nm, mid_cd, target_date
                )
                shelf_life_hours = shelf_hours
                arrival_time = arrival
                expiry_time = expiry

                # 유통 시간에 따른 발주 조정 계수
                if shelf_life_hours < 6:
                    delivery_factor = 0.7
                elif shelf_life_hours < 8:
                    delivery_factor = 0.85

        # ============================================================
        # 11. 발주량 계산 (공식)
        # (일평균 × 요일계수) + 안전재고 × 회전율가중치 - 재고
        # ============================================================

        # 일평균 판매량 (실제 날짜 범위 기준)
        if history and len(history) >= 2:
            latest_date = datetime.strptime(history[0]["sales_date"], "%Y-%m-%d")
            oldest_date = datetime.strptime(history[-1]["sales_date"], "%Y-%m-%d")
            actual_days = (latest_date - oldest_date).days + 1
            daily_avg_raw = sum(sales_list) / actual_days if actual_days > 0 else 0
        elif history:
            daily_avg_raw = sales_list[0] if sales_list else 0
        else:
            daily_avg_raw = 0

        # 요일 계수 적용 (발주 대상일 기준)
        daily_avg = daily_avg_raw * weekday_factor

        # 안전재고 (푸드류 1일, 그 외 3일)
        if mid_cd in ALERT_CATEGORIES:
            safety_stock_days = 1  # 푸드류: 유통기한 짧음
        else:
            safety_stock_days = 3  # 일반 상품
        safety_stock = daily_avg * safety_stock_days

        # 필요량 = (일평균 + 안전재고) × 회전율가중치 × 배송계수
        total_needed = (daily_avg + safety_stock) * turnover_weight * delivery_factor

        # 폐기율 반영 (폐기율 높으면 발주 감소, 푸드류는 더 보수적)
        from src.prediction.rules.base import calculate_disuse_factor
        is_food = mid_cd in ALERT_CATEGORIES
        disuse_factor = calculate_disuse_factor(disuse_rate, is_food=is_food)
        adjusted_prediction = int(total_needed * disuse_factor)

        # 11-1. 재고 차감
        order_unit_qty = product.get("order_unit_qty") or 1
        expected_stock = current_stock + pending_receiving_qty
        raw_needed = max(0, adjusted_prediction - expected_stock)

        # 13. 발주단위 올림
        if raw_needed > 0 and order_unit_qty > 1:
            units = (raw_needed + order_unit_qty - 1) // order_unit_qty
            final_order_qty = units * order_unit_qty
        elif raw_needed > 0:
            final_order_qty = raw_needed
        else:
            final_order_qty = 0

        # 배송 관련 경고 추가
        warning = None
        if delivery_type and shelf_life_hours:
            if shelf_life_hours < 6:
                warning = f"{delivery_type}({format_time_remaining(shelf_life_hours)}유통) 짧음"
            elif shelf_life_hours < 8:
                warning = f"{delivery_type}({format_time_remaining(shelf_life_hours)}유통)"

        return {
            "item_cd": item_cd,
            "item_nm": product.get("item_nm"),
            "mid_cd": mid_cd,
            "orderable_day": product.get("orderable_day") or "일월화수목금토",
            "order_unit_qty": order_unit_qty,
            "current_stock": current_stock,
            "pending_receiving_qty": pending_receiving_qty,  # 미입고 수량 (중복발주 방지)
            "expected_stock": expected_stock,  # 예상 재고 (현재 + 미입고)
            "daily_avg_raw": round(daily_avg_raw, 1),  # 원본 일평균 (요일 계수 적용 전)
            "weekday_factor": round(weekday_factor, 2),  # 요일 계수
            "daily_avg": round(daily_avg, 1),  # 조정된 일평균 (요일 계수 적용 후)
            "safety_stock": round(safety_stock, 1),  # 안전재고 (3일분)
            "predicted_sales": adjusted_prediction,  # 필요량 (일평균 + 안전재고)
            "raw_needed": raw_needed,  # 재고 차감 후 필요량
            "final_order_qty": final_order_qty,  # 발주단위 올림 후 최종 발주량
            "target_date": target_date.strftime("%Y-%m-%d"),
            "target_weekday": py_weekday,  # 발주 대상 요일 (0=월, 6=일)
            "history_days": len(sales_list),
            "shelf_life_days": shelf_life_days,
            "disuse_rate": round(disuse_rate, 3),
            "disuse_factor": round(disuse_factor, 2),  # 폐기계수 (푸드류는 더 보수적)
            "turnover_rate": turnover_rate,
            "turnover_weight": turnover_weight,  # 회전율 가중치
            "recommended_order_qty": final_order_qty,  # 호환성용 alias
            "stock_days": stock_days,
            # 배송 관련 정보 (푸드류)
            "delivery_type": delivery_type,
            "shelf_life_hours": shelf_life_hours,
            "arrival_time": arrival_time.strftime("%m/%d %H:%M") if arrival_time else None,
            "expiry_time": expiry_time.strftime("%m/%d %H:%M") if expiry_time else None,
            "delivery_factor": delivery_factor,
            "warning": warning
        }

    def get_order_candidates(self, min_sales: int = 1) -> List[str]:
        """
        발주 대상 상품 목록 조회
        (최근 판매가 있는 상품)

        Args:
            min_sales: 최소 판매량 기준

        Returns:
            상품 코드 리스트
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 최근 7일 내 판매가 있는 상품
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        cursor.execute("""
            SELECT DISTINCT item_cd
            FROM daily_sales
            WHERE sales_date >= ?
            AND sale_qty >= ?
            AND LENGTH(item_cd) = 13
            AND item_cd LIKE '88%'
        """, (week_ago, min_sales))

        rows = cursor.fetchall()
        return [row["item_cd"] for row in rows]

    def generate_order_list(
        self,
        item_codes: Optional[List[str]] = None,
        min_order_qty: int = 1,
        target_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        발주 목록 생성

        Args:
            item_codes: 대상 상품 코드 (None이면 자동 선택)
            min_order_qty: 최소 발주량 (이하는 제외)
            target_date: 발주 대상 날짜

        Returns:
            발주 목록 [{item_cd, item_nm, recommended_qty, orderable_day, ...}, ...]
        """
        if target_date is None:
            target_date = datetime.now() + timedelta(days=1)

        # 대상 상품 목록
        if item_codes is None:
            item_codes = self.get_order_candidates()

        logger.info(f"대상 상품: {len(item_codes)}개")

        order_list = []
        warnings = []
        skipped_items = []

        for i, item_cd in enumerate(item_codes):
            result = self.predict_item(item_cd, target_date)

            if result.get("error"):
                continue

            # 최종 발주량 (스마트 판단 적용)
            final_qty = result.get("final_order_qty", 0)
            recommended_qty = result.get("recommended_qty", 0)
            warning = result.get("warning")
            risk_level = result.get("risk_level", "low")

            # 경고가 있으면 기록
            if warning:
                warnings.append({
                    "item_nm": result.get("item_nm"),
                    "warning": warning,
                    "risk_level": risk_level,
                    "shelf_life": result.get("shelf_life_days"),
                    "recommended": recommended_qty,
                    "final": final_qty
                })

            # 발주량이 0이면 스킵 (스마트 판단으로 보류된 경우 포함)
            if final_qty <= 0:
                if recommended_qty > 0:
                    skipped_items.append({
                        "item_nm": result.get("item_nm"),
                        "reason": result.get("order_decision", "")
                    })
                continue

            if final_qty >= min_order_qty:
                order_list.append({
                    "item_cd": item_cd,
                    "item_nm": result.get("item_nm"),
                    "mid_cd": result.get("mid_cd"),
                    "orderable_day": result.get("orderable_day"),
                    "order_unit_qty": result.get("order_unit_qty", 1),
                    "current_stock": result.get("current_stock", 0),
                    "pending_receiving_qty": result.get("pending_receiving_qty", 0),  # 미입고 수량
                    "expected_stock": result.get("expected_stock", 0),  # 예상 재고
                    "predicted_sales": result.get("predicted_sales", 0),
                    "recommended_qty": recommended_qty,
                    "final_order_qty": final_qty,
                    "avg_daily_sales": result.get("avg_daily_sales", 0),
                    "shelf_life_days": result.get("shelf_life_days"),
                    "turnover_rate": result.get("turnover_rate"),
                    "disuse_rate": result.get("disuse_rate"),
                    "risk_level": risk_level,
                    "warning": warning,
                    # 배송 관련 정보 추가
                    "delivery_type": result.get("delivery_type"),
                    "shelf_life_hours": result.get("shelf_life_hours"),
                    "arrival_time": result.get("arrival_time"),
                    "expiry_time": result.get("expiry_time")
                })

        # 발주량 내림차순 정렬
        order_list.sort(key=lambda x: x["final_order_qty"], reverse=True)

        logger.info(f"발주 대상: {len(order_list)}개")

        # 경고 출력
        high_risk = [w for w in warnings if w['risk_level'] == 'high']
        medium_risk = [w for w in warnings if w['risk_level'] == 'medium']

        if high_risk:
            high_risk_details = "; ".join(f"{w['item_nm']}: {w['warning']}" for w in high_risk[:5])
            logger.warning(f"주의 필요 {len(high_risk)}개 상품: {high_risk_details}")

        if medium_risk:
            logger.info(f"참고: {len(medium_risk)}개 상품 (최소발주량 미달 발주)")

        if skipped_items:
            skipped_details = "; ".join(f"{item['item_nm']}: {item['reason']}" for item in skipped_items[:3])
            logger.info(f"발주 보류 {len(skipped_items)}개 상품 (스마트 판단): {skipped_details}")

        # 경고 파일 저장
        if warnings or skipped_items:
            self._save_warnings_to_file(warnings, skipped_items, target_date)

        return order_list

    def _save_warnings_to_file(
        self,
        warnings: List[Dict[str, Any]],
        skipped_items: List[Dict[str, Any]],
        target_date: datetime
    ) -> str:
        """
        경고 정보를 파일로 저장

        Args:
            warnings: 경고 목록
            skipped_items: 스킵된 상품 목록
            target_date: 발주 대상 날짜

        Returns:
            저장된 파일 경로
        """
        import csv
        from pathlib import Path

        # 파일 경로
        data_dir = Path(__file__).parent.parent.parent / "data"
        data_dir.mkdir(exist_ok=True)

        date_str = target_date.strftime("%Y%m%d")
        file_path = data_dir / f"order_warnings_{date_str}.csv"

        with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)

            # 헤더
            writer.writerow(['구분', '상품명', '유통기한(일)', '추천량', '최종발주량', '위험도', '상세내용'])

            # 경고 상품
            for w in warnings:
                writer.writerow([
                    '[!] 주의' if w['risk_level'] == 'high' else '[*] 참고',
                    w['item_nm'],
                    w.get('shelf_life', ''),
                    w.get('recommended', ''),
                    w.get('final', ''),
                    w['risk_level'],
                    w['warning']
                ])

            # 스킵된 상품
            for item in skipped_items:
                writer.writerow([
                    '[i] 보류',
                    item['item_nm'],
                    '',
                    '',
                    0,
                    'skipped',
                    item['reason']
                ])

        logger.info(f"파일 저장: {file_path}")
        return str(file_path)


    def log_prediction(self, prediction_result: Dict[str, Any]) -> None:
        """
        예측 결과를 DB에 저장 (나중에 실제 판매량과 비교용)

        Args:
            prediction_result: predict_item() 반환값
        """
        from src.infrastructure.database.repos import PredictionRepository

        try:
            repo = PredictionRepository(store_id=self.store_id)
            repo.save_prediction(
                prediction_date=datetime.now().strftime("%Y-%m-%d"),
                target_date=prediction_result.get("target_date"),
                item_cd=prediction_result.get("item_cd"),
                mid_cd=prediction_result.get("mid_cd"),
                predicted_qty=prediction_result.get("predicted_sales", 0),
                model_type="adaptive_rule"  # 적응형 규칙 기반
            )
        except Exception as e:
            logger.warning(f"예측 로그 저장 실패: {e}")

    def update_prediction_accuracy(self, target_date: str) -> Dict[str, Any]:
        """
        특정 날짜의 예측 정확도 업데이트 (실제 판매량 반영)

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD)

        Returns:
            {"updated": 업데이트 건수, "mae": 평균절대오차}
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 해당 날짜의 예측 목록
        store_filter = "AND pl.store_id = ?" if self.store_id else ""
        store_params = (self.store_id,) if self.store_id else ()
        cursor.execute(f"""
            SELECT pl.id, pl.item_cd, pl.predicted_qty
            FROM prediction_logs pl
            WHERE pl.target_date = ? AND pl.actual_qty IS NULL
            {store_filter}
        """, (target_date,) + store_params)

        predictions = cursor.fetchall()

        if not predictions:
            return {"updated": 0, "message": "no predictions to update"}

        updated = 0
        errors = []

        for pred in predictions:
            pred_id, item_cd, predicted_qty = pred["id"], pred["item_cd"], pred["predicted_qty"]

            # 실제 판매량 조회
            ds_filter = "AND store_id = ?" if self.store_id else ""
            ds_params = (self.store_id,) if self.store_id else ()
            cursor.execute(f"""
                SELECT sale_qty FROM daily_sales
                WHERE item_cd = ? AND sales_date = ? {ds_filter}
            """, (item_cd, target_date) + ds_params)

            actual_row = cursor.fetchone()
            if actual_row:
                actual_qty = actual_row["sale_qty"]

                # 업데이트
                cursor.execute("""
                    UPDATE prediction_logs
                    SET actual_qty = ?
                    WHERE id = ?
                """, (actual_qty, pred_id))

                errors.append(abs(predicted_qty - actual_qty))
                updated += 1

        conn.commit()

        mae = sum(errors) / len(errors) if errors else 0

        return {
            "updated": updated,
            "mae": round(mae, 2),
            "target_date": target_date
        }


# 테스트용
if __name__ == "__main__":
    predictor = OrderPredictor()

    # 발주 목록 생성 테스트
    order_list = predictor.generate_order_list(min_order_qty=1)

    print("\n[발주 추천 목록]")
    for item in order_list[:10]:
        print(f"  {item['item_nm']}: {item['final_order_qty']}개 "
              f"(재고:{item['current_stock']}, 예측:{item['predicted_sales']})")

    predictor.close()
