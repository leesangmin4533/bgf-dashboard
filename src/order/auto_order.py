"""
자동 발주 시스템
- 예측 모듈과 발주 실행기 통합
- DB 기반 판매량 예측 -> 발주 목록 생성 -> BGF 시스템에서 발주 실행
- 상품별 발주 가능 요일에 맞춰 요일별 그룹핑 발주
"""

import csv
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set
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
)
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

        # 자동발주 상품 목록 (BGF 본부 관리 - 중복 발주 방지)
        self._auto_order_items: set = set()

        # 스마트발주 상품 목록 (BGF 본부 관리 - 중복 발주 방지)
        self._smart_order_items: set = set()

        # 드라이버가 있으면 발주 실행기 및 미입고 수집기 초기화
        if driver:
            from .order_executor import OrderExecutor
            self.executor = OrderExecutor(driver)
            self.pending_collector = OrderPrepCollector(driver, save_to_db=True, store_id=self.store_id)

        # DB 인벤토리 Repository
        self._inventory_repo = RealtimeInventoryRepository(store_id=self.store_id)

        # 자동발주 / 스마트발주 상품 캐시 Repository
        self._auto_order_repo = AutoOrderItemRepository(store_id=self.store_id)
        self._smart_order_repo = SmartOrderItemRepository(store_id=self.store_id)

    def load_unavailable_from_db(self) -> None:
        """
        DB에서 미취급 상품 목록 로드

        발주 시작 전 호출하여 이전에 조회 실패한 상품을 미리 제외
        """
        unavailable = self._inventory_repo.get_unavailable_items(store_id=self.store_id)
        self._unavailable_items.update(unavailable)
        if unavailable:
            logger.info(f"DB에서 미취급 상품 {len(unavailable)}개 로드됨")

    def load_cut_items_from_db(self) -> None:
        """
        DB에서 발주중지(CUT) 상품 목록 로드

        발주 시작 전 호출하여 이전에 발주중지로 확인된 상품을 미리 제외
        """
        cut_items = self._inventory_repo.get_cut_items(store_id=self.store_id)
        self._cut_items.update(cut_items)
        if cut_items:
            logger.info(f"DB에서 발주중지 상품 {len(cut_items)}개 로드됨")

    def load_auto_order_items(self, skip_site_fetch: bool = False) -> None:
        """
        자동발주 + 스마트발주 상품 목록 조회 (사이트 우선 + DB 캐시 fallback)

        발주현황 조회 메뉴를 한 번만 열고, 자동/스마트 탭 순서로 수집한 뒤 닫는다.

        Args:
            skip_site_fetch: True면 사이트 조회 건너뛰고 DB 캐시만 사용
                (daily_job Phase 1.2에서 이미 DB 캐시를 갱신한 경우)
        """
        auto_site_ok = False
        smart_site_ok = False

        # --- 사이트 조회 시도 ---
        if skip_site_fetch:
            logger.info("사이트 조회 건너뜀 (Phase 1.2에서 DB 캐시 갱신 완료)")
        elif self.driver:
            try:
                from src.collectors.order_status_collector import OrderStatusCollector

                collector = OrderStatusCollector(self.driver, store_id=self.store_id)

                if not collector.navigate_to_order_status_menu():
                    logger.warning("발주 현황 조회 메뉴 이동 실패")
                else:
                    # (1) 자동발주 수집
                    auto_detail = collector.collect_auto_order_items_detail()
                    if auto_detail is not None:  # None이 아니면 성공 (0건 포함)
                        self._auto_order_items = {
                            item["item_cd"] for item in auto_detail
                        }
                        saved = self._auto_order_repo.refresh(auto_detail, store_id=self.store_id)
                        logger.info(
                            f"자동발주 상품 {len(self._auto_order_items)}개 "
                            f"사이트 조회 완료 (DB 캐시 {saved}건 갱신)"
                        )
                        auto_site_ok = True
                    else:
                        logger.warning("자동발주 상품 사이트 조회 실패")
                        # auto_site_ok = False (기본값) → DB fallback으로 진행

                    # (2) 스마트발주 수집 (같은 화면, 라디오만 전환)
                    smart_detail = collector.collect_smart_order_items_detail()
                    if smart_detail is not None:  # None이 아니면 성공 (0건 포함)
                        self._smart_order_items = {
                            item["item_cd"] for item in smart_detail
                        }
                        saved = self._smart_order_repo.refresh(smart_detail, store_id=self.store_id)
                        logger.info(
                            f"스마트발주 상품 {len(self._smart_order_items)}개 "
                            f"사이트 조회 완료 (DB 캐시 {saved}건 갱신)"
                        )
                        smart_site_ok = True
                    else:
                        logger.warning("스마트발주 상품 사이트 조회 실패")
                        # smart_site_ok = False (기본값) → DB fallback으로 진행

                    # 발주현황조회(STBJ070_M0) 탭을 반드시 닫아야 함
                    # 열려있으면 단품별발주가 이 탭 컨텍스트에서 실행되어 오류 발생
                    if not collector.close_menu():
                        logger.warning("collector.close_menu() 실패 - 직접 탭 닫기 시도")
                        from src.utils.nexacro_helpers import close_tab_by_frame_id
                        from src.settings.ui_config import FRAME_IDS
                        close_tab_by_frame_id(self.driver, FRAME_IDS["ORDER_STATUS"])
                        time.sleep(0.5)

            except Exception as e:
                logger.warning(f"발주 제외 상품 사이트 조회 실패: {e}")
                # 예외 발생 시에도 탭이 열려있을 수 있으므로 닫기 시도
                try:
                    from src.utils.nexacro_helpers import close_tab_by_frame_id
                    from src.settings.ui_config import FRAME_IDS
                    close_tab_by_frame_id(self.driver, FRAME_IDS["ORDER_STATUS"])
                    time.sleep(0.5)
                except Exception as e:
                    logger.warning(f"발주 현황 탭 닫기 실패: {e}")

        # --- DB 캐시 fallback (자동) ---
        if not auto_site_ok:
            cached = self._auto_order_repo.get_all_item_codes(store_id=self.store_id)
            if cached:
                self._auto_order_items = set(cached)
                logger.info(
                    f"자동발주 상품 DB 캐시 {len(cached)}개 사용 "
                    f"(마지막 갱신: {self._auto_order_repo.get_last_updated(store_id=self.store_id)})"
                )
            else:
                logger.info("자동발주 상품: 사이트 조회 실패 + DB 캐시 없음 - 제외 없이 진행")

        # --- DB 캐시 fallback (스마트) ---
        if not smart_site_ok:
            cached = self._smart_order_repo.get_all_item_codes(store_id=self.store_id)
            if cached:
                self._smart_order_items = set(cached)
                logger.info(
                    f"스마트발주 상품 DB 캐시 {len(cached)}개 사용 "
                    f"(마지막 갱신: {self._smart_order_repo.get_last_updated(store_id=self.store_id)})"
                )
            else:
                logger.info("스마트발주 상품: 사이트 조회 실패 + DB 캐시 없음 - 제외 없이 진행")

    def load_inventory_cache_from_db(self) -> None:
        """
        DB에서 재고/미입고 데이터를 예측기 캐시에 로드

        발주 전 조회된 데이터가 DB에 있으면 이를 활용하여
        불필요한 재조회를 방지
        """
        all_data = self._inventory_repo.get_all(available_only=True, store_id=self.store_id)
        if not all_data:
            logger.info("DB에 인벤토리 데이터 없음")
            return

        stock_cache = {}
        pending_cache = {}

        for item in all_data:
            item_cd = item.get('item_cd')
            if item_cd:
                stock_cache[item_cd] = item.get('stock_qty', 0)
                pending_cache[item_cd] = item.get('pending_qty', 0)

        # 예측기에 캐시 설정
        if self.use_improved_predictor and self.improved_predictor:
            self.improved_predictor.set_stock_cache(stock_cache)
            self.improved_predictor.set_pending_cache(pending_cache)
        else:
            self.predictor.set_pending_qty_cache(pending_cache)

        logger.info(f"DB에서 인벤토리 캐시 로드: {len(stock_cache)}개 상품")

    def get_inventory_summary(self) -> Dict[str, Any]:
        """
        인벤토리 DB 요약 정보 조회

        Returns:
            {"total": N, "available": N, "unavailable": N, "with_pending": N}
        """
        return self._inventory_repo.get_summary(store_id=self.store_id)

    def _exclude_filtered_items(self, order_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """발주 목록에서 미취급/발주중지/자동발주/스마트발주 상품 제외

        Args:
            order_list: 원본 발주 목록

        Returns:
            필터링된 발주 목록
        """
        # 점포 미취급 상품 제외
        if self._unavailable_items:
            before_count = len(order_list)
            order_list = [item for item in order_list if item.get("item_cd") not in self._unavailable_items]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"점포 미취급 {excluded}개 상품 제외")

        # 발주중지(CUT) 상품 제외
        if self._cut_items:
            before_count = len(order_list)
            order_list = [item for item in order_list if item.get("item_cd") not in self._cut_items]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"발주중지(CUT) {excluded}개 상품 제외")

        # 자동발주 상품 제외 (대시보드 토글 설정 반영, 매장별)
        settings_repo = AppSettingsRepository(store_id=self.store_id)
        if settings_repo.get("EXCLUDE_AUTO_ORDER", True) and self._auto_order_items:
            before_count = len(order_list)
            order_list = [item for item in order_list if item.get("item_cd") not in self._auto_order_items]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"자동발주(본부관리) {excluded}개 상품 제외")
        elif not settings_repo.get("EXCLUDE_AUTO_ORDER", True):
            logger.info("자동발주 상품 제외 OFF (대시보드 설정) - 제외 안 함")

        # 스마트발주 상품 제외 (대시보드 토글 설정 반영)
        if settings_repo.get("EXCLUDE_SMART_ORDER", True) and self._smart_order_items:
            before_count = len(order_list)
            order_list = [item for item in order_list if item.get("item_cd") not in self._smart_order_items]
            excluded = before_count - len(order_list)
            if excluded > 0:
                logger.info(f"스마트발주(본부관리) {excluded}개 상품 제외")
        elif not settings_repo.get("EXCLUDE_SMART_ORDER", True):
            logger.info("스마트발주 상품 제외 OFF (대시보드 설정) - 제외 안 함")

        # 발주정지 상품 제외 (common.db stopped_items)
        try:
            from src.infrastructure.database.repos import StoppedItemRepository
            stopped_repo = StoppedItemRepository()
            stopped_items = stopped_repo.get_active_item_codes()
            if stopped_items:
                before_count = len(order_list)
                order_list = [item for item in order_list
                              if item.get("item_cd") not in stopped_items]
                excluded = before_count - len(order_list)
                if excluded > 0:
                    logger.info(f"발주정지 {excluded}개 상품 제외")
        except Exception as e:
            logger.warning(f"발주정지 상품 조회 실패: {e}")

        return order_list

    def _warn_stale_cut_items(self, order_list: List[Dict[str, Any]]) -> None:
        """발주 목록 내 CUT 상태 미검증(stale) 상품 경고

        queried_at이 CUT_STATUS_STALE_DAYS 이상 갱신되지 않은 상품을 경고 로그로 출력.
        실제 발주를 차단하지는 않으나 운영자에게 위험 알림.
        """
        try:
            from src.settings.constants import CUT_STATUS_STALE_DAYS
            from datetime import datetime, timedelta

            threshold = datetime.now() - timedelta(days=CUT_STATUS_STALE_DAYS)
            stale_items = []

            for item in order_list:
                item_cd = item.get("item_cd")
                if not item_cd:
                    continue
                inv = self._inventory_repo.get_by_item(item_cd)
                if not inv or not inv.get("queried_at"):
                    stale_items.append((item_cd, "never"))
                    continue
                try:
                    queried_dt = datetime.fromisoformat(inv["queried_at"])
                    if queried_dt < threshold:
                        days_old = (datetime.now() - queried_dt).days
                        stale_items.append((item_cd, f"{days_old}d"))
                except (ValueError, TypeError):
                    stale_items.append((item_cd, "parse_err"))

            if stale_items:
                logger.warning(
                    f"[CUT 미검증 경고] {len(stale_items)}개 상품의 CUT 상태가 "
                    f"{CUT_STATUS_STALE_DAYS}일 이상 미갱신: "
                    f"{stale_items[:10]}"
                )
        except Exception as e:
            logger.debug(f"stale CUT 경고 실패 (무시): {e}")

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
        """3일발주 미달성 상품 후속 발주 (자동발주 목록과 분리)

        사용자가 수동 발주한 상품만 대상. mids 항목의 DS_YN에서
        발주 횟수를 확인하고, 입고/판매 상태에 따라 후속 발주 판단.
        """
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
                    continue  # 이미 달성
                if placed == 0:
                    continue  # 사용자가 아직 발주하지 않음 → 후속 대상 아님

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
                    logger.info(f"3일발주 후속: {item_cd} ({reason})")

            if added:
                logger.info(f"3일발주 후속 {added}개 발주 목록에 추가")

        except Exception as e:
            logger.warning(f"3일발주 후속 처리 실패 (발주 플로우 계속): {e}")

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
        from src.settings.constants import NEW_PRODUCT_DS_MIN_ORDERS
        if not ds_yn:
            return 0, NEW_PRODUCT_DS_MIN_ORDERS
        try:
            parts = ds_yn.split("(")[0].split("/")
            placed = int(parts[0])
            required = int(parts[1]) if len(parts) > 1 else NEW_PRODUCT_DS_MIN_ORDERS
            return placed, required
        except (ValueError, IndexError):
            return 0, NEW_PRODUCT_DS_MIN_ORDERS

    def close(self) -> None:
        """예측기 및 수집기 리소스 정리"""
        self.predictor.close()
        if self.pending_collector:
            self.pending_collector.close_menu()

    def prefetch_pending_quantities(
        self,
        item_codes: List[str],
        max_items: int = 50
    ) -> Dict[str, int]:
        """
        미입고 수량 및 실시간 재고 사전 조회 (중복 발주 방지용)

        단품별 발주 화면에서 상품별 발주/입고 이력을 조회하여
        미입고 수량(발주 - 입고)과 실시간 재고를 조회합니다.

        Args:
            item_codes: 조회할 상품코드 목록
            max_items: 최대 조회 건수

        Returns:
            {상품코드: 미입고수량, ...}
            (실시간 재고는 self._last_stock_data에 저장됨)
        """
        if not self.pending_collector:
            logger.warning("드라이버 없음 - 미입고 수량 조회 건너뜀")
            return {}

        # 제한된 수만 조회
        if len(item_codes) > max_items:
            logger.info(f"{len(item_codes)}개 중 {max_items}개만 조회")
            item_codes = item_codes[:max_items]

        logger.info(f"미입고 수량 및 실시간 재고 조회: {len(item_codes)}개 상품...")

        # 메뉴 이동 및 날짜 선택
        if not self.pending_collector._menu_navigated:
            if not self.pending_collector.navigate_to_menu():
                logger.error("단품별 발주 메뉴 이동 실패")
                return {}

        if not self.pending_collector._date_selected:
            if not self.pending_collector.select_order_date():
                logger.error("발주일 선택 실패")
                return {}

        # 상품별 미입고 수량 및 실시간 재고 조회
        pending_data = {}
        stock_data = {}  # 실시간 재고
        success_count = 0
        unavailable_count = 0
        cut_count = 0

        for i, item_cd in enumerate(item_codes):
            result = self.pending_collector.query_item_order_history(item_cd)
            if result:
                pending_data[item_cd] = result.get('pending_qty', 0)
                # 실시간 재고도 저장 (BGF 시스템에서 조회한 값)
                # order_prep_collector는 'current_stock' 키로 반환
                if 'current_stock' in result and result['current_stock'] is not None:
                    stock_data[item_cd] = result['current_stock']
                # 발주중지(CUT) 상품 감지
                if result.get('is_cut_item'):
                    self._cut_items.add(item_cd)
                    cut_count += 1
                elif item_cd in self._cut_items:
                    # CUT 해제된 상품 (본부에서 복원)
                    self._cut_items.discard(item_cd)
                    logger.info(f"[CUT 해제] {item_cd}: 발주 가능 확인")
                success_count += 1
            else:
                # 조회 실패 = 점포 미취급 상품
                self._unavailable_items.add(item_cd)
                unavailable_count += 1

            # 진행 상황 출력 (10개마다)
            if (i + 1) % PROGRESS_LOG_INTERVAL == 0:
                logger.info(f"  [{i+1}/{len(item_codes)}] 조회 완료...")

            time.sleep(AFTER_ACTION_WAIT)  # 요청 간 간격

        logger.info(f"조회 완료: {success_count}/{len(item_codes)}건")
        logger.info(f"  - 미입고 수량: {len(pending_data)}개 상품")
        logger.info(f"  - 실시간 재고: {len(stock_data)}개 상품")
        if unavailable_count > 0:
            logger.info(f"  - 점포 미취급: {unavailable_count}개 상품 (발주 제외됨)")
        if cut_count > 0:
            logger.info(f"  - 발주중지(CUT): {cut_count}개 상품 (발주 제외됨)")

        # 미입고 있는 상품 출력
        pending_items = {k: v for k, v in pending_data.items() if v > 0}
        if pending_items:
            logger.info(f"미입고 상품: {len(pending_items)}개")
            for item_cd, qty in list(pending_items.items())[:10]:
                stock = stock_data.get(item_cd, '?')
                logger.info(f"  - {item_cd}: 미입고 {qty}개, 재고 {stock}개")

        # 메뉴 탭 닫기 (발주 실행을 위해)
        self.pending_collector.close_menu()

        # 실시간 재고 데이터 저장 (execute에서 사용)
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
            candidates = self.improved_predictor.get_order_candidates(
                target_date=target_date,
                min_order_qty=min_order_qty,
                exclude_items=skip_codes if skip_codes else None
            )

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
                        # ★ 미입고분으로 충분한 경우 FORCE 보충 생략
                        if r.pending_qty > 0 and r.current_stock + r.pending_qty > 0:
                            logger.info(
                                f"[FORCE보충생략] {r.item_nm[:20]}: "
                                f"stock={r.current_stock}+pending={r.pending_qty} "
                                f"-> 미입고분 충분"
                            )
                            continue
                        if r.order_qty < 1:
                            r.order_qty = 1
                        if FORCE_MAX_DAYS > 0 and r.adjusted_qty > 0:
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

            # ★ 신상품 도입 현황
            from src.settings.constants import NEW_PRODUCT_AUTO_INTRO_ENABLED, NEW_PRODUCT_MODULE_ENABLED
            # 미도입 자동 발주 [보류: 유통기한 매핑 미구현]
            if NEW_PRODUCT_AUTO_INTRO_ENABLED:
                order_list = self._add_new_product_items(order_list)
            # 3일발주 후속 관리 (사용자 수동 발주 상품 → 3회 달성까지 자동 후속)
            if NEW_PRODUCT_MODULE_ENABLED:
                order_list = self._process_3day_follow_orders(order_list)

            # 사전 평가 결과가 없으면 기본 정렬 (중분류 오름차순 → 발주량 내림차순)
            # 사전 평가 결과가 있으면 이미 우선순위 정렬 적용됨
            if not eval_results:
                order_list.sort(key=lambda x: (x.get("mid_cd", ""), -x.get("final_order_qty", 0)))

            # 푸드류 요일별 총량 상한 적용
            try:
                before_count = len(order_list)
                order_list = apply_food_daily_cap(order_list, target_date=target_date, store_id=self.store_id)
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
        max_pending_items: int = 200,
        margin_collect_categories: Optional[Set[str]] = None,
        skip_exclusion_fetch: bool = False
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

        Returns:
            결과 {success, success_count, fail_count, ...}
        """
        if not self.executor:
            return {"success": False, "message": "WebDriver not provided - cannot execute orders"}

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
            if self._cut_items:
                before_cut = len(order_list)
                order_list = [item for item in order_list if item.get("item_cd") not in self._cut_items]
                cut_removed = before_cut - len(order_list)
                if cut_removed > 0:
                    logger.info(f"[CUT 재필터] prefetch 실시간 감지 포함 {cut_removed}개 CUT 상품 제외")

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
            if self._cut_items:
                before_cut = len(order_list)
                order_list = [item for item in order_list if item.get("item_cd") not in self._cut_items]
                cut_removed = before_cut - len(order_list)
                if cut_removed > 0:
                    logger.info(f"[CUT 재필터] prefetch 실시간 감지 포함 {cut_removed}개 CUT 상품 제외")

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

        # 발주 목록 출력
        self.print_recommendations(order_list)

        # 발주 일정 미리보기
        self.preview_schedule(order_list)

        # [v10] 발주 전 화면 상태 초기화 (pending_collector 메뉴 닫힌 후 상태 정리)
        self._ensure_clean_screen_state()

        # 실제 발주 실행
        logger.info(f"{'테스트 모드 (dry_run)' if dry_run else '실제 발주 실행'}")

        result = self.executor.execute_orders(
            order_list=order_list,
            target_date=target_date,
            dry_run=dry_run
        )

        # 발주 성공한 상품들을 order_tracking에 저장 (폐기 관리용)
        if not dry_run and result.get('results'):
            self._save_to_order_tracking(order_list, result['results'])

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
        """실시간 재고/미입고를 반영하여 need_qty를 재계산

        예측 시점의 stale 재고 데이터 대신 실시간 데이터로 need를 다시 산출한다.
        improved_predictor의 need 공식과 동일:
            need = adjusted_prediction + safety_stock - current_stock - pending_qty

        단기유통(≤1일) 푸드류는 미입고 할인 적용:
            오늘 배송분은 오늘 소진 예상 → 내일 발주 차감에 50%만 반영

        Args:
            predicted_sales: 예측 판매량 (adjusted_prediction)
            safety_stock: 안전재고
            new_stock: 실시간 재고
            new_pending: 실시간 미입고
            daily_avg: 일평균 판매량 (올림 임계값 판단용)
            order_unit_qty: 발주 배수 단위
            promo_type: 행사 유형 ("1+1", "2+1", "")
            expiration_days: 유통기한 일수 (None이면 할인 미적용)
            mid_cd: 중분류 코드 (푸드 카테고리 판별용)

        Returns:
            재계산된 발주 수량 (정수, 배수 단위 적용, 행사 배수 보정 포함)
        """
        # 단기유통 푸드류 미입고 할인: 유통기한 1일 이하 푸드의 미입고는
        # 오늘 배송분 = 오늘 소진 예상 → 내일 발주 차감에 할인율만 반영
        effective_pending = new_pending
        if (expiration_days is not None and expiration_days <= 1
                and mid_cd in FOOD_CATEGORIES
                and new_pending > 0):
            effective_pending = max(0, int(new_pending * FOOD_SHORT_EXPIRY_PENDING_DISCOUNT))
            if effective_pending != new_pending:
                logger.debug(
                    f"[단기유통할인] mid_cd={mid_cd}, 유통기한={expiration_days}일: "
                    f"미입고 {new_pending} → {effective_pending} "
                    f"(할인율={FOOD_SHORT_EXPIRY_PENDING_DISCOUNT})"
                )

        need = predicted_sales + safety_stock - new_stock - effective_pending
        if need <= 0:
            return 0

        # 올림 규칙 (improved_predictor._apply_order_rules와 동일)
        from src.prediction.prediction_config import PREDICTION_PARAMS
        min_threshold = PREDICTION_PARAMS.get("min_order_threshold", 0.5)
        round_up_threshold = PREDICTION_PARAMS.get("round_up_threshold", 0.3)

        if need < min_threshold:
            return 0
        elif need < 1.0:
            order_qty = 1
        else:
            if need - int(need) >= round_up_threshold:
                order_qty = int(need) + 1
            else:
                order_qty = int(need)

        # 발주 배수 단위 적용
        if order_unit_qty > 1 and order_qty > 0:
            order_qty = max(order_unit_qty, ((order_qty + order_unit_qty - 1) // order_unit_qty) * order_unit_qty)

        # 행사 배수 보정 (1+1->최소2, 2+1->최소3)
        if promo_type and order_qty > 0:
            from src.settings.constants import PROMO_MIN_STOCK_UNITS
            promo_unit = PROMO_MIN_STOCK_UNITS.get(promo_type, 1)
            if order_qty < promo_unit:
                order_qty = promo_unit

        return order_qty

    def _apply_pending_and_stock_to_order_list(
        self,
        order_list: List[Dict[str, Any]],
        pending_data: Dict[str, int],
        stock_data: Dict[str, int],
        min_order_qty: int = 1
    ) -> List[Dict[str, Any]]:
        """
        기존 발주 목록에 미입고/실시간재고 데이터를 직접 반영

        [v11 개선] 실시간 재고가 변경된 경우 need를 재계산하여 과대발주 방지
        - 기존(v10): 원발주 - (신재고-원재고) → 올림/배수가 적용된 원발주 기준이라 과대
        - 개선(v11): need = predicted_sales + safety - 신재고 - 신미입고 로 재계산

        Args:
            order_list: 기존 발주 목록
            pending_data: {item_cd: pending_qty} 미입고 수량
            stock_data: {item_cd: stock_qty} 실시간 재고
            min_order_qty: 최소 발주량 (미만 시 제거)

        Returns:
            조정된 발주 목록
        """
        adjusted_list = []
        cut_excluded = 0
        unavailable_excluded = 0
        recalculated_count = 0
        self._last_stock_discrepancies = []  # 재고 불일치 진단용

        logger.info(f"[미입고조정 시작] 원본 발주 목록: {len(order_list)}개 상품")

        for item in order_list:
            item_cd = item.get('item_cd')
            if not item_cd:
                continue

            # 발주중지(CUT) 상품 스킵
            if item_cd in self._cut_items:
                cut_excluded += 1
                continue

            # prefetch 실패(미취급) 상품 스킵
            if item_cd in self._unavailable_items:
                unavailable_excluded += 1
                continue

            # 복사본 생성 (원본 수정 방지)
            adjusted_item = item.copy()

            # 기존 값
            original_qty = item.get('final_order_qty', 0)
            original_stock = item.get('current_stock', 0)
            original_pending = item.get('pending_receiving_qty', 0)

            # 새 값 적용
            new_pending = pending_data.get(item_cd, original_pending)
            new_stock = stock_data.get(item_cd, original_stock) if stock_data else original_stock

            item_name = item.get('item_nm', item_cd)

            # 미입고/재고 변동 여부 확인
            stock_changed = (new_stock != original_stock) or (new_pending != original_pending)

            if stock_changed:
                # ★ v11: need 재계산 방식 (실시간 재고 기반)
                predicted_sales = item.get('predicted_sales', 0)
                safety_stock = item.get('safety_stock', 0)
                daily_avg = item.get('daily_avg', 0)
                order_unit_qty = item.get('order_unit_qty', 1)
                promo_type = item.get('promo_type', '')
                expiration_days = item.get('expiration_days')
                mid_cd = item.get('mid_cd', '')

                new_qty = self._recalculate_need_qty(
                    predicted_sales=predicted_sales,
                    safety_stock=safety_stock,
                    new_stock=max(0, new_stock),  # 음수 재고는 0으로
                    new_pending=new_pending,
                    daily_avg=daily_avg,
                    order_unit_qty=order_unit_qty,
                    promo_type=promo_type,
                    expiration_days=expiration_days,
                    mid_cd=mid_cd
                )
                recalculated_count += 1

                # 재고 불일치 진단 데이터 수집
                self._last_stock_discrepancies.append({
                    "item_cd": item_cd,
                    "item_nm": item_name,
                    "mid_cd": item.get("mid_cd", ""),
                    "stock_at_prediction": original_stock,
                    "pending_at_prediction": original_pending,
                    "stock_at_order": new_stock,
                    "pending_at_order": new_pending,
                    "stock_source": item.get("stock_source", ""),
                    "is_stock_stale": item.get("is_stock_stale", False),
                    "original_order_qty": original_qty,
                    "recalculated_order_qty": new_qty,
                })

                logger.info(
                    f"[미입고조정] {item_name[:20]}: "
                    f"원발주={original_qty}, 원재고={original_stock}, 원미입고={original_pending} → "
                    f"신재고={new_stock}, 신미입고={new_pending} → "
                    f"재계산(pred={predicted_sales}+safe={safety_stock:.1f}-stk={max(0,new_stock)}-pnd={new_pending})={new_qty}"
                )
            else:
                new_qty = original_qty

            # 최소 발주량 미만이면 제외 (단기유통 보호 → FORCE/URGENT 보호 순)
            if new_qty < min_order_qty:
                # ★ 단기유통 푸드 보호: 미입고 증가로 발주 소멸 시 원발주 유지
                item_exp_days = adjusted_item.get('expiration_days')
                item_mid_cd = adjusted_item.get('mid_cd', '')
                if (item_exp_days is not None and item_exp_days <= 1
                        and item_mid_cd in FOOD_CATEGORIES
                        and original_qty > 0
                        and stock_changed
                        and new_pending > original_pending):
                    # 미입고 증가로 발주 0이 된 경우: 원발주 유지
                    new_qty = original_qty
                    logger.info(
                        f"[단기유통보호] {item_name[:20]}: "
                        f"미입고 {original_pending}->{new_pending} 증가로 발주 소멸 → "
                        f"유통기한 {item_exp_days}일 → 원발주 {original_qty}개 유지"
                    )
                else:
                    eval_result = self._last_eval_results.get(item_cd)
                    if eval_result and eval_result.decision in (
                        EvalDecision.FORCE_ORDER, EvalDecision.URGENT_ORDER
                    ):
                        # ★ 실시간 재고 또는 미입고분으로 충분한 경우 FORCE/URGENT 보호도 생략
                        if new_stock + new_pending > 0:
                            logger.info(
                                f"[보호생략] {item_name[:20]}: {eval_result.decision.name}이지만 "
                                f"가용재고={new_stock}+미입고={new_pending}={new_stock + new_pending} → 제외"
                            )
                            continue
                        new_qty = 1  # FORCE/URGENT 최소 보장
                        logger.info(
                            f"[보호] {item_name[:20]}: {eval_result.decision.name} → 최소 1개 유지"
                        )
                    else:
                        continue

            # 업데이트
            adjusted_item['final_order_qty'] = new_qty
            adjusted_item['recommended_qty'] = new_qty
            adjusted_item['current_stock'] = new_stock
            adjusted_item['pending_receiving_qty'] = new_pending
            adjusted_item['expected_stock'] = new_stock + new_pending

            adjusted_list.append(adjusted_item)

        if cut_excluded > 0:
            logger.info(f"발주중지(CUT) {cut_excluded}개 상품 제외 (재고/미입고 조정 단계)")
        if unavailable_excluded > 0:
            logger.info(f"미취급/조회실패 {unavailable_excluded}개 상품 제외 (재고/미입고 조정 단계)")
        if recalculated_count > 0:
            logger.info(f"[v11] need 재계산: {recalculated_count}개 상품 (실시간 재고 반영)")

        # 중분류 코드 오름차순 → 같은 중분류 내 발주량 내림차순
        adjusted_list.sort(key=lambda x: (x.get("mid_cd", ""), -x.get("final_order_qty", 0)))

        # 전량 소멸 경고
        if not adjusted_list and order_list:
            logger.warning(
                f"[전량소멸] 원본 {len(order_list)}개 상품이 조정 후 전부 제외됨! "
                f"(CUT={cut_excluded}, 미취급={unavailable_excluded}, "
                f"조정소멸={len(order_list) - cut_excluded - unavailable_excluded})"
            )

        return adjusted_list

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
        """
        발주 성공한 상품들을 order_tracking에 저장

        Args:
            order_list: 원본 발주 목록
            results: 발주 실행 결과

        Returns:
            저장된 건수
        """
        # 발주 목록을 item_cd로 인덱싱
        order_dict = {item['item_cd']: item for item in order_list}

        saved_count = 0
        for res in results:
            if not res.get('success'):
                continue

            item_cd = res.get('item_cd')
            order_date = res.get('order_date')
            actual_qty = res.get('actual_qty', 0)

            if not item_cd or actual_qty <= 0:
                continue

            # 원본 정보 가져오기
            order_info = order_dict.get(item_cd, {})
            item_nm = order_info.get('item_nm', item_cd)
            mid_cd = order_info.get('mid_cd', '')

            # 배송 차수 판별 (푸드류만 해당)
            # order_date는 "%Y-%m-%d" 또는 "%Y%m%d" 형식 모두 허용
            try:
                order_datetime = datetime.strptime(order_date, "%Y-%m-%d")
            except ValueError:
                order_datetime = datetime.strptime(order_date, "%Y%m%d")
            if mid_cd in ALERT_CATEGORIES:
                delivery_type = get_delivery_type(item_nm) or "1차"
                # use_product_expiry 카테고리(012 빵)는 상품별 유통기한 전달
                exp_days = None
                cat_cfg = ALERT_CATEGORIES.get(mid_cd, {})
                if cat_cfg.get("use_product_expiry"):
                    pd_info = self._product_repo.get(item_cd)
                    exp_days = pd_info.get('expiration_days') if pd_info else None
                shelf_hours, arrival_time, expiry_time = calculate_shelf_life_after_arrival(
                    item_nm, mid_cd, order_datetime, expiration_days=exp_days
                )
            else:
                # 비푸드류: product_details.expiration_days 기반 유통기한 추적
                delivery_type = "일반"
                arrival_time = order_datetime + timedelta(days=1)  # D+1 도착
                try:
                    pd_repo = ProductDetailRepository()  # db_type="common"
                    pd_info = pd_repo.get(item_cd)
                    exp_days = pd_info.get('expiration_days') if pd_info else None
                    if exp_days and exp_days > 0:
                        expiry_time = arrival_time + timedelta(days=exp_days)
                    else:
                        # 유통기한 정보 없으면 추적 스킵
                        logger.debug(f"비푸드 유통기한 미등록, tracking 스킵: {item_cd}")
                        continue
                except Exception:
                    logger.debug(f"비푸드 유통기한 조회 실패, tracking 스킵: {item_cd}")
                    continue

            # order_tracking에 저장
            try:
                self.tracking_repo.save_order(
                    order_date=order_date,
                    item_cd=item_cd,
                    item_nm=item_nm,
                    mid_cd=mid_cd,
                    delivery_type=delivery_type,
                    order_qty=actual_qty,
                    arrival_time=arrival_time.strftime("%Y-%m-%d %H:%M"),
                    expiry_time=expiry_time.strftime("%Y-%m-%d %H:%M"),
                    store_id=self.store_id,
                    order_source='auto'
                )
                saved_count += 1
                if arrival_time and expiry_time:
                    logger.info(f"Tracking: {item_nm[:15]} → 도착:{arrival_time.strftime('%m/%d %H:%M')} 폐기:{expiry_time.strftime('%m/%d %H:%M')}")
                else:
                    logger.info(f"Tracking: {item_nm[:15]} → 발주:{actual_qty}개")

                # 비푸드류는 inventory_batches에도 배치 생성
                if mid_cd not in ALERT_CATEGORIES and exp_days and exp_days > 0:
                    try:
                        batch_repo = InventoryBatchRepository(store_id=self.store_id)
                        batch_repo.create_batch(
                            item_cd=item_cd,
                            item_nm=item_nm,
                            mid_cd=mid_cd,
                            receiving_date=arrival_time.strftime("%Y-%m-%d"),
                            expiration_days=exp_days,
                            initial_qty=actual_qty,
                            store_id=self.store_id
                        )
                        logger.debug(f"inventory_batches 생성: {item_nm} (발주일: {order_date}, {actual_qty}개)")
                    except Exception as e:
                        logger.warning(f"inventory_batches 생성 실패 ({item_cd}): {e}")

            except Exception as e:
                logger.warning(f"Tracking 저장 실패 ({item_cd}): {e}")

        if saved_count > 0:
            logger.info(f"발주 추적 등록: {saved_count}건")

    def _update_eval_order_results(
        self,
        order_list: List[Dict[str, Any]],
        results: List[Dict[str, Any]]
    ) -> None:
        """eval_outcomes에 predicted_qty, actual_order_qty, order_status 업데이트

        발주 실행 후 호출하여 예측기 산출량과 실제 발주량, 상태를 기록한다.
        predicted_qty는 evaluation 시점이 아닌 발주 후에 기록 (순환 의존 방지).

        Args:
            order_list: 원본 발주 목록 (final_order_qty = 예측기 산출)
            results: 발주 실행 결과 (actual_qty, success)
        """
        today = datetime.now().strftime("%Y-%m-%d")
        order_dict = {item['item_cd']: item for item in order_list}
        updated = 0

        try:
            for res in results:
                item_cd = res.get('item_cd')
                if not item_cd:
                    continue

                order_info = order_dict.get(item_cd, {})
                predicted_qty = order_info.get('final_order_qty')
                actual_qty = res.get('actual_qty', 0) if res.get('success') else 0
                order_status = 'success' if res.get('success') else 'fail'

                if res.get('dry_run'):
                    order_status = 'pending'
                    actual_qty = predicted_qty

                self._eval_calibrator.outcome_repo.update_order_result(
                    eval_date=today,
                    item_cd=item_cd,
                    predicted_qty=predicted_qty,
                    actual_order_qty=actual_qty,
                    order_status=order_status
                )
                updated += 1

            if updated > 0:
                logger.info(f"eval_outcomes 발주 결과 업데이트: {updated}건")
        except Exception as e:
            logger.warning(f"eval_outcomes 발주 결과 업데이트 실패: {e}")

    def run_daily_order(
        self,
        min_order_qty: int = 1,
        max_items: Optional[int] = None,
        dry_run: bool = True,
        prefetch_pending: bool = True,
        skip_exclusion_fetch: bool = False
    ) -> Dict[str, Any]:
        """
        일일 자동 발주 실행

        Args:
            min_order_qty: 최소 발주량
            max_items: 최대 발주 상품 수
            dry_run: True면 실제 발주 안함
            prefetch_pending: True면 발주 전 미입고 수량 조회 (중복 발주 방지)
            skip_exclusion_fetch: True면 자동/스마트발주 목록 사이트 재조회 건너뜀

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
            skip_exclusion_fetch=skip_exclusion_fetch
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
