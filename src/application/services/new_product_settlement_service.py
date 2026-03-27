"""
NewProductSettlementService — 신제품 안착 판정 서비스

신규 입점 제품이 안착했는지를 5개 KPI로 종합 판정한다.

KPI:
    1. velocity_ratio: 카테고리 중위값 대비 일평균 판매속도 비율
    2. cv: 판매 안정성 (변동계수, %)
    3. sellthrough_rate: 소진율 (판매/입고, %)
    4. expiry_risk: 폐기 위험 ('High'/'Mid'/'Low')
    5. rank: 카테고리 내 상대 순위 (0.0=최상위, 1.0=최하위)

판정:
    70점+ → settled (안착 완료, 발주 모델 편입)
    50~69 + 연장 < 2회 → extended (관찰 연장)
    그 외 → failed (안착 실패, 발주 중단 권고)

읽기: detected_new_products, new_product_daily_tracking, daily_sales, product_details
쓰기: detected_new_products (판정 결과 컬럼만)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import statistics

from src.infrastructure.database.repos import (
    DetectedNewProductRepository,
    NewProductDailyTrackingRepository,
    ProductDetailRepository,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 설정 상수 ────────────────────────────────────────────

# 유통기한별 분석 윈도우: (min_days, max_days) → (window_days, phase1_check_days)
ANALYSIS_WINDOW_BY_EXPIRY = {
    (0, 3): (14, 5),       # 초단기: 삼각김밥, 도시락
    (4, 14): (21, 7),      # 단기: 디저트, 케이크
    (15, 60): (30, 10),    # 중기: 음료, 유제품
    (61, 9999): (45, 14),  # 장기: 과자, 라면, 잡화
}

# 유통기한 폴백 (product_details 없을 때)
EXPIRY_FALLBACK = {
    '001': 1, '002': 1, '003': 1,
    '004': 2, '005': 3, '012': 3,
    'default': 7,
}

MAX_EXTENSIONS = 2  # 최대 연장 횟수


@dataclass
class SettlementResult:
    """안착 판정 결과"""
    item_cd: str
    item_nm: str
    verdict: str            # 'settled' / 'extended' / 'failed'
    score: float
    velocity: float
    velocity_ratio: float
    cv: Optional[float]
    sellthrough_rate: float
    expiry_risk: str
    rank: float
    extension_count: int
    analysis_window_days: int


class NewProductSettlementService:
    """신제품 안착 판정 서비스"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.new_product_repo = DetectedNewProductRepository(store_id=store_id)
        self.tracking_repo = NewProductDailyTrackingRepository(store_id=store_id)
        self.product_detail_repo = ProductDetailRepository()

    # ── 공개 API ──────────────────────────────────────────

    def _get_analysis_conn(self):
        """분석용 DB 연결 (store + common ATTACH)"""
        from src.infrastructure.database.connection import DBRouter, attach_common_with_views
        conn = DBRouter.get_store_connection(self.store_id)
        return attach_common_with_views(conn, self.store_id)

    def analyze(self, item_cd: str) -> Optional[SettlementResult]:
        """단일 제품 안착 분석 + DB 업데이트"""
        conn = None
        try:
            conn = self._get_analysis_conn()
            cursor = conn.cursor()

            # 1. 기본 정보 조회
            product = self._get_product_info(cursor, item_cd)
            if not product:
                logger.warning(f"[안착판정] {item_cd}: 제품 정보 없음")
                return None

            # 2. 유통기한 + 분석 윈도우
            expiration_days = self._get_expiration_days(cursor, item_cd, product['mid_cd'])
            window_days, _ = get_analysis_window(expiration_days)

            # 3. KPI 계산
            velocity, velocity_ratio = self._calc_velocity(cursor, product)
            cv, cv_unstable_threshold = self._calc_cv(item_cd)
            sellthrough_rate = self._calc_sellthrough(cursor, item_cd, product['first_receiving_date'])
            expiry_risk = self._calc_expiry_risk(cursor, item_cd, velocity, expiration_days)
            rank = self._calc_category_rank(cursor, item_cd, product['mid_cd'], velocity)

            # 4. 점수화 + 판정
            score = calculate_settlement_score(
                velocity_ratio, cv, sellthrough_rate,
                expiry_risk, rank, expiration_days,
                cv_unstable_threshold=cv_unstable_threshold,
            )
            ext_count = product.get('extension_count') or 0
            verdict = determine_verdict(score, ext_count)

            # 4.5 버즈형 급락 감지 → settled 오버라이드
            daily_qtys = self._get_daily_qtys_for_trend(item_cd)
            trend = self._calc_trend(daily_qtys)
            if trend < 0.5 and verdict == 'settled':
                verdict = 'extended'
                logger.info(
                    f"[버즈급락감지] {item_cd} settled→extended "
                    f"오버라이드 (trend={trend:.2f})"
                )

            # 4.6 KPI 개별 로그
            cv_str = f"{cv:.0f}" if cv is not None else "N/A"
            logger.info(
                f"[안착판정] {item_cd} → {verdict} | "
                f"점수:{score:.0f} velocity:{velocity_ratio:.2f} "
                f"CV:{cv_str}% 소진율:{sellthrough_rate:.0f}% "
                f"폐기위험:{expiry_risk} 순위:{rank:.2f} 추세:{trend:.2f}"
            )

            result = SettlementResult(
                item_cd=item_cd,
                item_nm=product.get('item_nm', ''),
                verdict=verdict,
                score=score,
                velocity=velocity,
                velocity_ratio=velocity_ratio,
                cv=cv,
                sellthrough_rate=sellthrough_rate,
                expiry_risk=expiry_risk,
                rank=rank,
                extension_count=ext_count,
                analysis_window_days=window_days,
            )

            # 5. DB 업데이트
            self._save_result(result, product)

            return result

        except Exception as e:
            logger.error(f"[안착판정] {item_cd} 분석 실패: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def run_due_settlements(self) -> List[SettlementResult]:
        """판정 기한 도달한 제품 일괄 처리"""
        today = datetime.now().strftime("%Y-%m-%d")

        # analysis_window_days가 아직 설정 안 된 제품도 초기화
        self._init_analysis_windows()

        due_items = self.new_product_repo.get_settlement_due(
            today, store_id=self.store_id
        )

        if not due_items:
            return []

        results = []
        for item in due_items:
            result = self.analyze(item['item_cd'])
            if result:
                results.append(result)

        if results:
            settled = sum(1 for r in results if r.verdict == 'settled')
            extended = sum(1 for r in results if r.verdict == 'extended')
            failed = sum(1 for r in results if r.verdict == 'failed')
            logger.info(
                f"[안착판정] 일괄처리 완료: "
                f"settled={settled}, extended={extended}, failed={failed}"
            )

        return results

    def get_settlement_summary(self) -> List[Dict]:
        """현재 모니터링 중인 신제품 + KPI 요약"""
        items = self.new_product_repo.get_by_lifecycle_status(
            ["detected", "monitoring", "stable", "slow_start"],
            store_id=self.store_id,
        )

        summaries = []
        for item in items:
            tracking = self.tracking_repo.get_tracking_history(
                item['item_cd'], store_id=self.store_id
            )
            summary = {
                **item,
                'tracking_days': len(tracking),
                'latest_sales': tracking[-1]['sales_qty'] if tracking else 0,
                'latest_stock': tracking[-1]['stock_qty'] if tracking else 0,
            }
            summaries.append(summary)

        return summaries

    # ── KPI 계산 ──────────────────────────────────────────

    SMALL_CD_MIN_SIMILAR = 3  # 소분류 기반 유사상품 최소 상품 수

    def _calc_velocity(self, cursor, product: Dict) -> Tuple[float, float]:
        """판매 속도 + 카테고리 대비 비율

        similar_item_avg가 없으면 직접 계산 (lifecycle 전이 대기 불필요).
        """
        total_sold = product.get('total_sold_qty') or 0
        sold_days = product.get('sold_days') or 0
        velocity = total_sold / sold_days if sold_days > 0 else 0.0

        similar_avg = product.get('similar_item_avg')

        # similar_avg가 없으면 직접 계산
        if similar_avg is None:
            item_cd = product['item_cd']
            mid_cd = product.get('mid_cd') or '999'
            small_cd = self._get_small_cd(cursor, item_cd)
            similar_avg = self._fetch_similar_avg(
                cursor, item_cd, small_cd, mid_cd
            )
            # 계산된 값을 DB에도 반영 (이후 재계산 방지)
            if similar_avg is not None:
                self.new_product_repo.update_lifecycle(
                    item_cd=item_cd,
                    similar_item_avg=similar_avg,
                    store_id=self.store_id,
                )

        if similar_avg is None or similar_avg == 0:
            # 유사 상품 자체가 없는 경우만 0.5 (희귀 카테고리 신제품)
            velocity_ratio = 0.5 if velocity > 0 else 0.0
        else:
            velocity_ratio = velocity / similar_avg

        return velocity, velocity_ratio

    def _get_small_cd(self, cursor, item_cd: str) -> Optional[str]:
        """common.product_details에서 소분류 코드 조회"""
        try:
            cursor.execute(
                "SELECT small_cd FROM common.product_details WHERE item_cd = ?",
                (item_cd,),
            )
            row = cursor.fetchone()
            return row['small_cd'] if row and row['small_cd'] else None
        except Exception:
            return None

    def _fetch_similar_avg(
        self, cursor, item_cd: str, small_cd: Optional[str], mid_cd: str,
    ) -> Optional[float]:
        """유사 상품 일평균 판매 중위값 (small_cd 우선 → mid_cd 폴백)

        NewProductMonitor.calculate_similar_avg()와 동일한 로직.
        """
        if mid_cd == '999':
            return None

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        # 1단계: small_cd 기반 조회
        if small_cd and small_cd.strip():
            avgs = self._query_similar_avgs(
                cursor, start_date, end_date, mid_cd, item_cd,
                small_cd=small_cd,
            )
            if avgs and len(avgs) >= self.SMALL_CD_MIN_SIMILAR:
                result = round(statistics.median(avgs), 2)
                logger.debug(
                    f"[안착판정] {item_cd}: small_cd={small_cd} "
                    f"유사상품 {len(avgs)}개, median={result}"
                )
                return result

        # 2단계: mid_cd 전체 폴백
        avgs = self._query_similar_avgs(
            cursor, start_date, end_date, mid_cd, item_cd,
        )
        if avgs:
            return round(statistics.median(avgs), 2)

        return None

    def _query_similar_avgs(
        self, cursor, start_date: str, end_date: str,
        mid_cd: str, item_cd: str, small_cd: Optional[str] = None,
    ) -> List[float]:
        """유사상품 일평균 판매량 리스트 조회"""
        if small_cd:
            cursor.execute(
                """
                SELECT p.item_cd,
                       COALESCE(SUM(ds.sale_qty), 0) as total_sales,
                       COUNT(DISTINCT ds.sales_date) as data_days
                FROM common.products p
                JOIN common.product_details pd ON p.item_cd = pd.item_cd
                LEFT JOIN daily_sales ds
                    ON p.item_cd = ds.item_cd
                    AND ds.sales_date BETWEEN ? AND ?
                WHERE p.mid_cd = ?
                  AND pd.small_cd = ?
                  AND p.item_cd != ?
                GROUP BY p.item_cd
                HAVING data_days > 0
                """,
                (start_date, end_date, mid_cd, small_cd, item_cd),
            )
        else:
            cursor.execute(
                """
                SELECT p.item_cd,
                       COALESCE(SUM(ds.sale_qty), 0) as total_sales,
                       COUNT(DISTINCT ds.sales_date) as data_days
                FROM common.products p
                LEFT JOIN daily_sales ds
                    ON p.item_cd = ds.item_cd
                    AND ds.sales_date BETWEEN ? AND ?
                WHERE p.mid_cd = ?
                  AND p.item_cd != ?
                GROUP BY p.item_cd
                HAVING data_days > 0
                """,
                (start_date, end_date, mid_cd, item_cd),
            )

        rows = cursor.fetchall()
        if not rows:
            return []

        daily_avgs = []
        for row in rows:
            days = row['data_days']
            if days > 0:
                daily_avgs.append(row['total_sales'] / days)

        return daily_avgs

    def _calc_cv(self, item_cd: str) -> Tuple[Optional[float], int]:
        """판매 안정성 (변동계수, %) + 요일 패턴 기반 불안정 임계값

        Returns:
            (cv, cv_unstable_threshold): CV값과 요일 패턴 보정된 임계값
        """
        tracking = self.tracking_repo.get_tracking_history(
            item_cd, store_id=self.store_id
        )

        daily_with_dates = [
            (row['tracking_date'], row['sales_qty'])
            for row in tracking if row['sales_qty'] > 0
        ]

        if len(daily_with_dates) < 3:
            return None, 80  # 데이터 부족

        daily_qtys = [s for _, s in daily_with_dates]

        mean = statistics.mean(daily_qtys)
        if mean <= 0:
            return 999.0, 80

        stdev = statistics.stdev(daily_qtys)
        cv = round(stdev / mean * 100, 1)

        # 요일 패턴 CV 보정: 주중/주말 판매 평균 차이 50% 초과 → 임계값 완화
        cv_unstable_threshold = 80
        weekday_sales = [
            s for d, s in daily_with_dates
            if datetime.strptime(d, "%Y-%m-%d").weekday() < 5
        ]
        weekend_sales = [
            s for d, s in daily_with_dates
            if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5
        ]
        if weekday_sales and weekend_sales:
            wd_avg = statistics.mean(weekday_sales) or 0.001
            we_avg = statistics.mean(weekend_sales)
            if abs(wd_avg - we_avg) / wd_avg > 0.5:
                cv_unstable_threshold = 90

        return cv, cv_unstable_threshold

    def _get_daily_qtys_for_trend(self, item_cd: str) -> list:
        """추적 이력에서 일별 판매량 리스트 조회 (trend 계산용)"""
        tracking = self.tracking_repo.get_tracking_history(
            item_cd, store_id=self.store_id
        )
        return [row['sales_qty'] for row in tracking if row['sales_qty'] > 0]

    def _calc_trend(self, daily_qtys: list) -> float:
        """최근 절반 평균 / 초기 절반 평균. 0.5 미만이면 버즈 후 급락."""
        if len(daily_qtys) < 6:
            return 1.0
        mid = len(daily_qtys) // 2
        early_avg = statistics.mean(daily_qtys[:mid]) or 0.001
        recent_avg = statistics.mean(daily_qtys[mid:])
        return recent_avg / early_avg

    def _calc_sellthrough(
        self, cursor, item_cd: str, first_receiving_date: str
    ) -> float:
        """소진율 (판매/입고, %) — daily_sales.buy_qty 활용"""
        today = datetime.now().strftime("%Y-%m-%d")

        cursor.execute(
            """
            SELECT COALESCE(SUM(buy_qty), 0) as total_buy,
                   COALESCE(SUM(sale_qty), 0) as total_sale
            FROM daily_sales
            WHERE item_cd = ? AND sales_date BETWEEN ? AND ?
            """,
            (item_cd, first_receiving_date, today),
        )
        row = cursor.fetchone()

        total_buy = row['total_buy'] if row else 0
        total_sale = row['total_sale'] if row else 0

        if total_buy > 0:
            return round(total_sale / total_buy * 100, 1)
        return 0.0

    def _calc_expiry_risk(
        self, cursor, item_cd: str, velocity: float, expiration_days: int
    ) -> str:
        """폐기 위험 점수"""
        # 최신 재고 조회
        cursor.execute(
            "SELECT stock_qty FROM realtime_inventory WHERE item_cd = ? AND is_available = 1",
            (item_cd,),
        )
        row = cursor.fetchone()
        current_stock = row['stock_qty'] if row else 0

        if velocity > 0:
            days_to_sellout = current_stock / velocity
        else:
            days_to_sellout = 999.0

        if days_to_sellout > expiration_days * 0.7:
            return 'High'
        elif days_to_sellout > expiration_days * 0.4:
            return 'Mid'
        else:
            return 'Low'

    def _calc_category_rank(
        self, cursor, item_cd: str, mid_cd: str, velocity: float
    ) -> float:
        """카테고리 내 상대 순위 (0.0=최상위, 1.0=최하위)"""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        cursor.execute(
            """
            SELECT p.item_cd,
                   COALESCE(SUM(ds.sale_qty), 0) as total_sales,
                   COUNT(DISTINCT ds.sales_date) as data_days
            FROM common.products p
            LEFT JOIN daily_sales ds
                ON p.item_cd = ds.item_cd
                AND ds.sales_date BETWEEN ? AND ?
            WHERE p.mid_cd = ?
            GROUP BY p.item_cd
            HAVING data_days > 0
            """,
            (start_date, end_date, mid_cd),
        )
        rows = cursor.fetchall()

        if not rows:
            return 0.5  # 비교 불가

        velocities = []
        for row in rows:
            days = row['data_days']
            if days > 0:
                velocities.append(row['total_sales'] / days)

        if not velocities:
            return 0.5

        velocities.sort(reverse=True)
        total = len(velocities)

        # 현재 제품의 위치 찾기
        for i, v in enumerate(velocities):
            if abs(v - velocity) < 0.01:
                return round(i / max(total - 1, 1), 3)

        # 정확한 매치 없으면 삽입 위치 기반
        for i, v in enumerate(velocities):
            if velocity >= v:
                return round(i / max(total - 1, 1), 3)

        return 1.0  # 최하위

    # ── 헬퍼 ──────────────────────────────────────────────

    def _get_product_info(self, cursor, item_cd: str) -> Optional[Dict]:
        """detected_new_products에서 제품 정보 조회"""
        cursor.execute(
            """
            SELECT * FROM detected_new_products
            WHERE item_cd = ? AND store_id = ?
            ORDER BY first_receiving_date DESC LIMIT 1
            """,
            (item_cd, self.store_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _get_expiration_days(
        self, cursor, item_cd: str, mid_cd: str
    ) -> int:
        """유통기한 조회 (product_details → fallback)"""
        cursor.execute(
            "SELECT expiration_days FROM common.product_details WHERE item_cd = ?",
            (item_cd,),
        )
        row = cursor.fetchone()
        if row and row['expiration_days'] and row['expiration_days'] > 0:
            return int(row['expiration_days'])

        return EXPIRY_FALLBACK.get(mid_cd, EXPIRY_FALLBACK['default'])

    def _init_analysis_windows(self) -> None:
        """analysis_window_days가 NULL인 제품에 초기값 설정"""
        conn = None
        try:
            from src.infrastructure.database.connection import DBRouter, attach_common_with_views
            conn = DBRouter.get_store_connection(self.store_id)
            conn = attach_common_with_views(conn, self.store_id)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT item_cd, mid_cd FROM detected_new_products
                WHERE analysis_window_days IS NULL
                  AND store_id = ?
                  AND lifecycle_status IN ('detected', 'monitoring', 'stable', 'slow_start')
                """,
                (self.store_id,),
            )
            rows = cursor.fetchall()

            for row in rows:
                expiry = self._get_expiration_days(cursor, row['item_cd'], row['mid_cd'] or '999')
                window_days, _ = get_analysis_window(expiry)
                cursor.execute(
                    """
                    UPDATE detected_new_products
                    SET analysis_window_days = ?
                    WHERE item_cd = ? AND store_id = ?
                    """,
                    (window_days, row['item_cd'], self.store_id),
                )

            if rows:
                conn.commit()
                logger.info(f"[안착판정] analysis_window_days 초기화: {len(rows)}건")
        except Exception as e:
            logger.warning(f"[안착판정] analysis_window 초기화 실패: {e}")
        finally:
            if conn:
                conn.close()

    def _save_result(self, result: SettlementResult, product: Dict) -> None:
        """판정 결과 DB 저장"""
        today = datetime.now().strftime("%Y-%m-%d")

        # lifecycle 연동
        lifecycle_update = None
        new_ext_count = result.extension_count

        if result.verdict == 'settled':
            current = product.get('lifecycle_status', '')
            if current in ('monitoring', 'slow_start'):
                lifecycle_update = 'stable'
        elif result.verdict == 'extended':
            new_ext_count = result.extension_count + 1
            # analysis_window_days 연장: +window/2
            extended_window = result.analysis_window_days + result.analysis_window_days // 2
            self.new_product_repo.update_settlement(
                item_cd=result.item_cd,
                settlement_score=result.score,
                settlement_verdict=result.verdict,
                settlement_date=today,
                analysis_window_days=extended_window,
                extension_count=new_ext_count,
                store_id=self.store_id,
            )
            return
        elif result.verdict == 'failed':
            lifecycle_update = 'no_demand'

        self.new_product_repo.update_settlement(
            item_cd=result.item_cd,
            settlement_score=result.score,
            settlement_verdict=result.verdict,
            settlement_date=today,
            analysis_window_days=result.analysis_window_days,
            extension_count=new_ext_count,
            lifecycle_status=lifecycle_update,
            store_id=self.store_id,
        )


# ── 순수 함수 (테스트 용이) ──────────────────────────────

def get_analysis_window(expiration_days: int) -> Tuple[int, int]:
    """유통기한 → (window_days, phase1_check_days) 반환"""
    for (min_d, max_d), result in ANALYSIS_WINDOW_BY_EXPIRY.items():
        if min_d <= expiration_days <= max_d:
            return result
    return (30, 10)


def calculate_settlement_score(
    velocity_ratio: float,
    cv: Optional[float],
    sellthrough_rate: float,
    expiry_risk: str,
    rank: float,
    expiration_days: int,
    cv_unstable_threshold: int = 80,
) -> float:
    """안착 점수 계산 (100점 만점)

    유통기한 3일 이하: 폐기 리스크 가중 강화
    cv_unstable_threshold: 요일 패턴 보정된 불안정 임계값 (기본 80, 요일편차 클 때 90)
    """
    is_short_expiry = expiration_days <= 3

    # Velocity (기본 30점 / 단기 20점)
    w_v = 20 if is_short_expiry else 30
    if velocity_ratio >= 0.8:
        v_score = w_v
    elif velocity_ratio >= 0.5:
        v_score = w_v * 0.5
    else:
        v_score = 0

    # 안정성 (기본 25점 / 단기 20점)
    w_s = 20 if is_short_expiry else 25
    if cv is None:
        s_score = w_s * 0.5
    elif cv < 50:
        s_score = w_s
    elif cv < cv_unstable_threshold:
        s_score = w_s * 0.5
    else:
        s_score = 0

    # 소진율 (기본 20점 / 단기 25점)
    w_st = 25 if is_short_expiry else 20
    if sellthrough_rate >= 80:
        st_score = w_st
    elif sellthrough_rate >= 60:
        st_score = w_st * 0.5
    else:
        st_score = 0

    # 폐기 위험 (기본 15점 / 단기 25점)
    w_e = 25 if is_short_expiry else 15
    risk_scores = {'Low': w_e, 'Mid': w_e * 0.5, 'High': 0}
    e_score = risk_scores.get(expiry_risk, 0)

    # 카테고리 순위 (공통 10점)
    if rank <= 0.3:
        r_score = 10
    elif rank <= 0.7:
        r_score = 5
    else:
        r_score = 0

    return v_score + s_score + st_score + e_score + r_score


def determine_verdict(score: float, extension_count: int) -> str:
    """판정 결과 결정"""
    if score >= 70:
        return 'settled'
    elif score >= 50 and extension_count < MAX_EXTENSIONS:
        return 'extended'
    else:
        return 'failed'
