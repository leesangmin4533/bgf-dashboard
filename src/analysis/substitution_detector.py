"""
소분류 내 상품 대체/잠식(Cannibalization) 감지 모듈

같은 소분류(small_cd) 내에서 신상품의 수요 증가에 따른
기존 상품의 수요 감소(잠식)를 자동 감지하고,
잠식된 상품의 발주량 조정 계수를 제공합니다.

알고리즘:
  1. 같은 small_cd 내 상품별 14일 이동평균 비교 (전반 vs 후반)
  2. 수요 증가(gainer)와 감소(loser) 상품 식별
  3. 소분류 총량 변화가 20% 이내일 때만 잠식으로 판정
  4. 감소율에 따라 발주 조정 계수 부여 (0.7~0.9)
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from src.infrastructure.database.connection import DBRouter
from src.infrastructure.database.repos.substitution_repo import SubstitutionEventRepository
from src.utils.logger import get_logger
from src.settings.constants import (
    SUBSTITUTION_LOOKBACK_DAYS,
    SUBSTITUTION_RECENT_WINDOW,
    SUBSTITUTION_DECLINE_THRESHOLD,
    SUBSTITUTION_GROWTH_THRESHOLD,
    SUBSTITUTION_TOTAL_CHANGE_LIMIT,
    SUBSTITUTION_MIN_DAILY_AVG,
    SUBSTITUTION_MIN_ITEMS_IN_GROUP,
    SUBSTITUTION_COEF_MILD,
    SUBSTITUTION_COEF_MODERATE,
    SUBSTITUTION_COEF_SEVERE,
    SUBSTITUTION_FEEDBACK_EXPIRY_DAYS,
)

logger = get_logger(__name__)


class SubstitutionDetector:
    """소분류 내 상품 대체/잠식 감지기

    Usage:
        detector = SubstitutionDetector(store_id="46513")
        result = detector.detect_all("2026-03-01")
        coef = detector.get_adjustment("8800279678694")
    """

    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        self.repo = SubstitutionEventRepository(store_id=store_id)
        self._cache: Dict[str, float] = {}
        self._preloaded = False

    def detect_all(self, target_date: str) -> dict:
        """전체 소분류에 대해 잠식 분석

        Args:
            target_date: 분석 기준일 (YYYY-MM-DD)
        Returns:
            {"analyzed_groups": N, "events_detected": N, "by_small_cd": {...}, "errors": [...]}
        """
        # 1) 만료 이벤트 정리
        self.repo.expire_old_events(target_date)

        # 2) 판매 데이터가 있는 소분류 목록 조회
        small_cds = self._get_active_small_cds(target_date)

        analyzed = 0
        detected = 0
        by_small_cd: Dict[str, int] = {}
        errors: List[str] = []

        for small_cd in small_cds:
            try:
                events = self.detect_cannibalization(small_cd, target_date)
                analyzed += 1
                if events:
                    detected += len(events)
                    by_small_cd[small_cd] = len(events)
            except Exception as e:
                errors.append(f"{small_cd}: {e}")
                logger.debug(f"잠식 감지 실패 ({small_cd}): {e}")

        logger.info(
            f"[잠식감지] 분석 완료: {analyzed}개 소분류, "
            f"{detected}건 잠식 감지"
        )

        return {
            "analyzed_groups": analyzed,
            "events_detected": detected,
            "by_small_cd": by_small_cd,
            "errors": errors,
        }

    def detect_cannibalization(
        self, small_cd: str, target_date: str,
        days: int = SUBSTITUTION_LOOKBACK_DAYS
    ) -> List[dict]:
        """특정 소분류 내 잠식 감지

        Args:
            small_cd: 소분류 코드
            target_date: 기준일
            days: 분석 기간 (기본 30일)
        Returns:
            감지된 잠식 이벤트 목록
        """
        items = self._get_items_in_small_cd(small_cd)
        if len(items) < SUBSTITUTION_MIN_ITEMS_IN_GROUP:
            return []

        # 날짜 계산
        end_date = target_date
        start_date = (
            datetime.strptime(target_date, "%Y-%m-%d")
            - timedelta(days=days)
        ).strftime("%Y-%m-%d")

        # 상품별 판매 데이터 배치 조회 (N+1 방지)
        all_item_cds = [item["item_cd"] for item in items]
        sales_batch = self._get_sales_data_batch(all_item_cds, start_date, end_date)

        # 상품별 이동평균 계산
        items_with_avg: List[dict] = []
        for item in items:
            sales = sales_batch.get(item["item_cd"], [])
            prior_avg, recent_avg = self._calculate_moving_averages(
                sales, SUBSTITUTION_RECENT_WINDOW
            )
            if prior_avg < SUBSTITUTION_MIN_DAILY_AVG:
                continue  # 판매 미미한 상품 제외
            ratio = recent_avg / prior_avg if prior_avg > 0 else 1.0
            items_with_avg.append({
                **item,
                "prior_avg": prior_avg,
                "recent_avg": recent_avg,
                "ratio": ratio,
            })

        if len(items_with_avg) < SUBSTITUTION_MIN_ITEMS_IN_GROUP:
            return []

        # 소분류 총량 변화 확인
        total_prior = sum(i["prior_avg"] for i in items_with_avg)
        total_recent = sum(i["recent_avg"] for i in items_with_avg)
        if total_prior > 0:
            total_change_rate = abs(total_recent - total_prior) / total_prior
        else:
            total_change_rate = 0.0

        if total_change_rate > SUBSTITUTION_TOTAL_CHANGE_LIMIT:
            return []  # 총량 자체가 변화 -> 잠식이 아닌 외부 요인

        # gainer/loser 분류
        gainers, losers = self._classify_items(items_with_avg)

        if not gainers or not losers:
            return []

        # 이벤트 생성
        events: List[dict] = []
        expires_at = (
            datetime.strptime(target_date, "%Y-%m-%d")
            + timedelta(days=SUBSTITUTION_FEEDBACK_EXPIRY_DAYS)
        ).strftime("%Y-%m-%d")

        for loser in losers:
            for gainer in gainers:
                decline_rate = loser["ratio"]  # < 1.0
                coef = self._compute_adjustment_coefficient(decline_rate)
                confidence = self._calculate_confidence(
                    gainer, loser, total_change_rate
                )

                record = {
                    "store_id": self.store_id,
                    "detection_date": target_date,
                    "small_cd": small_cd,
                    "small_nm": loser.get("small_nm"),
                    "gainer_item_cd": gainer["item_cd"],
                    "gainer_item_nm": gainer.get("item_nm"),
                    "gainer_prior_avg": round(gainer["prior_avg"], 2),
                    "gainer_recent_avg": round(gainer["recent_avg"], 2),
                    "gainer_growth_rate": round(gainer["ratio"], 3),
                    "loser_item_cd": loser["item_cd"],
                    "loser_item_nm": loser.get("item_nm"),
                    "loser_prior_avg": round(loser["prior_avg"], 2),
                    "loser_recent_avg": round(loser["recent_avg"], 2),
                    "loser_decline_rate": round(decline_rate, 3),
                    "adjustment_coefficient": coef,
                    "total_change_rate": round(total_change_rate, 3),
                    "confidence": confidence,
                    "is_active": 1,
                    "expires_at": expires_at,
                }
                self.repo.upsert_event(record)
                events.append(record)

                logger.info(
                    f"[잠식감지] {small_cd}: "
                    f"gainer={gainer.get('item_nm', gainer['item_cd'])} "
                    f"(+{(gainer['ratio']-1)*100:.0f}%), "
                    f"loser={loser.get('item_nm', loser['item_cd'])} "
                    f"({(loser['ratio']-1)*100:.0f}%), "
                    f"계수={coef}, 신뢰도={confidence}"
                )

        return events

    # -----------------------------------------------------------------
    # 데이터 조회
    # -----------------------------------------------------------------

    def _get_active_small_cds(self, target_date: str) -> List[str]:
        """판매 데이터가 있는 소분류 코드 목록 조회"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            # store DB에서 최근 판매가 있는 item_cd의 small_cd 조회
            from src.infrastructure.database.connection import attach_common_with_views
            conn = attach_common_with_views(conn, self.store_id)
            rows = conn.execute("""
                SELECT DISTINCT pd.small_cd
                FROM daily_sales ds
                JOIN common.product_details pd ON ds.item_cd = pd.item_cd
                WHERE ds.store_id = ?
                  AND ds.sales_date >= date(?, '-30 days')
                  AND pd.small_cd IS NOT NULL
                  AND pd.small_cd != ''
            """, (self.store_id, target_date)).fetchall()
            return [r["small_cd"] for r in rows]
        finally:
            conn.close()

    def _get_items_in_small_cd(self, small_cd: str) -> List[dict]:
        """소분류 내 상품 목록 조회 (common DB product_details + products)"""
        conn = DBRouter.get_common_connection()
        try:
            rows = conn.execute("""
                SELECT pd.item_cd, p.item_nm, p.mid_cd,
                       pd.small_cd, pd.small_nm
                FROM product_details pd
                JOIN products p ON pd.item_cd = p.item_cd
                WHERE pd.small_cd = ?
            """, (small_cd,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _get_sales_data(
        self, item_cd: str, start_date: str, end_date: str
    ) -> List[dict]:
        """판매 데이터 조회 (store DB daily_sales) - 단건"""
        batch = self._get_sales_data_batch([item_cd], start_date, end_date)
        return batch.get(item_cd, [])

    def _get_sales_data_batch(
        self, item_cds: List[str], start_date: str, end_date: str
    ) -> Dict[str, List[dict]]:
        """판매 데이터 배치 조회 (store DB daily_sales) - N+1 방지"""
        if not item_cds:
            return {}
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            placeholders = ",".join("?" * len(item_cds))
            rows = conn.execute(f"""
                SELECT item_cd, sales_date, sale_qty
                FROM daily_sales
                WHERE item_cd IN ({placeholders})
                  AND sales_date >= ? AND sales_date <= ?
                ORDER BY item_cd, sales_date
            """, item_cds + [start_date, end_date]).fetchall()
            result: Dict[str, List[dict]] = {cd: [] for cd in item_cds}
            for r in rows:
                result.setdefault(r["item_cd"], []).append(dict(r))
            return result
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # 계산 로직
    # -----------------------------------------------------------------

    def _calculate_moving_averages(
        self, sales: List[dict], recent_days: int
    ) -> Tuple[float, float]:
        """판매 데이터를 전반/후반으로 나누어 일평균 계산

        Args:
            sales: 판매 데이터 리스트 (날짜 순)
            recent_days: 최근 윈도우 크기
        Returns:
            (prior_avg, recent_avg)
        """
        if not sales:
            return 0.0, 0.0

        # 날짜 기준으로 분할
        all_dates = sorted(set(s["sales_date"] for s in sales))
        if len(all_dates) < recent_days:
            # 데이터 부족 시 전체를 반반
            mid = len(all_dates) // 2
            if mid == 0:
                total = sum(s["sale_qty"] or 0 for s in sales)
                avg = total / max(len(all_dates), 1)
                return avg, avg
            split_date = all_dates[mid]
        else:
            split_date = all_dates[-recent_days]

        prior_sales = [s for s in sales if s["sales_date"] < split_date]
        recent_sales = [s for s in sales if s["sales_date"] >= split_date]

        prior_total = sum(s["sale_qty"] or 0 for s in prior_sales)
        recent_total = sum(s["sale_qty"] or 0 for s in recent_sales)

        prior_days = max(len(set(s["sales_date"] for s in prior_sales)), 1)
        recent_days_actual = max(
            len(set(s["sales_date"] for s in recent_sales)), 1
        )

        return prior_total / prior_days, recent_total / recent_days_actual

    def _classify_items(
        self, items_with_avg: List[dict]
    ) -> Tuple[List[dict], List[dict]]:
        """상품을 gainer(수요 증가)와 loser(수요 감소)로 분류

        Returns:
            (gainers, losers)
        """
        gainers = [
            i for i in items_with_avg
            if i["ratio"] >= SUBSTITUTION_GROWTH_THRESHOLD
        ]
        losers = [
            i for i in items_with_avg
            if i["ratio"] <= SUBSTITUTION_DECLINE_THRESHOLD
        ]
        return gainers, losers

    def _compute_adjustment_coefficient(self, decline_rate: float) -> float:
        """감소율 기반 발주 조정 계수 계산

        Args:
            decline_rate: recent/prior 비율 (예: 0.5 = 50%로 감소)
        Returns:
            조정 계수 (0.7 ~ 1.0)
        """
        if decline_rate <= 0.3:
            return SUBSTITUTION_COEF_SEVERE     # 0.7
        elif decline_rate <= 0.5:
            return SUBSTITUTION_COEF_MODERATE   # 0.8
        elif decline_rate <= 0.7:
            return SUBSTITUTION_COEF_MILD       # 0.9
        else:
            return 1.0

    def _calculate_confidence(
        self, gainer: dict, loser: dict, total_change_rate: float
    ) -> float:
        """잠식 판정 신뢰도 계산

        기준:
        - 총량 변화가 작을수록 높음 (잠식에 의한 이동)
        - gainer 증가율과 loser 감소율이 클수록 높음
        """
        # 총량 안정성: 0~20% 변화 -> 1.0~0.5
        stability = max(0.5, 1.0 - total_change_rate * 2.5)

        # 변화 강도: gainer 증가율 + loser 감소량
        growth_strength = min(1.0, (gainer["ratio"] - 1.0) / 1.0)  # 0~1
        decline_strength = min(1.0, (1.0 - loser["ratio"]) / 0.7)  # 0~1

        confidence = (
            stability * 0.4
            + growth_strength * 0.3
            + decline_strength * 0.3
        )
        return round(min(1.0, max(0.0, confidence)), 2)

    # -----------------------------------------------------------------
    # 피드백 조회 (ImprovedPredictor에서 사용)
    # -----------------------------------------------------------------

    def get_adjustment(self, item_cd: str) -> float:
        """상품의 활성 잠식 조정 계수 조회

        Args:
            item_cd: 상품 코드
        Returns:
            조정 계수 (0.7~1.0, 잠식 없으면 1.0)
        """
        if item_cd in self._cache:
            return self._cache[item_cd]

        if self._preloaded:
            return self._cache.get(item_cd, 1.0)

        today = datetime.now().strftime("%Y-%m-%d")
        events = self.repo.get_active_events(item_cd, today)
        if not events:
            self._cache[item_cd] = 1.0
            return 1.0

        # 가장 낮은(보수적) 계수 사용
        coef = min(e.get("adjustment_coefficient", 1.0) for e in events)
        self._cache[item_cd] = coef
        return coef

    def preload(self, item_cds: List[str]) -> None:
        """여러 상품의 잠식 이벤트 배치 프리로드

        Args:
            item_cds: 상품 코드 목록
        """
        if not item_cds:
            self._preloaded = True
            return

        today = datetime.now().strftime("%Y-%m-%d")
        events_map = self.repo.get_active_events_batch(item_cds, today)

        for item_cd in item_cds:
            events = events_map.get(item_cd, [])
            if events:
                coef = min(
                    e.get("adjustment_coefficient", 1.0) for e in events
                )
                self._cache[item_cd] = coef
            else:
                self._cache[item_cd] = 1.0

        self._preloaded = True
