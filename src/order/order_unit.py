"""
발주 단위 변환 모듈
- 낱개 → 묶음 → 박스 변환
- 최소 주문량 적용
"""

import math
from typing import Dict, Any, Optional, Tuple


class OrderUnitConverter:
    """발주 단위 변환기"""

    def __init__(self) -> None:
        pass

    def convert_to_order_unit(
        self,
        qty: int,
        order_unit_qty: int = 1,
        order_unit_name: str = "낱개",
        case_unit_qty: int = 1,
        round_up: bool = True
    ) -> Dict[str, Any]:
        """
        발주 단위로 변환

        Args:
            qty: 원래 수량 (낱개)
            order_unit_qty: 발주 단위당 수량
            order_unit_name: 발주 단위명 (낱개, 묶음, 박스)
            case_unit_qty: 박스당 수량
            round_up: 올림 처리 여부

        Returns:
            {
                "original_qty": 원래 수량,
                "converted_qty": 변환된 수량,
                "unit_name": 단위명,
                "units": 단위 수,
                "unit_size": 단위당 수량,
            }
        """
        if qty <= 0:
            return {
                "original_qty": qty,
                "converted_qty": 0,
                "unit_name": order_unit_name,
                "units": 0,
                "unit_size": order_unit_qty,
            }

        # 단위 크기 결정 (0 이하 방지)
        unit_size = max(order_unit_qty, 1)

        # 묶음인 경우 case_unit_qty 사용
        if order_unit_name == "묶음" and case_unit_qty > 1:
            unit_size = case_unit_qty

        # 단위 수 계산
        if round_up:
            units = math.ceil(qty / unit_size)
        else:
            units = qty // unit_size

        # 변환된 수량
        converted_qty = units * unit_size

        return {
            "original_qty": qty,
            "converted_qty": converted_qty,
            "unit_name": order_unit_name,
            "units": units,
            "unit_size": unit_size,
        }

    def apply_min_order(
        self,
        qty: int,
        min_order_qty: int = 1
    ) -> int:
        """
        최소 주문량 적용

        Args:
            qty: 원래 수량
            min_order_qty: 최소 주문량

        Returns:
            적용된 수량
        """
        if qty <= 0:
            return 0
        return max(qty, min_order_qty)

    def get_order_summary(
        self,
        qty: int,
        product_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        상품 정보 기반 발주 요약

        Args:
            qty: 발주 수량 (낱개)
            product_info: 상품 상세 정보

        Returns:
            발주 요약 정보
        """
        if not product_info:
            return self.convert_to_order_unit(qty)

        order_unit_qty = max(int(product_info.get("order_unit_qty", 1) or 1), 1)
        order_unit_name = product_info.get("order_unit_name", "낱개") or "낱개"
        case_unit_qty = max(int(product_info.get("case_unit_qty", 1) or 1), 1)

        result = self.convert_to_order_unit(
            qty=qty,
            order_unit_qty=order_unit_qty,
            order_unit_name=order_unit_name,
            case_unit_qty=case_unit_qty,
        )

        # 발주 가능 여부
        orderable_status = product_info.get("orderable_status", "")
        result["orderable"] = orderable_status not in ["발주불가", "단종", "품절"]

        # 발주 가능 요일
        result["orderable_day"] = product_info.get("orderable_day", "일월화수목금토")

        return result

    def is_orderable_today(
        self,
        product_info: Dict[str, Any],
        today_weekday: Optional[int] = None
    ) -> bool:
        """
        오늘 발주 가능 여부

        Args:
            product_info: 상품 정보
            today_weekday: 오늘 요일 (0=월요일)

        Returns:
            발주 가능 여부
        """
        if today_weekday is None:
            from datetime import datetime
            today_weekday = datetime.now().weekday()

        orderable_day = product_info.get("orderable_day", "일월화수목금토")

        # 요일 매핑
        day_map = {
            0: "월",
            1: "화",
            2: "수",
            3: "목",
            4: "금",
            5: "토",
            6: "일",
        }

        today_char = day_map.get(today_weekday, "")
        return today_char in orderable_day


def format_order_display(
    qty: int,
    unit_name: str = "낱개",
    unit_size: int = 1
) -> str:
    """
    발주 수량 표시 포맷

    Args:
        qty: 수량
        unit_name: 단위명
        unit_size: 단위당 수량

    Returns:
        표시 문자열 (예: "2박스 (48개)")
    """
    unit_size = max(unit_size, 1)  # 0 방지
    if unit_size <= 1 or unit_name == "낱개":
        return f"{qty}개"

    units = qty // unit_size
    return f"{units}{unit_name} ({qty}개)"


# 단독 실행 시 테스트
if __name__ == "__main__":
    converter = OrderUnitConverter()

    # 테스트
    print("발주 단위 변환 테스트")
    print("-" * 40)

    # 15개 → 24개 박스 단위
    result = converter.convert_to_order_unit(
        qty=15,
        order_unit_qty=24,
        order_unit_name="박스",
    )
    print(f"15개 → 박스(24개): {result}")
    print(f"표시: {format_order_display(result['converted_qty'], result['unit_name'], result['unit_size'])}")

    # 7개 → 6개 묶음 단위
    result = converter.convert_to_order_unit(
        qty=7,
        order_unit_qty=6,
        order_unit_name="묶음",
    )
    print(f"7개 → 묶음(6개): {result}")
    print(f"표시: {format_order_display(result['converted_qty'], result['unit_name'], result['unit_size'])}")
