"""
자동 발주 시스템
- 예측 모듈과 발주 실행기 통합
- DB 기반 판매량 예측 -> 발주 목록 생성 -> BGF 시스템에서 발주 실행
- 상품별 발주 가능 요일에 맞춰 요일별 그룹핑 발주
"""

import csv
import json
import math
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.utils.logger import get_logger
from src.utils.popup_manager import close_all_popups, close_alerts

logger = get_logger(__name__)

from src.settings.timing import (
    AFTER_ACTION_WAIT, ORDER_SCREEN_CLEANUP_WAIT, DOM_SETTLE_WAIT,
    INTER_REQUEST_DELAY, PROGRESS_LOG_INTERVAL,
)
from src.settings.constants import (
    DEFAULT_ORDERABLE_DAYS,
    LOG_SEPARATOR_NORMAL, LOG_SEPARATOR_WIDE, LOG_SEPARATOR_EXTRA,
    LOG_SEPARATOR_FULL,
    PASS_MAX_ORDER_QTY, ENABLE_PASS_SUPPRESSION,
    DEFAULT_STORE_ID,
    FORCE_MAX_DAYS,
    FOOD_CATEGORIES,
    FOOD_SHORT_EXPIRY_PENDING_DISCOUNT,
    CATEGORY_EXPIRY_DAYS,
    CATEGORY_SITE_BUDGET_ENABLED,
    MAX_ORDER_MULTIPLIER,
)

from src.infrastructure.database.repos import (
    ProductDetailRepository,
    OrderRepository,
    SalesRepository,
    OrderTrackingRepository,
    RealtimeInventoryRepository,
    AutoOrderItemRepository,
    SmartOrderItemRepository,
    AppSettingsRepository,
    InventoryBatchRepository,
    OrderExclusionRepository,
)
from src.infrastructure.database.repos.order_exclusion_repo import ExclusionType
from src.prediction.predictor import OrderPredictor
from src.prediction.improved_predictor import ImprovedPredictor, PredictionResult, PredictionLogger
from src.prediction.pre_order_evaluator import PreOrderEvaluator, EvalDecision
from src.prediction.eval_config import EvalConfig
from src.prediction.eval_calibrator import EvalCalibrator
from src.collectors.product_info_collector import ProductInfoCollector
from src.collectors.order_prep_collector import OrderPrepCollector
from src.alert.config import ALERT_CATEGORIES
from src.alert.delivery_utils import (
    get_delivery_type,
    calculate_shelf_life_after_arrival
)
from src.prediction.categories.food_daily_cap import apply_food_daily_cap
from src.prediction.category_demand_forecaster import CategoryDemandForecaster
from src.prediction.large_category_forecaster import LargeCategoryForecaster

# OrderUnitConverter가 없을 수 있으므로 안전하게 import
try:
    from .order_unit import OrderUnitConverter, format_order_display
except ImportError:
    OrderUnitConverter = None
    format_order_display = lambda qty, unit, size: f"{qty}개"


class AutoOrderSystem:
    """
    자동 발주 시스템
    - 예측 -> 발주 목록 생성 -> BGF 시스템 발주 실행
    - 발주 후 order_tracking에 저장 (폐기 관리용)
    - 미입고 수량 사전 조회로 중복 발주 방지
    """

    def __init__(self, driver: Optional[Any] = None, use_improved_predictor: bool = True, store_id: str = DEFAULT_STORE_ID) -> None:
        """
        Args:
            driver: Selenium WebDriver (로그인된 상태)
                    None이면 예측/목록 생성만 수행 (발주 실행 안함)
            use_improved_predictor: True면 개선된 예측기 사용 (31일 데이터 기반)
            store_id: 점포 코드
        """
        self.driver = driver
        self.use_improved_predictor = use_improved_predictor
        self.store_id = store_id

        # 예측기 선택
        if use_improved_predictor:
            self.improved_predictor = ImprovedPredictor(store_id=store_id)
            self.predictor = OrderPredictor(store_id=store_id)  # 폴백용
            logger.info(f"개선된 예측기 사용 (31일 데이터 기반, store_id={store_id})")
        else:
            self.improved_predictor = None
            self.predictor = OrderPredictor(store_id=store_id)
            logger.info("기존 예측기 사용")

        self.executor = None
        self.tracking_repo = OrderTrackingRepository(store_id=self.store_id)
        self.pending_collector = None
        self.prediction_logger = PredictionLogger(store_id=self.store_id)  # 예측 로그 저장용
        self._eval_config = EvalConfig.load()
        self.pre_order_evaluator = PreOrderEvaluator(config=self._eval_config, store_id=self.store_id)
        self._eval_calibrator = EvalCalibrator(config=self._eval_config, store_id=self.store_id)

        # FORCE/URGENT 보호용 사전 평가 결과 (조정 단계에서 참조)
        self._last_eval_results: Dict = {}

        # 미취급 상품 목록 (조회 시 "해당 점포에 상품이 없습니다" Alert 발생)
        self._unavailable_items: set = set()
        # 상품 상세 정보 캐시 (반복 DB 조회 방지)
        self._product_detail_cache: Dict[str, Any] = {}
        self._product_repo = ProductDetailRepository()  # db_type="common" → 항상 common.db

        # 발주중지(CUT) 상품 목록
        self._cut_items: set = set()

        # 발주 제외 사유 기록 (배치 저장용)
        self._exclusion_records: List[Dict[str, Any]] = []
        self._exclusion_repo = OrderExclusionRepository(store_id=self.store_id)

        # 자동발주 상품 목록 (BGF 본부 관리 - 중복 발주 방지)
        self._auto_order_items: set = set()

        # 스마트발주 상품 목록 (BGF 본부 관리 - 중복 발주 방지)
        self._smart_order_items: set = set()

        # 카테고리 총량 예측기 (신선식품 floor 보충)
        self._category_forecaster = CategoryDemandForecaster(store_id=self.store_id)

        # 대분류 기반 카테고리 총량 예측기 (large_cd level floor 보충)
        self._large_category_forecaster = LargeCategoryForecaster(store_id=self.store_id)

        # 드라이버가 있으면 발주 실행기 및 미입고 수집기 초기화
        if driver:
            from .order_executor import OrderExecutor
            self.executor = OrderExecutor(driver, store_id=self.store_id)
            self.pending_collector = OrderPrepCollector(driver, save_to_db=True, store_id=self.store_id)

        # DB 인벤토리 Repository
        self._inventory_repo = RealtimeInventoryRepository(store_id=self.store_id)

        # 자동발주 / 스마트발주 상품 캐시 Repository
        self._auto_order_repo = AutoOrderItemRepository(store_id=self.store_id)
        self._smart_order_repo = SmartOrderItemRepository(store_id=self.store_id)

        # 추출된 클래스 인스턴스 (god-class-decomposition)
        from .order_data_loader import OrderDataLoader
        from .order_filter import OrderFilter
        from .order_adjuster import OrderAdjuster
        from .order_tracker import OrderTracker
        self._loader = OrderDataLoader(store_id=self.store_id)
        self._filter = OrderFilter(store_id=self.store_id)
        self._adjuster = OrderAdjuster()
        self._tracker = OrderTracker(self.tracking_repo, self._product_repo, self.store_id)

    def __getattr__(self, name: str):
        """추출된 클래스 인스턴스 lazy 생성 (테스트 호환용)

        테스트에서 object.__new__(AutoOrderSystem) 으로 __init__ 우회 시
        _adjuster, _loader, _filter, _tracker 및 공유 상태가 없으므로 여기서 생성.
        """
        # 추출된 클래스 인스턴스
        if name == '_adjuster':
            from .order_adjuster import OrderAdjuster
            obj = OrderAdjuster()
            self.__dict__['_adjuster'] = obj
            return obj
        if name == '_loader':
            from .order_data_loader import OrderDataLoader
            store_id = self.__dict__.get('store_id', DEFAULT_STORE_ID)
            obj = OrderDataLoader(store_id=store_id)
            self.__dict__['_loader'] = obj
            return obj
        if name == '_filter':
            from .order_filter import OrderFilter
            store_id = self.__dict__.get('store_id', DEFAULT_STORE_ID)
            obj = OrderFilter(store_id=store_id)
            self.__dict__['_filter'] = obj
            return obj
        if name == '_tracker':
            from .order_tracker import OrderTracker as OT
            tracking_repo = self.__dict__.get('tracking_repo')
            product_repo = self.__dict__.get('_product_repo')
            store_id = self.__dict__.get('store_id', DEFAULT_STORE_ID)
            obj = OT(tracking_repo, product_repo, store_id)
            self.__dict__['_tracker'] = obj
            return obj
        # 공유 mutable 상태 (테스트에서 __init__ 우회 시 기본값)
        _defaults = {
            '_exclusion_records': list,
            '_cut_items': set,
            '_unavailable_items': set,
            '_last_eval_results': dict,
            '_last_stock_discrepancies': list,
            '_auto_order_items': set,
            '_smart_order_items': set,
            '_cut_lost_items': list,
        }
        if name in _defaults:
            obj = _defaults[name]()
            self.__dict__[name] = obj
            return obj
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    def _refilter_cut_items(
        self, order_list: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """CUT 상품을 order_list에서 제거하고 탈락 정보를 반환.

        Returns:
            (filtered_list, cut_lost_items)
            cut_lost_items: 푸드 카테고리이며 predicted_sales > 0인 CUT 탈락 상품
        """
        if not self._cut_items:
            return order_list, []

        cut_lost_items = [
            item for item in order_list
            if item.get("item_cd") in self._cut_items
            and item.get("mid_cd", "") in FOOD_CATEGORIES
            and (item.get("predicted_sales", 0) or item.get("final_order_qty", 0)) > 0
        ]
        filtered = [
            item for item in order_list
            if item.get("item_cd") not in self._cut_items
        ]
        cut_removed = len(order_list) - len(filtered)
        if cut_removed > 0:
            logger.info(f"[CUT 재필터] prefetch 실시간 감지 포함 {cut_removed}개 CUT 상품 제외")
        return filtered, cut_lost_items

    def load_unavailable_from_db(self) -> None:
        """DB에서 미취급 상품 목록 로드"""
        try:
            self._unavailable_items.update(
                self._loader.load_unavailable(self._inventory_repo)
            )
        except Exception as e:
            logger.warning(f"미취급 상품 목록 DB 로드 실패 (빈 목록으로 계속): {e}")

    def load_cut_items_from_db(self) -> None:
        """DB에서 발주중지(CUT) 상품 목록 로드"""
        try:
            self._cut_items.update(
                self._loader.load_cut_items(self._inventory_repo)
            )
        except Exception as e:
            logger.warning(f"발주중지(CUT) 상품 목록 DB 로드 실패 (빈 목록으로 계속): {e}")

    def load_auto_order_items(self, skip_site_fetch: bool = False) -> None:
        """자동발주 + 스마트발주 상품 목록 조회 (사이트 우선 + DB 캐시 fallback)"""
        try:
            auto_items, smart_items = self._loader.load_auto_order_items(
                driver=self.driver,
                auto_order_repo=self._auto_order_repo,
                smart_order_repo=self._smart_order_repo,
                skip_site_fetch=skip_site_fetch,
            )
            self._auto_order_items = auto_items
            self._smart_order_items = smart_items
        except Exception as e:
            logger.warning(f"자동/스마트발주 상품 목록 로드 실패 (빈 목록으로 계속): {e}")

    def load_inventory_cache_from_db(self) -> None:
        """DB에서 재고/미입고 데이터를 예측기 캐시에 로드"""
        self._loader.load_inventory_cache(
            inventory_repo=self._inventory_repo,
            predictor=self.predictor,
            improved_predictor=self.improved_predictor,
            use_improved=self.use_improved_predictor,
        )

    def get_inventory_summary(self) -> Dict[str, Any]:
        """
        인벤토리 DB 요약 정보 조회

        Returns:
            {"total": N, "available": N, "unavailable": N, "with_pending": N}
        """
        return self._inventory_repo.get_summary(store_id=self.store_id)

    def _exclude_filtered_items(self, order_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """발주 목록에서 전역제외/미취급/CUT/자동발주/스마트발주/발주정지 상품 제외"""
        return self._filter.exclude_filtered_items(
            order_list=order_list,
            unavailable_items=self._unavailable_items,
            cut_items=self._cut_items,
            auto_order_items=self._auto_order_items,
            smart_order_items=self._smart_order_items,
            exclusion_records=self._exclusion_records,
        )

    def _deduct_manual_food_orders(
        self,
        order_list: List[Dict[str, Any]],
        min_order_qty: int = 1,
    ) -> List[Dict[str, Any]]:
        """푸드 카테고리 수동 발주분 차감"""
        return self._filter.deduct_manual_food_orders(
            order_list=order_list,
            min_order_qty=min_order_qty,
            exclusion_records=self._exclusion_records,
        )

    def _warn_stale_cut_items(self, order_list: List[Dict[str, Any]]) -> None:
        """발주 목록 내 CUT 상태 미검증(stale) 상품 경고"""
        self._filter.warn_stale_cut_items(order_list, self._inventory_repo)

    def _add_new_product_items(self, order_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """신상품 도입 현황: 미도입 상품을 발주 목록에 추가

        미도입 + 발주가능 + 아직 미발주 상품을 qty=1로 추가
        이미 order_list에 있는 상품은 건너뜀
        """
        try:
            from src.infrastructure.database.repos import NewProductStatusRepository
            from src.settings.constants import NEW_PRODUCT_INTRO_ORDER_QTY

            month_ym = datetime.now().strftime("%Y%m")
            np_repo = NewProductStatusRepository(store_id=self.store_id)
            unordered = np_repo.get_unordered_items(
                self.store_id, month_ym, item_type="midoip"
            )
            if not unordered:
                return order_list

            existing_codes = {item.get("item_cd") for item in order_list}
            added = 0
            for item in unordered:
                item_cd = item.get("item_cd", "")
                if item_cd in existing_codes:
                    continue
                if item_cd in self._cut_items:
                    continue

                order_list.append({
                    "item_cd": item_cd,
                    "item_nm": item.get("item_nm", ""),
                    "mid_cd": "",
                    "final_order_qty": NEW_PRODUCT_INTRO_ORDER_QTY,
                    "orderable_day": DEFAULT_ORDERABLE_DAYS,
                    "source": "new_product_introduction",
                })
                existing_codes.add(item_cd)
                added += 1

            if added:
                logger.info(f"신상품 미도입 {added}개 발주 목록에 추가 (qty={NEW_PRODUCT_INTRO_ORDER_QTY})")

        except Exception as e:
            logger.warning(f"신상품 미도입 상품 추가 실패: {e}")

        return order_list

    # ------------------------------------------------------------------
    # 3일발주 후속 관리 (미도입 자동 발주와 완전 분리)
    # ------------------------------------------------------------------

    def _process_3day_follow_orders(self, order_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """3일발주 미달성 상품 후속 발주 (분산 발주 + AI 총량 합산)

        Phase A: 기존 mids 항목 기반 후속 발주 (BGF 수집 데이터)
        Phase B: new_product_3day_tracking 기반 분산 발주 (our_order_count 추적)
        Phase C: AI 예측 목록과 총량 합산 (중복 방지)
        """
        try:
            # Phase A: 기존 mids 후속 발주 (레거시 호환 — placed>0만 대상)
            order_list = self._process_3day_follow_legacy(order_list)

            # Phase B: 분산 발주 추적 기반 (new_product_3day_tracking)
            from src.application.services.new_product_order_service import (
                get_today_new_product_orders,
                merge_with_ai_orders,
                record_order_completed,
            )

            def _sales_fn(item_cd, last_ordered_at):
                """마지막 발주 이후 판매량 조회"""
                return self._get_sales_after_date(item_cd, last_ordered_at)

            def _stock_fn(item_cd):
                """현재 재고 조회"""
                inv = self._inventory_repo.get(item_cd, store_id=self.store_id)
                return inv.get("stock_qty", 0) if inv else 0

            np_orders = get_today_new_product_orders(
                store_id=self.store_id,
                sales_fn=_sales_fn,
                stock_fn=_stock_fn,
            )

            if np_orders:
                # CUT 상품 제외
                np_orders = [o for o in np_orders if o.get("product_code") not in self._cut_items]

                # Phase C: AI 예측 목록과 합산
                order_list = merge_with_ai_orders(order_list, np_orders)

                # 발주 완료 후 추적 업데이트는 execute() 성공 후 호출
                self._pending_np3day_orders = np_orders
                logger.info(f"신상품3일 분산발주 {len(np_orders)}개 합산 완료")
            else:
                self._pending_np3day_orders = []

        except Exception as e:
            logger.warning(f"3일발주 후속 처리 실패 (발주 플로우 계속): {e}")

        return order_list

    def _process_3day_follow_legacy(self, order_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """레거시 3일발주 후속: mids 항목 기반 (placed>0, BGF DS_YN 파싱)"""
        try:
            from src.infrastructure.database.repos import NewProductStatusRepository
            from src.settings.constants import NEW_PRODUCT_INTRO_ORDER_QTY, NEW_PRODUCT_DS_MIN_ORDERS
            from src.domain.new_product.convenience_order_scheduler import (
                plan_3day_orders, should_order_today,
            )

            month_ym = datetime.now().strftime("%Y%m")
            np_repo = NewProductStatusRepository(store_id=self.store_id)
            mids = np_repo.get_missing_items(self.store_id, month_ym, item_type="mids")
            if not mids:
                return order_list

            weekly = np_repo.get_weekly_status(self.store_id, month_ym)
            week_periods = {w["week_no"]: (w["sta_dd"], w["end_dd"]) for w in weekly}

            existing_codes = {item.get("item_cd") for item in order_list}
            added = 0
            today_str = datetime.now().strftime("%Y-%m-%d")

            for item in mids:
                item_cd = item.get("item_cd", "")
                if item_cd in existing_codes or item_cd in self._cut_items:
                    continue

                placed, required = self._parse_ds_yn(item.get("ds_yn", ""))
                if placed >= required:
                    continue
                if placed == 0:
                    continue

                week_no = item.get("week_no")
                period = week_periods.get(week_no, ("", ""))
                sta_dd, end_dd = period[0], period[1]
                if not sta_dd or not end_dd:
                    continue

                plan = plan_3day_orders(item_cd, sta_dd, end_dd)
                last_order_sold = self._check_item_sold_after_receiving(item_cd)

                inv = self._inventory_repo.get(item_cd, store_id=self.store_id)
                current_stock = inv.get("stock_qty", 0) if inv else 0

                should, reason = should_order_today(
                    item_cd=item_cd,
                    today=today_str,
                    order_plan=plan,
                    current_stock=current_stock,
                    daily_avg_sales=0.5,
                    shelf_life_days=30,
                    orders_placed=placed,
                    last_order_sold=last_order_sold,
                )

                if should:
                    order_list.append({
                        "item_cd": item_cd,
                        "item_nm": item.get("item_nm", ""),
                        "mid_cd": "",
                        "final_order_qty": NEW_PRODUCT_INTRO_ORDER_QTY,
                        "orderable_day": DEFAULT_ORDERABLE_DAYS,
                        "source": "new_product_3day_follow",
                    })
                    existing_codes.add(item_cd)
                    added += 1
                    logger.info(f"3일발주 후속(레거시): {item_cd} ({reason})")

            if added:
                logger.info(f"3일발주 후속(레거시) {added}개 발주 목록에 추가")

        except Exception as e:
            logger.warning(f"3일발주 레거시 처리 실패: {e}")

        return order_list

    def _get_sales_after_date(self, item_cd: str, after_date: str) -> int:
        """특정 날짜 이후 판매량 합계 조회"""
        try:
            sales_repo = SalesRepository(store_id=self.store_id)
            sales = sales_repo.get_sales_history(item_cd, days=30, store_id=self.store_id)
            if not sales:
                return 0
            total = 0
            for record in sales:
                sale_date = record.get("sales_date", "")
                if after_date and sale_date > after_date[:10]:
                    total += record.get("sale_qty", 0)
            return total
        except Exception:
            return 0

    def _update_np3day_tracking_after_order(self) -> None:
        """발주 성공 후 new_product_3day_tracking 업데이트"""
        if not hasattr(self, '_pending_np3day_orders') or not self._pending_np3day_orders:
            return
        try:
            from src.application.services.new_product_order_service import record_order_completed
            from src.infrastructure.database.repos import NP3DayTrackingRepo

            repo = NP3DayTrackingRepo(store_id=self.store_id)
            for np_item in self._pending_np3day_orders:
                code = np_item.get("product_code", "")
                week_label = np_item.get("week_label", "")
                base_name = np_item.get("base_name", "")
                if not code or not week_label:
                    continue

                # base_name이 있으면 그룹 단위 조회, 없으면 product_code 단위
                if base_name:
                    tracking = repo.get_group_tracking(self.store_id, week_label, base_name)
                else:
                    tracking = repo.get_tracking(self.store_id, week_label, code)
                if not tracking:
                    continue

                our_count_after = tracking.get("our_order_count", 0) + 1
                record_order_completed(
                    store_id=self.store_id,
                    week_label=week_label,
                    product_code=code,
                    week_start=tracking.get("week_start", ""),
                    interval_days=tracking.get("order_interval_days", 0),
                    our_order_count_after=our_count_after,
                    base_name=base_name,
                    selected_code=code,
                )
            self._pending_np3day_orders = []
        except Exception as e:
            logger.warning(f"신상품3일 추적 업데이트 실패: {e}")

    # ------------------------------------------------------------------
    # 스마트발주 오버라이드: 단품별발주로 제출하여 수동 전환
    # ------------------------------------------------------------------

    def _inject_smart_order_items(
        self,
        order_list: List[Dict[str, Any]],
        target_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """스마트발주 상품을 예측 기반으로 발주 목록에 주입

        스마트발주 상품 전체를 우리 예측 파이프라인으로 관리:
        - 이미 목록에 있는 상품 → smart_override 플래그만 추가
        - 목록에 없는 상품 → predict_batch()로 예측 후 추가
        - 예측 qty=0인 상품도 추가 (단품별발주 제출 → 스마트→수동 전환)

        Returns:
            주입된 order_list
        """
        try:
            from src.infrastructure.database.repos import AppSettingsRepository
            from src.settings.constants import SMART_OVERRIDE_MIN_QTY

            settings_repo = AppSettingsRepository(store_id=self.store_id)
            if not settings_repo.get("SMART_ORDER_OVERRIDE", False):
                return order_list

            smart_details = self._smart_order_repo.get_all_detail(
                store_id=self.store_id
            )
            if not smart_details:
                logger.info("[SmartOverride] 스마트발주 상품 없음")
                return order_list

            smart_set = {s["item_cd"] for s in smart_details}
            existing_codes = {item.get("item_cd") for item in order_list}

            # 이미 목록에 있는 스마트 상품 → 플래그만 추가
            for item in order_list:
                if item.get("item_cd") in smart_set:
                    item["smart_override"] = True

            # 목록에 없는 스마트 상품 → 예측 수행
            # [C-1 Fix] 미취급(is_available=0) 상품도 제외 (BGF Alert 방지)
            unavailable = self._unavailable_items or set()
            unavail_filtered = [
                s for s in smart_details
                if s["item_cd"] in unavailable
                and s["item_cd"] not in existing_codes
            ]
            if unavail_filtered:
                logger.info(
                    f"[SmartOverride] is_available=0 제외: {len(unavail_filtered)}건"
                )

            # [C-3 Fix] 영구제외/발주정지 등 _exclusion_records 상품도 제외
            excluded_cds = {
                r.get("item_cd") for r in (self._exclusion_records or [])
                if r.get("item_cd")
            }
            excl_filtered = [
                s for s in smart_details
                if s["item_cd"] in excluded_cds
                and s["item_cd"] not in existing_codes
            ]
            if excl_filtered:
                logger.info(
                    f"[SmartOverride] exclusion_records 제외: {len(excl_filtered)}건 "
                    f"(사유: {', '.join(set(r.get('exclusion_type','?') for r in self._exclusion_records if r.get('item_cd') in {e['item_cd'] for e in excl_filtered}))})"
                )

            missing = [
                s for s in smart_details
                if s["item_cd"] not in existing_codes
                and s["item_cd"] not in self._cut_items
                and s["item_cd"] not in unavailable
                and s["item_cd"] not in excluded_cds
            ]

            if not missing:
                in_list = len(smart_set & existing_codes)
                logger.info(
                    f"[SmartOverride] {len(smart_set)}개 전부 "
                    f"발주 목록 포함({in_list}개) 또는 CUT"
                )
                return order_list

            # predict_batch로 예측 (FORCE_ORDER 보충과 동일 패턴)
            missing_codes = [s["item_cd"] for s in missing]
            missing_info = {s["item_cd"]: s for s in missing}
            # [C-4 Fix] predict_batch 실패 시 pred_map={} 폴백 (기존 로직에서 qty=0 처리됨)
            # → 과발주 위험 없이 SMART_OVERRIDE_MIN_QTY로 사용자가 최소 수량 제어 가능
            try:
                predictions = self.improved_predictor.predict_batch(
                    missing_codes, target_date
                )
                pred_map = {r.item_cd: r for r in predictions}
            except Exception as e:
                logger.warning(
                    f"[SmartOverride] predict_batch 실패 ({len(missing_codes)}건): {e} "
                    f"→ 전체 qty=0 폴백 (SMART_OVERRIDE_MIN_QTY={SMART_OVERRIDE_MIN_QTY})"
                )
                pred_map = {}

            added_positive = 0
            skipped_zero = 0

            for item_cd in missing_codes:
                pred = pred_map.get(item_cd)
                info = missing_info.get(item_cd, {})
                qty = pred.order_qty if pred and pred.order_qty > 0 else 0

                # SMART_OVERRIDE_MIN_QTY 적용 (0이면 0발주, 1이면 최소 1개)
                if qty == 0 and SMART_OVERRIDE_MIN_QTY > 0:
                    qty = SMART_OVERRIDE_MIN_QTY

                # qty=0 스마트발주 취소: BGF 단품별(채택)으로 전환하여 스마트 자동발주 차단
                # 라이브 검증 (2026-03-14): PYUN_QTY=0 → BGF 수락, "단품별(채택)" 전환 확인
                if qty <= 0:
                    skipped_zero += 1
                    # cancel_smart=True → 발주 목록에 포함 (qty=0으로 BGF 제출)
                    cancel_entry = {
                        "item_cd": item_cd,
                        "item_nm": info.get("item_nm", ""),
                        "mid_cd": info.get("mid_cd", ""),
                        "final_order_qty": 0,
                        "order_unit_qty": 1,
                        "orderable_day": DEFAULT_ORDERABLE_DAYS,
                        "smart_override": True,
                        "cancel_smart": True,
                        "source": "smart_cancel",
                    }
                    order_list.append(cancel_entry)
                    existing_codes.add(item_cd)
                    logger.info(f"[SmartOverride] qty=0 취소 주입: {item_cd} ({info.get('item_nm', '')})")
                    continue

                # [C-2 Fix] order_unit_qty 조회 (캐시 활용 — _convert 패턴과 동일)
                # finalize_order_unit_qty가 최종 보정하지만, Floor/Cap 단계 정확도를 위해 여기서도 설정
                unit = 1
                try:
                    if item_cd not in self._product_detail_cache:
                        self._product_detail_cache[item_cd] = self._product_repo.get(item_cd)
                    _pd = self._product_detail_cache.get(item_cd)
                    unit = max(1, int(_pd.get("order_unit_qty") or 1)) if _pd else 1
                except Exception:
                    logger.debug(f"[SmartOverride] {item_cd} order_unit_qty 조회 실패, unit=1 폴백")

                order_entry = {
                    "item_cd": item_cd,
                    "item_nm": info.get("item_nm", ""),
                    "mid_cd": info.get("mid_cd", ""),
                    "final_order_qty": qty,
                    "order_unit_qty": unit,
                    "orderable_day": DEFAULT_ORDERABLE_DAYS,
                    "smart_override": True,
                    "source": "smart_override",
                }

                if pred:
                    order_entry.update({
                        "predicted_sales": round(getattr(pred, "adjusted_qty", 0), 2),
                        "current_stock": getattr(pred, "current_stock", 0),
                        "pending_receiving_qty": getattr(pred, "pending_qty", 0),
                        "safety_stock": getattr(pred, "safety_stock", 0),
                        # dryrun Excel 호환: 예측 상세 필드 추가
                        "demand_pattern": getattr(pred, "demand_pattern", ""),
                        "data_days": getattr(pred, "data_days", 0),
                        "sell_day_ratio": getattr(pred, "sell_day_ratio", 1.0),
                        "model_type": getattr(pred, "model_type", "rule"),
                        "daily_avg": getattr(pred, "predicted_qty", 0),
                        "weekday_coef": getattr(pred, "weekday_coef", 1.0),
                        "confidence": getattr(pred, "confidence", 0),
                        "rule_order_qty": getattr(pred, "rule_order_qty", None),
                        "ml_order_qty": getattr(pred, "ml_order_qty", None),
                        "ml_weight_used": getattr(pred, "ml_weight_used", None),
                        "wma_raw": getattr(pred, "wma_raw", 0.0),
                        "need_qty": getattr(pred, "need_qty", 0.0),
                        "proposal_summary": getattr(pred, "proposal_summary", ""),
                        "round_floor": getattr(pred, "round_floor", 0),
                        "round_ceil": getattr(pred, "round_ceil", 0),
                    })

                order_list.append(order_entry)
                existing_codes.add(item_cd)
                added_positive += 1

            # [B-1 Fix] OVERRIDE 모드 명시 — exclude filter에서 "스마트 제외 OFF" 로그와 구분
            # EXCLUDE_SMART=False(기본) + OVERRIDE=True 조합 시 의도적 이중 처리임을 명확화
            cut_cnt = len([s for s in smart_details if s["item_cd"] in self._cut_items])
            unavail_cnt = len(unavail_filtered) if unavail_filtered else 0
            excl_cnt = len(excl_filtered) if excl_filtered else 0
            cancel_cnt = len([o for o in order_list if o.get("cancel_smart")])
            logger.info(
                f"[SmartOverride:OVERRIDE모드] 스마트발주 {len(smart_set)}개 처리: "
                f"기존목록={len(smart_set) - len(missing) - cut_cnt - unavail_cnt - excl_cnt}개, "
                f"예측추가={added_positive}개, "
                f"취소(qty=0)={cancel_cnt}개"
                f"{f', CUT={cut_cnt}개' if cut_cnt else ''}"
                f"{f', 미취급={unavail_cnt}개' if unavail_cnt else ''}"
                f"{f', 제외={excl_cnt}개' if excl_cnt else ''}"
            )

        except Exception as e:
            logger.warning(f"[SmartOverride] 스마트발주 오버라이드 실패: {e}")

        return order_list

    def _check_item_sold_after_receiving(self, item_cd: str) -> bool:
        """이전 발주분 입고 후 판매 여부 확인

        daily_sales에서 해당 상품의 최근 14일 이력 조회.
        buy_qty > 0인 날(입고일) 이후에 sale_qty > 0이면 판매됨.
        """
        try:
            sales_repo = SalesRepository(store_id=self.store_id)
            sales = sales_repo.get_sales_history(item_cd, days=14, store_id=self.store_id)
            if not sales:
                return False

            recv_date = None
            for record in reversed(sales):
                if record.get("buy_qty", 0) > 0:
                    recv_date = record.get("sales_date", "")
                    break

            if not recv_date:
                return False

            return any(
                r.get("sale_qty", 0) > 0
                for r in sales
                if r.get("sales_date", "") > recv_date
            )
        except Exception:
            return False

    @staticmethod
    def _parse_ds_yn(ds_yn: str):
        """DS_YN 파싱: '1/3(미달성)' -> (placed=1, required=3)"""
        from .order_data_loader import OrderDataLoader
        return OrderDataLoader.parse_ds_yn(ds_yn)

    def close(self) -> None:
        """예측기 및 수집기 리소스 정리"""
        self.predictor.close()
        if self.pending_collector:
            self.pending_collector.close_menu()

    def prefetch_pending_quantities(
        self,
        item_codes: List[str],
        max_items: int = 500
    ) -> Dict[str, int]:
        """미입고 수량 및 실시간 재고 사전 조회 (중복 발주 방지용)"""
        try:
            pending_data, stock_data, new_cut_items, new_unavailable, new_exclusions = \
                self._loader.prefetch_pending(
                    self.pending_collector, item_codes, max_items
                )
        except Exception as e:
            logger.warning(f"미입고/재고 사전 조회 실패 (빈 데이터로 계속): {e}")
            return {}

        # CUT 해제: 성공 조회되었으나 CUT 아닌 상품은 _cut_items에서 제거
        queried_ok = set(pending_data.keys())
        for item_cd in list(self._cut_items):
            if item_cd in queried_ok and item_cd not in new_cut_items:
                self._cut_items.discard(item_cd)
                logger.info(f"[CUT 해제] {item_cd}: 발주 가능 확인")

        # Facade 상태 업데이트
        self._cut_items.update(new_cut_items)
        self._unavailable_items.update(new_unavailable)
        self._exclusion_records.extend(new_exclusions)
        self._last_stock_data = stock_data

        return pending_data

    def _convert_prediction_result_to_dict(self, result: PredictionResult) -> Dict[str, Any]:
        """
        ImprovedPredictor의 PredictionResult를 기존 dict 형식으로 변환

        Args:
            result: PredictionResult 객체

        Returns:
            기존 발주 목록과 호환되는 dict
        """
        # 상품 상세 정보 조회 (캐시 활용)
        if result.item_cd not in self._product_detail_cache:
            self._product_detail_cache[result.item_cd] = self._product_repo.get(result.item_cd)
        product_detail = self._product_detail_cache[result.item_cd]

        orderable_day = DEFAULT_ORDERABLE_DAYS  # 기본값
        order_unit_qty = 1
        promo_type = ""

        if product_detail:
            orderable_day = product_detail.get("orderable_day") or DEFAULT_ORDERABLE_DAYS
            order_unit_qty = product_detail.get("order_unit_qty") or 1
            promo_type = product_detail.get("promo_type") or ""

        return {
            "item_cd": result.item_cd,
            "item_nm": result.item_nm,
            "mid_cd": result.mid_cd,
            "orderable_day": orderable_day,
            "order_unit_qty": order_unit_qty,
            "promo_type": promo_type,
            "current_stock": result.current_stock,
            "pending_receiving_qty": result.pending_qty,
            "expected_stock": result.current_stock + result.pending_qty,
            "predicted_sales": round(result.adjusted_qty, 2),
            "daily_avg": result.predicted_qty,
            "weekday_coef": result.weekday_coef,
            "safety_stock": result.safety_stock,
            "final_order_qty": result.order_qty,
            "recommended_qty": result.order_qty,
            "target_date": result.target_date,
            "confidence": result.confidence,
            "data_days": result.data_days,
            # 재고 소스 메타 (재고 불일치 진단용)
            "stock_source": getattr(result, "stock_source", ""),
            "pending_source": getattr(result, "pending_source", ""),
            "is_stock_stale": getattr(result, "is_stock_stale", False),
            # 유통기한 (단기유통 보호용)
            "expiration_days": self._get_expiration_days_for_item(result, product_detail),
            # dryrun-excel-export: 기존 누락 필드 + 신규 5개 필드
            "demand_pattern": getattr(result, "demand_pattern", ""),
            "sell_day_ratio": getattr(result, "sell_day_ratio", 1.0),
            "model_type": getattr(result, "model_type", "rule"),
            "rule_order_qty": getattr(result, "rule_order_qty", None),
            "ml_order_qty": getattr(result, "ml_order_qty", None),
            "ml_weight_used": getattr(result, "ml_weight_used", None),
            "wma_raw": getattr(result, "wma_raw", 0.0),
            "feat_prediction": (
                getattr(result, "predicted_qty", 0.0)  # blended = WMA+Feature
            ),
            "need_qty": getattr(result, "need_qty", 0.0),
            "proposal_summary": getattr(result, "proposal_summary", ""),
            "round_floor": getattr(result, "round_floor", 0),
            "round_ceil": getattr(result, "round_ceil", 0),
        }

    def _get_expiration_days_for_item(self, result, product_detail: Optional[Dict]) -> int:
        """상품의 유통기한 일수 조회 (3단계 폴백)

        우선순위:
        1. PredictionResult.food_expiration_days (예측 단계에서 설정)
        2. product_detail.expiration_days (DB 조회)
        3. CATEGORY_EXPIRY_DAYS[mid_cd] (카테고리 기본값)
        4. 365 (기본값 - 비푸드)
        """
        # 1. PredictionResult에서 조회
        food_exp = getattr(result, 'food_expiration_days', None)
        if food_exp and food_exp > 0:
            return food_exp

        # 2. product_detail에서 조회
        if product_detail:
            pd_exp = product_detail.get('expiration_days')
            if pd_exp and pd_exp > 0:
                return pd_exp

        # 3. 카테고리 기본값
        mid_cd = getattr(result, 'mid_cd', '')
        cat_exp = CATEGORY_EXPIRY_DAYS.get(mid_cd)
        if cat_exp and cat_exp > 0:
            return cat_exp

        # 4. 기본값
        return 365

    def _get_site_order_counts_by_midcd(self, order_date: str) -> Dict[str, int]:
        """
        site(사용자) 발주 수량을 mid_cd별로 집계.
        수동발주 포함 — floor/cap 계산 시 수동발주 수량을 인식하여 과다 보충 방지.

        Args:
            order_date: 발주일 (YYYY-MM-DD)

        Returns:
            {mid_cd: qty} 예: {'001': 5, '002': 12}
            에러 시 빈 dict (기존 동작 유지)
        """
        try:
            from src.infrastructure.database.connection import DBRouter, attach_common_with_views
            conn = DBRouter.get_connection(store_id=self.store_id)
            attach_common_with_views(conn, self.store_id)

            sql = """
                SELECT p.mid_cd, COALESCE(SUM(ot.order_qty), 0) as total_qty
                FROM order_tracking ot
                JOIN common.products p ON ot.item_cd = p.item_cd
                WHERE ot.order_source = 'site'
                  AND ot.order_date = ?
                  AND ot.store_id = ?
                GROUP BY p.mid_cd
            """
            rows = conn.execute(sql, (order_date, self.store_id)).fetchall()
            result = {row[0]: row[1] for row in rows}
            if result:
                logger.info(f"[SiteBudget] site 발주 수량 조회: {result}")
            return result
        except Exception as e:
            logger.warning(f"[SiteBudget] site 발주 조회 실패 (폴백: 빈 dict): {e}")
            return {}

    def get_recommendations(
        self,
        min_order_qty: int = 1,
        max_items: Optional[int] = None,
        target_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        발주 추천 목록 생성 (예측 기반)

        Args:
            min_order_qty: 최소 발주량 (이하는 제외)
            max_items: 최대 상품 수 (None이면 전체)
            target_date: 발주 대상 날짜

        Returns:
            발주 추천 목록 [{item_cd, item_nm, final_order_qty, orderable_day, ...}, ...]
        """
        logger.info("발주 추천 목록 생성 중...")

        # 개선된 예측기 사용
        if self.use_improved_predictor and self.improved_predictor:
            logger.info("개선된 예측기로 발주량 계산...")

            # 미입고 수량 캐시 설정 (있으면)
            if hasattr(self, '_pending_cache') and self._pending_cache:
                self.improved_predictor.set_pending_cache(self._pending_cache)

            # 실시간 재고 캐시 설정 (있으면)
            if hasattr(self, '_last_stock_data') and self._last_stock_data:
                self.improved_predictor.set_stock_cache(self._last_stock_data)
                logger.info(f"  - 실시간 재고 캐시: {len(self._last_stock_data)}개 상품")

            # ★ 사전 발주 평가 (상위 필터)
            skip_codes = set()
            force_order_codes = []
            eval_results = {}
            try:
                eval_results = self.pre_order_evaluator.evaluate_all(write_log=True)
                self._last_eval_results = eval_results  # 조정 단계에서 FORCE/URGENT 보호용
                if eval_results:
                    order_codes, skip_codes = self.pre_order_evaluator.get_filtered_items(eval_results)
                    # FORCE_ORDER 상품코드 보관 (예측 누락 시 보충용)
                    force_order_codes = [
                        cd for cd, r in eval_results.items()
                        if r.decision == EvalDecision.FORCE_ORDER
                    ]
                    logger.info(f"사전 평가: SKIP {len(skip_codes)}개 상품 제외")

                    # 평가 결과를 DB에 저장 (사후 검증용)
                    try:
                        saved = self._eval_calibrator.save_eval_results(eval_results)
                        logger.info(f"평가 결과 DB 저장: {saved}건")
                    except Exception as save_err:
                        logger.warning(f"평가 결과 DB 저장 실패: {save_err}")
            except Exception as e:
                logger.warning(f"사전 발주 평가 실패 (원본 플로우 유지): {e}")

            # 발주 대상 추출 (스킵 상품 제외)
            try:
                candidates = self.improved_predictor.get_order_candidates(
                    target_date=target_date,
                    min_order_qty=min_order_qty,
                    exclude_items=skip_codes if skip_codes else None
                )
            except Exception as e:
                logger.warning(f"예측 엔진 발주 후보 생성 실패 (빈 목록으로 계속): {e}")
                candidates = []

            # PASS 상품 발주량 억제 (과잉발주 보정)
            if ENABLE_PASS_SUPPRESSION and eval_results:
                pass_codes = {
                    cd for cd, r in eval_results.items()
                    if r.decision == EvalDecision.PASS
                }
                if pass_codes:
                    pass_adjusted = 0
                    for r in candidates:
                        if r.item_cd in pass_codes and r.order_qty > PASS_MAX_ORDER_QTY:
                            r.order_qty = PASS_MAX_ORDER_QTY
                            pass_adjusted += 1
                    if pass_adjusted:
                        logger.info(f"PASS 발주량 억제: {pass_adjusted}개 상품 (상한={PASS_MAX_ORDER_QTY})")

            # FORCE_ORDER 상품 중 예측 결과에 누락된 것 보충 (CUT 상품 제외)
            if force_order_codes:
                predicted_codes = {r.item_cd for r in candidates}
                missing_force = [
                    cd for cd in force_order_codes
                    if cd not in predicted_codes and cd not in self._cut_items
                ]
                if missing_force:
                    logger.info(f"FORCE_ORDER 보충 예측: {len(missing_force)}개 상품")
                    extra = self.improved_predictor.predict_batch(missing_force, target_date)
                    # 발주량 0이어도 FORCE이므로 최소 1개 보장
                    # FORCE 상한: 일평균 × FORCE_MAX_DAYS 초과 방지
                    for r in extra:
                        # ★ 재고 또는 미입고분이 있으면 FORCE 보충 생략
                        if r.current_stock + r.pending_qty > 0:
                            logger.info(
                                f"[FORCE보충생략] {r.item_nm[:20]}: "
                                f"stock={r.current_stock}+pending={r.pending_qty} "
                                f"-> 재고/미입고분 충분"
                            )
                            self._exclusion_records.append({
                                "item_cd": r.item_cd,
                                "item_nm": r.item_nm,
                                "mid_cd": r.mid_cd,
                                "exclusion_type": ExclusionType.FORCE_SUPPRESSED,
                                "predicted_qty": r.order_qty,
                                "current_stock": r.current_stock,
                                "pending_qty": r.pending_qty,
                                "detail": f"FORCE보충 생략, stock={r.current_stock}+pending={r.pending_qty} 충분",
                            })
                            continue
                        if r.order_qty < 1:
                            r.order_qty = 1
                        if FORCE_MAX_DAYS > 0 and r.adjusted_qty > 0 and math.isfinite(r.adjusted_qty):
                            force_cap = max(1, int(r.adjusted_qty * FORCE_MAX_DAYS))
                            if r.order_qty > force_cap:
                                logger.info(
                                    f"[FORCE보충상한] {r.item_nm[:20]}: "
                                    f"qty={r.order_qty} -> cap={force_cap}"
                                )
                                r.order_qty = force_cap
                        candidates.append(r)

            # 사전 평가 우선순위 기반 정렬 (FORCE→URGENT→NORMAL→나머지)
            if eval_results:
                priority_map = {
                    EvalDecision.FORCE_ORDER: 0,
                    EvalDecision.URGENT_ORDER: 1,
                    EvalDecision.NORMAL_ORDER: 2,
                    EvalDecision.PASS: 3,
                }
                def _sort_key(r: Any) -> tuple:
                    er = eval_results.get(r.item_cd)
                    pri = priority_map.get(er.decision, 3) if er else 3
                    return (pri, r.mid_cd, -r.order_qty)
                candidates.sort(key=_sort_key)

            # 예측 결과 로그 저장 (Phase 1.7에서 이미 저장했으면 스킵)
            try:
                saved_count = self.prediction_logger.log_predictions_batch_if_needed(candidates)
                if saved_count > 0:
                    logger.info(f"예측 로그 저장: {saved_count}/{len(candidates)}건")
                else:
                    logger.info(f"예측 로그: 이미 기록됨 (Phase 1.7), 스킵")
            except Exception as e:
                logger.warning(f"예측 로그 저장 실패: {e}")

            # PredictionResult -> dict 변환
            order_list = [self._convert_prediction_result_to_dict(r) for r in candidates]

            # 공통 제외 필터 적용 (미취급/CUT/자동발주/스마트발주)
            order_list = self._exclude_filtered_items(order_list)
            self._warn_stale_cut_items(order_list)

            # ★ 신상품 도입 현황 (실패해도 기존 발주 플로우 중단 금지)
            try:
                from src.settings.constants import NEW_PRODUCT_AUTO_INTRO_ENABLED, NEW_PRODUCT_MODULE_ENABLED
                # 미도입 자동 발주 [보류: 유통기한 매핑 미구현]
                if NEW_PRODUCT_AUTO_INTRO_ENABLED:
                    order_list = self._add_new_product_items(order_list)
                # 3일발주 후속 관리 (사용자 수동 발주 상품 → 3회 달성까지 자동 후속)
                if NEW_PRODUCT_MODULE_ENABLED:
                    order_list = self._process_3day_follow_orders(order_list)
            except Exception as e:
                logger.warning(f"신상품 3일발주 수집/합산 실패 — skip: {e}")

            # ★ 스마트발주 오버라이드: 예측 기반으로 스마트→수동 전환
            order_list = self._inject_smart_order_items(order_list, target_date)

            # 사전 평가 결과가 없으면 기본 정렬 (중분류 오름차순 → 발주량 내림차순)
            # 사전 평가 결과가 있으면 이미 우선순위 정렬 적용됨
            if not eval_results:
                order_list.sort(key=lambda x: (x.get("mid_cd", ""), -x.get("final_order_qty", 0)))

            # site(사용자) 발주 카테고리별 집계
            site_order_counts = {}
            if CATEGORY_SITE_BUDGET_ENABLED:
                today_str = datetime.now().strftime('%Y-%m-%d')
                site_order_counts = self._get_site_order_counts_by_midcd(today_str)

            # ★ 카테고리 총량 floor 보충 (신선식품) — Cap 전에 실행하여 최선의 상품 선별
            try:
                from src.prediction.prediction_config import PREDICTION_PARAMS
                if self._category_forecaster and PREDICTION_PARAMS.get("category_floor", {}).get("enabled", False):
                    before_qty = sum(item.get('final_order_qty', 0) for item in order_list)
                    order_list = self._category_forecaster.supplement_orders(
                        order_list, eval_results, self._cut_items,
                        site_order_counts=site_order_counts
                    )
                    after_qty = sum(item.get('final_order_qty', 0) for item in order_list)
                    if after_qty > before_qty:
                        logger.info(
                            f"[카테고리Floor] 보충: {before_qty}개 → {after_qty}개 "
                            f"(+{after_qty - before_qty}개)"
                        )
            except Exception as e:
                logger.warning(f"카테고리 총량 floor 보충 실패 (원본 유지): {e}")

            # ★ 대분류(large_cd) 기반 카테고리 총량 floor 보충
            try:
                if self._large_category_forecaster and self._large_category_forecaster.enabled:
                    before_qty = sum(item.get('final_order_qty', 0) for item in order_list)
                    order_list = self._large_category_forecaster.supplement_orders(
                        order_list, eval_results, self._cut_items,
                        site_order_counts=site_order_counts
                    )
                    after_qty = sum(item.get('final_order_qty', 0) for item in order_list)
                    if after_qty > before_qty:
                        logger.info(
                            f"[대분류Floor] 보충: {before_qty}개 → {after_qty}개 "
                            f"(+{after_qty - before_qty}개)"
                        )
            except Exception as e:
                logger.warning(f"대분류 카테고리 총량 floor 보충 실패 (원본 유지): {e}")

            # ★ CUT 대체 보충 (발주중지 상품 수요를 동일 mid_cd 대체 상품에 배분)
            try:
                from src.prediction.prediction_config import PREDICTION_PARAMS
                cut_replacement_cfg = PREDICTION_PARAMS.get("cut_replacement", {})
                if cut_replacement_cfg.get("enabled", False):
                    # eval_results에서 CUT SKIP 수요 데이터 복원 (pre_order_evaluator 보존분)
                    # NOTE: EvalDecision은 모듈 상단(L56)에서 이미 임포트됨 — 여기서 재임포트하면
                    # Python이 함수 전체에서 로컬 변수로 취급하여 L804 등에서 참조 에러 발생
                    if eval_results:
                        existing_cut_cds = {item.get("item_cd") for item in self._cut_lost_items}
                        for cd, r in eval_results.items():
                            if (r.decision == EvalDecision.SKIP
                                    and "CUT" in r.reason
                                    and r.mid_cd in FOOD_CATEGORIES
                                    and r.daily_avg > 0
                                    and cd not in existing_cut_cds):
                                self._cut_lost_items.append({
                                    "item_cd": cd,
                                    "mid_cd": r.mid_cd,
                                    "predicted_sales": r.daily_avg,
                                    "item_nm": r.item_nm,
                                })
                        if self._cut_lost_items:
                            logger.info(
                                f"[CUT보충] 대상: {len(self._cut_lost_items)}건 "
                                f"(eval={len(self._cut_lost_items) - len(existing_cut_cds)}건 복원)"
                            )

                    if self._cut_lost_items:
                        from src.order.cut_replacement import CutReplacementService
                        svc = CutReplacementService(store_id=self.store_id)
                        before_qty = sum(item.get('final_order_qty', 0) for item in order_list)
                        order_list = svc.supplement_cut_shortage(
                            order_list=order_list,
                            cut_lost_items=self._cut_lost_items,
                            eval_results=eval_results,
                        )
                        after_qty = sum(item.get('final_order_qty', 0) for item in order_list)
                        if after_qty > before_qty:
                            logger.info(
                                f"[CUT보충] 보충: {before_qty}개 → {after_qty}개 "
                                f"(+{after_qty - before_qty}개)"
                            )
            except Exception as e:
                logger.warning(f"CUT 대체 보충 실패 (원본 유지): {e}")

            # 푸드류 요일별 총량 상한 적용 — 마지막에 실행하여 Floor/CUT 보충 결과를 최종 절삭
            try:
                before_count = len(order_list)
                order_list = apply_food_daily_cap(
                    order_list, target_date=target_date, store_id=self.store_id,
                    site_order_counts=site_order_counts
                )
                after_count = len(order_list)
                if before_count != after_count:
                    logger.info(f"푸드류 총량 상한 적용: {before_count} → {after_count}개")
            except Exception as e:
                logger.warning(f"푸드류 총량 상한 적용 실패 (원본 유지): {e}")

            logger.info(f"개선된 예측기: {len(order_list)}개 상품 추천")

        else:
            # 기존 예측기 사용
            order_list = self.predictor.generate_order_list(
                min_order_qty=min_order_qty,
                target_date=target_date
            )

            # 공통 제외 필터 적용 (미취급/CUT/자동발주/스마트발주)
            order_list = self._exclude_filtered_items(order_list)
            self._warn_stale_cut_items(order_list)

            # ★ 스마트발주 오버라이드 (기존 예측기 경로)
            order_list = self._inject_smart_order_items(order_list, target_date)

        if max_items and len(order_list) > max_items:
            order_list = order_list[:max_items]
            logger.info(f"상위 {max_items}개 상품으로 제한")

        return order_list

    def print_recommendations(self, order_list: List[Dict[str, Any]], top_n: int = 20) -> None:
        """발주 추천 목록을 로그로 출력

        Args:
            order_list: 발주 추천 목록
            top_n: 출력할 최대 상품 수
        """
        logger.info(LOG_SEPARATOR_EXTRA)
        logger.info(f"발주 추천 목록 (상위 {min(top_n, len(order_list))}개)")
        logger.info(LOG_SEPARATOR_EXTRA)
        logger.info(f"{'No.':<4} {'상품명':<30} {'발주량':>8} {'재고':>6} {'예측':>6} {'발주요일':<12}")
        logger.info(f"{'-'*70}")

        for i, item in enumerate(order_list[:top_n]):
            item_nm = (item.get('item_nm') or '')[:28]
            qty = item.get('final_order_qty', 0)
            stock = item.get('current_stock', 0)
            pred = item.get('predicted_sales', 0)
            days = item.get('orderable_day', '-')

            logger.info(f"{i+1:<4} {item_nm:<30} {qty:>8} {stock:>6} {pred:>6} {days:<12}")

        if len(order_list) > top_n:
            logger.info(f"... 외 {len(order_list) - top_n}개 상품")

        logger.info(f"총 발주 대상: {len(order_list)}개 상품")

    def preview_schedule(self, order_list: List[Dict[str, Any]]) -> None:
        """발주 일정 미리보기 (요일별 그룹핑)

        Args:
            order_list: 발주 목록
        """
        if not self.executor:
            logger.warning("발주 실행기 없음 - 일정 미리보기 불가")
            return

        grouped = self.executor.group_orders_by_date(order_list)

        logger.info("발주 일정 미리보기")
        for date, items in grouped.items():
            logger.info(f"  {date}: {len(items)}개 상품")
            for item in items[:5]:
                item_nm = (item.get('item_nm') or '')[:20]
                qty = item.get('final_order_qty', 0)
                logger.info(f"    - {item_nm}: {qty}개")
            if len(items) > 5:
                logger.info(f"    ... 외 {len(items) - 5}개")

    def execute(
        self,
        order_list: Optional[List[Dict[str, Any]]] = None,
        min_order_qty: int = 1,
        max_items: Optional[int] = None,
        target_date: Optional[str] = None,
        dry_run: bool = True,
        prefetch_pending: bool = True,
        max_pending_items: int = 500,
        margin_collect_categories: Optional[Set[str]] = None,
        skip_exclusion_fetch: bool = False,
        target_dates: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        자동 발주 실행

        Args:
            order_list: 발주 목록 (None이면 자동 생성)
            min_order_qty: 최소 발주량
            max_items: 최대 상품 수
            target_date: 발주 날짜 (YYYY-MM-DD, None이면 상품별 자동 선택)
            dry_run: True면 실제 발주 안함 (테스트용)
            prefetch_pending: True면 발주 전 미입고 수량 사전 조회 (중복 발주 방지)
            max_pending_items: 미입고 조회 최대 상품 수
            margin_collect_categories: 매가/이익율 수집 대상 카테고리 (부분 발주 시)
            skip_exclusion_fetch: True면 자동/스마트발주 목록 사이트 조회 건너뛰고 DB 캐시 사용
            target_dates: 발주할 날짜 목록 (YYYY-MM-DD). None이면 전체 발주

        Returns:
            결과 {success, success_count, fail_count, ...}
        """
        if not self.executor:
            return {"success": False, "message": "WebDriver not provided - cannot execute orders"}

        # 발주 제외 사유 기록 초기화 (새 실행 사이클)
        self._exclusion_records.clear()
        self._cut_lost_items = []  # CUT 탈락 상품 초기화 (이전 실행 오염 방지)

        # DB에서 미취급 상품 목록 로드 (이전 조회에서 실패한 상품)
        self.load_unavailable_from_db()

        # DB에서 발주중지(CUT) 상품 목록 로드
        self.load_cut_items_from_db()

        # CUT 미확인 의심 상품 우선 재조회 (재고0 + 오래 조회 안 된 상품)
        if self.pending_collector:
            try:
                from src.settings.constants import CUT_STATUS_STALE_DAYS, CUT_STALE_PRIORITY_CHECK
                suspect_items = self._inventory_repo.get_stale_suspicious_items(
                    stale_days=CUT_STATUS_STALE_DAYS,
                    max_items=CUT_STALE_PRIORITY_CHECK,
                    store_id=self.store_id
                )
                if suspect_items:
                    cut_before = len(self._cut_items)
                    logger.info(f"CUT 미확인 의심 상품 {len(suspect_items)}개 우선 재조회...")
                    self.prefetch_pending_quantities(
                        item_codes=suspect_items,
                        max_items=len(suspect_items)
                    )
                    new_cuts = len(self._cut_items) - cut_before
                    if new_cuts > 0:
                        logger.info(f"  → {new_cuts}개 실제 CUT 확인 → 발주 제외")
            except Exception as e:
                logger.warning(f"CUT 의심 상품 우선 재조회 실패 (무시): {e}")

        # 발주 현황 조회 > 자동/스마트 탭에서 상품 로드 (중복 발주 방지)
        self.load_auto_order_items(skip_site_fetch=skip_exclusion_fetch)

        # 부분 발주: 미입고 조회 + 매가/이익율 수집
        if prefetch_pending and self.pending_collector and order_list is not None:
            candidate_items = [item.get('item_cd') for item in order_list if item.get('item_cd')]
            logger.info(f"부분 발주: {len(candidate_items)}개 상품 미입고+정보 조회...")

            # 매가/이익율 미수집 상품 추가
            if margin_collect_categories and self._product_repo:
                margin_missing = self._product_repo.get_items_missing_margin(
                    mid_codes=list(margin_collect_categories), days=30
                )
                order_item_set = set(candidate_items)
                extra = [cd for cd in margin_missing if cd not in order_item_set]
                if extra:
                    extra = extra[:50]  # 최대 50개
                    candidate_items.extend(extra)
                    logger.info(f"매가 미수집 {len(margin_missing)}개 중 {len(extra)}개 추가 조회")

            effective_max_pending = min(max_pending_items, len(candidate_items))
            pending_data = self.prefetch_pending_quantities(
                item_codes=candidate_items,
                max_items=effective_max_pending
            )

            # [CUT 순서 정정] prefetch에서 실시간 감지된 CUT 상품을 메인 필터로 재적용
            order_list, _lost = self._refilter_cut_items(order_list)
            self._cut_lost_items.extend(_lost)

            if pending_data or (hasattr(self, '_last_stock_data') and self._last_stock_data):
                before_total = sum(item.get('final_order_qty', 0) for item in order_list)
                order_list = self._apply_pending_and_stock_to_order_list(
                    order_list=order_list,
                    pending_data=pending_data,
                    stock_data=getattr(self, '_last_stock_data', {}),
                    min_order_qty=min_order_qty
                )
                after_total = sum(item.get('final_order_qty', 0) for item in order_list)
                diff = before_total - after_total
                if diff != 0:
                    logger.info(f"미입고+재고 반영: {before_total}개 → {after_total}개 ({abs(diff)}개 {'감소' if diff > 0 else '증가'})")
                if after_total == 0 and before_total > 0:
                    logger.warning(
                        f"[경고] 미입고+재고 조정으로 발주량 전량 소멸: "
                        f"{before_total}개 → 0개. "
                        f"FORCE/URGENT 보호 적용 여부 확인 필요"
                    )
            else:
                logger.info("미입고 데이터 없음")

        # 미입고 수량 사전 조회 (중복 발주 방지)
        # [v10 최적화] get_recommendations() 1회만 호출, 미입고 데이터는 직접 반영
        elif prefetch_pending and self.pending_collector and order_list is None:
            # 1단계: 초기 발주 목록 생성 (미입고 미반영)
            logger.info("1단계: 초기 발주 목록 생성 (미입고 미반영)...")
            order_list = self.get_recommendations(
                min_order_qty=min_order_qty,
                max_items=max_items
            )

            if not order_list:
                logger.info("발주 대상 상품 없음")
                return {"success": True, "success_count": 0, "fail_count": 0, "message": "no items"}

            # 2단계: 발주 대상 상품코드 추출
            candidate_items = [item.get('item_cd') for item in order_list if item.get('item_cd')]
            logger.info(f"2단계: 발주 대상 {len(candidate_items)}개 상품의 미입고 조회...")

            # max_pending_items를 실제 발주 대상 수에 맞춤
            effective_max_pending = min(max_pending_items, len(candidate_items))

            # 3단계: 미입고 수량 및 실시간 재고 조회
            pending_data = self.prefetch_pending_quantities(
                item_codes=candidate_items,
                max_items=effective_max_pending
            )

            # [CUT 순서 정정] prefetch에서 실시간 감지된 CUT 상품을 메인 필터로 재적용
            order_list, _lost = self._refilter_cut_items(order_list)
            self._cut_lost_items.extend(_lost)

            # 4단계: 기존 발주 목록을 미입고/실시간재고 데이터로 직접 업데이트
            # [v10 최적화] get_recommendations() 재호출 없이 직접 조정
            if pending_data or (hasattr(self, '_last_stock_data') and self._last_stock_data):
                logger.info(f"4단계: 미입고+실시간재고 직접 반영 (재호출 없음)...")

                before_total = sum(item.get('final_order_qty', 0) for item in order_list)

                # 발주 목록 직접 업데이트
                order_list = self._apply_pending_and_stock_to_order_list(
                    order_list=order_list,
                    pending_data=pending_data,
                    stock_data=getattr(self, '_last_stock_data', {}),
                    min_order_qty=min_order_qty
                )

                after_total = sum(item.get('final_order_qty', 0) for item in order_list)
                diff = before_total - after_total
                if diff > 0:
                    logger.info(f"미입고+재고 반영 효과: {before_total}개 → {after_total}개 ({diff}개 감소)")
                elif diff < 0:
                    logger.info(f"미입고+재고 반영 효과: {before_total}개 → {after_total}개 ({abs(diff)}개 증가)")
                else:
                    logger.info(f"미입고+재고 반영 효과: 변화 없음 ({after_total}개)")
                if after_total == 0 and before_total > 0:
                    logger.warning(
                        f"[경고] 미입고+재고 조정으로 발주량 전량 소멸: "
                        f"{before_total}개 → 0개. "
                        f"FORCE/URGENT 보호 적용 여부 확인 필요"
                    )
            else:
                logger.info("미입고 데이터 없음, 초기 목록 사용")

        elif order_list is None:
            # prefetch_pending=False이거나 pending_collector 없는 경우
            order_list = self.get_recommendations(
                min_order_qty=min_order_qty,
                max_items=max_items
            )

        # 캐시 초기화
        self.predictor.clear_pending_qty_cache()
        if self.use_improved_predictor and self.improved_predictor:
            self.improved_predictor.clear_pending_cache()
            self.improved_predictor.clear_stock_cache()
        self._pending_cache = {}
        self._stock_cache = {}
        self._last_stock_data = {}

        if not order_list:
            logger.info("발주 대상 상품 없음")
            return {"success": True, "success_count": 0, "fail_count": 0, "message": "no items"}

        # ===== 푸드 수동발주 차감 =====
        order_list = self._deduct_manual_food_orders(order_list, min_order_qty)

        if not order_list:
            logger.info("수동발주 차감 후 발주 대상 없음")
            return {"success": True, "success_count": 0, "fail_count": 0, "message": "all deducted by manual orders"}

        # 발주 목록 출력
        self.print_recommendations(order_list)

        # 발주 일정 미리보기
        self.preview_schedule(order_list)

        # [v10] 발주 전 화면 상태 초기화 (pending_collector 메뉴 닫힌 후 상태 정리)
        self._ensure_clean_screen_state()

        # [v11] 발주 직전 order_unit_qty 최종 보정 (common.db 배치 재조회)
        if order_list:
            self._finalize_order_unit_qty(order_list)

        # 발주 실행 (전품목 직접 발주 - orderable_day는 안전재고 계산에만 사용)
        logger.info(f"{'테스트 모드 (dry_run)' if dry_run else '실제 발주 실행'}")

        if order_list:
            result = self.executor.execute_orders(
                order_list=order_list,
                target_date=target_date,
                dry_run=dry_run,
                target_dates=target_dates,
            )
        else:
            result = {"success": True, "success_count": 0, "fail_count": 0, "results": []}

        # 발주 성공한 상품들을 order_tracking에 저장 (폐기 관리용)
        if not dry_run and result.get('results'):
            self._save_to_order_tracking(order_list, result['results'])
            # 신상품 3일발주 분산 추적 업데이트 (실패해도 발주 결과에 영향 없음)
            try:
                self._update_np3day_tracking_after_order()
            except Exception as e:
                logger.error(f"신상품 발주 추적 업데이트 실패 — 수동 확인 필요: {e}")

        # eval_outcomes에 predicted_qty, actual_order_qty, order_status 업데이트
        if result.get('results'):
            self._update_eval_order_results(order_list, result['results'])

        # ★ 발주 분석: 스냅샷 저장 (자동발주 vs 사용자 수정 추적용)
        # 실제 발주일(order_date)별로 분리 저장하여 입고 데이터와 정확히 매칭
        if not dry_run and result.get('results'):
            try:
                from collections import defaultdict
                from src.analysis.order_diff_tracker import OrderDiffTracker
                diff_tracker = OrderDiffTracker(store_id=self.store_id)

                results_list = result.get('results', [])
                today = datetime.now().strftime("%Y-%m-%d")

                # results에서 실제 order_date별로 그룹핑
                date_groups = defaultdict(list)
                for r in results_list:
                    od = r.get('order_date', today)
                    date_groups[od].append(r)

                # order_list를 item_cd 기준으로 매핑
                item_map = {
                    item.get('item_cd'): item
                    for item in order_list if item.get('item_cd')
                }

                total_saved = 0
                for od, od_results in date_groups.items():
                    # 해당 날짜의 상품만 추출
                    od_order_list = [
                        item_map[r.get('item_cd')]
                        for r in od_results
                        if r.get('item_cd') and r.get('item_cd') in item_map
                    ]

                    snapshot_count = diff_tracker.save_snapshot(
                        order_date=od,
                        order_list=od_order_list,
                        results=od_results,
                        eval_results=self._last_eval_results,
                    )
                    total_saved += snapshot_count

                if total_saved > 0:
                    dates_str = ", ".join(sorted(date_groups.keys()))
                    logger.info(f"[발주분석] 스냅샷 저장: {total_saved}건 (발주일: {dates_str})")
            except Exception as e:
                logger.debug(f"[발주분석] 스냅샷 저장 실패 (무시): {e}")

        # ★ 재고 불일치 진단 저장
        if not dry_run and getattr(self, '_last_stock_discrepancies', None):
            try:
                from src.analysis.stock_discrepancy_diagnoser import StockDiscrepancyDiagnoser
                from src.infrastructure.database.repos import OrderAnalysisRepository

                diagnoser = StockDiscrepancyDiagnoser()
                analysis_repo = OrderAnalysisRepository()
                today = datetime.now().strftime("%Y-%m-%d")

                diagnosed = []
                for raw in self._last_stock_discrepancies:
                    diag = diagnoser.diagnose(
                        stock_at_prediction=raw["stock_at_prediction"],
                        pending_at_prediction=raw["pending_at_prediction"],
                        stock_at_order=raw["stock_at_order"],
                        pending_at_order=raw["pending_at_order"],
                        stock_source=raw.get("stock_source", ""),
                        is_stock_stale=raw.get("is_stock_stale", False),
                        original_order_qty=raw["original_order_qty"],
                        recalculated_order_qty=raw["recalculated_order_qty"],
                    )
                    # 메타 정보 합치기
                    diag["item_cd"] = raw["item_cd"]
                    diag["item_nm"] = raw.get("item_nm", "")
                    diag["mid_cd"] = raw.get("mid_cd", "")
                    diagnosed.append(diag)

                # 유의미한 불일치만 저장
                significant = [d for d in diagnosed if diagnoser.is_significant(d)]
                if significant:
                    saved = analysis_repo.save_stock_discrepancies(
                        store_id=self.store_id or "",
                        order_date=today,
                        discrepancies=significant,
                    )
                    summary = diagnoser.summarize_discrepancies(significant)
                    logger.info(
                        f"[재고불일치] {saved}건 저장 "
                        f"(HIGH={summary['by_severity'].get('HIGH',0)}, "
                        f"MEDIUM={summary['by_severity'].get('MEDIUM',0)})"
                    )
                elif diagnosed:
                    logger.debug(f"[재고불일치] 변동 {len(diagnosed)}건 모두 미미 → 저장 생략")
            except Exception as e:
                logger.debug(f"[재고불일치] 진단 저장 실패 (무시): {e}")

        # ★ 발주 제외 사유 배치 저장
        if not dry_run and self._exclusion_records:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                saved = self._exclusion_repo.save_exclusions_batch(
                    today, self._exclusion_records
                )
                logger.info(f"[발주제외사유] {saved}건 저장 (총 {len(self._exclusion_records)}건 수집)")
                self._exclusion_records.clear()
            except Exception as e:
                logger.warning(f"[발주제외사유] 저장 실패 (무시): {e}")

        # 드라이런 리포트용 데이터 첨부
        result["order_list"] = order_list
        result["eval_results"] = self._last_eval_results

        return result

    def _recalculate_need_qty(
        self,
        predicted_sales: float,
        safety_stock: float,
        new_stock: int,
        new_pending: int,
        daily_avg: float,
        order_unit_qty: int = 1,
        promo_type: str = "",
        expiration_days: Optional[int] = None,
        mid_cd: str = ""
    ) -> int:
        """실시간 재고/미입고를 반영하여 need_qty를 재계산"""
        return self._adjuster.recalculate_need_qty(
            predicted_sales=predicted_sales,
            safety_stock=safety_stock,
            new_stock=new_stock,
            new_pending=new_pending,
            daily_avg=daily_avg,
            order_unit_qty=order_unit_qty,
            promo_type=promo_type,
            expiration_days=expiration_days,
            mid_cd=mid_cd,
        )

    def _apply_pending_and_stock_to_order_list(
        self,
        order_list: List[Dict[str, Any]],
        pending_data: Dict[str, int],
        stock_data: Dict[str, int],
        min_order_qty: int = 1
    ) -> List[Dict[str, Any]]:
        """기존 발주 목록에 미입고/실시간재고 데이터를 직접 반영"""
        adjusted_list, stock_discrepancies = self._adjuster.apply_pending_and_stock(
            order_list=order_list,
            pending_data=pending_data,
            stock_data=stock_data,
            min_order_qty=min_order_qty,
            cut_items=self._cut_items,
            unavailable_items=self._unavailable_items,
            exclusion_records=self._exclusion_records,
            last_eval_results=self._last_eval_results,
        )
        self._last_stock_discrepancies = stock_discrepancies
        return adjusted_list

    def _finalize_order_unit_qty(self, order_list: List[Dict[str, Any]]) -> None:
        """발주 직전 order_unit_qty 최종 보정 (배치 조회).

        product_details 수집 타이밍에 따라 order_unit_qty가 1(폴백)로 남아있을 수 있음.
        발주 실행 직전에 common.db 최신값으로 일괄 보정하여 과발주 방지.

        기존 order_executor._refetch_order_unit_qty()를 대체 (superset):
        - 배치 조회 (건건→500개 청크)
        - unit=1뿐 아니라 모든 상품 최신값 비교
        - 변경 시 실발주량 영향 로깅

        수정 이력:
        - 2026-03-14: 신규 생성 (order_executor._refetch 대체)
        """
        from src.infrastructure.database.connection import DBRouter

        if not order_list:
            return

        # 1. 전체 item_cd 수집
        item_codes = [item.get("item_cd", "") for item in order_list if item.get("item_cd")]
        if not item_codes:
            return

        # 2. 배치 조회 (500개씩 청크 — SQLite 바인드 변수 제한 999개)
        CHUNK_SIZE = 500
        db_units: Dict[str, int] = {}
        try:
            conn = DBRouter.get_connection(table="product_details")
            try:
                cursor = conn.cursor()
                for i in range(0, len(item_codes), CHUNK_SIZE):
                    chunk = item_codes[i:i + CHUNK_SIZE]
                    placeholders = ",".join("?" * len(chunk))
                    cursor.execute(
                        f"SELECT item_cd, order_unit_qty FROM product_details "
                        f"WHERE item_cd IN ({placeholders})",
                        chunk,
                    )
                    for row in cursor.fetchall():
                        cd = row[0]
                        unit = int(row[1] or 1) if row[1] else 1
                        db_units[cd] = unit
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[배수보정] DB 배치 조회 실패, 보정 스킵: {e}")
            return

        # 3. 비교 & 보정 (order_unit_qty만 갱신, final_order_qty는 변경 안 함)
        unit_corrected = 0
        for item in order_list:
            item_cd = item.get("item_cd", "")
            if not item_cd or item_cd not in db_units:
                continue

            fresh_unit = db_units[item_cd]
            old_unit = item.get("order_unit_qty", 1) or 1

            if fresh_unit == old_unit:
                continue

            qty = item.get("final_order_qty", 0)
            # 변경 전 실발주량
            old_mult = max(1, (qty + old_unit - 1) // old_unit) if qty > 0 else 0
            old_actual = old_mult * old_unit
            # 변경 후 실발주량
            new_mult = max(1, (qty + fresh_unit - 1) // fresh_unit) if qty > 0 else 0
            new_mult = min(new_mult, MAX_ORDER_MULTIPLIER)
            new_actual = new_mult * fresh_unit

            item["order_unit_qty"] = fresh_unit
            unit_corrected += 1

            logger.warning(
                f"[배수보정] {item_cd}: unit {old_unit}→{fresh_unit}, "
                f"배수 {old_mult}→{new_mult}, "
                f"실발주 {old_actual}→{new_actual} (need={qty})"
            )

        if unit_corrected:
            logger.info(f"[배수보정] {unit_corrected}건 order_unit_qty 보정 완료")

    def _ensure_clean_screen_state(self) -> None:
        """
        발주 실행 전 화면 상태 초기화

        [v10] pending_collector에서 메뉴를 닫은 후 OrderExecutor가
        새로운 메뉴로 이동하기 전에 화면 상태를 깨끗하게 정리

        수행 작업:
        1. 남아있는 Alert 모두 처리
        2. 열린 팝업 닫기
        3. 열린 탭 닫기 (단품별발주 탭 등)
        """
        if not self.driver:
            return

        logger.info("=" * 60)
        logger.info("발주 실행 전 화면 상태 초기화 시작")
        logger.info("=" * 60)

        try:
            # 1. Alert 모두 처리
            alert_count = close_alerts(self.driver, max_attempts=5, silent=False)
            if alert_count > 0:
                logger.info(f"[OK] Alert {alert_count}개 처리 완료")

            # 2. 팝업 모두 닫기 (통합 팝업 매니저 사용)
            popup_count = close_all_popups(self.driver, silent=False)
            if popup_count > 0:
                logger.info(f"[OK] 팝업 {popup_count}개 닫기 완료")

            # 3. 열린 탭 닫기 (nexacro MdiFrame extrabutton 사용)
            # 주의: SINGLE_ORDER 탭은 navigate_to_single_order()에서 처리하므로 여기서 닫지 않음
            from src.utils.nexacro_helpers import close_tab_by_frame_id
            from src.settings.ui_config import FRAME_IDS
            for fid_key in ["ORDER_STATUS"]:  # SINGLE_ORDER 제거 (발주 실행을 위해 유지)
                try:
                    fid = FRAME_IDS.get(fid_key, "")
                    if fid:
                        close_tab_by_frame_id(self.driver, fid)
                        logger.info(f"[OK] {fid_key} 탭 닫기 완료")
                except Exception as e:
                    logger.debug(f"탭 닫기 실패 (무시 가능): {e}")

            time.sleep(ORDER_SCREEN_CLEANUP_WAIT)

            logger.info("=" * 60)
            logger.info("화면 상태 초기화 완료 - 발주 실행 준비됨")
            logger.info("=" * 60)

        except Exception as e:
            logger.warning(f"화면 상태 초기화 실패: {e}")

    def _save_to_order_tracking(
        self,
        order_list: List[Dict[str, Any]],
        results: List[Dict[str, Any]]
    ) -> None:
        """발주 성공한 상품들을 order_tracking에 저장"""
        try:
            self._tracker.save_to_order_tracking(order_list, results)
        except Exception as e:
            logger.warning(f"order_tracking 저장 실패 (발주는 정상 완료): {e}")

    def _update_eval_order_results(
        self,
        order_list: List[Dict[str, Any]],
        results: List[Dict[str, Any]]
    ) -> None:
        """eval_outcomes에 predicted_qty, actual_order_qty, order_status 업데이트"""
        try:
            from .order_tracker import OrderTracker
            OrderTracker.update_eval_order_results(
                order_list, results, self._eval_calibrator
            )
        except Exception as e:
            logger.warning(f"eval_outcomes 업데이트 실패 (발주는 정상 완료): {e}")

    def run_daily_order(
        self,
        min_order_qty: int = 1,
        max_items: Optional[int] = None,
        dry_run: bool = True,
        prefetch_pending: bool = True,
        skip_exclusion_fetch: bool = False,
        target_dates: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        일일 자동 발주 실행

        Args:
            min_order_qty: 최소 발주량
            max_items: 최대 발주 상품 수
            dry_run: True면 실제 발주 안함
            prefetch_pending: True면 발주 전 미입고 수량 조회 (중복 발주 방지)
            skip_exclusion_fetch: True면 자동/스마트발주 목록 사이트 재조회 건너뜀
            target_dates: 발주할 날짜 목록 (YYYY-MM-DD). None이면 전체 발주

        Returns:
            결과 dict
        """
        logger.info(LOG_SEPARATOR_EXTRA)
        logger.info("일일 자동 발주 시작")
        logger.info(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"예측기: {'개선된 예측기 (31일 데이터 기반)' if self.use_improved_predictor else '기존 예측기'}")
        logger.info(f"미입고 조회: {'활성' if prefetch_pending else '비활성'}")
        logger.info(LOG_SEPARATOR_EXTRA)

        start_time = time.time()

        result = self.execute(
            min_order_qty=min_order_qty,
            max_items=max_items,
            dry_run=dry_run,
            prefetch_pending=prefetch_pending,
            skip_exclusion_fetch=skip_exclusion_fetch,
            target_dates=target_dates,
        )

        elapsed = time.time() - start_time

        # 발주 완료 후 팝업/Alert 정리 (홈화면 복귀 전)
        logger.info("=" * 60)
        logger.info("발주 완료 - 화면 정리 시작")
        try:
            alert_count = close_alerts(self.driver, max_attempts=10, silent=False)
            popup_count = close_all_popups(self.driver, silent=False)
            if alert_count > 0 or popup_count > 0:
                logger.info(f"[OK] Alert {alert_count}개, 팝업 {popup_count}개 정리 완료")
            else:
                logger.info("[OK] 정리할 Alert/팝업 없음")
        except Exception as e:
            logger.warning(f"화면 정리 실패 (무시 가능): {e}")
        logger.info("=" * 60)

        logger.info(LOG_SEPARATOR_EXTRA)
        logger.info("일일 자동 발주 완료")
        logger.info(f"소요 시간: {elapsed:.1f}초")
        logger.info(f"성공: {result.get('success_count', 0)}건")
        logger.info(f"실패: {result.get('fail_count', 0)}건")
        if self._cut_items:
            logger.info(f"발주중지(CUT) 상품 {len(self._cut_items)}개 제외됨:")
            for item_cd in sorted(self._cut_items)[:20]:
                logger.info(f"  - {item_cd}")
        logger.info(LOG_SEPARATOR_EXTRA)

        # 결과에 컷상품 정보 추가
        result['cut_items'] = list(self._cut_items)
        result['cut_count'] = len(self._cut_items)

        return result


class AutoOrderManager:
    """자동 발주 관리자"""

    def __init__(self, driver: Optional[Any] = None, store_id: Optional[str] = None) -> None:
        """
        Args:
            driver: Selenium WebDriver (상품 정보 수집용, 선택사항)
            store_id: 점포 코드
        """
        self.store_id = store_id
        self.predictor = OrderPredictor(store_id=store_id)
        self.converter = OrderUnitConverter()
        self.product_repo = ProductDetailRepository()  # db_type="common"
        self.order_repo = OrderRepository(store_id=self.store_id)
        self.sales_repo = SalesRepository(store_id=self.store_id)
        self.product_collector = ProductInfoCollector(driver)
        self._driver = driver

    def set_driver(self, driver: Any) -> None:
        """드라이버 설정 (BGF 로그인 후 호출)

        Args:
            driver: Selenium WebDriver 인스턴스
        """
        self._driver = driver
        self.product_collector.set_driver(driver)

    def prefetch_product_details(
        self,
        item_codes: Optional[List[str]] = None,
        max_items: int = 50,
    ) -> int:
        """
        발주 전 상품 상세 정보 사전 수집

        Args:
            item_codes: 수집할 상품 코드 목록 (None이면 자동 감지)
            max_items: 최대 수집 건수 (너무 많으면 시간 소요)

        Returns:
            수집 성공 건수
        """
        if not self._driver:
            logger.warning("드라이버 없음 - 상품 정보 수집 건너뜀")
            return 0

        # 상세 정보 없는 상품 목록
        if item_codes is None:
            item_codes = self.product_repo.get_items_without_details()

        if not item_codes:
            logger.info("모든 상품 정보가 이미 수집됨")
            return 0

        # 최대 건수 제한
        if len(item_codes) > max_items:
            logger.info(f"{len(item_codes)}개 중 {max_items}개만 수집 (제한)")
            item_codes = item_codes[:max_items]

        logger.info(f"상품 상세 정보 수집 시작: {len(item_codes)}개")
        return self.product_collector.fetch_missing_products(item_codes)

    def generate_order_list(
        self,
        target_date: Optional[str] = None,
        mid_cd: Optional[str] = None,
        min_order_qty: int = 1,
        check_orderable: bool = True,
        auto_fetch_details: bool = True,
        max_fetch_items: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        발주 목록 생성

        Args:
            target_date: 예측 대상 날짜
            mid_cd: 중분류 코드 (특정 카테고리만)
            min_order_qty: 최소 추천 발주량
            check_orderable: 발주 가능 여부 확인
            auto_fetch_details: 누락된 상품 정보 자동 수집 여부
            max_fetch_items: 자동 수집 시 최대 건수

        Returns:
            발주 목록
        """
        # 발주 전 상품 정보 자동 수집
        if auto_fetch_details and self._driver:
            self.prefetch_product_details(max_items=max_fetch_items)

        # 예측 기반 추천
        recommendations = self.predictor.get_order_recommendations(
            target_date=target_date,
            min_order_qty=min_order_qty,
            mid_cd=mid_cd,
        )

        order_list = []

        for rec in recommendations:
            item_cd = rec.get("item_cd")

            # 상품 상세 정보
            product_info = self.product_repo.get(item_cd)

            # 발주 가능 여부 확인
            if check_orderable and product_info:
                if not self.converter.is_orderable_today(product_info):
                    continue
                orderable_status = product_info.get("orderable_status", "")
                if orderable_status in ["발주불가", "단종", "품절"]:
                    continue

            # 발주 단위 변환
            order_qty = rec.get("recommended_order_qty", 0)
            if product_info:
                unit_result = self.converter.get_order_summary(order_qty, product_info)
                final_qty = unit_result.get("converted_qty", order_qty)
                unit_name = unit_result.get("unit_name", "낱개")
                unit_size = unit_result.get("unit_size", 1)
                units = unit_result.get("units", final_qty)
            else:
                final_qty = order_qty
                unit_name = "낱개"
                unit_size = 1
                units = order_qty

            order_item = {
                "item_cd": item_cd,
                "item_nm": rec.get("item_nm"),
                "mid_cd": rec.get("mid_cd"),
                "mid_nm": rec.get("mid_nm"),
                "predicted_qty": rec.get("predicted_qty", 0),
                "current_stock": rec.get("current_stock", 0),
                "recommended_qty": order_qty,
                "final_order_qty": final_qty,
                "order_unit": unit_name,
                "order_units": units,
                "unit_size": unit_size,
                "display": format_order_display(final_qty, unit_name, unit_size),
            }

            # 유통기한 정보
            if product_info:
                order_item["expiration_days"] = product_info.get("expiration_days")

            order_list.append(order_item)

        return order_list

    def save_order_list(
        self,
        order_list: List[Dict[str, Any]],
        order_date: Optional[str] = None,
    ) -> int:
        """
        발주 목록 DB 저장

        Args:
            order_list: 발주 목록
            order_date: 발주 날짜

        Returns:
            저장된 건수
        """
        if order_date is None:
            order_date = datetime.now().strftime("%Y-%m-%d")

        count = 0
        for item in order_list:
            self.order_repo.save_order(
                order_date=order_date,
                item_cd=item.get("item_cd"),
                mid_cd=item.get("mid_cd"),
                predicted_qty=item.get("predicted_qty", 0),
                recommended_qty=item.get("final_order_qty", 0),
                current_stock=item.get("current_stock", 0),
                order_unit=item.get("order_unit"),
                status="pending",
            )
            count += 1

        return count

    def export_to_csv(
        self,
        order_list: List[Dict[str, Any]],
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        발주 목록 CSV 내보내기

        Args:
            order_list: 발주 목록
            filename: 파일명

        Returns:
            저장된 파일 경로
        """
        if not order_list:
            return None

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"order_list_{timestamp}.csv"

        # data 폴더에 저장
        data_dir = Path(__file__).parent.parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        filepath = data_dir / filename

        fieldnames = [
            "item_cd", "item_nm", "mid_cd", "mid_nm",
            "predicted_qty", "current_stock", "final_order_qty",
            "order_unit", "order_units", "display"
        ]

        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(order_list)

        logger.info(f"CSV 저장: {filepath}")
        return str(filepath)

    def export_to_json(
        self,
        order_list: List[Dict[str, Any]],
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        발주 목록 JSON 내보내기

        Args:
            order_list: 발주 목록
            filename: 파일명

        Returns:
            저장된 파일 경로
        """
        if not order_list:
            return None

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"order_list_{timestamp}.json"

        # data 폴더에 저장
        data_dir = Path(__file__).parent.parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        filepath = data_dir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(order_list, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON 저장: {filepath}")
        return str(filepath)

    def print_order_summary(self, order_list: List[Dict[str, Any]]) -> None:
        """발주 목록 요약 출력 (카테고리별 통계 포함)

        Args:
            order_list: 발주 목록
        """
        if not order_list:
            logger.info("발주 목록 없음")
            return

        logger.info(LOG_SEPARATOR_FULL)
        logger.info("자동 발주 추천 목록")
        logger.info(LOG_SEPARATOR_FULL)

        # 요약 통계
        total_items = len(order_list)
        total_qty = sum(item.get("final_order_qty", 0) for item in order_list)

        # 카테고리별 통계
        by_category = {}
        for item in order_list:
            mid = item.get("mid_nm") or item.get("mid_cd") or "기타"
            if mid not in by_category:
                by_category[mid] = {"count": 0, "qty": 0}
            by_category[mid]["count"] += 1
            by_category[mid]["qty"] += item.get("final_order_qty", 0)

        logger.info("요약")
        logger.info(f"  총 상품 수: {total_items}개")
        logger.info(f"  총 발주량: {total_qty}개")

        logger.info("카테고리별")
        for mid, stats in sorted(by_category.items(), key=lambda x: x[1]["qty"], reverse=True)[:10]:
            logger.info(f"  {mid}: {stats['count']}상품, {stats['qty']}개")

        logger.info("상세 목록")
        logger.info(f"{'상품명':<35} {'예측':>5} {'재고':>5} {'발주':>10}")
        logger.info(LOG_SEPARATOR_NORMAL)

        for item in order_list[:20]:
            name = (item.get("item_nm") or item.get("item_cd", ""))[:33]
            pred = item.get("predicted_qty", 0)
            stock = item.get("current_stock", 0)
            display = item.get("display", "")
            logger.info(f"{name:<35} {pred:>5} {stock:>5} {display:>10}")

        if len(order_list) > 20:
            logger.info(f"... 외 {len(order_list) - 20}개 상품")

        logger.info(LOG_SEPARATOR_FULL)

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        """대기 중인 발주 목록 조회

        Returns:
            대기(pending) 상태의 발주 목록
        """
        return self.order_repo.get_pending_orders()

    def update_order_status(
        self,
        order_id: int,
        status: str,
        actual_qty: Optional[int] = None
    ) -> None:
        """발주 상태 업데이트

        Args:
            order_id: 발주 이력 ID
            status: 변경할 상태 (pending, ordered, cancelled)
            actual_qty: 실제 발주 수량
        """
        self.order_repo.update_status(order_id, status, actual_qty)


def run_auto_order(
    target_date: Optional[str] = None,
    mid_cd: Optional[str] = None,
    export: bool = True,
    save_to_db: bool = True,
    driver: Optional[Any] = None,
    auto_fetch_details: bool = True,
) -> List[Dict[str, Any]]:
    """
    자동 발주 실행

    Args:
        target_date: 예측 대상 날짜
        mid_cd: 중분류 코드
        export: 파일 내보내기 여부
        save_to_db: DB 저장 여부
        driver: Selenium WebDriver (상품 정보 수집용)
        auto_fetch_details: 상품 상세 정보 자동 수집 여부

    Returns:
        발주 목록 (dict 리스트)
    """
    manager = AutoOrderManager(driver=driver)

    logger.info("발주 목록 생성 중...")
    order_list = manager.generate_order_list(
        target_date=target_date,
        mid_cd=mid_cd,
        check_orderable=True,
        auto_fetch_details=auto_fetch_details,
    )

    # 요약 출력
    manager.print_order_summary(order_list)

    # 파일 내보내기
    if export and order_list:
        manager.export_to_csv(order_list)
        manager.export_to_json(order_list)

    # DB 저장
    if save_to_db and order_list:
        count = manager.save_order_list(order_list)
        logger.info(f"DB 저장 완료: {count}건")

    return order_list


# ============================================================================
# 테스트 함수들
# ============================================================================

def test_prediction_only(use_improved: bool = True, store_id: str = DEFAULT_STORE_ID) -> List[Dict[str, Any]]:
    """
    예측만 테스트 (WebDriver 없이)

    Args:
        use_improved: True면 개선된 예측기 사용
        store_id: 매장 ID

    Returns:
        발주 추천 목록
    """
    logger.info(LOG_SEPARATOR_EXTRA)
    logger.info("예측 테스트 (WebDriver 없이)")
    logger.info(f"예측기: {'개선된 예측기 (31일 데이터 기반)' if use_improved else '기존 예측기'}")
    logger.info(LOG_SEPARATOR_EXTRA)

    system = AutoOrderSystem(driver=None, use_improved_predictor=use_improved, store_id=store_id)

    try:
        # 발주 추천 목록 생성
        order_list = system.get_recommendations(
            min_order_qty=1,
            max_items=30
        )

        # 출력
        system.print_recommendations(order_list, top_n=30)

        # 요일별 그룹핑 미리보기 (executor 없이 직접 계산)
        if order_list:
            from collections import defaultdict
            from .order_executor import OrderExecutor

            # 임시로 group_orders_by_date 로직 직접 실행
            grouped = defaultdict(list)
            day_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
            today = datetime.now()

            for item in order_list:
                orderable_days = item.get("orderable_day", DEFAULT_ORDERABLE_DAYS) or DEFAULT_ORDERABLE_DAYS
                available = set(day_map.get(c) for c in orderable_days if c in day_map)

                if not available:
                    order_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
                else:
                    for i in range(1, 8):
                        check = today + timedelta(days=i)
                        if check.weekday() in available:
                            order_date = check.strftime("%Y-%m-%d")
                            break
                    else:
                        order_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")

                grouped[order_date].append(item)

            logger.info("발주 일정 미리보기")
            for date in sorted(grouped.keys()):
                items = grouped[date]
                logger.info(f"  {date}: {len(items)}개 상품")
                for item in items[:3]:
                    item_nm = (item.get('item_nm') or '')[:20]
                    qty = item.get('final_order_qty', 0)
                    logger.info(f"    - {item_nm}: {qty}개")
                if len(items) > 3:
                    logger.info(f"    ... 외 {len(items) - 3}개")

        return order_list

    finally:
        system.close()


def test_with_driver(driver: Any, dry_run: bool = True, max_items: int = 5, use_improved: bool = True, store_id: str = DEFAULT_STORE_ID) -> Dict[str, Any]:
    """
    WebDriver와 함께 테스트

    Args:
        driver: 로그인된 Selenium WebDriver
        dry_run: True면 실제 발주 안함
        max_items: 테스트할 최대 상품 수
        use_improved: True면 개선된 예측기 사용
        store_id: 매장 ID

    Returns:
        발주 실행 결과 dict
    """
    system = AutoOrderSystem(driver=driver, use_improved_predictor=use_improved, store_id=store_id)

    try:
        result = system.run_daily_order(
            min_order_qty=1,
            max_items=max_items,
            dry_run=dry_run
        )

        logger.info(f"테스트 결과: {result}")
        return result

    finally:
        system.close()


# 단독 실행 시 테스트
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="자동 발주 시스템")
    parser.add_argument("--date", "-d", type=str, help="예측 대상 날짜 (YYYY-MM-DD)")
    parser.add_argument("--mid", "-m", type=str, help="중분류 코드")
    parser.add_argument("--max-items", "-n", type=int, default=30, help="최대 상품 수")
    parser.add_argument("--min-qty", "-q", type=int, default=1, help="최소 발주량")
    parser.add_argument("--no-export", action="store_true", help="파일 내보내기 안 함")
    parser.add_argument("--no-save", action="store_true", help="DB 저장 안 함")
    parser.add_argument("--prediction-only", action="store_true", help="예측만 테스트 (발주 실행 안함)")

    args = parser.parse_args()

    if args.prediction_only:
        # 예측만 테스트
        test_prediction_only()
    else:
        # 기존 AutoOrderManager 사용
        run_auto_order(
            target_date=args.date,
            mid_cd=args.mid,
            export=not args.no_export,
            save_to_db=not args.no_save,
        )
