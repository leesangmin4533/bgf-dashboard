"""
BGF 사이트 인터페이스 -- 넥사크로 웹 스크래핑

기존 위치의 모듈을 새 경로에서 접근 가능하도록 re-export합니다.

Usage:
    from src.infrastructure.bgf_site import SalesAnalyzer
    from src.infrastructure.bgf_site import OrderExecutor
"""

import warnings
warnings.warn("Deprecated: use src.sales_analyzer / src.order.order_executor", DeprecationWarning, stacklevel=2)

# Browser + Login
from src.sales_analyzer import SalesAnalyzer  # noqa: F401

# Order execution
from src.order.order_executor import OrderExecutor  # noqa: F401
