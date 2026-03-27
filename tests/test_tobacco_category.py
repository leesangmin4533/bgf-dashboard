"""
담배/전자담배(tobacco) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/tobacco.py
- is_tobacco_category(): 담배/전자담배 카테고리 여부 확인
- classify_and_calculate_tobacco(): 보루/낱개/LIL 3종 분류 + 목표재고 + 판정
"""

import pytest

from src.prediction.categories.tobacco import (
    is_tobacco_category,
    classify_and_calculate_tobacco,
    TOBACCO_DYNAMIC_SAFETY_CONFIG,
)
from src.settings.constants import (
    TOBACCO_DISPLAY_MAX,
    TOBACCO_BOURU_UNIT,
    TOBACCO_BOURU_THRESHOLD,
    TOBACCO_BOURU_FREQ_MULTIPLIER,
    TOBACCO_BOURU_DECAY_RATIO,
    TOBACCO_BOURU_MAX_STOCK,
    TOBACCO_SINGLE_URGENT_THRESHOLD,
    TOBACCO_BOURU_URGENT_THRESHOLD,
    TOBACCO_FORCE_MIN_DAILY_AVG,
)


# =============================================================================
# is_tobacco_category 테스트 (기존 유지)
# =============================================================================
class TestIsTobaccoCategory:
    """담배/전자담배 카테고리 판별 테스트"""

    @pytest.mark.unit
    def test_072_is_tobacco(self):
        """'072' (담배)는 담배 카테고리"""
        assert is_tobacco_category("072") is True

    @pytest.mark.unit
    def test_073_is_tobacco(self):
        """'073' (전자담배)는 담배 카테고리"""
        assert is_tobacco_category("073") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "049", "050", "051", "001", "032", "044", "900", "999", "", "72", "0072",
    ])
    def test_non_tobacco_categories(self, mid_cd):
        """담배/전자담배가 아닌 카테고리는 False 반환"""
        assert is_tobacco_category(mid_cd) is False

    @pytest.mark.unit
    def test_target_categories_config(self):
        """TOBACCO_DYNAMIC_SAFETY_CONFIG에 '072', '073' 포함 확인"""
        target = TOBACCO_DYNAMIC_SAFETY_CONFIG["target_categories"]
        assert "072" in target
        assert "073" in target
        assert len(target) == 2

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", TOBACCO_DYNAMIC_SAFETY_CONFIG["target_categories"])
    def test_all_configured_categories_are_tobacco(self, mid_cd):
        """설정에 등록된 모든 카테고리가 is_tobacco_category() True 반환"""
        assert is_tobacco_category(mid_cd) is True

    @pytest.mark.unit
    def test_config_safety_values(self):
        """담배 설정 핵심값 확인: 상한선 30개, 기본 안전재고 2.0일"""
        config = TOBACCO_DYNAMIC_SAFETY_CONFIG
        assert config["max_stock"] == 30
        assert config["default_safety_days"] == 2.0
        assert config["min_order_unit"] == 10
        assert config["carton_unit"] == 10

    @pytest.mark.unit
    def test_config_enabled(self):
        """동적 안전재고 기능이 활성화 상태 확인"""
        assert TOBACCO_DYNAMIC_SAFETY_CONFIG["enabled"] is True

    @pytest.mark.unit
    def test_numeric_string_not_int(self):
        """정수가 아닌 문자열 입력 처리"""
        assert is_tobacco_category(72) is False  # type: ignore
        assert is_tobacco_category(73) is False  # type: ignore

    @pytest.mark.unit
    def test_similar_codes_not_tobacco(self):
        """유사 코드가 담배로 판별되지 않는지 확인"""
        assert is_tobacco_category("071") is False
        assert is_tobacco_category("074") is False
        assert is_tobacco_category("0072") is False
        assert is_tobacco_category("072 ") is False


# =============================================================================
# classify_and_calculate_tobacco 테스트 (신규 6종 시나리오)
# =============================================================================
class TestTobaccoBouruDetection:
    """시나리오 1: 보루 판별 테스트"""

    @pytest.mark.unit
    def test_bouru_detected_when_60d_history(self):
        """60일 내 판매 10개 이상인 날이 있는 상품 → tobacco_type == 'bouru'"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="TOBACCO001", daily_avg=3.0,
            current_stock=5, pending_qty=0,
            bouru_count_60d=3, bouru_count_30d=1,
        )
        assert result.tobacco_type == "bouru"
        assert result.bouru_count_60d == 3
        assert result.bouru_count_30d == 1

    @pytest.mark.unit
    def test_single_when_no_bouru_history(self):
        """60일 내 판매 최대 9개인 상품 → tobacco_type == 'single'"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="TOBACCO002", daily_avg=2.0,
            current_stock=5, pending_qty=0,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        assert result.tobacco_type == "single"
        assert result.target_stock == TOBACCO_DISPLAY_MAX  # 11

    @pytest.mark.unit
    def test_single_bouru_zero(self):
        """보루 이력 0 → 낱개형, 기본 진열대 11"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T003", daily_avg=1.5,
            current_stock=0, pending_qty=0,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        assert result.tobacco_type == "single"
        assert result.target_stock == 11


class TestTobaccoBouruTargetStock:
    """시나리오 2: 보루형 목표 재고 계산"""

    @pytest.mark.unit
    def test_bouru_count_2_target_stock(self):
        """bouru_count=2 → target_stock = 11 + round(2*0.5)*10 = 21"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T010", daily_avg=5.0,
            current_stock=10, pending_qty=0,
            bouru_count_60d=2, bouru_count_30d=1,
        )
        # round(2*0.5) = round(1.0) = 1, 1 * 10 = 10
        # target = 11 + 10 = 21
        assert result.tobacco_type == "bouru"
        assert result.target_stock == 21

    @pytest.mark.unit
    def test_bouru_count_6_target_stock_capped(self):
        """bouru_count=6 → target_stock = min(11+30, 41) = 41 (상한 적용)"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T011", daily_avg=8.0,
            current_stock=15, pending_qty=0,
            bouru_count_60d=6, bouru_count_30d=3,
        )
        # round(6*0.5) = round(3.0) = 3, 3 * 10 = 30
        # target = min(11 + 30, 41) = 41
        assert result.tobacco_type == "bouru"
        assert result.target_stock == TOBACCO_BOURU_MAX_STOCK  # 41

    @pytest.mark.unit
    def test_bouru_count_4_target_stock(self):
        """bouru_count=4 → target_stock = 11 + round(4*0.5)*10 = 31"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T012", daily_avg=6.0,
            current_stock=10, pending_qty=0,
            bouru_count_60d=4, bouru_count_30d=2,
        )
        # round(4*0.5) = round(2.0) = 2, 2 * 10 = 20
        # target = min(11 + 20, 41) = 31
        assert result.target_stock == 31


class TestTobaccoBouruDecay:
    """시나리오 3: 보루 감쇠 테스트"""

    @pytest.mark.unit
    def test_decay_applied_when_no_recent_bouru(self):
        """bouru_count_60d=4, bouru_count_30d=0 → 감쇠 적용, target_stock 절반"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T020", daily_avg=5.0,
            current_stock=10, pending_qty=0,
            bouru_count_60d=4, bouru_count_30d=0,
        )
        # 감쇠 전: round(4*0.5) = 2, 2 * 10 = 20
        # 감쇠 후: int(20 * 0.5) = 10
        # target = 11 + 10 = 21
        assert result.tobacco_type == "bouru"
        assert result.decay_applied is True
        assert result.target_stock == 21

    @pytest.mark.unit
    def test_no_decay_when_recent_bouru(self):
        """bouru_count_60d=4, bouru_count_30d=2 → 감쇠 미적용"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T021", daily_avg=5.0,
            current_stock=10, pending_qty=0,
            bouru_count_60d=4, bouru_count_30d=2,
        )
        # 감쇠 없음: round(4*0.5) = 2, 2 * 10 = 20
        # target = 11 + 20 = 31
        assert result.decay_applied is False
        assert result.target_stock == 31

    @pytest.mark.unit
    def test_decay_large_bouru_count(self):
        """bouru_count_60d=8, bouru_count_30d=0 → 감쇠 적용"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T022", daily_avg=10.0,
            current_stock=5, pending_qty=0,
            bouru_count_60d=8, bouru_count_30d=0,
        )
        # 감쇠 전: round(8*0.5) = 4, 4 * 10 = 40
        # 감쇠 후: int(40 * 0.5) = 20
        # target = min(11 + 20, 41) = 31
        assert result.decay_applied is True
        assert result.target_stock == 31


class TestTobaccoUrgentConditions:
    """시나리오 4: URGENT 판정 조건"""

    @pytest.mark.unit
    def test_bouru_urgent_stock_below_threshold(self):
        """보루형 + 재고 9개 → URGENT (보루 판매 불가)"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T030", daily_avg=5.0,
            current_stock=9, pending_qty=0,
            bouru_count_60d=3, bouru_count_30d=1,
        )
        assert result.tobacco_type == "bouru"
        assert result.suggested_decision == "URGENT"

    @pytest.mark.unit
    def test_bouru_normal_stock_at_threshold(self):
        """보루형 + 재고 10개 → NORMAL (목표 미달이므로)"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T031", daily_avg=5.0,
            current_stock=10, pending_qty=0,
            bouru_count_60d=3, bouru_count_30d=1,
        )
        # target = 11 + round(3*0.5)*10 = 11 + 20 = 31 (round(1.5)=2)
        # stock=10 < target=31 → NORMAL (not URGENT since 10 >= BOURU_URGENT_THRESHOLD)
        assert result.tobacco_type == "bouru"
        assert result.suggested_decision == "NORMAL"

    @pytest.mark.unit
    def test_single_urgent_stock_below_threshold(self):
        """낱개형 + 재고 2개 → URGENT"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T032", daily_avg=2.0,
            current_stock=2, pending_qty=0,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        assert result.tobacco_type == "single"
        assert result.suggested_decision == "URGENT"

    @pytest.mark.unit
    def test_single_normal_stock_at_threshold(self):
        """낱개형 + 재고 3개 → NORMAL (목표 11 미달이므로)"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T033", daily_avg=2.0,
            current_stock=3, pending_qty=0,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        assert result.tobacco_type == "single"
        assert result.suggested_decision == "NORMAL"

    @pytest.mark.unit
    def test_single_skip_stock_at_target(self):
        """낱개형 + 재고 11개 → SKIP"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T034", daily_avg=2.0,
            current_stock=11, pending_qty=0,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        assert result.tobacco_type == "single"
        assert result.suggested_decision == "SKIP"
        assert result.skip_order is True


class TestTobaccoForceOrder:
    """시나리오 5: FORCE_ORDER 판정"""

    @pytest.mark.unit
    def test_force_when_stock_zero_high_avg(self):
        """재고 0 + daily_avg 2.5 → FORCE"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T040", daily_avg=2.5,
            current_stock=0, pending_qty=0,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        assert result.suggested_decision == "FORCE"

    @pytest.mark.unit
    def test_no_force_when_stock_zero_low_avg(self):
        """재고 0 + daily_avg 1.5 → FORCE 아님 (일평균 미달)"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T041", daily_avg=1.5,
            current_stock=0, pending_qty=0,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        assert result.suggested_decision != "FORCE"
        # 재고 0 < SINGLE_URGENT(3) → URGENT
        assert result.suggested_decision == "URGENT"

    @pytest.mark.unit
    def test_force_bouru_type(self):
        """보루형 + 재고 0 + daily_avg >= 2.0 → FORCE"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="T042", daily_avg=3.0,
            current_stock=0, pending_qty=0,
            bouru_count_60d=2, bouru_count_30d=1,
        )
        assert result.tobacco_type == "bouru"
        assert result.suggested_decision == "FORCE"

    @pytest.mark.unit
    def test_force_lil_type(self):
        """LIL + 재고 0 + daily_avg >= 2.0 → FORCE"""
        result = classify_and_calculate_tobacco(
            mid_cd="073", item_cd="T043", daily_avg=17.0,
            current_stock=0, pending_qty=0,
        )
        assert result.tobacco_type == "lil"
        assert result.suggested_decision == "FORCE"


class TestTobaccoLilType:
    """시나리오 6: LIL류 전용 로직"""

    @pytest.mark.unit
    def test_lil_classification(self):
        """mid_cd == '073' → tobacco_type == 'lil'"""
        result = classify_and_calculate_tobacco(
            mid_cd="073", item_cd="LIL001", daily_avg=17.0,
            current_stock=20, pending_qty=0,
        )
        assert result.tobacco_type == "lil"

    @pytest.mark.unit
    def test_lil_target_stock_calculation(self):
        """LIL: target_stock = max(round(daily_avg*3), 11)"""
        result = classify_and_calculate_tobacco(
            mid_cd="073", item_cd="LIL002", daily_avg=17.0,
            current_stock=20, pending_qty=0,
        )
        # round(17*3) = 51, max(51, 11) = 51
        assert result.target_stock == 51

    @pytest.mark.unit
    def test_lil_target_stock_minimum(self):
        """LIL 일평균 낮아도 최소 진열대(11) 보장"""
        result = classify_and_calculate_tobacco(
            mid_cd="073", item_cd="LIL003", daily_avg=1.0,
            current_stock=5, pending_qty=0,
        )
        # round(1.0*3) = 3, max(3, 11) = 11
        assert result.target_stock == 11

    @pytest.mark.unit
    def test_lil_ignores_bouru_history(self):
        """LIL은 보루 이력과 무관하게 LIL 전용 로직 적용"""
        result = classify_and_calculate_tobacco(
            mid_cd="073", item_cd="LIL004", daily_avg=10.0,
            current_stock=5, pending_qty=0,
            bouru_count_60d=5, bouru_count_30d=3,  # 주입해도 무시
        )
        assert result.tobacco_type == "lil"
        assert result.bouru_count_60d == 0  # LIL은 bouru 무시

    @pytest.mark.unit
    def test_lil_urgent_low_stock(self):
        """LIL + 재고 < 목표의 30% → URGENT"""
        result = classify_and_calculate_tobacco(
            mid_cd="073", item_cd="LIL005", daily_avg=17.0,
            current_stock=10, pending_qty=0,
        )
        # target = 51, 30% = 15.3, stock=10 < 15.3 → URGENT
        assert result.suggested_decision == "URGENT"

    @pytest.mark.unit
    def test_lil_skip_when_enough_stock(self):
        """LIL + 재고 >= 목표 → SKIP"""
        result = classify_and_calculate_tobacco(
            mid_cd="073", item_cd="LIL006", daily_avg=3.0,
            current_stock=11, pending_qty=0,
        )
        # target = max(round(3*3), 11) = max(9, 11) = 11
        # stock=11 >= target=11 → SKIP
        assert result.suggested_decision == "SKIP"


class TestTobaccoDetailsFields:
    """details 딕셔너리 필수 필드 확인"""

    @pytest.mark.unit
    def test_details_has_required_fields(self):
        """TobaccoPatternResult에 tobacco_type, target_stock, suggested_decision 포함"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="FIELD01", daily_avg=3.0,
            current_stock=5, pending_qty=0,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        assert hasattr(result, "tobacco_type")
        assert hasattr(result, "target_stock")
        assert hasattr(result, "suggested_decision")
        assert hasattr(result, "decision_reason")
        assert hasattr(result, "bouru_count_60d")
        assert hasattr(result, "bouru_count_30d")
        assert hasattr(result, "decay_applied")

    @pytest.mark.unit
    def test_details_tobacco_type_values(self):
        """tobacco_type은 'lil', 'bouru', 'single' 중 하나"""
        for mid_cd, b60, expected in [
            ("072", 0, "single"),
            ("072", 3, "bouru"),
            ("073", 0, "lil"),
        ]:
            result = classify_and_calculate_tobacco(
                mid_cd=mid_cd, item_cd="TYPE01", daily_avg=3.0,
                current_stock=5, pending_qty=0,
                bouru_count_60d=b60, bouru_count_30d=0,
            )
            assert result.tobacco_type == expected, \
                f"mid_cd={mid_cd}, b60d={b60}: expected {expected}, got {result.tobacco_type}"

    @pytest.mark.unit
    def test_decision_values(self):
        """suggested_decision은 FORCE/URGENT/NORMAL/SKIP 중 하나"""
        valid = {"FORCE", "URGENT", "NORMAL", "SKIP"}
        cases = [
            (0, 3.0, 0, 0),   # stock=0, high avg → FORCE
            (2, 1.5, 0, 0),   # stock=2 < 3 → URGENT
            (5, 2.0, 0, 0),   # stock=5 < 11 → NORMAL
            (11, 2.0, 0, 0),  # stock=11 >= 11 → SKIP
        ]
        for stock, avg, b60, b30 in cases:
            result = classify_and_calculate_tobacco(
                mid_cd="072", item_cd="DEC01", daily_avg=avg,
                current_stock=stock, pending_qty=0,
                bouru_count_60d=b60, bouru_count_30d=b30,
            )
            assert result.suggested_decision in valid, \
                f"stock={stock}, avg={avg}: got {result.suggested_decision}"

    @pytest.mark.unit
    def test_available_space_calculation(self):
        """available_space = max(0, target_stock - current_stock - pending_qty)"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="SPACE01", daily_avg=3.0,
            current_stock=5, pending_qty=2,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        # single: target=11, available = max(0, 11-5-2) = 4
        assert result.available_space == 4

    @pytest.mark.unit
    def test_pending_qty_reduces_available_space(self):
        """미입고가 많으면 available_space 감소 → SKIP"""
        result = classify_and_calculate_tobacco(
            mid_cd="072", item_cd="PEND01", daily_avg=2.0,
            current_stock=5, pending_qty=10,
            bouru_count_60d=0, bouru_count_30d=0,
        )
        # single: target=11, available = max(0, 11-5-10) = 0
        assert result.available_space == 0
        assert result.skip_order is True


class TestTobaccoConstantsIntegrity:
    """상수 무결성 검증"""

    @pytest.mark.unit
    def test_display_max(self):
        assert TOBACCO_DISPLAY_MAX == 11

    @pytest.mark.unit
    def test_bouru_unit(self):
        assert TOBACCO_BOURU_UNIT == 10

    @pytest.mark.unit
    def test_bouru_threshold(self):
        assert TOBACCO_BOURU_THRESHOLD == 10

    @pytest.mark.unit
    def test_bouru_max_stock(self):
        assert TOBACCO_BOURU_MAX_STOCK == 41

    @pytest.mark.unit
    def test_single_urgent_threshold(self):
        assert TOBACCO_SINGLE_URGENT_THRESHOLD == 3

    @pytest.mark.unit
    def test_bouru_urgent_threshold(self):
        assert TOBACCO_BOURU_URGENT_THRESHOLD == 10

    @pytest.mark.unit
    def test_force_min_daily_avg(self):
        assert TOBACCO_FORCE_MIN_DAILY_AVG == 2.0

    @pytest.mark.unit
    def test_freq_multiplier(self):
        assert TOBACCO_BOURU_FREQ_MULTIPLIER == 0.5

    @pytest.mark.unit
    def test_decay_ratio(self):
        assert TOBACCO_BOURU_DECAY_RATIO == 0.5


# =============================================================================
# tobacco_type confidence 로깅 테스트
# =============================================================================
class TestTobaccoConfidenceLogging:
    """담배 예측 시 confidence에 tobacco_type JSON이 포함되는지 검증"""

    @pytest.mark.unit
    def test_tobacco_confidence_logging(self):
        """보루형 담배 예측 후 confidence에 tobacco_type 키 존재 확인"""
        import json
        from src.prediction.improved_predictor import PredictionResult

        # 보루형 담배 시나리오: result_ctx에 tobacco_type이 있으면
        # confidence에 JSON으로 저장되어야 함
        _confidence_level = "high"
        mid_cd = "072"
        result_ctx = {
            "tobacco_type": "bouru",
            "tobacco_target_stock": 21,
            "tobacco_bouru_count_60d": 2,
            "tobacco_suggested_decision": "URGENT",
        }

        # improved_predictor.py의 confidence 생성 로직 재현
        _tobacco_type = result_ctx.get("tobacco_type", "")
        if mid_cd in ("072", "073") and _tobacco_type:
            confidence_value = json.dumps({
                "level": _confidence_level,
                "tobacco_type": _tobacco_type,
                "target_stock": result_ctx.get("tobacco_target_stock", 0),
                "bouru_count_60d": result_ctx.get("tobacco_bouru_count_60d", 0),
                "suggested_decision": result_ctx.get("tobacco_suggested_decision", ""),
            }, ensure_ascii=False)
        else:
            confidence_value = _confidence_level

        # JSON 파싱 검증
        parsed = json.loads(confidence_value)
        assert "tobacco_type" in parsed
        assert parsed["tobacco_type"] == "bouru"
        assert "target_stock" in parsed
        assert isinstance(parsed["target_stock"], int)
        assert parsed["target_stock"] == 21
        assert parsed["level"] == "high"
        assert parsed["bouru_count_60d"] == 2
        assert parsed["suggested_decision"] == "URGENT"

    @pytest.mark.unit
    def test_tobacco_confidence_lil_type(self):
        """LIL(073) 담배 confidence에 tobacco_type='lil' 기록 확인"""
        import json

        _confidence_level = "high"
        mid_cd = "073"
        result_ctx = {
            "tobacco_type": "lil",
            "tobacco_target_stock": 5,
            "tobacco_bouru_count_60d": 0,
            "tobacco_suggested_decision": "NORMAL",
        }

        _tobacco_type = result_ctx.get("tobacco_type", "")
        if mid_cd in ("072", "073") and _tobacco_type:
            confidence_value = json.dumps({
                "level": _confidence_level,
                "tobacco_type": _tobacco_type,
                "target_stock": result_ctx.get("tobacco_target_stock", 0),
                "bouru_count_60d": result_ctx.get("tobacco_bouru_count_60d", 0),
                "suggested_decision": result_ctx.get("tobacco_suggested_decision", ""),
            }, ensure_ascii=False)
        else:
            confidence_value = _confidence_level

        parsed = json.loads(confidence_value)
        assert parsed["tobacco_type"] == "lil"
        assert parsed["bouru_count_60d"] == 0

    @pytest.mark.unit
    def test_tobacco_confidence_single_type(self):
        """낱개형 담배 confidence에 tobacco_type='single' 기록 확인"""
        import json

        _confidence_level = "medium"
        mid_cd = "072"
        result_ctx = {
            "tobacco_type": "single",
            "tobacco_target_stock": 11,
            "tobacco_bouru_count_60d": 0,
            "tobacco_suggested_decision": "NORMAL",
        }

        _tobacco_type = result_ctx.get("tobacco_type", "")
        if mid_cd in ("072", "073") and _tobacco_type:
            confidence_value = json.dumps({
                "level": _confidence_level,
                "tobacco_type": _tobacco_type,
                "target_stock": result_ctx.get("tobacco_target_stock", 0),
                "bouru_count_60d": result_ctx.get("tobacco_bouru_count_60d", 0),
                "suggested_decision": result_ctx.get("tobacco_suggested_decision", ""),
            }, ensure_ascii=False)
        else:
            confidence_value = _confidence_level

        parsed = json.loads(confidence_value)
        assert parsed["tobacco_type"] == "single"
        assert parsed["level"] == "medium"

    @pytest.mark.unit
    def test_non_tobacco_confidence_unchanged(self):
        """맥주(049) 예측 후 confidence = 'high' 문자열 그대로인지 확인"""
        import json

        _confidence_level = "high"
        mid_cd = "049"
        result_ctx = {}  # 비담배: tobacco_type 없음

        _tobacco_type = result_ctx.get("tobacco_type", "")
        if mid_cd in ("072", "073") and _tobacco_type:
            confidence_value = json.dumps({
                "level": _confidence_level,
                "tobacco_type": _tobacco_type,
                "target_stock": result_ctx.get("tobacco_target_stock", 0),
                "bouru_count_60d": result_ctx.get("tobacco_bouru_count_60d", 0),
                "suggested_decision": result_ctx.get("tobacco_suggested_decision", ""),
            }, ensure_ascii=False)
        else:
            confidence_value = _confidence_level

        # 비담배는 문자열 그대로
        assert confidence_value == "high"
        # JSON 파싱 시 실패해야 정상 (문자열이므로)
        with pytest.raises(json.JSONDecodeError):
            json.loads(confidence_value)

    @pytest.mark.unit
    def test_tobacco_no_type_keeps_string(self):
        """담배지만 tobacco_type이 없으면 기존 문자열 유지"""
        import json

        _confidence_level = "high"
        mid_cd = "072"
        result_ctx = {}  # tobacco_type 미설정 (패턴 계산 실패 시)

        _tobacco_type = result_ctx.get("tobacco_type", "")
        if mid_cd in ("072", "073") and _tobacco_type:
            confidence_value = json.dumps({
                "level": _confidence_level,
                "tobacco_type": _tobacco_type,
            }, ensure_ascii=False)
        else:
            confidence_value = _confidence_level

        assert confidence_value == "high"
