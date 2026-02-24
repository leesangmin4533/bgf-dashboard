"""
기본 예측 규칙
- 이동평균 (적응형)
- 요일 패턴
- 변동성 기반 안전재고
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import math


def moving_average(sales: List[int], window: int = 7) -> float:
    """
    이동평균 계산

    Args:
        sales: 판매량 리스트 (최근 순)
        window: 이동평균 윈도우 크기

    Returns:
        이동평균 값
    """
    if not sales:
        return 0.0

    # 윈도우 크기만큼만 사용
    data = sales[:window]
    if not data:
        return 0.0

    return sum(data) / len(data)


def weighted_moving_average(sales: List[int], weights: Optional[List[float]] = None) -> float:
    """
    가중 이동평균 계산 (최근 데이터에 더 높은 가중치)

    Args:
        sales: 판매량 리스트 (최근 순)
        weights: 가중치 리스트 (기본값: 선형 감소)

    Returns:
        가중 이동평균 값
    """
    if not sales:
        return 0.0

    n = len(sales)
    if weights is None:
        # 선형 감소 가중치: 최근 = n, 오래된 = 1
        weights = list(range(n, 0, -1))

    # 길이 맞추기
    weights = weights[:n]

    total_weight = sum(weights)
    if total_weight == 0:
        return 0.0

    weighted_sum = sum(s * w for s, w in zip(sales, weights))
    return weighted_sum / total_weight


def calculate_volatility(sales: List[int]) -> float:
    """
    판매량 변동성 계산 (변동계수 = 표준편차 / 평균)

    Args:
        sales: 판매량 리스트

    Returns:
        변동계수 (0~1+, 높을수록 변동성 높음)
    """
    if not sales or len(sales) < 2:
        return 0.0

    n = len(sales)
    mean = sum(sales) / n

    if mean == 0:
        return 0.0

    # 표준편차
    variance = sum((x - mean) ** 2 for x in sales) / n
    std_dev = math.sqrt(variance)

    # 변동계수 (CV)
    cv = std_dev / mean

    return round(cv, 3)


def calculate_adaptive_safety_factor(
    sales: List[int],
    base_factor: float = 1.3,
    volatility_weight: float = 0.5
) -> float:
    """
    변동성 기반 적응형 안전재고 배수 계산

    높은 변동성 = 더 높은 안전재고

    Args:
        sales: 과거 판매량 리스트
        base_factor: 기본 안전재고 배수
        volatility_weight: 변동성 가중치 (0~1)

    Returns:
        조정된 안전재고 배수 (1.0 ~ 2.5)
    """
    if not sales:
        return base_factor

    cv = calculate_volatility(sales)

    # 변동성에 따른 추가 배수 (cv가 높으면 배수 증가)
    # cv=0.3 → +0.15, cv=0.5 → +0.25, cv=1.0 → +0.5
    volatility_bonus = cv * volatility_weight

    adjusted_factor = base_factor + volatility_bonus

    # 범위 제한 (1.0 ~ 2.5)
    return max(1.0, min(2.5, round(adjusted_factor, 2)))


def calculate_weekday_pattern(
    sales_by_weekday: Dict[int, List[int]],
    target_weekday: int
) -> float:
    """
    요일별 패턴 계수 계산

    Args:
        sales_by_weekday: {요일: [판매량, ...], ...}
        target_weekday: 대상 요일 (0=월, 6=일)

    Returns:
        요일 계수 (전체 평균 대비)
    """
    if not sales_by_weekday:
        return 1.0

    # 전체 평균
    all_sales = []
    for day_sales in sales_by_weekday.values():
        all_sales.extend(day_sales)

    if not all_sales:
        return 1.0

    total_avg = sum(all_sales) / len(all_sales)
    if total_avg == 0:
        return 1.0

    # 대상 요일 평균
    target_sales = sales_by_weekday.get(target_weekday, [])
    if not target_sales:
        return 1.0

    target_avg = sum(target_sales) / len(target_sales)

    return target_avg / total_avg


def predict_sales(
    historical_sales: List[int],
    predict_days: int = 1,
    safety_factor: float = 1.0,
    weekday_factor: float = 1.0,
    use_adaptive_safety: bool = True
) -> int:
    """
    판매량 예측 (적응형)

    Args:
        historical_sales: 과거 판매량 (최근 순)
        predict_days: 예측 일수
        safety_factor: 기본 안전재고 배수
        weekday_factor: 요일 계수
        use_adaptive_safety: 변동성 기반 안전재고 사용 여부

    Returns:
        예측 판매량
    """
    if not historical_sales:
        return 0

    # 데이터 양에 따라 방법 선택
    n = len(historical_sales)

    if n >= 14:
        # 14일 이상: 7일 이동평균 (신뢰도 높음)
        base_prediction = moving_average(historical_sales, 7)
    elif n >= 7:
        # 7~13일: 전체 가중 이동평균
        base_prediction = weighted_moving_average(historical_sales)
    elif n >= 3:
        # 3~6일: 가중 이동평균 + 약간 보수적
        base_prediction = weighted_moving_average(historical_sales) * 1.1
    else:
        # 3일 미만: 평균 + 보수적 (데이터 부족)
        base_prediction = (sum(historical_sales) / n) * 1.2

    # 예측 일수 곱하기
    predicted = base_prediction * predict_days

    # 요일 계수 적용
    predicted *= weekday_factor

    # 안전재고 배수 적용 (적응형 또는 고정)
    if use_adaptive_safety and n >= 5:
        # 5일 이상 데이터가 있을 때만 적응형 사용
        adaptive_factor = calculate_adaptive_safety_factor(
            historical_sales, base_factor=safety_factor
        )
        predicted *= adaptive_factor
    else:
        predicted *= safety_factor

    return max(0, int(round(predicted)))


def calculate_turnover_factor(
    turnover_rate: float,
    stock_days: float,
    shelf_life_days: Optional[int] = None
) -> float:
    """
    회전율 기반 발주량 조정 계수 계산 (유통기한 반영)

    Args:
        turnover_rate: 회전율 (판매량/평균재고, 7일 기준)
        stock_days: 재고일수 (평균재고/일평균판매량)
        shelf_life_days: 유통기한 (일) - 유통기한별 기준 조정

    Returns:
        조정 계수 (0.5 ~ 1.2)
    """
    # 유통기한별 기준 조정
    if shelf_life_days and shelf_life_days > 0:
        # 재고일수가 유통기한의 일정 비율 초과 시 감소
        stock_ratio = stock_days / shelf_life_days if stock_days > 0 else 0

        if stock_ratio > 0.7:
            return 0.5  # 유통기한의 70% 이상 재고 → 50% 감소
        elif stock_ratio > 0.5:
            return 0.7  # 유통기한의 50% 이상 재고 → 30% 감소
        elif stock_ratio > 0.3:
            return 0.85  # 유통기한의 30% 이상 재고 → 15% 감소

        # 유통기한별 회전율 기준 조정
        if shelf_life_days <= 7:
            # 단기 (1~7일): 기존 기준
            high_turnover, low_turnover = 10, 3
            stock_limit = 5
        elif shelf_life_days <= 60:
            # 중기 (8~60일): 낮은 기준
            high_turnover, low_turnover = 5, 1
            stock_limit = shelf_life_days * 0.3
        else:
            # 장기 (61일+): 더 낮은 기준
            high_turnover, low_turnover = 2, 0.5
            stock_limit = shelf_life_days * 0.2
    else:
        # 유통기한 정보 없음: 기본 기준
        high_turnover, low_turnover = 10, 3
        stock_limit = 5

    # 재고일수가 한계 초과 시 감소
    if stock_days > stock_limit:
        return 0.7

    # 회전율 기반 조정
    if turnover_rate > high_turnover:
        return 1.2  # 품절 위험
    elif turnover_rate >= low_turnover:
        return 1.0  # 적정
    elif turnover_rate > 0:
        return 0.8  # 재고 과다
    else:
        return 1.0  # 데이터 없음


def calculate_disuse_factor(disuse_rate: float, is_food: bool = False) -> float:
    """
    폐기율 기반 발주량 조정 계수 계산 (푸드류 별도 기준)

    폐기율이 높으면 발주량을 줄여 폐기 손실 최소화

    Args:
        disuse_rate: 폐기율 (0.0 ~ 1.0, 예: 0.15 = 15%)
        is_food: 푸드류 여부 (True면 더 보수적 적용)

    Returns:
        조정 계수 (0.5 ~ 1.0)

    일반 상품:
        - 폐기율 5% 미만: 1.0
        - 폐기율 5~10%: 1.0
        - 폐기율 10~15%: 0.9
        - 폐기율 15~20%: 0.8
        - 폐기율 20% 이상: 0.7

    푸드류 (더 보수적):
        - 폐기율 5% 미만: 1.0
        - 폐기율 5~10%: 0.9
        - 폐기율 10~15%: 0.8
        - 폐기율 15~20%: 0.65
        - 폐기율 20% 이상: 0.5
    """
    if is_food:
        # 푸드류: 더 보수적 (유통기한 짧음)
        if disuse_rate < 0.05:
            return 1.0
        elif disuse_rate < 0.10:
            return 0.9
        elif disuse_rate < 0.15:
            return 0.8
        elif disuse_rate < 0.20:
            return 0.65
        else:
            return 0.5
    else:
        # 일반 상품
        if disuse_rate < 0.05:
            return 1.0
        elif disuse_rate < 0.10:
            return 1.0
        elif disuse_rate < 0.15:
            return 0.9
        elif disuse_rate < 0.20:
            return 0.8
        else:
            return 0.7


def evaluate_min_order_decision(
    recommended_qty: int,
    min_order_qty: int,
    shelf_life_days: int,
    turnover_rate: float,
    disuse_rate: float,
    current_stock: int
) -> Dict[str, Any]:
    """
    최소발주량 미달 시 스마트 판단

    Args:
        recommended_qty: 추천 발주량
        min_order_qty: 최소 발주량
        shelf_life_days: 유통기한 (일)
        turnover_rate: 회전율
        disuse_rate: 폐기율
        current_stock: 현재 재고

    Returns:
        {
            'should_order': 발주 여부 (bool),
            'final_qty': 최종 발주량,
            'reason': 판단 사유,
            'risk_level': 위험도 ('low', 'medium', 'high'),
            'warning': 경고 메시지 (있을 경우)
        }
    """
    result = {
        'should_order': False,
        'final_qty': 0,
        'reason': '',
        'risk_level': 'low',
        'warning': None
    }

    # 추천량이 0 이하면 발주 불필요
    if recommended_qty <= 0:
        result['reason'] = '재고 충분'
        return result

    # 추천량 >= 최소발주량이면 정상 발주
    if recommended_qty >= min_order_qty:
        result['should_order'] = True
        result['final_qty'] = recommended_qty
        result['reason'] = '정상 발주'
        return result

    # === 최소발주량 미달 시 스마트 판단 ===

    # 발주 임계값 계수 계산 (유통기한, 회전율, 폐기율 기반)
    threshold = 0.5  # 기본 50%

    # 유통기한에 따른 조정
    if shelf_life_days and shelf_life_days > 0:
        if shelf_life_days <= 7:
            threshold = 0.8  # 단기: 보수적 (80%)
        elif shelf_life_days <= 30:
            threshold = 0.6  # 중단기: 중간 (60%)
        elif shelf_life_days <= 90:
            threshold = 0.4  # 중기: 적극적 (40%)
        else:
            threshold = 0.3  # 장기: 매우 적극적 (30%)

    # 회전율에 따른 조정 (높으면 threshold 낮춤)
    if turnover_rate > 5:
        threshold -= 0.1  # 빠르게 팔림 → 발주 적극
    elif turnover_rate < 1:
        threshold += 0.15  # 느리게 팔림 → 발주 보수적

    # 폐기율에 따른 조정 (높으면 threshold 높임)
    if disuse_rate > 0.2:
        threshold += 0.2  # 폐기 많음 → 매우 보수적
    elif disuse_rate > 0.1:
        threshold += 0.1  # 폐기 있음 → 보수적

    # 범위 제한
    threshold = max(0.2, min(0.9, threshold))

    # 임계값 계산
    order_threshold = int(min_order_qty * threshold)

    # 판단
    if recommended_qty >= order_threshold:
        result['should_order'] = True
        result['final_qty'] = min_order_qty  # 최소발주량으로 발주
        result['reason'] = f'스마트 판단: 발주 (추천 {recommended_qty} ≥ 임계값 {order_threshold})'

        # 경고 레벨 결정
        gap = min_order_qty - recommended_qty
        gap_ratio = gap / min_order_qty

        if gap_ratio > 0.5:
            result['risk_level'] = 'high'
            result['warning'] = f'[!] 추천량({recommended_qty})이 최소발주량({min_order_qty})의 {int((1-gap_ratio)*100)}%'
        elif gap_ratio > 0.3:
            result['risk_level'] = 'medium'
            result['warning'] = f'[*] 추천량({recommended_qty}) < 최소발주량({min_order_qty}), 재고 주의'
    else:
        result['should_order'] = False
        result['final_qty'] = 0
        result['reason'] = f'스마트 판단: 스킵 (추천 {recommended_qty} < 임계값 {order_threshold})'
        result['risk_level'] = 'low'
        result['warning'] = f'[i] 발주 보류 (추천량 {recommended_qty} < 최소발주량 {min_order_qty})'

    return result


def calculate_order_quantity(
    predicted_sales: int,
    current_stock: int,
    min_order_qty: int = 1,
    order_unit_qty: int = 1,
    disuse_rate: float = 0.0
) -> int:
    """
    발주량 계산 (폐기율 반영)

    Args:
        predicted_sales: 예측 판매량
        current_stock: 현재 재고
        min_order_qty: 최소 발주 수량
        order_unit_qty: 발주 단위 수량 (배수)
        disuse_rate: 폐기율 (0.0 ~ 1.0)

    Returns:
        발주량 (발주 단위로 올림, 폐기율 반영)
    """
    # 폐기율 기반 조정 계수 적용
    disuse_factor = calculate_disuse_factor(disuse_rate)
    adjusted_prediction = int(predicted_sales * disuse_factor)

    # 필요량 = 조정된 예측 - 재고
    needed = adjusted_prediction - current_stock

    if needed <= 0:
        return 0

    # 발주 단위로 올림
    if order_unit_qty > 1:
        units = (needed + order_unit_qty - 1) // order_unit_qty
        order_qty = units * order_unit_qty
    else:
        order_qty = needed

    # 최소 발주량 적용
    if order_qty < min_order_qty:
        order_qty = min_order_qty

    return order_qty
