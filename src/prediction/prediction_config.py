"""
BGF 자동발주 예측 설정 파일 (통합)

이 파일의 역할:
1. 카테고리별 기본 설정 (CATEGORY_CONFIG, DEFAULT_CONFIG)
2. 예측 파라미터 (PREDICTION_PARAMS, ORDER_ADJUSTMENT_RULES)
3. 카테고리별 안전재고 패턴 분석 함수 (analyze_tobacco/ramen/beer/food_pattern)
4. 계절 계수 (SEASONAL_COEFFICIENTS, get_seasonal_coefficient)
5. 요일 계수 (WEEKDAY_COEFFICIENTS, get_weekday_coefficient)
6. 요일 판매 계수 (WEEKDAY_FACTORS, get_weekday_factor, get_weekday_factor_from_db)
7. ML 전환 조건 (ML_SWITCH_CONDITIONS)

카테고리별 Strategy 패턴은 src/prediction/categories/를 사용:
    from src.prediction.categories import (
        is_beer_category, get_safety_stock_with_beer_pattern, ...
    )

- 생성일: 2026-01-27
- 수정: 2026-02-14 - 계절 계수 7그룹 추가, __main__ 테스트 코드 제거
- 수정: 2026-02-18 - src.prediction.config 내용 통합 (CATEGORY_CONFIG, WEEKDAY_FACTORS, ML_SWITCH_CONDITIONS 등)
"""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from src.utils.logger import get_logger

logger = get_logger(__name__)

# =============================================================================
# 0. 카테고리별 기본 설정 (구 config.py에서 통합)
# =============================================================================
# mid_cd: 중분류 코드
# predict_days: 도시락/김밥은 1일, 나머지는 3일
CATEGORY_CONFIG = {
    # 도시락/김밥류 - 유통기한 짧음 (1일 예측)
    "001": {"name": "도시락", "shelf_life": 1, "predict_days": 1, "safety_factor": 1.2},
    "002": {"name": "김밥", "shelf_life": 1, "predict_days": 1, "safety_factor": 1.2},
    "003": {"name": "샌드위치", "shelf_life": 2, "predict_days": 3, "safety_factor": 1.3},
    "004": {"name": "햄버거", "shelf_life": 2, "predict_days": 3, "safety_factor": 1.3},

    # 유제품 (3일 예측)
    "047": {"name": "우유", "shelf_life": 7, "predict_days": 3, "safety_factor": 1.3},
    "048": {"name": "요구르트", "shelf_life": 14, "predict_days": 3, "safety_factor": 1.3},

    # 음료 (3일 예측)
    "044": {"name": "음료수", "shelf_life": 180, "predict_days": 3, "safety_factor": 1.5},
    "045": {"name": "커피음료", "shelf_life": 180, "predict_days": 3, "safety_factor": 1.5},

    # 주류 (3일 예측)
    "049": {"name": "맥주", "shelf_life": 180, "predict_days": 3, "safety_factor": 1.5},
    "050": {"name": "소주", "shelf_life": 365, "predict_days": 3, "safety_factor": 1.5},

    # 과자/스낵 (3일 예측)
    "016": {"name": "스낵", "shelf_life": 60, "predict_days": 3, "safety_factor": 1.5},
    "017": {"name": "비스킷", "shelf_life": 90, "predict_days": 3, "safety_factor": 1.5},

    # 라면/면류 (3일 예측)
    "032": {"name": "라면", "shelf_life": 180, "predict_days": 3, "safety_factor": 1.3},

    # 담배 (3일 예측)
    "072": {"name": "담배", "shelf_life": 365, "predict_days": 3, "safety_factor": 2.0},
}

# 기본값 (카테고리 매핑 안될 때)
DEFAULT_CONFIG = {
    "shelf_life": 30,
    "predict_days": 3,
    "safety_factor": 1.5
}

# 요일별 판매 계수 (기본값 - fallback용)
# 월=0, 화=1, ..., 일=6
WEEKDAY_FACTORS = {
    0: 0.9,   # 월요일 - 평균 이하
    1: 0.95,  # 화요일
    2: 1.0,   # 수요일 - 평균
    3: 1.0,   # 목요일
    4: 1.1,   # 금요일 - 평균 이상
    5: 1.2,   # 토요일 - 높음
    6: 1.15,  # 일요일
}

# 예측 모델 전환 조건
ML_SWITCH_CONDITIONS = {
    "min_total_days": 30,      # 최소 30일 데이터
    "min_item_records": 14,    # 상품당 최소 14건
}


# =============================================================================
# 1. 유통기한별 설정
# =============================================================================
SHELF_LIFE_CONFIG = {
    # 유통기한 그룹: (최소일, 최대일, 안전재고_일수, 폐기율)
    "ultra_short": {  # 초단기: 1-3일 (도시락, 김밥, 샌드위치)
        "min_days": 1,
        "max_days": 3,
        "safety_stock_days": 0.5,  # 0.5일치
        "disuse_rate": 0.024,      # 2.4% 폐기율 (실측)
        "order_freq": "daily",     # 매일 발주
    },
    "short": {  # 단기: 4-7일 (우유, 빵)
        "min_days": 4,
        "max_days": 7,
        "safety_stock_days": 0.5,  # 0.5일치
        "disuse_rate": 0.001,
        "order_freq": "daily",
    },
    "medium": {  # 중기: 8-30일 (요구르트, 디저트)
        "min_days": 8,
        "max_days": 30,
        "safety_stock_days": 1.0,  # 1일치
        "disuse_rate": 0.003,
        "order_freq": "every_other",  # 격일 발주 가능
    },
    "long": {  # 장기: 31-90일 (과자, 음료)
        "min_days": 31,
        "max_days": 90,
        "safety_stock_days": 1.5,  # 1.5일치
        "disuse_rate": 0.0,
        "order_freq": "weekly",
    },
    "ultra_long": {  # 초장기: 91일+ (라면, 통조림)
        "min_days": 91,
        "max_days": 9999,
        "safety_stock_days": 2.0,  # 2일치
        "disuse_rate": 0.0,
        "order_freq": "weekly",
    },
}


# =============================================================================
# 2. 카테고리별 요일 계수 (실제 31일 데이터 기반)
# =============================================================================
# 1.0 = 평균, 1.2 = 평균 대비 20% 증가
WEEKDAY_COEFFICIENTS = {
    # mid_cd: [일, 월, 화, 수, 목, 금, 토]

    # === 주류 (금/토 급증) ===
    "049": [0.97, 1.15, 1.22, 1.21, 1.37, 2.54, 2.37],  # 맥주
    "050": [0.91, 0.90, 1.06, 1.16, 1.55, 1.88, 2.14],  # 소주
    "051": [1.00, 1.00, 1.00, 1.00, 1.00, 1.30, 1.50],  # 전통주 (추정)
    "605": [1.00, 1.00, 1.00, 1.00, 1.00, 1.30, 1.40],  # 기타주류 (추정)

    # === 담배류 (금/토 증가) ===
    "072": [1.24, 0.99, 1.15, 1.21, 1.24, 1.44, 1.47],  # 담배
    "073": [1.10, 1.06, 1.06, 1.56, 1.68, 2.01, 2.18],  # 전자담배

    # === 즉석식품 (평일 안정) ===
    "001": [1.04, 0.98, 1.06, 0.98, 0.98, 0.98, 0.98],  # 도시락
    "002": [0.99, 1.02, 1.04, 0.98, 0.98, 1.02, 0.96],  # 주먹밥
    "003": [1.02, 1.01, 0.97, 0.97, 1.03, 1.03, 0.97],  # 김밥
    "004": [0.98, 0.98, 0.98, 0.98, 0.98, 0.98, 1.09],  # 샌드위치
    "032": [1.00, 1.16, 1.19, 1.23, 1.22, 1.39, 1.67],  # 면류

    # === 음료 (주말 증가) ===
    "044": [1.01, 0.81, 1.27, 1.36, 1.43, 1.44, 1.80],  # 탄산음료
    "040": [1.00, 1.00, 1.00, 1.00, 1.00, 1.10, 1.20],  # 기능건강음료
    "041": [1.00, 1.00, 1.00, 1.00, 1.00, 1.10, 1.15],  # 생수
    "042": [1.00, 1.00, 1.00, 1.00, 1.00, 1.05, 1.10],  # 커피음료
    "047": [1.04, 1.04, 1.37, 1.02, 1.11, 1.47, 1.49],  # 우유

    # === 과자류 (토요일 증가) ===
    "016": [1.01, 0.90, 0.99, 1.05, 0.97, 0.97, 1.11],  # 스낵류
    "015": [0.94, 1.04, 1.08, 1.07, 0.94, 1.01, 0.92],  # 비스켓/쿠키
    "019": [1.10, 0.93, 1.05, 0.98, 0.84, 1.05, 1.05],  # 초콜릿
    "020": [1.01, 1.09, 0.99, 1.00, 0.86, 1.08, 0.97],  # 캔디

    # === 기타 ===
    "023": [0.93, 0.94, 1.20, 1.06, 0.95, 0.98, 0.94],  # 육가공류
    "022": [0.98, 1.20, 0.95, 0.89, 0.93, 0.95, 1.08],  # 마른안주류
    "060": [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00],  # 홈/주방용품
    "900": [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00],  # 소모품
}

# 기본 요일 계수 (매핑 없는 카테고리용)
DEFAULT_WEEKDAY_COEFFICIENTS = [1.00, 0.95, 1.00, 1.00, 0.95, 1.05, 1.10]


# =============================================================================
# 2-1. 카테고리 그룹별 월 계절 계수
# =============================================================================
# 1.0 = 연평균 기준. 편의점 특성 반영:
#   - 음료: 여름 급증(6~8월), 겨울 감소
#   - 아이스크림/냉동: 여름 최대, 겨울 최소
#   - 즉석식품(도시락/주먹밥): 사계절 안정, 봄/가을 소폭 상승
#   - 주류: 여름(맥주↑), 겨울(소주↑)
#   - 과자/스낵: 겨울/명절 소폭 상승
#   - 담배: 계절 무관 (적용 안 함)
# key: 중분류 그룹 패턴, value: {월: 계수}
SEASONAL_COEFFICIENTS = {
    # 음료 (여름 급증)
    "beverage": {
        "mid_codes": ["040", "041", "042", "044", "047"],
        "months": {
            1: 0.80, 2: 0.82, 3: 0.90, 4: 0.95,
            5: 1.05, 6: 1.20, 7: 1.30, 8: 1.25,
            9: 1.05, 10: 0.95, 11: 0.85, 12: 0.80,
        },
    },
    # 아이스크림/냉동 (여름 최대, 겨울 최소)
    "frozen": {
        "mid_codes": ["034", "035"],
        "months": {
            1: 0.60, 2: 0.65, 3: 0.80, 4: 0.90,
            5: 1.10, 6: 1.35, 7: 1.50, 8: 1.45,
            9: 1.10, 10: 0.85, 11: 0.70, 12: 0.60,
        },
    },
    # 즉석식품 (사계절 안정, 봄/가을 소폭 상승)
    "food": {
        "mid_codes": ["001", "002", "003", "004", "005", "012"],
        "months": {
            1: 0.95, 2: 0.95, 3: 1.00, 4: 1.05,
            5: 1.05, 6: 1.00, 7: 0.95, 8: 0.95,
            9: 1.00, 10: 1.05, 11: 1.05, 12: 1.00,
        },
    },
    # 맥주 (여름 급증)
    "beer": {
        "mid_codes": ["049"],
        "months": {
            1: 0.75, 2: 0.78, 3: 0.85, 4: 0.95,
            5: 1.10, 6: 1.25, 7: 1.35, 8: 1.30,
            9: 1.05, 10: 0.90, 11: 0.80, 12: 0.85,
        },
    },
    # 소주 (겨울 상승, 여름 보통)
    "soju": {
        "mid_codes": ["050"],
        "months": {
            1: 1.10, 2: 1.05, 3: 1.00, 4: 0.95,
            5: 0.95, 6: 1.00, 7: 1.00, 8: 1.00,
            9: 0.95, 10: 1.00, 11: 1.05, 12: 1.15,
        },
    },
    # 면류/라면 (겨울 상승)
    "ramen": {
        "mid_codes": ["032"],
        "months": {
            1: 1.15, 2: 1.10, 3: 1.00, 4: 0.95,
            5: 0.90, 6: 0.85, 7: 0.85, 8: 0.85,
            9: 0.95, 10: 1.05, 11: 1.15, 12: 1.20,
        },
    },
    # 과자/스낵 (겨울/명절 소폭 상승)
    "snack": {
        "mid_codes": ["015", "016", "019", "020"],
        "months": {
            1: 1.05, 2: 1.05, 3: 1.00, 4: 0.98,
            5: 0.98, 6: 0.95, 7: 0.95, 8: 0.95,
            9: 1.00, 10: 1.02, 11: 1.05, 12: 1.08,
        },
    },
}

# 중분류 → 계절 그룹 역매핑 (초기화)
_SEASONAL_GROUP_LOOKUP: Dict[str, str] = {}
for _sg_name, _sg_info in SEASONAL_COEFFICIENTS.items():
    for _sg_mid in _sg_info["mid_codes"]:
        _SEASONAL_GROUP_LOOKUP[_sg_mid] = _sg_name


def get_seasonal_coefficient(mid_cd: str, month: int) -> float:
    """
    카테고리와 월로 계절 계수 반환

    Args:
        mid_cd: 중분류 코드
        month: 월 (1~12)

    Returns:
        계절 계수 (1.0 = 평균, 매핑 없으면 1.0)
    """
    group_name = _SEASONAL_GROUP_LOOKUP.get(mid_cd)
    if group_name is None:
        return 1.0
    return SEASONAL_COEFFICIENTS[group_name]["months"].get(month, 1.0)


# =============================================================================
# 3. 카테고리별 안전재고 설정
# =============================================================================
# 일평균 판매량 대비 안전재고 배수
SAFETY_STOCK_MULTIPLIER = {
    # 고회전 상품 (일평균 5개 이상): 안전재고 여유있게
    "high_turnover": {
        "min_daily_avg": 5.0,
        "multiplier": 1.5,  # 1.5일치
        "categories": ["900", "060", "049", "073"],  # 소모품, 홈용품, 맥주, 전자담배
    },
    # 중회전 상품 (일평균 2-5개)
    "medium_turnover": {
        "min_daily_avg": 2.0,
        "multiplier": 1.2,  # 1.2일치
        "categories": ["050", "044", "040", "032"],  # 소주, 탄산, 기능음료, 면류
    },
    # 저회전 상품 (일평균 2개 미만): 재고 최소화
    "low_turnover": {
        "min_daily_avg": 0.0,
        "multiplier": 0.8,  # 0.8일치
        "categories": [],  # 나머지 전부
    },
}

# 카테고리별 고정 안전재고 (유통기한/회전율 무시)
CATEGORY_FIXED_SAFETY_DAYS = {
    "072": 2.0,  # 담배: 2일치 (기본값, 동적 패턴으로 추가 조정)
}


# =============================================================================
# 4. 담배 동적 안전재고 설정 (보루 + 전량소진 패턴)
# =============================================================================
TOBACCO_DYNAMIC_SAFETY_CONFIG = {
    "enabled": True,              # 동적 안전재고 적용 여부
    "target_category": "072",     # 적용 대상: 담배

    # === 데이터 기간 설정 ===
    "analysis_days": 30,          # 기본 분석 기간 (30일)
    "min_data_days": 7,           # 최소 데이터 일수 (7일 미만이면 패턴 분석 스킵)
    "default_safety_days": 2.0,   # 데이터 부족 시 기본 안전재고 일수

    # === 보루 패턴 설정 ===
    "carton_unit": 10,            # 보루 단위 (10갑)
    "min_sales_for_carton": 10,   # 보루 판매로 간주할 최소 판매량

    # 보루 빈도별 추가 안전재고 (30일 환산 기준, 개수)
    "carton_buffer": {
        "high": 10,    # 빈도 4회 이상 (주 1회): +10개
        "medium": 5,   # 빈도 2~3회 (격주 1회): +5개
        "low": 0,      # 빈도 1회 이하: +0개
    },
    "carton_thresholds": {
        "high": 4,     # 4회 이상 = high
        "medium": 2,   # 2~3회 = medium
    },

    # === 전량 소진 패턴 설정 ===
    # 전량 소진 빈도별 안전재고 승수 (30일 환산 기준)
    "sellout_multiplier": {
        "high": 1.5,   # 3회 이상: ×1.5
        "medium": 1.2, # 1~2회: ×1.2
        "low": 1.0,    # 0회: ×1.0
    },
    "sellout_thresholds": {
        "high": 3,     # 3회 이상 = high
        "medium": 1,   # 1~2회 = medium
    },

    # === 최대 재고 상한선 설정 ===
    "max_stock_enabled": True,
    "max_stock": 30,              # 담배 상품당 최대 보유 수량
    "min_order_unit": 10,         # 최소 발주 단위 (입수)
}


# =============================================================================
# 5. 라면 동적 안전재고 설정 (회전율 기반)
# =============================================================================
RAMEN_DYNAMIC_SAFETY_CONFIG = {
    "enabled": True,                      # 동적 안전재고 적용 여부
    "target_categories": ["006", "032"],  # 적용 대상: 조리면(006), 면류(032)

    # === 데이터 기간 설정 ===
    "analysis_days": 30,          # 기본 분석 기간 (30일)
    "min_data_days": 7,           # 최소 데이터 일수 (7일 미만이면 기본값 적용)
    "default_safety_days": 2.0,   # 데이터 부족 시 기본 안전재고 일수

    # === 회전율별 안전재고 일수 ===
    # 일평균 판매량 기준
    "turnover_safety_days": {
        "high": {                 # 고회전: 일평균 5개 이상
            "min_daily_avg": 5.0,
            "safety_days": 2.5,   # 2.5일치 (품절 방지)
        },
        "medium": {               # 중회전: 일평균 2~4개
            "min_daily_avg": 2.0,
            "safety_days": 2.0,   # 2.0일치
        },
        "low": {                  # 저회전: 일평균 1개 이하
            "min_daily_avg": 0.0,
            "safety_days": 1.5,   # 1.5일치 (공간 절약)
        },
    },

    # === 최대 재고 상한선 (공간 제약) ===
    "max_stock_enabled": True,
    "max_stock_days": 5.0,        # 최대 일평균 × 5일치
}


# =============================================================================
# 5-2. 맥주 요일 기반 동적 안전재고 설정
# =============================================================================
BEER_CATEGORIES = ['049']  # 맥주

# 맥주 요일별 계수 (DB 기반 실측값)
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
BEER_WEEKDAY_COEF = {
    0: 1.15,  # 월
    1: 1.22,  # 화
    2: 1.21,  # 수
    3: 1.37,  # 목
    4: 2.54,  # 금 (급증)
    5: 2.37,  # 토 (급증)
    6: 0.97,  # 일
}

# 맥주 안전재고 설정
BEER_SAFETY_CONFIG = {
    "enabled": True,              # 동적 안전재고 적용 여부
    "target_category": "049",     # 맥주

    # === 데이터 기간 설정 ===
    "analysis_days": 30,          # 기본 분석 기간 (30일)
    "min_data_days": 7,           # 최소 데이터 일수

    # === 안전재고 일수 ===
    "default_days": 2,            # 기본 안전재고 (월~목)
    "weekend_days": 3,            # 주말 대비 안전재고 (금/토)
    "weekend_order_days": [4, 5], # 금(4), 토(5) 발주 시 3일치

    # === 최대 재고 상한선 (냉장고 공간 제약) ===
    "max_stock_enabled": True,
    "max_stock_days": 7,          # 최대 재고 상한선 (일평균 × 7일)
}


# =============================================================================
# 5-3. 푸드류 유통기한 기반 동적 안전재고 설정
# =============================================================================
FOOD_CATEGORIES = ['001', '002', '003', '004', '005', '012']
# 001: 도시락, 002: 주먹밥, 003: 김밥, 004: 샌드위치, 005: 햄버거, 012: 빵
# 006(조리면), 032(면류)는 라면 로직 적용
# 072(담배)는 담배 로직 적용

# 푸드류 유통기한 기반 안전재고: food.py의 정의를 사용 (중복 제거)
from src.prediction.categories.food import FOOD_EXPIRY_SAFETY_CONFIG  # noqa: E402
from src.prediction.categories.food import FOOD_EXPIRY_FALLBACK  # noqa: E402

# 푸드류 폐기율 계수 (기존 사용 중인 값)
FOOD_DISUSE_COEFFICIENT = {
    # 폐기율 범위: 계수
    (0.00, 0.05): 1.0,   # 5% 미만: 정상
    (0.05, 0.10): 0.9,   # 5~10%: 10% 감소
    (0.10, 0.15): 0.8,   # 10~15%: 20% 감소
    (0.15, 0.20): 0.65,  # 15~20%: 35% 감소
    (0.20, 1.00): 0.5,   # 20% 이상: 50% 감소
}


# =============================================================================
# 6. 예측 파라미터
# =============================================================================
PREDICTION_PARAMS = {
    # 이동평균 기간
    "moving_avg_days": 7,  # 7일 이동평균

    # 가중치 (최근 데이터에 더 높은 가중치)
    "weights": {
        "day_1": 0.25,  # 어제
        "day_2": 0.20,  # 2일전
        "day_3": 0.15,  # 3일전
        "day_4_7": 0.40,  # 4-7일전 합산
    },

    # 최소/최대 예측값
    "min_prediction": 0,
    "max_prediction": 100,

    # 반올림 설정
    "round_up_threshold": 0.5,  # 0.5 이상이면 올림 (과반)

    # 최소 발주 임계값 (긴급 패치: 0.5 → 0.1)
    "min_order_threshold": 0.1,   # 0.1개 이상이면 발주 허용 (간헐적 수요 지원)

    # 간헐적 수요 보정 설정 v2 (2026-02-07 긴급 패치)
    # 판매 기록이 있는 날만으로 가중이동평균을 계산하면 일평균이 과대추정됨
    # 예: 18일 중 3일만 판매, 총 10개 → 가중평균 3개 / 실제 일평균 0.56개
    "intermittent_demand": {
        "enabled": True,                      # 간헐적 수요 보정 활성화
        "threshold": 0.6,                     # 가용일 중 판매일 비율이 이 값 미만이면 보정 적용
        # ★ 신규 설정 (v2 - 2026-02-07)
        "very_intermittent_threshold": 0.3,   # 매우 간헐적 임계값 (월 1-2회)
        "min_prediction_floor": 0.2,          # 최소 예측값 보장 (개/일)
        "min_safety_stock_days": 0.5,         # 최소 안전재고 (일)
        "rop_enabled": True,                  # ROP (재주문점) 활성화
    },

    # 품절일 필터링 설정
    # WMA 계산 시 stock_qty=0(품절)인 날을 제외하여 실제 수요를 추정
    # 품절일의 sale_qty=0은 수요 부재가 아니라 공급 부재이므로 평균을 희석시킴
    "stockout_filter": {
        "enabled": True,              # 품절일 필터링 활성화
        "min_available_days": 3,      # 필터 후 최소 가용일 수 (미달 시 필터링 포기)
    },

    # 연관 상품 분석 설정
    # "삼각김밥 많이 팔리는 날 → 컵라면도 같이 나간다" 같은 동시 판매 패턴 활용
    "association": {
        "enabled": True,              # 연관 분석 활성화
        "max_boost": 1.15,            # 최대 15% 부스트
        "min_lift": 1.3,              # 유효 규칙 최소 리프트
        "min_confidence": 0.5,        # 유효 규칙 최소 신뢰도
        "trigger_lookback_days": 2,   # 최근 2일 판매 실적 참조
        "cache_ttl_seconds": 300,     # 규칙 캐시 TTL (5분)
    },

    # 연휴 종합 설정 (2026-02-18)
    # 연속 연휴 차등 계수 + WMA 연휴 데이터 보정
    "holiday": {
        "enabled": True,
        # 연휴 길이별 기본 계수
        "period_coefficients": {
            "single": 1.2,    # 단일 공휴일 (1일)
            "short": 1.3,     # 2~4일 연휴
            "long": 1.4,      # 5일+ 대형 연휴
        },
        "period_thresholds": {
            "short": 2,       # 이상이면 short
            "long": 5,        # 이상이면 long
        },
        # 연휴 내 위치별 보정 계수 (기본 계수에 곱해짐)
        "position_modifiers": {
            "pre_holiday": 1.15,   # 연휴 전날 (귀성길)
            "first_day": 1.35,     # 연휴 첫날
            "middle": 1.25,        # 연휴 중간
            "last_day": 1.10,      # 연휴 마지막날
            "post_holiday": 0.90,  # 연휴 후 첫 영업일
        },
        # 카테고리 그룹별 보정 (위치 계수에 곱해짐)
        "category_multipliers": {
            "food": 1.1,        # 도시락/김밥 등 편의식품
            "alcohol": 1.2,     # 주류 (연휴 음주)
            "tobacco": 1.0,     # 담배 (변동 적음)
            "perishable": 0.9,  # 소멸성 (연휴 중 폐기 위험)
            "general": 1.0,     # 기타
        },
    },

    # WMA 연휴 데이터 보정
    # 연휴 중 매출은 평소 패턴과 달라 예측 왜곡을 유발
    # 연휴일 WMA 가중치를 줄여서 영향 최소화
    "holiday_wma_correction": {
        "enabled": True,
        "holiday_weight_factor": 0.3,  # 연휴일 가중치를 30%로 감소
    },

    # WMA 행사 데이터 보정 (2026-02-20)
    # 1+1/2+1 등 행사 기간 매출은 정상 수요가 아니므로
    # WMA 계산 시 가중치를 줄여 행사 종료 후 과잉 발주 방지
    "promo_wma_correction": {
        "enabled": True,
        "promo_weight_factor": 0.25,  # 행사일 가중치를 25%로 감소
    },
}


# =============================================================================
# 6. 발주량 조정 규칙
# =============================================================================
ORDER_ADJUSTMENT_RULES = {
    # 요일별 추가 조정 (특수 케이스)
    "friday_boost": {
        "enabled": True,
        "categories": ["049", "050", "073"],  # 맥주, 소주, 전자담배
        "boost_rate": 1.15,  # 금요일 15% 추가 발주
    },

    # 폐기 방지 규칙
    "disuse_prevention": {
        "enabled": True,
        "shelf_life_threshold": 3,  # 유통기한 3일 이하
        "reduction_rate": 0.85,     # 15% 감량 발주
    },

    # 재고 과다 방지
    "overstock_prevention": {
        "enabled": True,
        "stock_days_threshold": 5,  # 5일치 이상 재고 시
        "skip_order": True,         # 발주 스킵
    },
}


# =============================================================================
# 7. 담배 동적 안전재고 분석 함수
# =============================================================================
def _get_db_path() -> str:
    """DB 경로 반환"""
    return str(Path(__file__).parent.parent.parent / "data" / "bgf_sales.db")


@dataclass
class TobaccoPatternResult:
    """담배 판매 패턴 분석 결과"""
    item_cd: str

    # 데이터 기간 정보
    actual_data_days: int          # 실제 데이터 일수
    analysis_days: int             # 분석 기준 일수 (30일)
    has_enough_data: bool          # 최소 데이터 충족 여부 (7일 이상)

    # 일평균 판매량
    daily_avg: float               # 일평균 판매량

    # 보루 패턴
    carton_count_raw: int          # 실제 보루 판매 일수
    carton_count_scaled: float     # 30일 환산 빈도
    carton_level: str              # high/medium/low
    carton_buffer: int             # 추가 안전재고 개수
    carton_dates: List[str]        # 보루 판매 날짜 목록

    # 전량 소진 패턴
    sellout_count_raw: int         # 실제 전량 소진 일수
    sellout_count_scaled: float    # 30일 환산 빈도
    sellout_level: str             # high/medium/low
    sellout_multiplier: float      # 안전재고 승수

    # 최대 재고 관련
    current_stock: int             # 현재 재고
    max_stock: int                 # 최대 재고 상한선 (30개)
    available_space: int           # 여유분 (30 - 현재재고 - 미입고)
    skip_order: bool               # 발주 스킵 여부
    skip_reason: str               # 스킵 사유

    # 최종 결과
    final_buffer: int              # 최종 추가 안전재고 (보루 버퍼)
    final_multiplier: float        # 최종 승수 (전량 소진)
    final_safety_stock: float      # 최종 안전재고 (개수)


def analyze_tobacco_pattern(
    item_cd: str,
    db_path: Optional[str] = None,
    current_stock: Optional[int] = None,
    pending_qty: int = 0
) -> TobaccoPatternResult:
    """
    담배 상품의 보루 + 전량소진 패턴 종합 분석

    Args:
        item_cd: 상품코드
        db_path: DB 경로 (기본: bgf_sales.db)
        current_stock: 현재 재고 (None이면 DB에서 조회)
        pending_qty: 미입고 수량 (기본 0)

    Returns:
        TobaccoPatternResult: 분석 결과 데이터클래스
    """
    config = TOBACCO_DYNAMIC_SAFETY_CONFIG
    if db_path is None:
        db_path = _get_db_path()

    analysis_days = config["analysis_days"]
    min_data_days = config["min_data_days"]
    carton_unit = config["carton_unit"]
    min_carton_sales = config["min_sales_for_carton"]
    max_stock = config["max_stock"]
    min_order_unit = config["min_order_unit"]
    default_safety_days = config["default_safety_days"]

    # DB에서 판매 데이터 조회 (날짜 오름차순 정렬)
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT sales_date, sale_qty, stock_qty
        FROM daily_sales
        WHERE item_cd = ?
        AND sales_date >= date('now', '-' || ? || ' days')
        ORDER BY sales_date ASC
    """, (item_cd, analysis_days))

    rows = cursor.fetchall()
    conn.close()

    # 실제 데이터 일수
    actual_data_days = len(rows)
    has_enough_data = actual_data_days >= min_data_days

    # 일평균 판매량 계산
    total_sales = sum(row[1] or 0 for row in rows)
    daily_avg = total_sales / actual_data_days if actual_data_days > 0 else 0

    # 현재 재고 조회 (파라미터로 안 주어지면 DB에서 가장 최근 값)
    if current_stock is None:
        current_stock = rows[-1][2] if rows and rows[-1][2] is not None else 0

    # 기본값 초기화
    carton_count_raw = 0
    carton_dates = []
    sellout_count_raw = 0

    if has_enough_data and rows:
        # === 보루 패턴 분석 ===
        # 조건: 판매량 >= 10 AND 10의 배수
        for i, (sales_date, sale_qty, stock_qty) in enumerate(rows):
            if sale_qty and sale_qty >= min_carton_sales and sale_qty % carton_unit == 0:
                carton_count_raw += 1
                carton_dates.append(sales_date)

        # === 전량 소진 패턴 분석 ===
        # 조건: 판매량 > 0 AND 판매량 == 전일 마감재고
        # 첫 번째 날은 전일 재고가 없으므로 제외
        for i in range(1, len(rows)):
            prev_date, prev_sale, prev_stock = rows[i - 1]
            curr_date, curr_sale, curr_stock = rows[i]

            # 전일 마감재고 = 전일 재고 (stock_qty는 마감 재고)
            # 당일 판매량 == 전일 마감재고 → 전량 소진
            if curr_sale and curr_sale > 0 and prev_stock is not None:
                if curr_sale == prev_stock:
                    sellout_count_raw += 1

    # === 30일 환산 빈도 계산 ===
    scale_factor = analysis_days / actual_data_days if actual_data_days > 0 else 1.0
    carton_count_scaled = carton_count_raw * scale_factor
    sellout_count_scaled = sellout_count_raw * scale_factor

    # === 보루 레벨 및 버퍼 결정 ===
    carton_thresholds = config["carton_thresholds"]
    carton_buffers = config["carton_buffer"]

    if carton_count_scaled >= carton_thresholds["high"]:
        carton_level = "high"
    elif carton_count_scaled >= carton_thresholds["medium"]:
        carton_level = "medium"
    else:
        carton_level = "low"

    carton_buffer = carton_buffers[carton_level] if has_enough_data else 0

    # === 전량 소진 레벨 및 승수 결정 ===
    sellout_thresholds = config["sellout_thresholds"]
    sellout_multipliers = config["sellout_multiplier"]

    if sellout_count_scaled >= sellout_thresholds["high"]:
        sellout_level = "high"
    elif sellout_count_scaled >= sellout_thresholds["medium"]:
        sellout_level = "medium"
    else:
        sellout_level = "low"

    sellout_multiplier = sellout_multipliers[sellout_level] if has_enough_data else 1.0

    # === 안전재고 계산 ===
    # 공식: (일평균 × 2일 + 보루버퍼) × 전량소진계수
    if has_enough_data:
        base_safety = daily_avg * default_safety_days
        final_safety_stock = (base_safety + carton_buffer) * sellout_multiplier
    else:
        # 데이터 부족: 기본 2일치만
        final_safety_stock = daily_avg * default_safety_days

    # === 최대 재고 상한선 체크 ===
    available_space = max_stock - current_stock - pending_qty
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"]:
        if available_space <= 0:
            # 여유분 없음 → 발주 스킵
            skip_order = True
            skip_reason = f"상한선 초과 (현재고{current_stock}+미입고{pending_qty}≥{max_stock})"
        elif available_space < min_order_unit:
            # 여유분 < 최소 입수(10개) → 발주 스킵
            skip_order = True
            skip_reason = f"여유분 부족 (여유{available_space}<입수{min_order_unit})"

    return TobaccoPatternResult(
        item_cd=item_cd,
        actual_data_days=actual_data_days,
        analysis_days=analysis_days,
        has_enough_data=has_enough_data,
        daily_avg=round(daily_avg, 2),
        carton_count_raw=carton_count_raw,
        carton_count_scaled=round(carton_count_scaled, 1),
        carton_level=carton_level,
        carton_buffer=carton_buffer,
        carton_dates=carton_dates,
        sellout_count_raw=sellout_count_raw,
        sellout_count_scaled=round(sellout_count_scaled, 1),
        sellout_level=sellout_level,
        sellout_multiplier=sellout_multiplier,
        current_stock=current_stock,
        max_stock=max_stock,
        available_space=available_space,
        skip_order=skip_order,
        skip_reason=skip_reason,
        final_buffer=carton_buffer,
        final_multiplier=sellout_multiplier,
        final_safety_stock=round(final_safety_stock, 2),
    )


def calculate_tobacco_dynamic_safety(
    item_cd: str,
    daily_avg: Optional[float] = None,
    db_path: Optional[str] = None,
    current_stock: Optional[int] = None,
    pending_qty: int = 0
) -> Tuple[float, Optional[TobaccoPatternResult]]:
    """
    담배 동적 안전재고 계산

    공식: (일평균 × 2일 + 보루버퍼) × 전량소진계수

    Args:
        item_cd: 상품코드
        daily_avg: 일평균 판매량 (None이면 DB에서 계산)
        db_path: DB 경로
        current_stock: 현재 재고 (None이면 DB에서 조회)
        pending_qty: 미입고 수량

    Returns:
        (최종 안전재고, 패턴 분석 결과)
    """
    config = TOBACCO_DYNAMIC_SAFETY_CONFIG

    if not config["enabled"]:
        # 비활성화 시 기본값 반환
        default_safety = (daily_avg or 0) * config["default_safety_days"]
        return default_safety, None

    # 패턴 분석 (현재 재고 및 미입고 포함)
    pattern = analyze_tobacco_pattern(item_cd, db_path, current_stock, pending_qty)

    # 패턴에서 계산된 안전재고 사용
    return pattern.final_safety_stock, pattern


# =============================================================================
# 8. 라면 동적 안전재고 분석 함수
# =============================================================================
@dataclass
class RamenPatternResult:
    """라면 판매 패턴 분석 결과"""
    item_cd: str

    # 데이터 기간 정보
    actual_data_days: int          # 실제 데이터 일수
    analysis_days: int             # 분석 기준 일수 (30일)
    has_enough_data: bool          # 최소 데이터 충족 여부 (7일 이상)

    # 회전율 정보
    total_sales: int               # 총 판매량
    daily_avg: float               # 일평균 판매량
    turnover_level: str            # high/medium/low
    safety_days: float             # 적용된 안전재고 일수

    # 재고 정보
    current_stock: int             # 현재 재고
    max_stock: float               # 최대 재고 상한선
    is_over_max_stock: bool        # 상한선 초과 여부

    # 최종 결과
    final_safety_stock: float      # 최종 안전재고 (개수)
    skip_order: bool               # 발주 스킵 여부


def analyze_ramen_pattern(
    item_cd: str,
    db_path: Optional[str] = None
) -> RamenPatternResult:
    """
    라면 상품의 회전율 기반 패턴 분석

    Args:
        item_cd: 상품코드
        db_path: DB 경로 (기본: bgf_sales.db)

    Returns:
        RamenPatternResult: 분석 결과 데이터클래스
    """
    config = RAMEN_DYNAMIC_SAFETY_CONFIG
    if db_path is None:
        db_path = _get_db_path()

    analysis_days = config["analysis_days"]
    min_data_days = config["min_data_days"]
    default_safety_days = config["default_safety_days"]
    turnover_config = config["turnover_safety_days"]

    # DB에서 판매 데이터 조회
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT sales_date, sale_qty, stock_qty
        FROM daily_sales
        WHERE item_cd = ?
        AND sales_date >= date('now', '-' || ? || ' days')
        ORDER BY sales_date DESC
    """, (item_cd, analysis_days))

    rows = cursor.fetchall()
    conn.close()

    # 실제 데이터 일수
    actual_data_days = len(rows)
    has_enough_data = actual_data_days >= min_data_days

    # 판매량 계산
    total_sales = sum(row[1] or 0 for row in rows)
    daily_avg = total_sales / actual_data_days if actual_data_days > 0 else 0

    # 현재 재고 (가장 최근 데이터)
    current_stock = rows[0][2] if rows and rows[0][2] is not None else 0

    # === 회전율 레벨 및 안전재고 일수 결정 ===
    if has_enough_data:
        if daily_avg >= turnover_config["high"]["min_daily_avg"]:
            turnover_level = "high"
            safety_days = turnover_config["high"]["safety_days"]
        elif daily_avg >= turnover_config["medium"]["min_daily_avg"]:
            turnover_level = "medium"
            safety_days = turnover_config["medium"]["safety_days"]
        else:
            turnover_level = "low"
            safety_days = turnover_config["low"]["safety_days"]
    else:
        # 데이터 부족: 기본값 적용
        turnover_level = "unknown"
        safety_days = default_safety_days

    # === 최종 안전재고 계산 ===
    final_safety_stock = daily_avg * safety_days

    # === 최대 재고 상한선 체크 ===
    max_stock = daily_avg * config["max_stock_days"] if daily_avg > 0 else 0
    is_over_max_stock = False
    skip_order = False

    if config["max_stock_enabled"] and max_stock > 0:
        if current_stock >= max_stock:
            is_over_max_stock = True
            skip_order = True

    return RamenPatternResult(
        item_cd=item_cd,
        actual_data_days=actual_data_days,
        analysis_days=analysis_days,
        has_enough_data=has_enough_data,
        total_sales=total_sales,
        daily_avg=round(daily_avg, 2),
        turnover_level=turnover_level,
        safety_days=safety_days,
        current_stock=current_stock,
        max_stock=round(max_stock, 1),
        is_over_max_stock=is_over_max_stock,
        final_safety_stock=round(final_safety_stock, 2),
        skip_order=skip_order,
    )


def calculate_ramen_dynamic_safety(
    item_cd: str,
    daily_avg: Optional[float] = None,
    db_path: Optional[str] = None
) -> Tuple[float, Optional[RamenPatternResult]]:
    """
    라면 동적 안전재고 계산

    Args:
        item_cd: 상품코드
        daily_avg: 일평균 판매량 (None이면 DB에서 조회)
        db_path: DB 경로

    Returns:
        (최종 안전재고, 패턴 분석 결과)
    """
    config = RAMEN_DYNAMIC_SAFETY_CONFIG

    if not config["enabled"]:
        # 비활성화 시 기본값 반환
        default_safety = (daily_avg or 0) * config["default_safety_days"]
        return default_safety, None

    # 패턴 분석
    pattern = analyze_ramen_pattern(item_cd, db_path)

    return pattern.final_safety_stock, pattern


def is_ramen_category(mid_cd: str) -> bool:
    """라면 카테고리 여부 확인"""
    return mid_cd in RAMEN_DYNAMIC_SAFETY_CONFIG["target_categories"]


def get_safety_stock_with_ramen_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None
) -> Tuple[float, Optional[RamenPatternResult]]:
    """
    안전재고 계산 (라면 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드

    Returns:
        (안전재고_개수, 라면_패턴_정보)
        - 라면이 아니면 패턴_정보는 None
    """
    # 라면이 아니면 기본값 반환
    if not is_ramen_category(mid_cd):
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 라면: 동적 안전재고 계산
    if item_cd and RAMEN_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_ramen_dynamic_safety(item_cd)
        return final_safety, pattern

    # 기본값
    default_days = RAMEN_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None


# =============================================================================
# 8-2. 맥주 요일 기반 동적 안전재고 분석 함수
# =============================================================================
@dataclass
class BeerPatternResult:
    """맥주 패턴 분석 결과"""
    item_cd: str
    daily_avg: float              # 일평균 판매량
    actual_data_days: int         # 실제 데이터 일수
    has_enough_data: bool         # 데이터 충분 여부

    # 요일 정보
    order_weekday: int            # 발주 요일 (0=월, 6=일)
    weekday_coef: float           # 적용된 요일 계수

    # 안전재고
    safety_days: int              # 안전재고 일수 (2 또는 3)
    safety_stock: float           # 안전재고 수량

    # 재고 상한선
    max_stock: float              # 최대 재고 상한선
    current_stock: int            # 현재 재고
    pending_qty: int              # 미입고 수량
    available_space: float        # 여유분 (상한선 - 현재 - 미입고)

    # 발주 스킵
    skip_order: bool              # 발주 스킵 여부
    skip_reason: str              # 스킵 사유

    # 최종 결과
    final_safety_stock: float     # 최종 안전재고


def is_beer_category(mid_cd: str) -> bool:
    """맥주 카테고리 여부 확인"""
    return mid_cd in BEER_CATEGORIES


def get_beer_weekday_coef(weekday: int) -> float:
    """
    맥주 요일별 계수 반환

    Args:
        weekday: Python weekday (0=월, 6=일)

    Returns:
        요일 계수
    """
    return BEER_WEEKDAY_COEF.get(weekday, 1.0)


def get_beer_safety_days(order_weekday: int) -> int:
    """
    발주 요일 기준 안전재고 일수 반환

    Args:
        order_weekday: 발주 요일 (0=월, 6=일)

    Returns:
        안전재고 일수 (금/토: 3일, 그 외: 2일)
    """
    config = BEER_SAFETY_CONFIG
    if order_weekday in config["weekend_order_days"]:
        return config["weekend_days"]
    return config["default_days"]


def analyze_beer_pattern(
    item_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0
) -> BeerPatternResult:
    """
    맥주 상품 패턴 분석

    Args:
        item_cd: 상품코드
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량

    Returns:
        BeerPatternResult
    """
    from datetime import datetime

    config = BEER_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

    # 오늘 요일 (발주 기준)
    order_weekday = datetime.now().weekday()  # 0=월, 6=일

    # 요일 계수
    weekday_coef = get_beer_weekday_coef(order_weekday)

    # 안전재고 일수 (금/토: 3일, 그 외: 2일)
    safety_days = get_beer_safety_days(order_weekday)

    # DB에서 일평균 판매량 조회
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(DISTINCT sales_date) as data_days,
               COALESCE(SUM(sale_qty), 0) as total_sales,
               MAX(stock_qty) as latest_stock
        FROM daily_sales
        WHERE item_cd = ?
        AND sales_date >= date('now', '-' || ? || ' days')
    """, (item_cd, config["analysis_days"]))

    row = cursor.fetchone()
    conn.close()

    data_days = row[0] or 0
    total_sales = row[1] or 0
    has_enough_data = data_days >= config["min_data_days"]

    # 일평균 계산
    daily_avg = (total_sales / data_days) if data_days > 0 else 0.0

    # 안전재고 수량
    safety_stock = daily_avg * safety_days

    # 최대 재고 상한선
    max_stock = daily_avg * config["max_stock_days"]

    # 여유분 계산
    available_space = max_stock - current_stock - pending_qty

    # 발주 스킵 판단
    skip_order = False
    skip_reason = ""

    if current_stock + pending_qty >= max_stock and max_stock > 0:
        skip_order = True
        skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return BeerPatternResult(
        item_cd=item_cd,
        daily_avg=round(daily_avg, 2),
        actual_data_days=data_days,
        has_enough_data=has_enough_data,
        order_weekday=order_weekday,
        weekday_coef=weekday_coef,
        safety_days=safety_days,
        safety_stock=round(safety_stock, 2),
        max_stock=round(max_stock, 2),
        current_stock=current_stock,
        pending_qty=pending_qty,
        available_space=round(available_space, 2),
        skip_order=skip_order,
        skip_reason=skip_reason,
        final_safety_stock=round(safety_stock, 2)
    )


def calculate_beer_dynamic_safety(
    item_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0
) -> Tuple[float, BeerPatternResult]:
    """
    맥주 동적 안전재고 계산

    Args:
        item_cd: 상품코드
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량

    Returns:
        (안전재고_수량, 패턴_결과)
    """
    pattern = analyze_beer_pattern(item_cd, db_path, current_stock, pending_qty)
    return pattern.final_safety_stock, pattern


def get_safety_stock_with_beer_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0
) -> Tuple[float, Optional[BeerPatternResult]]:
    """
    안전재고 계산 (맥주 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량

    Returns:
        (안전재고_개수, 맥주_패턴_정보)
        - 맥주가 아니면 패턴_정보는 None
    """
    # 맥주가 아니면 기본값 반환
    if not is_beer_category(mid_cd):
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 맥주: 동적 안전재고 계산
    if item_cd and BEER_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_beer_dynamic_safety(
            item_cd, None, current_stock, pending_qty
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = BEER_SAFETY_CONFIG["default_days"]
    return daily_avg * default_days, None


# =============================================================================
# 8-3. 푸드류 유통기한 기반 동적 안전재고 분석 함수
# → food.py로 이관 완료. categories/__init__.py에서 re-export.
#   FoodExpiryResult, is_food_category, get_food_expiry_group,
#   get_food_expiration_days, get_food_disuse_coefficient,
#   analyze_food_expiry_pattern, calculate_food_dynamic_safety,
#   get_safety_stock_with_food_pattern
# =============================================================================


# =============================================================================
# 9. 기본 헬퍼 함수
# =============================================================================
def get_shelf_life_group(expiration_days: Optional[int]) -> str:
    """유통기한 일수로 그룹 반환"""
    if expiration_days is None:
        return "ultra_long"  # 정보 없으면 장기로 취급

    for group, cfg in SHELF_LIFE_CONFIG.items():
        if cfg["min_days"] <= expiration_days <= cfg["max_days"]:
            return group
    return "ultra_long"


def get_weekday_coefficient(mid_cd: str, weekday: int) -> float:
    """
    카테고리와 요일로 계수 반환
    weekday: 0=일, 1=월, ..., 6=토
    """
    if mid_cd in WEEKDAY_COEFFICIENTS:
        return WEEKDAY_COEFFICIENTS[mid_cd][weekday]
    return DEFAULT_WEEKDAY_COEFFICIENTS[weekday]


def get_safety_stock_days(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int
) -> float:
    """
    안전재고 일수 계산 (기본)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수

    Returns:
        안전재고 일수
    """
    # 1. 카테고리별 고정값 우선 (담배 등)
    if mid_cd in CATEGORY_FIXED_SAFETY_DAYS:
        return CATEGORY_FIXED_SAFETY_DAYS[mid_cd]

    # 2. 유통기한 기반 기본값
    shelf_group = get_shelf_life_group(expiration_days)
    base_days = SHELF_LIFE_CONFIG[shelf_group]["safety_stock_days"]

    # 3. 회전율 기반 조정
    if daily_avg >= 5.0:
        multiplier = SAFETY_STOCK_MULTIPLIER["high_turnover"]["multiplier"]
    elif daily_avg >= 2.0:
        multiplier = SAFETY_STOCK_MULTIPLIER["medium_turnover"]["multiplier"]
    else:
        multiplier = SAFETY_STOCK_MULTIPLIER["low_turnover"]["multiplier"]

    return base_days * multiplier


def get_safety_stock_with_tobacco_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: Optional[int] = None,
    pending_qty: int = 0
) -> Tuple[float, Optional[TobaccoPatternResult]]:
    """
    안전재고 계산 (담배 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고 (담배 상한선 체크용)
        pending_qty: 미입고 수량 (담배 상한선 체크용)

    Returns:
        (안전재고_개수, 담배_패턴_정보)
        - 담배가 아니면 패턴_정보는 None
    """
    # 담배가 아니면 기본값 반환
    if mid_cd != TOBACCO_DYNAMIC_SAFETY_CONFIG["target_category"]:
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 담배: 동적 안전재고 계산
    if item_cd and TOBACCO_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_tobacco_dynamic_safety(
            item_cd, daily_avg, None, current_stock, pending_qty
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = TOBACCO_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None


# =============================================================================
# 9. 카테고리 매핑 (mid_cd -> 한글명)
# =============================================================================
CATEGORY_NAMES = {
    "001": "도시락", "002": "주먹밥", "003": "김밥", "004": "샌드위치류",
    "012": "빵", "014": "디저트", "015": "비스켓/쿠키", "016": "스낵류",
    "019": "초콜릿", "020": "캔디", "022": "마른안주류", "023": "육가공류",
    "032": "면류", "036": "의약외품", "037": "건강기능",
    "039": "과일야채음료", "040": "기능건강음료", "041": "생수",
    "042": "커피음료", "043": "차음료", "044": "탄산음료",
    "046": "요구르트", "047": "우유", "048": "얼음",
    "049": "맥주", "050": "소주", "051": "전통주",
    "057": "위생용품", "060": "홈/주방용품", "061": "문구류",
    "072": "담배", "073": "전자담배", "605": "기타주류", "900": "소모품",
}


# =============================================================================
# 10. 카테고리 설정 헬퍼 함수 (구 config.py에서 통합)
# =============================================================================
def get_category_config(mid_cd: str) -> Dict[str, Any]:
    """카테고리별 설정 반환"""
    return CATEGORY_CONFIG.get(mid_cd, DEFAULT_CONFIG)


def get_weekday_factor(weekday: int) -> float:
    """요일별 판매 계수 반환 (고정값 - fallback용)"""
    return WEEKDAY_FACTORS.get(weekday, 1.0)


def calculate_weekday_factors_from_db(db_path: Optional[str] = None, store_id: Optional[str] = None) -> Dict[int, float]:
    """
    DB에서 실제 요일별 판매량을 분석하여 계수 계산

    Args:
        db_path: DB 경로 (None이면 기본 경로)
        store_id: 매장 ID (None이면 전체)

    Returns:
        {0: 0.95, 1: 1.02, ...} 형태의 요일별 계수
    """
    if db_path is None:
        db_path = Path(__file__).parent.parent.parent / "data" / "bgf_sales.db"

    if not db_path.exists():
        return WEEKDAY_FACTORS.copy()

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 요일별 평균 판매량 계산
        store_filter = "AND store_id = ?" if store_id else ""
        store_params = (store_id,) if store_id else ()
        cursor.execute(f"""
            SELECT
                CAST(strftime('%w', sales_date) AS INTEGER) as weekday,
                AVG(sale_qty) as avg_sales
            FROM daily_sales
            WHERE sale_qty > 0
            {store_filter}
            GROUP BY weekday
        """, store_params)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return WEEKDAY_FACTORS.copy()

        # 전체 평균 계산
        weekday_avgs = {row[0]: row[1] for row in rows}
        total_avg = sum(weekday_avgs.values()) / len(weekday_avgs)

        if total_avg == 0:
            return WEEKDAY_FACTORS.copy()

        # 요일별 계수 계산 (전체 평균 대비)
        factors = {}
        for weekday in range(7):
            if weekday in weekday_avgs:
                # 계수 범위 제한 (0.7 ~ 1.5)
                raw_factor = weekday_avgs[weekday] / total_avg
                factors[weekday] = max(0.7, min(1.5, round(raw_factor, 2)))
            else:
                factors[weekday] = 1.0

        return factors

    except Exception as e:
        logger.warning(f"요일 계수 계산 실패: {e}")
        return WEEKDAY_FACTORS.copy()


# 캐시된 DB 기반 요일 계수 (store_id별 캐시)
_cached_weekday_factors: Dict[Optional[str], Dict[int, float]] = {}


def get_weekday_factor_from_db(weekday: int, refresh: bool = False, store_id: Optional[str] = None) -> float:
    """
    DB 기반 요일별 판매 계수 반환 (캐시 사용)

    Args:
        weekday: 요일 (0=일, 1=월, ..., 6=토) - Python의 weekday()와 다름
        refresh: 캐시 갱신 여부
        store_id: 매장 코드 (None이면 전체)
    """
    global _cached_weekday_factors

    if store_id not in _cached_weekday_factors or refresh:
        _cached_weekday_factors[store_id] = calculate_weekday_factors_from_db(store_id=store_id)

    return _cached_weekday_factors[store_id].get(weekday, 1.0)


# 테스트 코드 → scripts/archive/test_prediction_patterns.py 로 이동됨 (2026-02-14)
