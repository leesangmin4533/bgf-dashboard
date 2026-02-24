"""
ML Feature Builder
- FeatureResult를 numpy array로 변환
- 추가 Feature: 카테고리 원핫, 요일 원핫, 월별 사이클, 행사 여부
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 카테고리 그룹 매핑
CATEGORY_GROUPS = {
    "food_group": ["001", "002", "003", "004", "005", "012"],
    "alcohol_group": ["049", "050"],
    "tobacco_group": ["072", "073"],
    "perishable_group": ["013", "026", "046"],
    "general_group": [],  # 나머지 전체
}

# 그룹 역매핑 (mid_cd → 그룹명)
_GROUP_LOOKUP = {}
for group_name, codes in CATEGORY_GROUPS.items():
    for code in codes:
        _GROUP_LOOKUP[code] = group_name


def get_category_group(mid_cd: str) -> str:
    """중분류 코드로 카테고리 그룹 반환"""
    return _GROUP_LOOKUP.get(mid_cd, "general_group")


class MLFeatureBuilder:
    """ML용 Feature 빌더"""

    # Feature 컬럼 정의 (순서 고정)
    FEATURE_NAMES = [
        # 기본 통계
        "daily_avg_7",      # 7일 일평균
        "daily_avg_14",     # 14일 일평균
        "daily_avg_31",     # 31일 일평균
        "stock_qty",        # 현재 재고
        "pending_qty",      # 미입고 수량
        # 트렌드
        "trend_score",      # 트렌드 점수 (7일 vs 31일 비율)
        "cv",               # 변동계수 (coefficient of variation)
        # 시간 특성
        "weekday_sin",      # 요일 사인
        "weekday_cos",      # 요일 코사인
        "month_sin",        # 월 사인
        "month_cos",        # 월 코사인
        "is_weekend",       # 주말 여부
        "is_holiday",       # 공휴일 여부
        # 행사
        "promo_active",     # 행사 여부
        # 카테고리 그룹 원핫 (5개)
        "is_food_group",
        "is_alcohol_group",
        "is_tobacco_group",
        "is_perishable_group",
        "is_general_group",
        # 비즈니스 특성 (A-4 추가)
        "expiration_days",  # 유통기한 (일, 0=정보없음)
        "disuse_rate",      # 과거 30일 폐기율 (0.0~1.0)
        "margin_rate",      # 이익률 (%, 0=정보없음)
        # 외부 요인 (B-3 추가)
        "temperature",      # 기온 (정규화, 0=정보없음)
        "temperature_delta", # 전일 대비 기온 변화량 (정규화: delta/15, 0=정보없음)
        # Lag Features (기간대비 추가)
        "lag_7",            # 7일 전 판매량 (동일 요일)
        "lag_28",           # 28일 전 판매량 (동일 요일)
        "week_over_week",   # 전주 대비 변화율 (-1.0~+∞, 0=정보없음)
        # 연관 상품 (Association)
        "association_score", # 연관 상품 부스트 점수 (0.0~1.0)
        # 연휴 맥락 (2026-02-18 추가)
        "holiday_period_days",  # 연속 연휴 길이 (정규화: days/7, 0=비연휴)
        "is_pre_holiday",       # 연휴 전날 (1.0/0.0)
        "is_post_holiday",      # 연휴 후 첫 영업일 (1.0/0.0)
        # 입고 패턴 (2026-02-23 추가)
        "lead_time_avg",        # 평균 리드타임 (정규화: /3.0, cap 1.0)
        "lead_time_cv",         # 리드타임 안정성 CV (정규화: /2.0, cap 1.0)
        "short_delivery_rate",  # 숏배송율 (0~1, 정규화 불필요)
        "delivery_frequency",   # 14일 입고 빈도 (정규화: /14.0)
        "pending_age_days",     # 미입고 경과일 (정규화: /5.0, cap 1.0)
    ]

    @staticmethod
    def build_features(
        daily_sales: List[Dict[str, Any]],
        target_date: str,
        mid_cd: str,
        stock_qty: int = 0,
        pending_qty: int = 0,
        promo_active: bool = False,
        expiration_days: int = 0,
        disuse_rate: float = 0.0,
        margin_rate: float = 0.0,
        temperature: Optional[float] = None,
        temperature_delta: Optional[float] = None,
        lag_7: Optional[int] = None,
        lag_28: Optional[int] = None,
        week_over_week: Optional[float] = None,
        is_holiday: bool = False,
        association_score: float = 0.0,
        holiday_period_days: int = 0,
        is_pre_holiday: bool = False,
        is_post_holiday: bool = False,
        receiving_stats: Optional[Dict[str, float]] = None,
    ) -> Optional[np.ndarray]:
        """
        일별 판매 데이터에서 ML feature 배열 생성

        Args:
            daily_sales: [{sales_date, sale_qty, ...}, ...] (날짜 정렬)
            target_date: 예측 대상 날짜 (YYYY-MM-DD)
            mid_cd: 중분류 코드
            stock_qty: 현재 재고
            pending_qty: 미입고 수량
            promo_active: 행사 진행 여부
            expiration_days: 유통기한 (일, 0=정보없음)
            disuse_rate: 과거 30일 폐기율 (0.0~1.0)
            margin_rate: 이익률 (%, 0=정보없음)
            temperature: 기온 (None=정보없음)
            temperature_delta: 전일 대비 기온 변화량 (None=정보없음)
            lag_7: 7일 전 판매량 (None=정보없음)
            lag_28: 28일 전 판매량 (None=정보없음)
            week_over_week: 전주 대비 변화율 (None=정보없음)
            is_holiday: 공휴일 여부

        Returns:
            feature 배열 (1D numpy array) 또는 None
        """
        if not daily_sales or len(daily_sales) < 3:
            return None

        try:
            # 판매량 배열 추출
            sales = [float(d.get("sale_qty", 0) or 0) for d in daily_sales]

            # 일평균 계산
            daily_avg_7 = np.mean(sales[-7:]) if len(sales) >= 7 else np.mean(sales)
            daily_avg_14 = np.mean(sales[-14:]) if len(sales) >= 14 else np.mean(sales)
            daily_avg_31 = np.mean(sales[-31:]) if len(sales) >= 31 else np.mean(sales)

            # 트렌드 점수
            trend_score = (daily_avg_7 / daily_avg_31) if daily_avg_31 > 0 else 1.0

            # 변동계수
            std = np.std(sales[-14:]) if len(sales) >= 14 else np.std(sales)
            mean = daily_avg_14
            cv = (std / mean) if mean > 0 else 0.0

            # 시간 특성
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            weekday = dt.weekday()  # 0=월 ~ 6=일
            month = dt.month

            weekday_sin = np.sin(2 * np.pi * weekday / 7)
            weekday_cos = np.cos(2 * np.pi * weekday / 7)
            month_sin = np.sin(2 * np.pi * (month - 1) / 12)
            month_cos = np.cos(2 * np.pi * (month - 1) / 12)
            is_weekend = 1.0 if weekday >= 5 else 0.0
            holiday_flag = 1.0 if is_holiday else 0.0

            # 카테고리 그룹 원핫
            group = get_category_group(mid_cd)
            is_food = 1.0 if group == "food_group" else 0.0
            is_alcohol = 1.0 if group == "alcohol_group" else 0.0
            is_tobacco = 1.0 if group == "tobacco_group" else 0.0
            is_perishable = 1.0 if group == "perishable_group" else 0.0
            is_general = 1.0 if group == "general_group" else 0.0

            # 유통기한 정규화 (log 스케일, 0은 0 유지)
            norm_expiry = np.log1p(float(expiration_days)) if expiration_days > 0 else 0.0

            # Lag feature 정규화: 일평균 대비 비율 (스케일 일관성)
            # lag_7/lag_28: 일평균 기준 정규화 (0=정보없음)
            norm_lag_7 = 0.0
            norm_lag_28 = 0.0
            if lag_7 is not None and daily_avg_7 > 0:
                norm_lag_7 = float(lag_7) / daily_avg_7
            elif lag_7 is not None:
                norm_lag_7 = float(lag_7)

            if lag_28 is not None and daily_avg_31 > 0:
                norm_lag_28 = float(lag_28) / daily_avg_31
            elif lag_28 is not None:
                norm_lag_28 = float(lag_28)

            # week_over_week: 이미 비율이므로 클리핑만 (-1.0 ~ 3.0)
            norm_wow = 0.0
            if week_over_week is not None:
                norm_wow = max(-1.0, min(3.0, float(week_over_week)))

            # 입고 패턴 피처 정규화
            _recv = receiving_stats or {}
            _lt_avg = float(_recv.get("lead_time_avg", 0.0))
            _lt_std = float(_recv.get("lead_time_std", 0.0))
            _lt_mean = max(_lt_avg, 0.001)  # division by zero 방지
            _lt_cv = (_lt_std / _lt_mean) if _lt_avg > 0 else 0.25  # 데이터 없으면 0.25(낮은 변동)

            _norm_lt_avg = min(_lt_avg / 3.0, 1.0)
            _norm_lt_cv = min(_lt_cv / 2.0, 1.0)
            _norm_short_rate = float(_recv.get("short_delivery_rate", 0.0))
            _norm_freq = float(_recv.get("delivery_frequency", 0)) / 14.0
            _norm_pending_age = min(float(_recv.get("pending_age_days", 0)) / 5.0, 1.0)

            features = np.array([
                daily_avg_7,
                daily_avg_14,
                daily_avg_31,
                float(stock_qty),
                float(pending_qty),
                trend_score,
                cv,
                weekday_sin,
                weekday_cos,
                month_sin,
                month_cos,
                is_weekend,
                holiday_flag,
                1.0 if promo_active else 0.0,
                is_food,
                is_alcohol,
                is_tobacco,
                is_perishable,
                is_general,
                # 비즈니스 특성 (A-4 추가)
                norm_expiry,
                float(disuse_rate),
                float(margin_rate) / 100.0,  # 0~1로 정규화
                # 외부 요인 (B-3 추가)
                (temperature - 15.0) / 15.0 if temperature is not None else -2.0,  # 정규화: 15도=0, 30도=1, None=-2.0(sentinel)
                temperature_delta / 15.0 if temperature_delta is not None else 0.0,  # 정규화: +15도=1.0, -15도=-1.0, None=0
                # Lag Features (기간대비 추가)
                norm_lag_7,
                norm_lag_28,
                norm_wow,
                # 연관 상품 점수 (0.0~1.0)
                float(max(0.0, min(1.0, association_score))),
                # 연휴 맥락 (2026-02-18 추가)
                float(holiday_period_days) / 7.0,  # 정규화: 7일=1.0, 0=비연휴
                1.0 if is_pre_holiday else 0.0,
                1.0 if is_post_holiday else 0.0,
                # 입고 패턴 (2026-02-23 추가)
                _norm_lt_avg,
                _norm_lt_cv,
                _norm_short_rate,
                _norm_freq,
                _norm_pending_age,
            ], dtype=np.float32)

            return features

        except Exception as e:
            logger.warning(f"Feature 빌드 실패: {e}")
            return None

    @staticmethod
    def build_batch_features(
        items_data: List[Dict[str, Any]],
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], List[str]]:
        """
        여러 상품의 feature를 일괄 생성 (학습용)

        Args:
            items_data: [{
                "daily_sales": [...],
                "target_date": "YYYY-MM-DD",
                "mid_cd": "...",
                "actual_sale_qty": int,
                "stock_qty": int,
                "pending_qty": int,
                "promo_active": bool,
            }, ...]

        Returns:
            (X, y, item_codes) 또는 (None, None, [])
        """
        X_list = []
        y_list = []
        codes = []

        for item in items_data:
            features = MLFeatureBuilder.build_features(
                daily_sales=item.get("daily_sales", []),
                target_date=item.get("target_date", ""),
                mid_cd=item.get("mid_cd", ""),
                stock_qty=item.get("stock_qty", 0),
                pending_qty=item.get("pending_qty", 0),
                promo_active=item.get("promo_active", False),
                expiration_days=item.get("expiration_days", 0),
                disuse_rate=item.get("disuse_rate", 0.0),
                margin_rate=item.get("margin_rate", 0.0),
                temperature=item.get("temperature"),
                temperature_delta=item.get("temperature_delta"),
                lag_7=item.get("lag_7"),
                lag_28=item.get("lag_28"),
                week_over_week=item.get("week_over_week"),
                is_holiday=item.get("is_holiday", False),
                holiday_period_days=item.get("holiday_period_days", 0),
                is_pre_holiday=item.get("is_pre_holiday", False),
                is_post_holiday=item.get("is_post_holiday", False),
                receiving_stats=item.get("receiving_stats"),
            )

            if features is not None:
                X_list.append(features)
                y_list.append(float(item.get("actual_sale_qty", 0)))
                codes.append(item.get("item_cd", ""))

        if not X_list:
            return None, None, []

        X = np.vstack(X_list)
        y = np.array(y_list, dtype=np.float32)
        return X, y, codes
