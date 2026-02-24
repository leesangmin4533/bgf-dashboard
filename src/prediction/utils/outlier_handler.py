"""
이상치 처리 모듈

이상치 유형:
1. 상단 이상치: 평소보다 비정상적으로 많이 팔린 날 (단체 주문, 행사 등)
2. 하단 이상치: 0 또는 비정상적으로 적게 팔린 날 (휴무, 시스템 오류 등)

처리 방식:
- IQR (사분위 범위) 기반 탐지
- Z-score 기반 탐지
- 이상치는 제거하거나 중앙값으로 대체 또는 캡핑
"""

from typing import Any, Dict, List, Tuple, Literal, Optional
from dataclasses import dataclass
import statistics


# =============================================================================
# 카테고리별 이상치 설정
# =============================================================================
OUTLIER_CONFIG = {
    "default": {
        "method": "iqr",
        "multiplier": 1.5,
        "strategy": "cap"
    },
    "tobacco": {  # 담배: 보루 구매가 정상이므로 느슨하게
        "method": "iqr",
        "multiplier": 2.5,  # 더 느슨하게
        "strategy": "cap"
    },
    "alcohol": {  # 주류: 주말 급증이 정상이므로 느슨하게
        "method": "iqr",
        "multiplier": 2.0,
        "strategy": "cap"
    },
    "food": {  # 푸드: 폐기 위험 있으므로 엄격하게
        "method": "iqr",
        "multiplier": 1.2,
        "strategy": "cap"
    }
}


def get_outlier_config(mid_cd: str) -> Dict[str, Any]:
    """카테고리별 이상치 설정 반환"""
    if mid_cd in ["072", "073"]:
        return OUTLIER_CONFIG["tobacco"]
    elif mid_cd in ["049", "050", "051", "605"]:
        return OUTLIER_CONFIG["alcohol"]
    elif mid_cd in ["001", "002", "003", "004", "005", "012"]:
        return OUTLIER_CONFIG["food"]
    return OUTLIER_CONFIG["default"]


@dataclass
class OutlierResult:
    """이상치 처리 결과"""
    original_data: List[int]       # 원본 데이터
    cleaned_data: List[int]        # 처리된 데이터
    outlier_indices: List[int]     # 이상치 인덱스
    outlier_values: List[int]      # 이상치 값
    method: str                    # 사용된 방법
    stats: Dict[str, Any]         # 통계 정보 (Q1, Q3, IQR, bounds)


def detect_outliers_iqr(
    sales: List[int],
    multiplier: float = 1.5
) -> Tuple[List[int], List[int], Dict[str, Any]]:
    """
    IQR 기반 이상치 탐지

    Args:
        sales: 판매량 리스트
        multiplier: IQR 배수 (기본 1.5, 엄격하게 하려면 1.0)

    Returns:
        (이상치_인덱스, 이상치_값, 통계정보)

    공식:
        Q1 = 25번째 백분위수
        Q3 = 75번째 백분위수
        IQR = Q3 - Q1
        하한 = Q1 - (multiplier × IQR)
        상한 = Q3 + (multiplier × IQR)
    """
    if len(sales) < 4:
        return [], [], {"message": "데이터 부족 (최소 4개 필요)"}

    sorted_sales = sorted(sales)
    n = len(sorted_sales)

    # Q1, Q3 계산 (선형 보간)
    q1_idx = (n - 1) * 0.25
    q3_idx = (n - 1) * 0.75

    q1_lower = int(q1_idx)
    q1_frac = q1_idx - q1_lower
    q1 = sorted_sales[q1_lower] * (1 - q1_frac) + sorted_sales[min(q1_lower + 1, n - 1)] * q1_frac

    q3_lower = int(q3_idx)
    q3_frac = q3_idx - q3_lower
    q3 = sorted_sales[q3_lower] * (1 - q3_frac) + sorted_sales[min(q3_lower + 1, n - 1)] * q3_frac

    iqr = q3 - q1

    # 상하한 계산
    lower_bound = q1 - (multiplier * iqr)
    upper_bound = q3 + (multiplier * iqr)

    # 하한은 최소 0
    lower_bound = max(0, lower_bound)

    # 이상치 탐지
    outlier_indices = []
    outlier_values = []

    for i, val in enumerate(sales):
        if val < lower_bound or val > upper_bound:
            outlier_indices.append(i)
            outlier_values.append(val)

    stats = {
        "q1": round(q1, 2),
        "q3": round(q3, 2),
        "iqr": round(iqr, 2),
        "lower_bound": round(lower_bound, 2),
        "upper_bound": round(upper_bound, 2),
        "multiplier": multiplier
    }

    return outlier_indices, outlier_values, stats


def detect_outliers_zscore(
    sales: List[int],
    threshold: float = 2.0
) -> Tuple[List[int], List[int], Dict[str, Any]]:
    """
    Z-score 기반 이상치 탐지

    Args:
        sales: 판매량 리스트
        threshold: Z-score 임계값 (기본 2.0 = 약 95% 신뢰구간)

    Returns:
        (이상치_인덱스, 이상치_값, 통계정보)

    공식:
        Z = (x - 평균) / 표준편차
        |Z| > threshold 이면 이상치
    """
    if len(sales) < 3:
        return [], [], {"message": "데이터 부족 (최소 3개 필요)"}

    mean = statistics.mean(sales)
    stdev = statistics.stdev(sales) if len(sales) > 1 else 0

    if stdev == 0:
        return [], [], {
            "mean": round(mean, 2),
            "stdev": 0,
            "threshold": threshold,
            "message": "표준편차 0 (모든 값 동일)"
        }

    outlier_indices = []
    outlier_values = []
    z_scores = []

    for i, val in enumerate(sales):
        z = (val - mean) / stdev
        z_scores.append(round(z, 2))
        if abs(z) > threshold:
            outlier_indices.append(i)
            outlier_values.append(val)

    stats = {
        "mean": round(mean, 2),
        "stdev": round(stdev, 2),
        "threshold": threshold,
        "lower_bound": round(mean - threshold * stdev, 2),
        "upper_bound": round(mean + threshold * stdev, 2)
    }

    return outlier_indices, outlier_values, stats


def clean_outliers(
    sales: List[int],
    method: Literal["iqr", "zscore"] = "iqr",
    strategy: Literal["remove", "replace_median", "replace_mean", "cap"] = "cap",
    **kwargs: Any
) -> OutlierResult:
    """
    이상치 처리 메인 함수

    Args:
        sales: 판매량 리스트
        method: 탐지 방법 ("iqr" 또는 "zscore")
        strategy: 처리 전략
            - "remove": 이상치 제거 (데이터 수 감소)
            - "replace_median": 중앙값으로 대체
            - "replace_mean": 평균으로 대체
            - "cap": 상한/하한으로 캡핑 (권장)
        **kwargs: 탐지 메서드별 추가 파라미터
            - multiplier: IQR 배수 (기본 1.5)
            - threshold: Z-score 임계값 (기본 2.0)

    Returns:
        OutlierResult

    예시:
        >>> sales = [5, 6, 4, 7, 50, 5, 6]  # 50이 이상치
        >>> result = clean_outliers(sales, method="iqr", strategy="cap")
        >>> result.cleaned_data
        [5, 6, 4, 7, 10, 5, 6]  # 50이 상한(10)으로 캡핑됨
    """
    if not sales:
        return OutlierResult(
            original_data=[],
            cleaned_data=[],
            outlier_indices=[],
            outlier_values=[],
            method=method,
            stats={"message": "빈 데이터"}
        )

    # 최소 데이터 수 체크
    min_data = 5 if method == "iqr" else 3
    if len(sales) < min_data:
        return OutlierResult(
            original_data=list(sales),
            cleaned_data=list(sales),
            outlier_indices=[],
            outlier_values=[],
            method=method,
            stats={"message": f"데이터 부족 ({len(sales)}개 < {min_data}개)"}
        )

    # 이상치 탐지
    if method == "iqr":
        multiplier = kwargs.get("multiplier", 1.5)
        outlier_indices, outlier_values, stats = detect_outliers_iqr(sales, multiplier)
    else:  # zscore
        threshold = kwargs.get("threshold", 2.0)
        outlier_indices, outlier_values, stats = detect_outliers_zscore(sales, threshold)

    # 이상치가 없으면 원본 반환
    if not outlier_indices:
        return OutlierResult(
            original_data=list(sales),
            cleaned_data=list(sales),
            outlier_indices=[],
            outlier_values=[],
            method=method,
            stats=stats
        )

    # 처리 전략 적용
    cleaned = list(sales)

    if strategy == "remove":
        # 이상치 제거 (인덱스 역순으로 삭제)
        for idx in sorted(outlier_indices, reverse=True):
            del cleaned[idx]

    elif strategy == "replace_median":
        # 중앙값으로 대체
        median_val = int(statistics.median(sales))
        for idx in outlier_indices:
            cleaned[idx] = median_val

    elif strategy == "replace_mean":
        # 평균으로 대체
        mean_val = int(statistics.mean(sales))
        for idx in outlier_indices:
            cleaned[idx] = mean_val

    elif strategy == "cap":
        # 상한/하한으로 캡핑
        lower = max(0, int(stats.get("lower_bound", 0)))
        upper = int(stats.get("upper_bound", max(sales)))

        for idx in outlier_indices:
            if sales[idx] < lower:
                cleaned[idx] = lower
            elif sales[idx] > upper:
                cleaned[idx] = upper

    stats["strategy"] = strategy
    stats["outlier_count"] = len(outlier_indices)

    return OutlierResult(
        original_data=list(sales),
        cleaned_data=cleaned,
        outlier_indices=outlier_indices,
        outlier_values=outlier_values,
        method=method,
        stats=stats
    )


def is_zero_outlier(
    sales: List[int],
    zero_threshold: float = 0.3
) -> List[int]:
    """
    0 판매일이 이상치인지 판단

    Args:
        sales: 판매량 리스트
        zero_threshold: 0의 비율이 이 값 이하면 0을 이상치로 간주

    Returns:
        0 이상치 인덱스 리스트

    로직:
        - 전체 데이터 중 0의 비율 계산
        - 비율이 threshold 이하면 → 0은 비정상 (휴무 등)
        - 비율이 threshold 초과면 → 0은 정상 (원래 안 팔리는 상품)
    """
    if not sales:
        return []

    zero_count = sales.count(0)
    zero_ratio = zero_count / len(sales)

    # 0의 비율이 threshold 초과면 → 0은 정상
    if zero_ratio > zero_threshold:
        return []

    # 0의 비율이 threshold 이하면 → 0을 이상치로 간주
    zero_indices = [i for i, val in enumerate(sales) if val == 0]

    return zero_indices


def clean_sales_data(
    sales: List[int],
    mid_cd: Optional[str] = None,
    handle_zeros: bool = True
) -> OutlierResult:
    """
    판매 데이터 정제 (카테고리별 설정 자동 적용)

    Args:
        sales: 판매량 리스트
        mid_cd: 중분류 코드 (None이면 기본 설정)
        handle_zeros: 0 이상치 처리 여부

    Returns:
        OutlierResult
    """
    # 카테고리별 설정 가져오기
    config = get_outlier_config(mid_cd) if mid_cd else OUTLIER_CONFIG["default"]

    # 이상치 처리
    result = clean_outliers(
        sales,
        method=config["method"],
        strategy=config["strategy"],
        multiplier=config.get("multiplier", 1.5),
        threshold=config.get("threshold", 2.0)
    )

    # 0 이상치 추가 처리
    if handle_zeros and result.cleaned_data:
        zero_outliers = is_zero_outlier(result.cleaned_data)

        if zero_outliers:
            # 0을 중앙값으로 대체
            non_zero = [v for v in result.cleaned_data if v > 0]
            if non_zero:
                replacement = int(statistics.median(non_zero))
                for idx in zero_outliers:
                    result.cleaned_data[idx] = replacement

                result.outlier_indices.extend(zero_outliers)
                result.outlier_values.extend([0] * len(zero_outliers))
                result.stats["zero_outliers"] = len(zero_outliers)

    return result


# =============================================================================
# 테스트
# =============================================================================
if __name__ == "__main__":
    # 테스트 데이터
    print("=" * 60)
    print("이상치 처리 모듈 테스트")
    print("=" * 60)

    # 1. 상단 이상치 테스트
    print("\n1. 상단 이상치 (IQR)")
    sales1 = [5, 6, 4, 7, 50, 5, 6, 5, 7, 4]
    result1 = clean_outliers(sales1, method="iqr", strategy="cap")
    print(f"  원본: {result1.original_data}")
    print(f"  결과: {result1.cleaned_data}")
    print(f"  이상치: {result1.outlier_values} (인덱스: {result1.outlier_indices})")
    print(f"  통계: {result1.stats}")

    # 2. Z-score 테스트
    print("\n2. 상단 이상치 (Z-score)")
    result2 = clean_outliers(sales1, method="zscore", strategy="cap", threshold=2.0)
    print(f"  원본: {result2.original_data}")
    print(f"  결과: {result2.cleaned_data}")
    print(f"  이상치: {result2.outlier_values}")

    # 3. 0 이상치 테스트
    print("\n3. 0 이상치")
    sales3 = [5, 6, 0, 5, 6, 5, 6, 5]
    zero_outliers = is_zero_outlier(sales3)
    print(f"  원본: {sales3}")
    print(f"  0 이상치 인덱스: {zero_outliers}")

    # 4. 카테고리별 테스트
    print("\n4. 카테고리별 설정")
    for mid_cd, name in [("072", "담배"), ("049", "맥주"), ("001", "도시락")]:
        config = get_outlier_config(mid_cd)
        print(f"  {name}({mid_cd}): multiplier={config['multiplier']}")

    # 5. 통합 테스트
    print("\n5. 통합 정제 (담배)")
    sales5 = [5, 6, 4, 100, 5, 0, 6, 5, 7, 4]  # 100과 0이 이상치
    result5 = clean_sales_data(sales5, mid_cd="072", handle_zeros=True)
    print(f"  원본: {result5.original_data}")
    print(f"  결과: {result5.cleaned_data}")
    print(f"  이상치: {result5.outlier_values}")
