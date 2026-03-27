"""
PredictionCacheManager — 배치 캐시 통합 관리

ImprovedPredictor에서 추출된 단일 책임 클래스.
predict_batch에서 사용하는 7개 배치 캐시를 일괄 로드/관리한다.

god-class-decomposition PDCA Step 4
"""

from typing import Dict, List, Optional
from collections import Counter

from src.utils.logger import get_logger

logger = get_logger(__name__)


class PredictionCacheManager:
    """배치 캐시 통합 관리

    ImprovedPredictor.predict_batch()에서 사용되는 캐시를 로드하는 메서드 모음.
    각 메서드는 캐시 dict를 반환하여 호출자(Facade)가 적절히 저장한다.
    """

    def __init__(self, data_provider, store_id: str, db_path: str = None):
        """
        Args:
            data_provider: PredictionDataProvider 인스턴스
            store_id: 점포 코드
            db_path: DB 경로 (food coef 캐시용)
        """
        self._data = data_provider
        self.store_id = store_id
        self.db_path = db_path

    def load_new_products(self, existing_cache: dict = None) -> dict:
        """신제품 모니터링 캐시 로딩 (small_cd 포함)

        Returns:
            {item_cd: item_dict} 캐시
        """
        if existing_cache:
            return existing_cache
        try:
            from src.infrastructure.database.repos import DetectedNewProductRepository
            repo = DetectedNewProductRepository(store_id=self.store_id)
            items = repo.get_by_lifecycle_status(
                ["monitoring"], store_id=self.store_id
            )
            cache = {item["item_cd"]: item for item in items}
            if cache:
                self._enrich_with_small_cd(cache)
            return cache
        except Exception:
            return {}

    def _enrich_with_small_cd(self, cache: dict) -> None:
        """캐시에 small_cd 정보 추가 (product_details에서 배치 조회)"""
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_common_connection()
            try:
                cursor = conn.cursor()
                item_cds = list(cache.keys())
                placeholders = ", ".join("?" for _ in item_cds)
                cursor.execute(
                    f"SELECT item_cd, small_cd FROM product_details "
                    f"WHERE item_cd IN ({placeholders})",
                    item_cds,
                )
                for row in cursor.fetchall():
                    icd = row["item_cd"]
                    if icd in cache:
                        cache[icd]["small_cd"] = row["small_cd"]
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"small_cd 캐시 보강 실패: {e}")

    def load_receiving_stats(self) -> dict:
        """입고 패턴 통계 배치 캐시 로드

        Returns:
            {item_cd: stats_dict} 캐시
        """
        try:
            from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
            from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository

            recv_repo = ReceivingRepository(store_id=self.store_id)
            ot_repo = OrderTrackingRepository(store_id=self.store_id)

            pattern_stats = recv_repo.get_receiving_pattern_stats_batch(
                store_id=self.store_id, days=30
            )
            pending_ages = ot_repo.get_pending_age_batch(store_id=self.store_id)

            cache = {}
            all_items = set(pattern_stats.keys()) | set(pending_ages.keys())
            for item_cd in all_items:
                stats = dict(pattern_stats.get(item_cd, {}))
                stats["pending_age_days"] = pending_ages.get(item_cd, 0)
                cache[item_cd] = stats

            logger.info(f"[입고패턴] 캐시 로드: {len(cache)}개 상품")
            return cache
        except Exception as e:
            logger.warning(f"[입고패턴] 캐시 로드 실패 (무시): {e}")
            return {}

    def load_group_contexts(self, ml_predictor=None) -> tuple:
        """그룹 컨텍스트 캐시 프리로드

        Returns:
            (smallcd_peer_cache, item_smallcd_map, lifecycle_cache)
        """
        try:
            from src.prediction.ml.data_pipeline import MLDataPipeline
            pipeline = MLDataPipeline(self.db_path, store_id=self.store_id)
            smallcd_peer = pipeline.get_smallcd_peer_avg_batch()
            item_smallcd = pipeline.get_item_smallcd_map()
            lifecycle = pipeline.get_lifecycle_stages_batch()
            logger.info(
                f"[그룹컨텍스트] 캐시 로드: peer={len(smallcd_peer)}, "
                f"smallcd_map={len(item_smallcd)}, "
                f"lifecycle={len(lifecycle)}"
            )
            if ml_predictor:
                ml_predictor.load_group_models()
            return smallcd_peer, item_smallcd, lifecycle
        except Exception as e:
            logger.debug(f"[그룹컨텍스트] 캐시 로드 실패 (무시): {e}")
            return {}, {}, {}

    def load_food_weekday(self, item_codes: list, get_connection_fn=None) -> dict:
        """푸드 요일 계수 배치 캐시 프리로드

        Returns:
            {mid_cd: coef} 캐시
        """
        from datetime import datetime as _dt
        cache = {}

        if not get_connection_fn:
            return cache

        try:
            conn = get_connection_fn()
            try:
                cursor = conn.cursor()
                food_mids = set()
                chunk_size = 500
                for i in range(0, len(item_codes), chunk_size):
                    chunk = item_codes[i:i + chunk_size]
                    placeholders = ','.join('?' * len(chunk))
                    cursor.execute(f"""
                        SELECT DISTINCT mid_cd
                        FROM daily_sales
                        WHERE item_cd IN ({placeholders})
                        AND store_id = ?
                        AND mid_cd IN ('001','002','003','004','005','012')
                        AND sales_date >= date('now', '-14 days')
                    """, (*chunk, self.store_id))
                    for row in cursor.fetchall():
                        food_mids.add(row[0])

                if not food_mids:
                    return cache

                sqlite_weekday = _dt.now().strftime("%w")
                for mid in food_mids:
                    cursor.execute("""
                        SELECT
                            CAST(strftime('%w', sales_date) AS INTEGER) as wd,
                            SUM(sale_qty) as total_qty,
                            COUNT(DISTINCT sales_date) as day_cnt
                        FROM daily_sales
                        WHERE mid_cd = ? AND store_id = ?
                        AND sales_date >= date('now', '-28 days')
                        GROUP BY wd
                    """, (mid, self.store_id))
                    rows = cursor.fetchall()
                    if len(rows) >= 5:
                        wd_avgs = {}
                        for wd, total_qty, day_cnt in rows:
                            if day_cnt > 0:
                                wd_avgs[wd] = (total_qty or 0) / day_cnt
                        if wd_avgs:
                            overall = sum(wd_avgs.values()) / len(wd_avgs)
                            if overall > 0:
                                target_avg = wd_avgs.get(int(sqlite_weekday), overall)
                                coef = round(max(0.80, min(1.25, target_avg / overall)), 3)
                                cache[mid] = coef

                logger.info(
                    f"[푸드캐시] 프리로드: weekday {len(cache)}개 mid_cd"
                )
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[푸드캐시] 프리로드 실패 (개별 쿼리 폴백): {e}")

        return cache

    def load_ot_pending(self) -> Optional[dict]:
        """order_tracking 미입고 합계 배치 캐시 로드

        Returns:
            None = 로드 실패/비활성 -> 교차검증 스킵
            {} = 로드 성공 + pending 없음
            {item_cd: qty} = 로드 성공 + pending 있음
        """
        try:
            from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
            ot_repo = OrderTrackingRepository(store_id=self.store_id)
            result = ot_repo.get_pending_qty_sum_batch(store_id=self.store_id)

            if result:
                logger.info(
                    f"[미입고교차검증] 캐시 로드: {len(result)}개 상품 (pending 잔여)"
                )
            else:
                logger.debug(
                    "[미입고교차검증] 캐시 로드: order_tracking pending 없음"
                )
            return result
        except Exception as e:
            logger.warning(f"[미입고교차검증] 캐시 로드 실패 (무시, RI값 사용): {e}")
            return None

    def load_demand_patterns(self, item_codes: List[str]) -> dict:
        """수요 패턴 분류 배치 캐시 로드

        Returns:
            {item_cd: ClassificationResult} 캐시
        """
        try:
            from src.prediction.demand_classifier import DemandClassifier
            classifier = DemandClassifier(store_id=self.store_id)

            items = []
            for item_cd in item_codes:
                product = self._data.get_product_info(item_cd)
                if product:
                    items.append({"item_cd": item_cd, "mid_cd": product.get("mid_cd", "")})

            if items:
                cache = classifier.classify_batch(items)
                pattern_counts = Counter(
                    r.pattern.value for r in cache.values()
                )
                logger.info(
                    f"[수요패턴] 캐시 로드: {len(cache)}개 "
                    f"(daily={pattern_counts.get('daily', 0)}, "
                    f"frequent={pattern_counts.get('frequent', 0)}, "
                    f"intermittent={pattern_counts.get('intermittent', 0)}, "
                    f"slow={pattern_counts.get('slow', 0)})"
                )
                return cache
            return {}
        except Exception as e:
            logger.warning(f"[수요패턴] 캐시 로드 실패 (기존 파이프라인 유지): {e}")
            return {}
