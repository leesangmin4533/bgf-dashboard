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


# 대분류(large_cd) 슈퍼그룹 매핑 (2026-03-01 추가)
LARGE_CD_SUPERGROUPS = {
    "lcd_food":      ["01", "02"],                              # 간편식사, 신선식품
    "lcd_snack":     ["11", "12", "13", "14"],                  # 과자, 제과, 아이스크림, 빵
    "lcd_grocery":   ["21", "22", "23"],                        # 가공식품, 조미료, 면류
    "lcd_beverage":  ["31", "33", "34"],                        # 음료, 유제품, 커피
    "lcd_non_food":  ["41", "42", "43", "44", "45", "91"],      # 생활용품, 잡화, 기타
}

# 역매핑 (large_cd → 슈퍼그룹명)
_LARGE_CD_LOOKUP: Dict[str, str] = {}
for _sg_name, _lg_codes in LARGE_CD_SUPERGROUPS.items():
    for _lg_code in _lg_codes:
        _LARGE_CD_LOOKUP[_lg_code] = _sg_name


def get_large_cd_supergroup(large_cd: Optional[str]) -> Optional[str]:
    """large_cd로 슈퍼그룹 반환 (없거나 매핑 없으면 None)"""
    if not large_cd:
        return None
    return _LARGE_CD_LOOKUP.get(large_cd.zfill(2))


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
        # 카테고리 그룹 원핫 — 제거 (ml-improvement Phase C)
        # 그룹별 별도 모델이므로 항상 1.0 또는 0.0 → 정보 없음
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
        # 대분류 슈퍼그룹 원핫 — 제거 (ml-improvement Phase C)
        # 카테고리 그룹 원핫과 높은 상관 → 정보 중복
        # 그룹 컨텍스트 (food-ml-dual-model)
        "data_days_ratio",      # 데이터 충분도 (data_days/60, cap 1.0)
        "smallcd_peer_avg",     # 동일 소분류 7일 평균 매출 (정규화: /10, cap 1.0)
        "relative_position",    # 내 매출 / 피어 평균 (cap 3.0, 0=정보없음)
        "lifecycle_stage",      # 라이프사이클 단계 (0=신제품, 0.5=모니터링, 1.0=안정)
        # 외부 환경 계수 (35→39 피처 확장)
        "rain_qty_level",       # [35] 0=없음 1=이슬비 2=보통 3=폭우
        "sky_condition",        # [36] 0=맑음 1=구름많음 2=흐림 3=안개 4=황사
        "is_payday",            # [37] 0/1 급여일 당일+다음날
        "pm25_level",           # [38] 0=좋음 1=보통 2=나쁨 3=매우나쁨
        # 시간대별 매출 패턴 (2026-03-15 추가, hourly_sales_detail 기반)
        "morning_ratio",        # [39] 오전(6~11시) 매출 비중
        "lunch_ratio",          # [40] 점심(11~14시) 매출 비중
        "evening_ratio",        # [41] 저녁(17~21시) 매출 비중
        "night_ratio",          # [42] 야간(21~24시) 매출 비중
        "peak_hour_slot",       # [43] 주 피크 시간대 (0=아침/1=점심/2=저녁/3=야간)
        "hour_cv",              # [44] 시간대별 변동계수
        # 주류 특화 피처 (2026-03-18 추가, alcohol_group Acc@1 개선용)
        "is_friday",            # [45] 금요일 여부 (맥주/소주 피크 요일)
        "is_fri_or_sat",        # [46] 금~토 여부 (주말 음주 수요)
        "temp_x_weekend",       # [47] 기온×주말 교호작용 (더운 주말 맥주 급증)
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
        large_cd: Optional[str] = None,
        data_days: int = 0,
        smallcd_peer_avg: float = 0.0,
        lifecycle_stage: float = 1.0,
        # 외부 환경 계수 (35→39 피처 확장)
        rain_qty: float = 0.0,
        sky_nm: str = "",
        is_payday: int = 0,
        pm25: float = 0.0,
        # 시간대별 매출 패턴 (39→45 피처 확장)
        hourly_ratios: Optional[Dict[str, float]] = None,
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
            large_cd: 대분류 코드 (None=정보없음, 슈퍼그룹 원핫 all-zero)

        Returns:
            feature 배열 (1D numpy array) 또는 None
        """
        if not daily_sales or len(daily_sales) < 3:
            return None

        try:
            # 판매량 배열 추출 (품절일 imputation 포함)
            from src.prediction.prediction_config import PREDICTION_PARAMS
            _ml_so_cfg = PREDICTION_PARAMS.get("ml_stockout_filter", {})

            if _ml_so_cfg.get("enabled", False):
                _min_avail = _ml_so_cfg.get("min_available_days", 3)
                # 최근 14일 → 30일 2단계 폴백으로 비품절 평균 계산
                _avail_14 = [
                    float(d.get("sale_qty", 0) or 0)
                    for d in daily_sales[-14:]
                    if d.get("stock_qty") is not None and d.get("stock_qty", 0) > 0
                ]
                if len(_avail_14) >= _min_avail:
                    _so_avg = sum(_avail_14) / len(_avail_14)
                else:
                    _avail_30 = [
                        float(d.get("sale_qty", 0) or 0)
                        for d in daily_sales[-30:]
                        if d.get("stock_qty") is not None and d.get("stock_qty", 0) > 0
                    ]
                    _so_avg = (sum(_avail_30) / len(_avail_30)) if len(_avail_30) >= _min_avail else None

                if _so_avg is not None:
                    sales = []
                    for d in daily_sales:
                        _sq = float(d.get("sale_qty", 0) or 0)
                        _stk = d.get("stock_qty")
                        if _stk is not None and _stk == 0 and _sq == 0:
                            sales.append(_so_avg)
                        else:
                            sales.append(_sq)
                else:
                    sales = [float(d.get("sale_qty", 0) or 0) for d in daily_sales]
            else:
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

            # 카테고리 그룹 원핫 — 제거 (ml-improvement Phase C)
            # 그룹별 별도 모델이므로 항상 동일 값 → 정보 없음

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

            # 대분류 슈퍼그룹 원핫 — 제거 (ml-improvement Phase C)

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
                # 카테고리 그룹 원핫 5개 제거 (ml-improvement Phase C)
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
                # 대분류 슈퍼그룹 원핫 5개 제거 (ml-improvement Phase C)
                # 그룹 컨텍스트 (food-ml-dual-model)
                min(float(data_days) / 60.0, 1.0),  # data_days_ratio
                min(float(smallcd_peer_avg) / 10.0, 1.0),  # smallcd_peer_avg (정규화)
                min(float(daily_avg_7) / float(smallcd_peer_avg), 3.0) if smallcd_peer_avg > 0 else 0.0,  # relative_position
                float(lifecycle_stage),  # lifecycle_stage (0~1)
                # 외부 환경 계수 (35→39 피처 확장)
                float(
                    0 if rain_qty < 0.1 else
                    1 if rain_qty < 2 else
                    2 if rain_qty < 15 else
                    3
                ),  # [35] rain_qty_level
                float({"맑음": 0, "구름많음": 1, "흐림": 2, "안개": 3, "황사": 4}.get(sky_nm, 0)),  # [36] sky_condition
                float(is_payday),  # [37] is_payday
                float(
                    0 if pm25 < 16 else
                    1 if pm25 < 36 else
                    2 if pm25 < 76 else
                    3
                ),  # [38] pm25_level
                # 시간대별 매출 패턴 (2026-03-15 추가)
                *MLFeatureBuilder._hourly_feature_values(hourly_ratios),
                # 주류 특화 피처 (2026-03-18 추가)
                1.0 if weekday == 4 else 0.0,  # [45] is_friday
                1.0 if weekday in (4, 5) else 0.0,  # [46] is_fri_or_sat
                ((temperature - 15.0) / 15.0 if temperature is not None else 0.0) * is_weekend,  # [47] temp_x_weekend
            ], dtype=np.float32)

            return features

        except Exception as e:
            logger.warning(f"Feature 빌드 실패: {e}")
            return None

    @staticmethod
    def _hourly_feature_values(hourly_ratios: Optional[Dict[str, float]] = None) -> list:
        """시간대 Feature 6개 값 반환 (데이터 없으면 균등 기본값)"""
        _hr = hourly_ratios or {}
        return [
            float(_hr.get("morning_ratio", 0.25)),
            float(_hr.get("lunch_ratio", 0.25)),
            float(_hr.get("evening_ratio", 0.25)),
            float(_hr.get("night_ratio", 0.25)),
            float(_hr.get("peak_hour_slot", 1.0)),
            float(_hr.get("hour_cv", 0.5)),
        ]

    @staticmethod
    def calc_hourly_ratios(
        store_id: str, item_cd: str,
        base_date: str, days: int = 14
    ) -> dict:
        """hourly_sales_detail에서 최근 N일 시간대별 판매 비중 집계

        Args:
            store_id: 매장 ID
            item_cd: 상품 코드
            base_date: 기준 날짜 (YYYY-MM-DD)
            days: 조회 기간 (일)

        Returns:
            {morning_ratio, lunch_ratio, evening_ratio, night_ratio,
             peak_hour_slot, hour_cv} 또는 {} (데이터 없음)
        """
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_store_connection(store_id)
            try:
                rows = conn.execute("""
                    SELECT hour, SUM(sale_qty) as qty
                    FROM hourly_sales_detail
                    WHERE item_cd = ?
                      AND sales_date >= date(?, ?)
                      AND sales_date < ?
                    GROUP BY hour
                """, (item_cd, base_date,
                      f"-{days} days", base_date)).fetchall()
            finally:
                conn.close()

            if not rows:
                return {}

            # 시간대별 집계
            slots = {"morning": 0, "lunch": 0, "evening": 0, "night": 0}
            total = 0
            for hour, qty in rows:
                total += qty
                if 6 <= hour < 11:
                    slots["morning"] += qty
                elif 11 <= hour < 14:
                    slots["lunch"] += qty
                elif 17 <= hour < 21:
                    slots["evening"] += qty
                else:
                    slots["night"] += qty

            if total == 0:
                return {}

            # 피크 슬롯 (0=아침/1=점심/2=저녁/3=야간)
            slot_names = ["morning", "lunch", "evening", "night"]
            peak_slot = slot_names.index(
                max(slots, key=slots.get)
            )

            # 변동계수 (시간대별 수요 집중도)
            slot_vals = list(slots.values())
            _mean = np.mean(slot_vals)
            hour_cv = (np.std(slot_vals) / _mean) if _mean > 0 else 0.5

            return {
                "morning_ratio": round(slots["morning"] / total, 4),
                "lunch_ratio": round(slots["lunch"] / total, 4),
                "evening_ratio": round(slots["evening"] / total, 4),
                "night_ratio": round(slots["night"] / total, 4),
                "peak_hour_slot": float(peak_slot),
                "hour_cv": round(float(hour_cv), 4),
            }
        except Exception:
            return {}

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
                large_cd=item.get("large_cd"),
                data_days=item.get("data_days", 0),
                smallcd_peer_avg=item.get("smallcd_peer_avg", 0.0),
                lifecycle_stage=item.get("lifecycle_stage", 1.0),
                rain_qty=item.get("rain_qty", 0.0),
                sky_nm=item.get("sky_nm", ""),
                is_payday=item.get("is_payday", 0),
                pm25=item.get("pm25", 0.0),
                hourly_ratios=item.get("hourly_ratios"),
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
