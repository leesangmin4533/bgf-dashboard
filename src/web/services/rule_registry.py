"""
규칙 현황판 - Rule Registry

예측/발주 시스템에 적용되는 모든 규칙을 등록하고,
각 규칙의 활성 상태, 현재값, 소스 파일 정보를 제공한다.

설정 파일(prediction_config.py, constants.py, eval_params.json 등)에서
실시간으로 값을 읽어 항상 최신 상태를 반영한다.
"""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# RuleInfo 데이터 클래스
# ---------------------------------------------------------------------------

@dataclass
class RuleInfo:
    """규칙 정보"""
    rule_id: str          # 고유 ID (예: pred_wma_31)
    name: str             # 표시 이름
    group: str            # 그룹 ID (prediction, category, cost, eval, order, ml, params)
    enabled: bool         # 활성 여부
    current_value: str    # 현재 설정값 (표시용 문자열)
    source_file: str      # 소스 파일 경로 (상대)
    description: str      # 설명

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 그룹 정의
# ---------------------------------------------------------------------------

RULE_GROUPS = {
    "prediction": "예측 규칙",
    "category": "카테고리별 안전재고",
    "cost": "비용 최적화 (CostOptimizer)",
    "eval": "사전 평가 (PreOrderEvaluator)",
    "order": "발주 실행 규칙",
    "ml": "ML 모델",
    "params": "보정 파라미터 (eval_params.json)",
}

# ---------------------------------------------------------------------------
# eval_params.json 로더
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"


def _load_eval_params() -> dict:
    """eval_params.json 로드 (매 호출마다 fresh read)"""
    path = _CONFIG_DIR / "eval_params.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 규칙 빌더
# ---------------------------------------------------------------------------

def _build_prediction_rules() -> List[RuleInfo]:
    """예측 규칙 (10개)"""
    from src.prediction.prediction_config import (
        PREDICTION_PARAMS,
        ORDER_ADJUSTMENT_RULES,
    )

    rules = []

    # 1. WMA 31일
    from src.settings.constants import PREDICTION_DATA_DAYS
    rules.append(RuleInfo(
        rule_id="pred_wma",
        name="WMA (가중이동평균)",
        group="prediction",
        enabled=True,
        current_value=f"{PREDICTION_DATA_DAYS}일 데이터",
        source_file="src/settings/constants.py",
        description="최근 31일 판매 데이터의 가중이동평균으로 기본 예측량 산출",
    ))

    # 2. 품절일 Imputation
    sf = PREDICTION_PARAMS.get("stockout_filter", {})
    rules.append(RuleInfo(
        rule_id="pred_stockout_filter",
        name="품절일 Imputation",
        group="prediction",
        enabled=sf.get("enabled", False),
        current_value=f"min_available_days={sf.get('min_available_days', '?')}",
        source_file="src/prediction/prediction_config.py",
        description="품절(stock_qty=0)인 날을 제외하고 비품절일 평균으로 대체",
    ))

    # 3. 이상치 처리
    rules.append(RuleInfo(
        rule_id="pred_outlier_iqr",
        name="이상치 처리 (IQR)",
        group="prediction",
        enabled=True,
        current_value="데이터 5일 이상 시 적용",
        source_file="src/prediction/improved_predictor.py",
        description="IQR 기반으로 판매량 이상치를 제거하여 예측 왜곡 방지",
    ))

    # 4. Feature 블렌딩
    rules.append(RuleInfo(
        rule_id="pred_feature_blending",
        name="Feature 블렌딩 (EWM+동요일)",
        group="prediction",
        enabled=True,
        current_value="고품질 40%, 중품질 25%, 저품질 10%",
        source_file="src/prediction/improved_predictor.py",
        description="지수가중이동평균 + 동요일 가중 예측을 WMA와 블렌딩",
    ))

    # 5. 간헐적 수요 보정
    id_cfg = PREDICTION_PARAMS.get("intermittent_demand", {})
    rules.append(RuleInfo(
        rule_id="pred_intermittent_demand",
        name="간헐적 수요 보정",
        group="prediction",
        enabled=id_cfg.get("enabled", False),
        current_value=f"threshold={id_cfg.get('threshold', '?')}, very={id_cfg.get('very_intermittent_threshold', '?')}",
        source_file="src/prediction/prediction_config.py",
        description="판매일 비율이 낮은 상품의 예측량을 보정 (과대추정 방지)",
    ))

    # 6. 휴일 계수
    rules.append(RuleInfo(
        rule_id="pred_holiday_coef",
        name="휴일 계수",
        group="prediction",
        enabled=True,
        current_value="1.3x",
        source_file="src/prediction/improved_predictor.py",
        description="공휴일/연휴에 예측량 30% 증가",
    ))

    # 7. 기온 계수
    rules.append(RuleInfo(
        rule_id="pred_weather_coef",
        name="기온 계수 (WEATHER_COEFFICIENTS)",
        group="prediction",
        enabled=True,
        current_value="음료 +15%(30도↑), 도시락 -10%(30도↑), 즉석 +10%(5도↓)",
        source_file="src/prediction/improved_predictor.py",
        description="카테고리별 기온-수요 관계에 따라 예측량 조정",
    ))

    # 8. 요일 계수
    rules.append(RuleInfo(
        rule_id="pred_weekday_coef",
        name="요일 계수",
        group="prediction",
        enabled=True,
        current_value="카테고리×요일별 계수 (0.8~2.5)",
        source_file="src/prediction/prediction_config.py",
        description="카테고리별 요일 판매 패턴 반영 (예: 맥주 금요일 2.54x)",
    ))

    # 9. 계절 계수
    rules.append(RuleInfo(
        rule_id="pred_seasonal_coef",
        name="계절 계수",
        group="prediction",
        enabled=True,
        current_value="7그룹 (음료/냉동/즉석/맥주/소주/라면/스낵)",
        source_file="src/prediction/prediction_config.py",
        description="월별 계절 수요 변동 반영 (예: 아이스크림 7월 1.50x)",
    ))

    # 10. 금요일 부스트 + 폐기방지 + 과재고방지
    fri = ORDER_ADJUSTMENT_RULES.get("friday_boost", {})
    disuse = ORDER_ADJUSTMENT_RULES.get("disuse_prevention", {})
    over = ORDER_ADJUSTMENT_RULES.get("overstock_prevention", {})
    rules.append(RuleInfo(
        rule_id="pred_order_adjustment",
        name="발주량 조정 규칙 (금요일/폐기/과재고)",
        group="prediction",
        enabled=fri.get("enabled", False) or disuse.get("enabled", False) or over.get("enabled", False),
        current_value=f"금요일 {fri.get('boost_rate', '?')}x, 폐기방지 {disuse.get('reduction_rate', '?')}x, 과재고 {over.get('stock_days_threshold', '?')}일",
        source_file="src/prediction/prediction_config.py",
        description="금요일 주류 추가발주, 유통기한 임박 감량, 재고 과다 시 스킵",
    ))

    return rules


def _build_category_rules() -> List[RuleInfo]:
    """카테고리별 안전재고 규칙 (5개)"""
    from src.prediction.prediction_config import (
        TOBACCO_DYNAMIC_SAFETY_CONFIG,
        RAMEN_DYNAMIC_SAFETY_CONFIG,
        BEER_SAFETY_CONFIG,
        FOOD_EXPIRY_SAFETY_CONFIG,
    )

    rules = []

    # 11. 담배
    t_cfg = TOBACCO_DYNAMIC_SAFETY_CONFIG
    rules.append(RuleInfo(
        rule_id="cat_tobacco",
        name="담배 동적 안전재고 (보루+소진)",
        group="category",
        enabled=t_cfg.get("enabled", False),
        current_value=f"max_stock={t_cfg.get('max_stock', 30)}, 보루버퍼 high={t_cfg['carton_buffer']['high']}",
        source_file="src/prediction/prediction_config.py",
        description="보루 판매 빈도 + 전량 소진 빈도로 안전재고 동적 계산",
    ))

    # 12. 라면
    r_cfg = RAMEN_DYNAMIC_SAFETY_CONFIG
    rules.append(RuleInfo(
        rule_id="cat_ramen",
        name="라면 회전율 기반 안전재고",
        group="category",
        enabled=r_cfg.get("enabled", False),
        current_value=f"high={r_cfg['turnover_safety_days']['high']['safety_days']}일, mid={r_cfg['turnover_safety_days']['medium']['safety_days']}일, low={r_cfg['turnover_safety_days']['low']['safety_days']}일",
        source_file="src/prediction/prediction_config.py",
        description="일평균 판매량 기준 회전율 3단계(high/mid/low)별 안전재고 일수",
    ))

    # 13. 맥주/소주
    b_cfg = BEER_SAFETY_CONFIG
    rules.append(RuleInfo(
        rule_id="cat_beer_soju",
        name="맥주 요일 기반 안전재고",
        group="category",
        enabled=b_cfg.get("enabled", False),
        current_value=f"평일 {b_cfg.get('default_days', 2)}일, 주말 {b_cfg.get('weekend_days', 3)}일",
        source_file="src/prediction/prediction_config.py",
        description="금/토 발주 시 3일치, 그 외 2일치 안전재고 (냉장고 공간 고려)",
    ))

    # 14. 푸드류
    f_cfg = FOOD_EXPIRY_SAFETY_CONFIG
    eg = f_cfg.get("expiry_groups", {})
    rules.append(RuleInfo(
        rule_id="cat_food",
        name="푸드류 유통기한 기반 안전재고",
        group="category",
        enabled=f_cfg.get("enabled", False),
        current_value=f"ultra_short={eg.get('ultra_short', {}).get('safety_days', '?')}일, short={eg.get('short', {}).get('safety_days', '?')}일",
        source_file="src/prediction/categories/food.py",
        description="유통기한 그룹별(1일/2~3일/4~7일/8~30일) 안전재고 일수 차등 적용",
    ))

    # 15. 소멸성 (떡/과일/요구르트) - 항상 활성
    rules.append(RuleInfo(
        rule_id="cat_perishable",
        name="소멸성 상품 요일 가중 안전재고",
        group="category",
        enabled=True,
        current_value="요일별 가중 안전재고 (CategoryStrategy)",
        source_file="src/prediction/categories/perishable.py",
        description="떡, 과일, 요구르트 등 소멸성 상품의 요일 가중 안전재고",
    ))

    return rules


def _build_cost_rules() -> List[RuleInfo]:
    """비용 최적화 규칙 (3개)"""
    params = _load_eval_params()
    cost = params.get("cost_optimization", {})

    rules = []

    # 16. CostOptimizer 활성화
    rules.append(RuleInfo(
        rule_id="cost_enabled",
        name="CostOptimizer 활성화",
        group="cost",
        enabled=cost.get("enabled", False),
        current_value=f"enabled={cost.get('enabled', False)}",
        source_file="config/eval_params.json",
        description="마진 기반 안전재고 승수, 폐기 계수, SKIP 임계값 조정 활성화",
    ))

    # 17. 2D 매트릭스
    has_matrix = "margin_multiplier_matrix" in cost
    rules.append(RuleInfo(
        rule_id="cost_2d_matrix",
        name="마진x회전율 2D 매트릭스",
        group="cost",
        enabled=cost.get("enabled", False) and has_matrix,
        current_value=f"회전율 high>={cost.get('turnover_high_threshold', 5)}, mid>={cost.get('turnover_mid_threshold', 2)}",
        source_file="config/eval_params.json",
        description="마진 등급(high/mid/base/low) x 회전율(high/mid/low) 2D 매트릭스로 승수 결정",
    ))

    # 18. 판매비중 보너스
    rules.append(RuleInfo(
        rule_id="cost_share_bonus",
        name="판매비중 보너스",
        group="cost",
        enabled=cost.get("enabled", False),
        current_value=f"threshold>={cost.get('category_share_bonus_threshold', 0.1)*100:.0f}%, bonus=+{cost.get('category_share_bonus_value', 0.05)}",
        source_file="config/eval_params.json",
        description="중분류별 30일 매출 비중이 임계값 이상이면 composite_score에 보너스 추가",
    ))

    return rules


def _build_eval_rules() -> List[RuleInfo]:
    """사전 평가 규칙 (7개)"""
    params = _load_eval_params()

    rules = []

    # 19. FORCE_ORDER
    from src.settings.constants import FORCE_MIN_DAILY_AVG
    rules.append(RuleInfo(
        rule_id="eval_force_order",
        name="FORCE_ORDER (품절 시 강제발주)",
        group="eval",
        enabled=True,
        current_value=f"현재고+미입고=0, min_daily_avg>={FORCE_MIN_DAILY_AVG}",
        source_file="src/prediction/pre_order_evaluator.py",
        description="현재 완전 품절 상태이면 강제 발주 (일평균 미달 시 NORMAL 다운그레이드)",
    ))

    # 20. URGENT
    eu = params.get("exposure_urgent", {})
    eu_val = eu.get("value", eu) if isinstance(eu, dict) else eu
    rules.append(RuleInfo(
        rule_id="eval_urgent",
        name="URGENT (긴급 발주)",
        group="eval",
        enabled=True,
        current_value=f"노출일 < {eu_val}일 + 중인기 이상",
        source_file="src/prediction/pre_order_evaluator.py",
        description="재고 노출 기간이 임계값 미만이고 인기도가 중 이상이면 긴급 발주",
    ))

    # 21. NORMAL
    en = params.get("exposure_normal", {})
    en_val = en.get("value", en) if isinstance(en, dict) else en
    rules.append(RuleInfo(
        rule_id="eval_normal",
        name="NORMAL (일반 발주)",
        group="eval",
        enabled=True,
        current_value=f"노출일 < {en_val}일",
        source_file="src/prediction/pre_order_evaluator.py",
        description="재고 노출 기간이 일반 임계값 미만이면 일반 발주",
    ))

    # 22. SKIP
    es = params.get("exposure_sufficient", {})
    es_val = es.get("value", es) if isinstance(es, dict) else es
    rules.append(RuleInfo(
        rule_id="eval_skip",
        name="SKIP (발주 생략)",
        group="eval",
        enabled=True,
        current_value=f"노출일 > {es_val}일 + 저인기",
        source_file="src/prediction/pre_order_evaluator.py",
        description="재고가 충분하고 저인기 상품이면 발주 생략",
    ))

    # 23. PASS
    rules.append(RuleInfo(
        rule_id="eval_pass",
        name="PASS (안전재고 위임)",
        group="eval",
        enabled=True,
        current_value="FORCE/URGENT/NORMAL/SKIP 외 전부",
        source_file="src/prediction/pre_order_evaluator.py",
        description="위 판정에 해당하지 않으면 안전재고 기반 예측기에 위임",
    ))

    # 24. 인기도 가중치
    w_da = params.get("weight_daily_avg", {})
    w_sr = params.get("weight_sell_day_ratio", {})
    w_tr = params.get("weight_trend", {})
    w_da_v = w_da.get("value", w_da) if isinstance(w_da, dict) else w_da
    w_sr_v = w_sr.get("value", w_sr) if isinstance(w_sr, dict) else w_sr
    w_tr_v = w_tr.get("value", w_tr) if isinstance(w_tr, dict) else w_tr
    rules.append(RuleInfo(
        rule_id="eval_popularity_weights",
        name="인기도 가중치",
        group="eval",
        enabled=True,
        current_value=f"일평균={w_da_v}, 판매일비율={w_sr_v}, 트렌드={w_tr_v}",
        source_file="config/eval_params.json",
        description="인기도 점수 = 일평균*W1 + 판매일비율*W2 + 트렌드*W3",
    ))

    # 25. 노출 임계값
    rules.append(RuleInfo(
        rule_id="eval_exposure_thresholds",
        name="노출 임계값 (urgent/normal/sufficient)",
        group="eval",
        enabled=True,
        current_value=f"urgent={eu_val}, normal={en_val}, sufficient={es_val}",
        source_file="config/eval_params.json",
        description="FORCE/URGENT/NORMAL/SKIP 판정 기준이 되는 재고 노출일 임계값",
    ))

    return rules


def _build_order_rules() -> List[RuleInfo]:
    """발주 실행 규칙 (10개)"""
    from src.settings.constants import (
        ENABLE_PASS_SUPPRESSION,
        PASS_MAX_ORDER_QTY,
        DEFAULT_MIN_ORDER_QTY,
        MAX_ORDER_QTY_BY_CATEGORY,
        TOBACCO_MAX_STOCK,
    )
    from src.prediction.categories.food_daily_cap import FOOD_DAILY_CAP_CONFIG

    rules = []

    # 26. 미취급 필터
    rules.append(RuleInfo(
        rule_id="order_unavailable_filter",
        name="미취급 상품 필터",
        group="order",
        enabled=True,
        current_value="is_available=0 → 제외",
        source_file="src/order/auto_order.py",
        description="실시간 재고에서 미취급(is_available=0) 상품 자동 제외",
    ))

    # 27. CUT 필터
    rules.append(RuleInfo(
        rule_id="order_cut_filter",
        name="발주중지(CUT) 필터",
        group="order",
        enabled=True,
        current_value="is_cut=1 → 제외",
        source_file="src/order/auto_order.py",
        description="발주 중지 상태(is_cut=1) 상품 자동 제외",
    ))

    # 28. 자동발주 제외
    rules.append(RuleInfo(
        rule_id="order_auto_exclude",
        name="자동발주(본부관리) 제외",
        group="order",
        enabled=True,
        current_value="EXCLUDE_AUTO_ORDER=True (기본값)",
        source_file="src/order/auto_order.py",
        description="BGF 본부에서 자동발주 관리하는 상품 제외 (중복 발주 방지)",
    ))

    # 29. 스마트발주 제외
    rules.append(RuleInfo(
        rule_id="order_smart_exclude",
        name="스마트발주(본부관리) 제외",
        group="order",
        enabled=True,
        current_value="EXCLUDE_SMART_ORDER=True (기본값)",
        source_file="src/order/auto_order.py",
        description="BGF 스마트발주 관리 상품 제외 (중복 발주 방지)",
    ))

    # 30. PASS 발주량 억제
    rules.append(RuleInfo(
        rule_id="order_pass_suppression",
        name="PASS 발주량 억제",
        group="order",
        enabled=ENABLE_PASS_SUPPRESSION,
        current_value=f"max={PASS_MAX_ORDER_QTY}개",
        source_file="src/settings/constants.py",
        description="PASS 판정 상품의 발주량을 최대 N개로 제한 (과잉 방지)",
    ))

    # 31. 최소 발주량
    rules.append(RuleInfo(
        rule_id="order_min_order_qty",
        name="최소 발주량 필터",
        group="order",
        enabled=True,
        current_value=f"min_order_qty={DEFAULT_MIN_ORDER_QTY}",
        source_file="src/settings/constants.py",
        description="발주량이 최소 수량 미만이면 제외 (운영 비효율 방지)",
    ))

    # 32. 발주 단위 올림
    rules.append(RuleInfo(
        rule_id="order_round_to_unit",
        name="발주 단위 올림 (ceil)",
        group="order",
        enabled=True,
        current_value="order_unit_qty 단위로 올림",
        source_file="src/domain/order/order_adjuster.py",
        description="상품별 발주 단위(입수)에 맞춰 올림 처리 (예: 12개 단위면 3개→12개)",
    ))

    # 33. max_stock 초과 시 내림
    rules.append(RuleInfo(
        rule_id="order_max_stock_floor",
        name="max_stock 초과 시 내림 (floor)",
        group="order",
        enabled=True,
        current_value=f"담배 max={TOBACCO_MAX_STOCK}개",
        source_file="src/domain/order/order_adjuster.py",
        description="발주 단위 올림 후 최대 재고를 초과하면 내림(floor)으로 조정",
    ))

    # 34. 카테고리별 최대 발주량
    cat_max_str = ", ".join(f"{k}:{v}" for k, v in MAX_ORDER_QTY_BY_CATEGORY.items())
    rules.append(RuleInfo(
        rule_id="order_category_max_qty",
        name="카테고리별 최대 발주량",
        group="order",
        enabled=bool(MAX_ORDER_QTY_BY_CATEGORY),
        current_value=cat_max_str or "미설정",
        source_file="src/settings/constants.py",
        description="특정 카테고리의 과다 예측 방지를 위한 상한선",
    ))

    # 35. 푸드 총량 상한
    fdc = FOOD_DAILY_CAP_CONFIG
    rules.append(RuleInfo(
        rule_id="order_food_daily_cap",
        name="푸드류 요일별 총량 상한",
        group="order",
        enabled=fdc.get("enabled", False),
        current_value=f"waste_buffer={fdc.get('waste_buffer', 3)}, explore={fdc.get('explore_ratio', 0.25)*100:.0f}%, fail_filter={'ON' if fdc.get('explore_fail_enabled') else 'OFF'}",
        source_file="src/prediction/categories/food_daily_cap.py",
        description="푸드류 총 발주량을 요일 평균+버퍼로 제한, 탐색/활용 25%/75% 배분",
    ))

    return rules


def _build_ml_rules() -> List[RuleInfo]:
    """ML 모델 규칙 (3개)"""
    rules = []

    # 36. ML 예측
    ml_available = False
    try:
        model_dir = Path(__file__).parent.parent.parent.parent / "models"
        if model_dir.exists():
            ml_available = any(model_dir.glob("*.pkl"))
    except Exception:
        pass

    rules.append(RuleInfo(
        rule_id="ml_prediction",
        name="ML 예측 모듈",
        group="ml",
        enabled=ml_available,
        current_value="모델 파일 존재" if ml_available else "모델 미학습 (규칙 기반만 사용)",
        source_file="src/prediction/ml/model.py",
        description="학습된 ML 모델이 있으면 규칙 기반 예측과 병행 사용",
    ))

    # 37. 앙상블
    rules.append(RuleInfo(
        rule_id="ml_ensemble",
        name="RF+GB 앙상블",
        group="ml",
        enabled=ml_available,
        current_value="Random Forest + Gradient Boosting",
        source_file="src/prediction/ml/trainer.py",
        description="두 모델의 예측을 결합하여 안정적인 예측 수행",
    ))

    # 38. 비대칭 손실함수
    try:
        from src.prediction.ml.trainer import GROUP_QUANTILE_ALPHA
        alpha_str = ", ".join(f"{k}={v}" for k, v in GROUP_QUANTILE_ALPHA.items())
    except ImportError:
        alpha_str = "import 실패"
        GROUP_QUANTILE_ALPHA = {}

    rules.append(RuleInfo(
        rule_id="ml_asymmetric_loss",
        name="비대칭 손실함수 (Quantile Loss)",
        group="ml",
        enabled=bool(GROUP_QUANTILE_ALPHA),
        current_value=alpha_str,
        source_file="src/prediction/ml/trainer.py",
        description="카테고리별 alpha 값으로 품절/폐기 비용 비대칭 반영",
    ))

    return rules


def _build_params_rules() -> List[RuleInfo]:
    """보정 파라미터 규칙 (eval_params.json의 13개 파라미터)"""
    params = _load_eval_params()

    rules = []
    # cost_optimization은 cost 그룹에서 처리하므로 제외
    skip_keys = {"cost_optimization"}

    for key, info in params.items():
        if key in skip_keys:
            continue
        if not isinstance(info, dict):
            continue

        val = info.get("value", "?")
        default = info.get("default", "?")
        desc = info.get("description", key)
        min_v = info.get("min", "")
        max_v = info.get("max", "")

        changed = val != default
        range_str = f" (범위: {min_v}~{max_v})" if min_v != "" else ""

        rules.append(RuleInfo(
            rule_id=f"param_{key}",
            name=key,
            group="params",
            enabled=True,
            current_value=f"{val} (기본: {default}){range_str}" + (" [변경됨]" if changed else ""),
            source_file="config/eval_params.json",
            description=desc,
        ))

    return rules


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def get_all_rules() -> List[RuleInfo]:
    """모든 규칙 목록 반환"""
    rules = []
    rules.extend(_build_prediction_rules())
    rules.extend(_build_category_rules())
    rules.extend(_build_cost_rules())
    rules.extend(_build_eval_rules())
    rules.extend(_build_order_rules())
    rules.extend(_build_ml_rules())
    rules.extend(_build_params_rules())
    return rules


def get_rules_by_group() -> Dict[str, Dict]:
    """그룹별 규칙 목록 반환"""
    all_rules = get_all_rules()
    grouped = {}
    for group_id, group_name in RULE_GROUPS.items():
        group_rules = [r for r in all_rules if r.group == group_id]
        grouped[group_id] = {
            "name": group_name,
            "rules": [r.to_dict() for r in group_rules],
            "active_count": sum(1 for r in group_rules if r.enabled),
            "total_count": len(group_rules),
        }
    return grouped


def get_rule_summary() -> Dict:
    """규칙 요약 (총 수, 활성 수, 비활성 수, 그룹 수)"""
    all_rules = get_all_rules()
    active = sum(1 for r in all_rules if r.enabled)
    return {
        "total": len(all_rules),
        "active": active,
        "inactive": len(all_rules) - active,
        "groups": len(RULE_GROUPS),
    }


def trace_product_rules(item_cd: str, store_id: Optional[str] = None) -> Dict:
    """
    상품별 규칙 적용 추적

    Args:
        item_cd: 상품코드
        store_id: 매장 ID

    Returns:
        상품 정보 + 적용된 규칙 목록
    """
    from src.settings.constants import DEFAULT_STORE_ID
    from src.prediction.prediction_config import (
        FOOD_CATEGORIES,
        RAMEN_DYNAMIC_SAFETY_CONFIG,
        TOBACCO_DYNAMIC_SAFETY_CONFIG,
        BEER_SAFETY_CONFIG,
        FOOD_EXPIRY_SAFETY_CONFIG,
        WEEKDAY_COEFFICIENTS,
        CATEGORY_NAMES,
    )
    from src.prediction.categories.food_daily_cap import FOOD_DAILY_CAP_CONFIG
    import sqlite3

    store_id = store_id or DEFAULT_STORE_ID

    # 상품 정보 조회
    db_path = str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")
    item_nm = ""
    mid_cd = ""
    mid_nm = ""

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT item_nm, mid_cd FROM products WHERE item_cd = ? LIMIT 1
        """, (item_cd,))
        row = cursor.fetchone()
        if row:
            item_nm = row[0] or ""
            mid_cd = row[1] or ""
            mid_nm = CATEGORY_NAMES.get(mid_cd, mid_cd)
        conn.close()
    except Exception:
        pass

    # 각 규칙별 적용 여부 판정
    traced = []
    all_rules = get_all_rules()

    for rule in all_rules:
        applied = False
        reason = ""
        detail = ""

        if rule.group == "prediction":
            # 예측 규칙: 대부분 모든 상품에 적용
            if rule.rule_id == "pred_weather_coef":
                # 기온 계수: 특정 카테고리만
                weather_cats = ["010", "034", "039", "043", "045", "048", "021", "100",
                                "001", "002", "003", "004", "005",
                                "027", "028", "031", "033", "035"]
                applied = mid_cd in weather_cats
                reason = f"mid_cd={mid_cd} {'포함' if applied else '미포함'}"
            elif rule.rule_id in ("pred_stockout_filter", "pred_intermittent_demand"):
                applied = rule.enabled
                reason = "활성" if applied else "비활성"
            else:
                applied = True
                reason = "전체 상품 적용"

        elif rule.group == "category":
            if rule.rule_id == "cat_tobacco":
                applied = mid_cd == TOBACCO_DYNAMIC_SAFETY_CONFIG.get("target_category", "072")
                reason = f"mid_cd={mid_cd} {'== 072' if applied else '!= 072'}"
            elif rule.rule_id == "cat_ramen":
                applied = mid_cd in RAMEN_DYNAMIC_SAFETY_CONFIG.get("target_categories", [])
                reason = f"mid_cd={mid_cd} {'포함' if applied else '미포함'}"
            elif rule.rule_id == "cat_beer_soju":
                applied = mid_cd in ["049"]
                reason = f"mid_cd={mid_cd} {'== 049' if applied else '!= 049'}"
            elif rule.rule_id == "cat_food":
                applied = mid_cd in FOOD_CATEGORIES
                reason = f"mid_cd={mid_cd} {'포함' if applied else '미포함'}"
            elif rule.rule_id == "cat_perishable":
                applied = mid_cd in ["046", "014", "023"]  # 요구르트, 디저트, 육가공
                reason = f"mid_cd={mid_cd} {'포함' if applied else '미포함'}"

        elif rule.group == "cost":
            applied = rule.enabled
            reason = "CostOptimizer " + ("활성" if applied else "비활성")

        elif rule.group == "eval":
            # 사전 평가 규칙: 모든 상품에 적용 가능
            applied = True
            reason = "전체 상품 적용 (판정 결과는 상품 상태에 따라 다름)"

        elif rule.group == "order":
            if rule.rule_id == "order_food_daily_cap":
                applied = mid_cd in FOOD_DAILY_CAP_CONFIG.get("target_categories", [])
                reason = f"mid_cd={mid_cd} {'포함' if applied else '미포함'}"
            elif rule.rule_id == "order_category_max_qty":
                from src.settings.constants import MAX_ORDER_QTY_BY_CATEGORY
                applied = mid_cd in MAX_ORDER_QTY_BY_CATEGORY
                reason = f"mid_cd={mid_cd} {'설정됨' if applied else '미설정'}"
            else:
                applied = True
                reason = "전체 상품 적용"

        elif rule.group == "ml":
            applied = rule.enabled
            reason = "ML " + ("활성" if applied else "비활성")

        elif rule.group == "params":
            applied = True
            reason = "전체 상품 적용"

        traced.append({
            "rule_id": rule.rule_id,
            "name": rule.name,
            "group": rule.group,
            "group_name": RULE_GROUPS.get(rule.group, rule.group),
            "enabled": rule.enabled,
            "applied": applied,
            "reason": reason,
            "current_value": rule.current_value,
        })

    return {
        "item_cd": item_cd,
        "item_nm": item_nm,
        "mid_cd": mid_cd,
        "mid_nm": mid_nm,
        "rules": traced,
        "applied_count": sum(1 for t in traced if t["applied"]),
        "total_count": len(traced),
    }
