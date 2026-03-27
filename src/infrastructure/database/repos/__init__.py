"""
Repository -- 전체 re-export

기존 src/db/repository.py의 18개 클래스를 분할한 후,
여기서 전체 re-export하여 import 편의 제공.

Usage:
    from src.infrastructure.database.repos import SalesRepository
    from src.infrastructure.database.repos import OrderTrackingRepository
"""

# --- Store-scoped repositories (매장별 DB) ---
from .sales_repo import SalesRepository
from .order_repo import OrderRepository
from .prediction_repo import PredictionRepository
from .order_tracking_repo import OrderTrackingRepository
from .receiving_repo import ReceivingRepository
from .inventory_repo import RealtimeInventoryRepository
from .inventory_batch_repo import InventoryBatchRepository
from .promotion_repo import PromotionRepository
from .eval_outcome_repo import EvalOutcomeRepository
from .calibration_repo import CalibrationRepository
from .auto_order_item_repo import AutoOrderItemRepository
from .smart_order_item_repo import SmartOrderItemRepository
from .fail_reason_repo import FailReasonRepository
from .validation_repo import ValidationRepository
from .collection_log_repo import CollectionLogRepository
from .new_product_repo import NewProductStatusRepository
from .waste_cause_repo import WasteCauseRepository
from .waste_slip_repo import WasteSlipRepository
from .bayesian_optimization_repo import BayesianOptimizationRepository
from .order_exclusion_repo import OrderExclusionRepository
from .manual_order_repo import ManualOrderItemRepository
from .detected_new_product_repo import DetectedNewProductRepository
from .np_tracking_repo import NewProductDailyTrackingRepository
from .hourly_sales_repo import HourlySalesRepository
from .substitution_repo import SubstitutionEventRepository
from .dessert_decision_repo import DessertDecisionRepository
from .beverage_decision_repo import BeverageDecisionRepository
from .np_3day_tracking_repo import NewProduct3DayTrackingRepository as NP3DayTrackingRepo

# --- Common DB repositories (공통 DB) ---
from .product_detail_repo import ProductDetailRepository
from .external_factor_repo import ExternalFactorRepository
from .app_settings_repo import AppSettingsRepository
from .store_repo import StoreRepository
from .stopped_item_repo import StoppedItemRepository
from .user_repo import DashboardUserRepository
from .signup_repo import SignupRequestRepository

# --- 분석 전용 DB (data/order_analysis.db) ---
from .order_analysis_repo import OrderAnalysisRepository

__all__ = [
    # Store-scoped
    "SalesRepository",
    "OrderRepository",
    "PredictionRepository",
    "OrderTrackingRepository",
    "ReceivingRepository",
    "RealtimeInventoryRepository",
    "InventoryBatchRepository",
    "PromotionRepository",
    "EvalOutcomeRepository",
    "CalibrationRepository",
    "AutoOrderItemRepository",
    "SmartOrderItemRepository",
    "FailReasonRepository",
    "ValidationRepository",
    "CollectionLogRepository",
    "NewProductStatusRepository",
    "WasteCauseRepository",
    "WasteSlipRepository",
    "BayesianOptimizationRepository",
    "OrderExclusionRepository",
    "ManualOrderItemRepository",
    "DetectedNewProductRepository",
    "NewProductDailyTrackingRepository",
    "HourlySalesRepository",
    "SubstitutionEventRepository",
    "DessertDecisionRepository",
    "BeverageDecisionRepository",
    "NP3DayTrackingRepo",
    # Common
    "ProductDetailRepository",
    "ExternalFactorRepository",
    "AppSettingsRepository",
    "StoreRepository",
    "StoppedItemRepository",
    "DashboardUserRepository",
    "SignupRequestRepository",
    # Analysis
    "OrderAnalysisRepository",
]
