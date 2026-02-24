"""
Repository — 역방향 호환 shim

실제 정의: src/infrastructure/database/repos/
이 파일은 기존 import 경로 호환을 위한 re-export입니다.

    from src.db.repository import SalesRepository  → OK (이 shim 경유)
    from src.infrastructure.database.repos import SalesRepository  → OK (정식 경로)
"""

import warnings
warnings.warn("Deprecated: use src.infrastructure.database.repos", DeprecationWarning, stacklevel=2)

# BaseRepository
from src.infrastructure.database.base_repository import BaseRepository  # noqa: F401

# Store-scoped repositories
from src.infrastructure.database.repos import (  # noqa: F401
    SalesRepository,
    OrderRepository,
    PredictionRepository,
    OrderTrackingRepository,
    ReceivingRepository,
    RealtimeInventoryRepository,
    InventoryBatchRepository,
    PromotionRepository,
    EvalOutcomeRepository,
    CalibrationRepository,
    AutoOrderItemRepository,
    SmartOrderItemRepository,
    FailReasonRepository,
    ValidationRepository,
    CollectionLogRepository,
    NewProductStatusRepository,
)

# Common DB repositories
from src.infrastructure.database.repos import (  # noqa: F401
    ProductDetailRepository,
    ExternalFactorRepository,
    AppSettingsRepository,
    StoreRepository,
)
