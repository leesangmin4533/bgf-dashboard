# ML 하네스 상세 설계서

**Feature**: ml-harness
**Created**: 2026-03-28
**Phase**: Design
**Plan Reference**: `docs/01-plan/features/ml-harness.plan.md`

---

## 1. 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│                    Presentation 계층                         │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │ api_ml_perf.py   │  │ index.html (ML 성능 탭)          │ │
│  │ (Flask Blueprint) │  │ Chart.js 차트 4개                │ │
│  └────────┬─────────┘  └──────────────────────────────────┘ │
├───────────┼─────────────────────────────────────────────────┤
│           │          Application ���층                        │
│  ┌────────▼─────────┐  ┌────────────────────────────────┐  │
│  │ daily_job.py     │  �� KakaoNotifier                  │  │
│  │ Phase 1.59       │  │ ML 섹션 추가                    │  │
│  └────────┬─────────┘  └────────────────────────────────┘  │
├───────────┼─────────────────────────────────────────────────┤
│           │          Domain / Analysis 계층                  │
│  ┌────────▼──────────────────────────────────────────────┐  │
│  │ MLWeightCalibrator (src/prediction/)                  │  │
│  │  ├─ analyze()  → AccuracyTracker 호출 → 성능 분석     │  │
│  │  ├─ calibrate() → 보수적 가중치 조정                   │  │
│  │  └─ rollback()  → 3일 연속 악화 시 복원               │  │
│  └────────┬──────────────────────────────────────────────┘  │
├───────────┼─────────────────────────────────────────────────┤
│           │          Infrastructure 계층                     │
│  ┌────────▼──────────┐  ┌────────────────────────────────┐  │
│  │ AccuracyTracker   │  │ Store DB                       │  │
│  │ (기존, 변경 없음)  │  │ ml_weight_history              │  │
│  │ get_rule_vs_ml_   │  │ ml_weight_overrides            │  │
│  │ accuracy()        │  │                                │  │
│  └───────────────────┘  └────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 신규 파일 목록

| # | 파일 | 계층 | 역할 | 예상 LOC |
|---|------|------|------|---------|
| 1 | `src/prediction/ml_weight_calibrator.py` | Domain | 가중치 분석+조정+롤백 | ~250 |
| 2 | `src/web/routes/api_ml_performance.py` | Presentation | REST API 5개 | ~180 |
| 3 | `tests/test_ml_weight_calibrator.py` | Test | 단위 테스트 | ~400 |

## 3. 수정 파일 목록

| # | 파일 | 수정 내용 | 예상 변경량 |
|---|------|----------|-----------|
| 1 | `src/infrastructure/database/schema.py` | 2개 테이블 DDL + _STORE_COLUMN_PATCHES | +20줄 |
| 2 | `src/prediction/improved_predictor.py` | `_get_ml_weight()`에 DB 오버라이드 조회 추가 | +15줄 |
| 3 | `src/scheduler/daily_job.py` | Phase 1.59 블록 추��� | +25줄 |
| 4 | `src/notification/kakao_notifier.py` | `send_report()`에 ML 섹션 추가 | +15줄 |
| 5 | `src/web/routes/__init__.py` | Blueprint 등록 1줄 | +2줄 |
| 6 | `src/web/templates/index.html` | ML 성능 탭 + 패널 HTML/JS | +200줄 |
| 7 | `src/settings/constants.py` | ML 캘리브레이션 상수 | +20줄 |

---

## 4. MLWeightCalibrator 상세 설계

### 4.1 클래스 구조

```python
# src/prediction/ml_weight_calibrator.py

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
import sqlite3

@dataclass
class WeightCalibrationResult:
    """단일 그룹의 가중치 조정 결과"""
    category_group: str          # food_group, alcohol_group, ...
    previous_weight: float
    new_weight: float
    rule_mae: float
    blended_mae: float
    ml_contribution: float       # (rule_mae - blended_mae) / rule_mae
    action: str                  # 'increase' | 'decrease' | 'rollback' | 'hold'
    reason: str
    sample_count: int


class MLWeightCalibrator:
    """ML 가중치 보수적 자동 조정

    FoodWasteRateCalibrator와 동일한 보수적 패턴:
    - 불감대 (-0.02 ~ +0.03)
    - 소폭 조정 (±0.02 step)
    - 안전 범위 (그룹별 하한/상한)
    - 3일 연속 악화 시 롤백
    """

    def __init__(self, store_id: str):
        self.store_id = store_id

    def calibrate(self) -> Dict[str, Any]:
        """전체 그룹 가중치 조정 실행

        Returns:
            {
                "calibrated": bool,
                "results": [WeightCalibrationResult, ...],
                "rollbacks": [str, ...],  # 롤백된 그룹명
                "alerts": [str, ...],     # 카카오 알림 메시지
            }
        """

    def get_current_weights(self) -> Dict[str, float]:
        """현재 적용 중인 가중치 (DB 오버라이드 > 하드코딩)"""

    def get_weight_history(self, days: int = 30) -> List[Dict]:
        """가중치 조정 이력 조회"""

    def _analyze_group(self, group: str, stats: Dict) -> WeightCalibrationResult:
        """단일 그룹 분석 → 조정 결정"""

    def _check_rollback(self, group: str) -> Optional[float]:
        """3일 연속 악화 시 롤백 대상 가중치 반환"""

    def _save_history(self, result: WeightCalibrationResult):
        """ml_weight_history INSERT"""

    def _update_override(self, group: str, new_weight: float):
        """ml_weight_overrides UPSERT"""
```

### 4.2 calibrate() 상세 흐름

```python
def calibrate(self) -> Dict[str, Any]:
    if not ML_WEIGHT_AUTO_CALIBRATION_ENABLED:
        return {"calibrated": False, "reason": "disabled", "results": []}

    # 1. 데이터 충분성 확인 (14일)
    tracker = AccuracyTracker(store_id=self.store_id)
    stats = tracker.get_rule_vs_ml_accuracy(days=ML_WEIGHT_CAL_LOOKBACK_DAYS)

    if stats.get("total_records", 0) < ML_WEIGHT_CAL_MIN_SAMPLES:
        return {"calibrated": False, "reason": "insufficient_data", "results": []}

    # 2. 그룹별 분석 + 조정
    results = []
    rollbacks = []
    alerts = []
    by_group = stats.get("by_group", {})

    for group in ML_WEIGHT_GROUPS:
        group_stats = by_group.get(group, {})
        if group_stats.get("count", 0) < ML_WEIGHT_CAL_MIN_GROUP_SAMPLES:
            continue

        # 롤백 체크 (3일 연속 악화)
        rollback_weight = self._check_rollback(group)
        if rollback_weight is not None:
            result = WeightCalibrationResult(
                category_group=group,
                previous_weight=self._get_current_weight(group),
                new_weight=rollback_weight,
                rule_mae=group_stats.get("rule_mae", 0),
                blended_mae=group_stats.get("blended_mae", 0),
                ml_contribution=0,
                action="rollback",
                reason="3일 연속 Blended MAE > Rule MAE",
                sample_count=group_stats.get("count", 0),
            )
            self._update_override(group, rollback_weight)
            self._save_history(result)
            results.append(result)
            rollbacks.append(group)
            alerts.append(f"[ML 롤백] {group}: {result.previous_weight:.2f}→{rollback_weight:.2f}")
            continue

        # 일반 조정
        result = self._analyze_group(group, group_stats)
        self._save_history(result)
        if result.action in ("increase", "decrease"):
            self._update_override(group, result.new_weight)
        if result.action == "decrease":
            alerts.append(
                f"[ML 가중치 하향] {group}: {result.previous_weight:.2f}→{result.new_weight:.2f} "
                f"(기여도 {result.ml_contribution:+.1%})"
            )
        results.append(result)

    return {
        "calibrated": any(r.action != "hold" for r in results),
        "results": [asdict(r) for r in results],
        "rollbacks": rollbacks,
        "alerts": alerts,
    }
```

### 4.3 _analyze_group() ���정 로직

```python
def _analyze_group(self, group: str, stats: Dict) -> WeightCalibrationResult:
    current_weight = self._get_current_weight(group)
    rule_mae = stats.get("rule_mae", 0)
    blended_mae = stats.get("blended_mae", 0)
    count = stats.get("count", 0)

    # ml_contribution: 양수면 ML이 개선, 음수면 악화
    ml_contribution = (rule_mae - blended_mae) / rule_mae if rule_mae > 0 else 0

    lo, hi = ML_WEIGHT_SAFE_RANGE[group]

    # 판정
    if ml_contribution > ML_WEIGHT_CAL_DEADBAND_UPPER:  # +0.03
        # ML이 3%+ 개선 → 상향
        new_weight = min(hi, round(current_weight + ML_WEIGHT_CAL_STEP, 2))
        action = "increase" if new_weight != current_weight else "hold"
        reason = f"ML 기여도 {ml_contribution:+.1%} > +3% 임계값"
    elif ml_contribution < ML_WEIGHT_CAL_DEADBAND_LOWER:  # -0.02
        # ML이 2%+ 악화 → 하향
        new_weight = max(lo, round(current_weight - ML_WEIGHT_CAL_STEP, 2))
        action = "decrease" if new_weight != current_weight else "hold"
        reason = f"ML 기여도 {ml_contribution:+.1%} < -2% 임계값"
    else:
        # 불감대 → 유지
        new_weight = current_weight
        action = "hold"
        reason = f"ML 기여도 {ml_contribution:+.1%} (불감대 내)"

    return WeightCalibrationResult(
        category_group=group,
        previous_weight=current_weight,
        new_weight=new_weight,
        rule_mae=round(rule_mae, 4),
        blended_mae=round(blended_mae, 4),
        ml_contribution=round(ml_contribution, 4),
        action=action,
        reason=reason,
        sample_count=count,
    )
```

### 4.4 _check_rollback() 3일 연속 악화 감지

```python
def _check_rollback(self, group: str) -> Optional[float]:
    """최근 3일 이력에서 연속 decrease이면 3일 전 가���치로 롤백"""
    conn = self._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT action, previous_weight
            FROM ml_weight_history
            WHERE store_id = ? AND category_group = ?
            ORDER BY calibration_date DESC
            LIMIT 3
        """, (self.store_id, group))
        rows = cursor.fetchall()
    finally:
        conn.close()

    if len(rows) < 3:
        return None

    # 3일 연속 decrease인지 확인
    if all(row[0] == "decrease" for row in rows):
        return rows[-1][1]  # 3일 전의 previous_weight로 롤백

    return None
```

---

## 5. improved_predictor.py 수정

### 5.1 _get_ml_weight() DB 오버라이드 추가

```python
# 수정 위치: improved_predictor.py _get_ml_weight() 내부 (L2762)
# 기존:
#   max_w = self.ML_MAX_WEIGHT.get(group, self.ML_MAX_WEIGHT_DEFAULT)
# 변경:

def _get_ml_weight(self, mid_cd: str, data_days: int, item_cd: str = "") -> float:
    # ... 기존 data_days < 30 체크 ...

    from src.prediction.ml.feature_builder import get_category_group
    group = get_category_group(mid_cd)
    group_mae = self._get_group_mae(group)

    if group_mae is None:
        return 0.10

    # ★ DB 오버라이드 조회 (캐시 사용)
    max_w = self._get_calibrated_max_weight(group)

    # MAE → 가중치 선형 매핑 (기존 로직 유지)
    weight = max(0.05, min(max_w, max_w - (group_mae - 0.5) * (max_w / 1.5)))

    if data_days < 60:
        weight *= 0.6

    return round(weight, 2)


def _get_calibrated_max_weight(self, group: str) -> float:
    """DB 오버라이드 > 하드코딩 폴백 (배치 시작 시 1회 캐시)"""
    # lazy init 캐시
    if not hasattr(self, '_ml_weight_override_cache'):
        self._ml_weight_override_cache = self._load_weight_overrides()

    return self._ml_weight_override_cache.get(
        group, self.ML_MAX_WEIGHT.get(group, self.ML_MAX_WEIGHT_DEFAULT)
    )


def _load_weight_overrides(self) -> Dict[str, float]:
    """ml_weight_overrides 테이블에서 전체 조회"""
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_store_connection(self._store_id)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT category_group, max_weight
                FROM ml_weight_overrides
                WHERE store_id = ?
            """, (self._store_id,))
            return {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            conn.close()
    except Exception:
        return {}
```

---

## 6. DB 스키마 상세

### 6.1 schema.py STORE_SCHEMA 추가

```python
# schema.py STORE_SCHEMA 리스트에 추가

# ml_weight_history — ML 가중치 조정 이력
"""CREATE TABLE IF NOT EXISTS ml_weight_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    calibration_date TEXT NOT NULL,
    category_group TEXT NOT NULL,
    previous_weight REAL NOT NULL,
    new_weight REAL NOT NULL,
    rule_mae REAL,
    blended_mae REAL,
    ml_contribution REAL,
    action TEXT NOT NULL,
    reason TEXT,
    sample_count INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(store_id, calibration_date, category_group)
)""",

# ml_weight_overrides — 현재 적용 중인 가중치 (UPSERT)
"""CREATE TABLE IF NOT EXISTS ml_weight_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    category_group TEXT NOT NULL,
    max_weight REAL NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(store_id, category_group)
)""",
```

### 6.2 인덱스

```sql
CREATE INDEX IF NOT EXISTS idx_ml_weight_history_date
    ON ml_weight_history(store_id, calibration_date);
CREATE INDEX IF NOT EXISTS idx_ml_weight_history_group
    ON ml_weight_history(category_group, calibration_date);
```

### 6.3 _STORE_COLUMN_PATCHES

```python
# 기존 DB에 테이블이 없을 경우를 대비 (CREATE IF NOT EXISTS이므로 안전)
# _STORE_COLUMN_PATCHES에는 추가 불필요 — STORE_SCHEMA에 포함되므로
# ensure_schema() 시 자동 생성됨
```

---

## 7. constants.py 상수 정의

```python
# src/settings/constants.py 에 추가

# ── ML 가중치 자동 보정 (ml-harness) ──────────────────────
ML_WEIGHT_AUTO_CALIBRATION_ENABLED = True
ML_WEIGHT_CAL_LOOKBACK_DAYS = 14           # MAE 비교 기간
ML_WEIGHT_CAL_MIN_SAMPLES = 100            # 최소 레코드 수 (전체)
ML_WEIGHT_CAL_MIN_GROUP_SAMPLES = 20       # 최소 레코드 수 (그룹당)
ML_WEIGHT_CAL_STEP = 0.02                  # 1회 조정 폭
ML_WEIGHT_CAL_DEADBAND_UPPER = 0.03        # 상향 임계값 (ML이 3%+ 개선)
ML_WEIGHT_CAL_DEADBAND_LOWER = -0.02       # 하향 임계값 (ML이 2%+ 악화)
ML_WEIGHT_CAL_ROLLBACK_DAYS = 3            # 연속 악화 롤백 기준 일수

ML_WEIGHT_GROUPS = [
    "food_group", "perishable_group",
    "alcohol_group", "tobacco_group", "general_group",
]

ML_WEIGHT_SAFE_RANGE = {
    "food_group":       (0.05, 0.30),
    "perishable_group": (0.05, 0.30),
    "alcohol_group":    (0.10, 0.40),
    "tobacco_group":    (0.10, 0.40),
    "general_group":    (0.10, 0.40),
}
```

---

## 8. daily_job.py Phase 1.59

```python
# src/scheduler/daily_job.py — Phase 1.58 뒤에 삽입

            # ★ ML 가중치 자동 보정 (Phase 1.59 - ml-harness)
            try:
                from src.settings.constants import ML_WEIGHT_AUTO_CALIBRATION_ENABLED
                if ML_WEIGHT_AUTO_CALIBRATION_ENABLED:
                    logger.info("[Phase 1.59] ML Weight Calibration")
                    from src.prediction.ml_weight_calibrator import MLWeightCalibrator
                    ml_calibrator = MLWeightCalibrator(store_id=self.store_id)
                    ml_cal_result = ml_calibrator.calibrate()

                    if ml_cal_result.get("calibrated"):
                        adjusted = [r for r in ml_cal_result.get("results", [])
                                    if r.get("action") != "hold"]
                        logger.info(
                            f"[Phase 1.59] ML 가중치 조정: {len(adjusted)}개 그룹 "
                            f"(롤백: {len(ml_cal_result.get('rollbacks', []))})"
                        )
                    else:
                        reason = ml_cal_result.get("reason", "no_change")
                        logger.debug(f"[Phase 1.59] ML 가중치 미조정: {reason}")

                    # 알림 메시지 저장 (Phase 3 카카오 발송 시 사용)
                    self._ml_cal_alerts = ml_cal_result.get("alerts", [])
                else:
                    logger.debug("[Phase 1.59] ML weight calibration disabled")
            except Exception as e:
                logger.warning(f"[Phase 1.59] ML 가중치 보정 실패 (발주 플로우 계속): {e}")
```

---

## 9. KakaoNotifier ML 섹션

### 9.1 send_report() 수정

```python
# src/notification/kakao_notifier.py send_report() 내부
# [TOP 카테고리] 섹션 다음에 추가

    # [ML 성능] 섹션
    ml_info = report.get("ml_performance", {})
    if ml_info:
        lines.append("")
        lines.append("[ML 모델 성능]")
        if ml_info.get("rule_mae") is not None:
            lines.append(f"- Rule MAE: {ml_info['rule_mae']:.3f}")
            lines.append(f"- Blended MAE: {ml_info['blended_mae']:.3f}")
            contrib = ml_info.get("ml_contribution", 0)
            sign = "+" if contrib >= 0 else ""
            lines.append(f"- ML 기여도: {sign}{contrib:.1%}")

        # 가중치 조정 알림
        ml_alerts = report.get("ml_alerts", [])
        if ml_alerts:
            for alert in ml_alerts[:3]:  # 최대 3건
                lines.append(f"  ⚠ {alert}")
```

### 9.2 daily_job.py Phase 3에서 report dict 구성

```python
# daily_job Phase 3 (리포트 발송) 에서 report dict에 ML 섹션 추가
tracker = AccuracyTracker(store_id=self.store_id)
rule_vs_ml = tracker.get_rule_vs_ml_accuracy(days=14)
if rule_vs_ml.get("total_records", 0) > 0:
    report["ml_performance"] = {
        "rule_mae": rule_vs_ml.get("rule_mae"),
        "blended_mae": rule_vs_ml.get("blended_mae"),
        "ml_contribution": rule_vs_ml.get("ml_contribution", 0) / 100,
    }
report["ml_alerts"] = getattr(self, "_ml_cal_alerts", [])
```

---

## 10. REST API 상세

### 10.1 api_ml_performance.py

```python
# src/web/routes/api_ml_performance.py

from flask import Blueprint, jsonify, request
from src.prediction.accuracy import AccuracyTracker
from src.prediction.ml_weight_calibrator import MLWeightCalibrator
from src.settings.constants import DEFAULT_STORE_ID, ML_WEIGHT_SAFE_RANGE
from src.utils.logger import get_logger

logger = get_logger(__name__)
ml_bp = Blueprint("ml", __name__)


@ml_bp.route("/performance", methods=["GET"])
def get_performance():
    """Rule vs ML MAE 일별 추이

    Query: store_id, days (기본 14)
    Response: {store_id, days, daily: [{date, rule_mae, blended_mae, contribution, count}]}
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 14))

    try:
        tracker = AccuracyTracker(store_id=store_id)
        data = tracker.get_rule_vs_ml_accuracy(days=days)
        return jsonify({"store_id": store_id, "days": days, **data})
    except Exception as e:
        logger.error(f"ML performance 조회 실패: {e}")
        return jsonify({"error": "조회 실패"}), 500


@ml_bp.route("/contribution", methods=["GET"])
def get_contribution():
    """그룹별 ML 기여도 현황

    Response: {store_id, by_group: {group: {rule_mae, blended_mae, count, avg_ml_weight}}}
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 14))

    try:
        tracker = AccuracyTracker(store_id=store_id)
        data = tracker.get_rule_vs_ml_accuracy(days=days)
        return jsonify({
            "store_id": store_id,
            "total_records": data.get("total_records", 0),
            "rule_mae": data.get("rule_mae"),
            "blended_mae": data.get("blended_mae"),
            "ml_contribution": data.get("ml_contribution"),
            "by_group": data.get("by_group", {}),
        })
    except Exception as e:
        logger.error(f"ML contribution 조회 실패: {e}")
        return jsonify({"error": "조회 실패"}), 500


@ml_bp.route("/weight-history", methods=["GET"])
def get_weight_history():
    """가중치 조정 이력

    Query: store_id, days (기본 30)
    Response: {store_id, history: [{date, group, prev, new, action, reason, contribution}]}
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 30))

    try:
        calibrator = MLWeightCalibrator(store_id=store_id)
        history = calibrator.get_weight_history(days=days)
        return jsonify({"store_id": store_id, "days": days, "history": history})
    except Exception as e:
        logger.error(f"ML weight history 조회 실패: {e}")
        return jsonify({"error": "조회 실패"}), 500


@ml_bp.route("/effectiveness", methods=["GET"])
def get_effectiveness():
    """가중치 구간별 효과성

    Response: {total_records, buckets: [{range, count, mae, rule_mae, improvement}]}
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 14))

    try:
        tracker = AccuracyTracker(store_id=store_id)
        data = tracker.get_ml_weight_effectiveness(days=days)
        return jsonify({"store_id": store_id, **data})
    except Exception as e:
        logger.error(f"ML effectiveness 조회 실패: {e}")
        return jsonify({"error": "조회 실패"}), 500


@ml_bp.route("/current-weights", methods=["GET"])
def get_current_weights():
    """현재 적용 중인 가중치 (DB 오버라이드 vs 하드코딩)

    Response: {store_id, weights: {group: {current, default, safe_range, source}}}
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)

    try:
        calibrator = MLWeightCalibrator(store_id=store_id)
        current = calibrator.get_current_weights()

        from src.prediction.improved_predictor import ImprovedPredictor
        defaults = ImprovedPredictor.ML_MAX_WEIGHT

        weights = {}
        for group, default_w in defaults.items():
            db_w = current.get(group)
            weights[group] = {
                "current": db_w if db_w is not None else default_w,
                "default": default_w,
                "safe_range": list(ML_WEIGHT_SAFE_RANGE.get(group, (0.05, 0.40))),
                "source": "db" if db_w is not None else "hardcoded",
            }

        return jsonify({"store_id": store_id, "weights": weights})
    except Exception as e:
        logger.error(f"ML current weights 조회 실패: {e}")
        return jsonify({"error": "조회 실패"}), 500
```

### 10.2 Blueprint 등록

```python
# src/web/routes/__init__.py register_blueprints()에 추가
from .api_ml_performance import ml_bp
app.register_blueprint(ml_bp, url_prefix="/api/ml")
```

---

## 11. 대시보드 UI 상세

### 11.1 탭 추가 (index.html)

```html
<!-- nav-tabs-wrap에 추가 -->
<button class="nav-tab" data-tab="ml-perf">ML 성능</button>

<!-- tab-panel 추가 -->
<div id="ml-perf-panel" class="tab-panel">
    <!-- 요약 카드 4개 -->
    <div class="metric-grid" id="ml-summary-cards">
        <div class="metric-card">
            <div class="metric-header">
                <span class="metric-label">Rule MAE</span>
            </div>
            <div class="metric-value" id="ml-rule-mae">-</div>
        </div>
        <div class="metric-card">
            <div class="metric-header">
                <span class="metric-label">Blended MAE</span>
            </div>
            <div class="metric-value" id="ml-blended-mae">-</div>
        </div>
        <div class="metric-card">
            <div class="metric-header">
                <span class="metric-label">ML 기여도</span>
            </div>
            <div class="metric-value" id="ml-contribution">-</div>
        </div>
        <div class="metric-card">
            <div class="metric-header">
                <span class="metric-label">조정 횟수 (30d)</span>
            </div>
            <div class="metric-value" id="ml-adj-count">-</div>
        </div>
    </div>

    <!-- Rule vs ML MAE 일별 차트 -->
    <div class="card" style="margin-top:16px;">
        <h3 style="margin-bottom:12px;">Rule vs ML MAE 추이 (14일)</h3>
        <canvas id="ml-mae-chart" height="200"></canvas>
    </div>

    <!-- 카테고리별 가중치 현황 -->
    <div class="card" style="margin-top:16px;">
        <h3 style="margin-bottom:12px;">카테고리별 가중치 현황</h3>
        <div id="ml-weight-bars"></div>
    </div>

    <!-- 가중치 조정 이력 -->
    <div class="card" style="margin-top:16px;">
        <h3 style="margin-bottom:12px;">가중치 조정 이력</h3>
        <table class="data-table" id="ml-history-table">
            <thead>
                <tr>
                    <th>날짜</th><th>그룹</th><th>이전</th><th>신규</th>
                    <th>액션</th><th>기여도</th><th>사유</th>
                </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>
</div>
```

### 11.2 JavaScript 로직

```javascript
// ML 성능 탭 활성화 시 데이터 로드
async function loadMLPerformance(storeId) {
    const [contrib, weights, history, effectiveness] = await Promise.all([
        fetch(`/api/ml/contribution?store_id=${storeId}`).then(r => r.json()),
        fetch(`/api/ml/current-weights?store_id=${storeId}`).then(r => r.json()),
        fetch(`/api/ml/weight-history?store_id=${storeId}&days=30`).then(r => r.json()),
        fetch(`/api/ml/effectiveness?store_id=${storeId}`).then(r => r.json()),
    ]);

    // 요약 카드
    document.getElementById('ml-rule-mae').textContent =
        contrib.rule_mae?.toFixed(3) ?? '-';
    document.getElementById('ml-blended-mae').textContent =
        contrib.blended_mae?.toFixed(3) ?? '-';
    document.getElementById('ml-contribution').textContent =
        contrib.ml_contribution != null ? `${contrib.ml_contribution > 0 ? '+' : ''}${contrib.ml_contribution.toFixed(1)}%` : '-';
    document.getElementById('ml-adj-count').textContent =
        history.history?.filter(h => h.action !== 'hold').length ?? 0;

    // MAE 차트 (Chart.js)
    renderMAEChart(contrib);

    // 가중치 바
    renderWeightBars(weights);

    // 이력 테이블
    renderHistoryTable(history);
}

function renderMAEChart(data) {
    const ctx = document.getElementById('ml-mae-chart').getContext('2d');
    const byGroup = data.by_group || {};
    const groups = Object.keys(byGroup);

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: groups.map(g => g.replace('_group', '')),
            datasets: [
                {
                    label: 'Rule MAE',
                    data: groups.map(g => byGroup[g].rule_mae),
                    backgroundColor: 'rgba(234,88,12,0.6)',   // --red
                },
                {
                    label: 'Blended MAE',
                    data: groups.map(g => byGroup[g].blended_mae),
                    backgroundColor: 'rgba(59,130,246,0.6)',   // --blue
                },
            ],
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'top' } },
            scales: { y: { beginAtZero: true } },
        },
    });
}

function renderWeightBars(data) {
    const container = document.getElementById('ml-weight-bars');
    container.innerHTML = '';
    const weights = data.weights || {};

    for (const [group, info] of Object.entries(weights)) {
        const pct = ((info.current - info.safe_range[0]) /
                     (info.safe_range[1] - info.safe_range[0])) * 100;
        const source = info.source === 'db' ? '(DB)' : '(기본)';
        container.innerHTML += `
            <div style="margin-bottom:8px;">
                <div style="display:flex;justify-content:space-between;font-size:0.85rem;">
                    <span>${group.replace('_group','')}</span>
                    <span>${info.current.toFixed(2)} ${source}
                          (${info.safe_range[0]}~${info.safe_range[1]})</span>
                </div>
                <div style="background:#FDE68A;border-radius:4px;height:8px;margin-top:4px;">
                    <div style="background:#1C1208;border-radius:4px;height:8px;
                                width:${Math.min(100,Math.max(0,pct))}%;"></div>
                </div>
            </div>`;
    }
}

function renderHistoryTable(data) {
    const tbody = document.querySelector('#ml-history-table tbody');
    tbody.innerHTML = '';
    const history = data.history || [];

    if (history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;">조정 이력 없음</td></tr>';
        return;
    }

    for (const h of history.slice(0, 30)) {
        const actionBadge = {
            increase: '<span class="metric-badge" style="background:#dcfce7;color:#16a34a;">상향</span>',
            decrease: '<span class="metric-badge" style="background:#fee2e2;color:#ea580c;">하향</span>',
            rollback: '<span class="metric-badge" style="background:#fef3c7;color:#d97706;">롤백</span>',
            hold: '<span class="metric-badge" style="background:#f3f4f6;color:#6b7280;">유지</span>',
        }[h.action] || h.action;

        tbody.innerHTML += `<tr>
            <td>${h.calibration_date}</td>
            <td>${h.category_group?.replace('_group','')}</td>
            <td>${h.previous_weight?.toFixed(2)}</td>
            <td>${h.new_weight?.toFixed(2)}</td>
            <td>${actionBadge}</td>
            <td>${h.ml_contribution != null ? (h.ml_contribution * 100).toFixed(1) + '%' : '-'}</td>
            <td style="font-size:0.8rem;">${h.reason || ''}</td>
        </tr>`;
    }
}
```

---

## 12. 테스트 설계

### 12.1 test_ml_weight_calibrator.py 구조

```python
class TestMLWeightCalibrator:
    """MLWeightCalibrator 단위 테스트"""

    # ── calibrate() 기본 동작 ──
    def test_calibrate_disabled(self):
        """토글 OFF → calibrated=False"""

    def test_calibrate_insufficient_data(self):
        """14일 미만 데이터 → calibrated=False, reason=insufficient_data"""

    def test_calibrate_insufficient_group_data(self):
        """그룹 내 20건 미만 → 해당 그룹 스킵"""

    # ── _analyze_group() 판정 ──
    def test_increase_when_contribution_above_3pct(self):
        """ML 기여도 > +3% → 가중치 +0.02"""

    def test_decrease_when_contribution_below_neg2pct(self):
        """ML 기여도 < -2% → 가중치 -0.02"""

    def test_hold_in_deadband(self):
        """ML 기여도 -2%~+3% → hold"""

    def test_increase_capped_at_upper_bound(self):
        """상한 도달 시 더 이��� 증가 안 함"""

    def test_decrease_capped_at_lower_bound(self):
        """하한 도달 시 더 이상 감소 안 함"""

    # ── 롤백 ──
    def test_rollback_after_3_consecutive_decreases(self):
        """3일 연속 decrease → 롤백"""

    def test_no_rollback_with_2_decreases(self):
        """2일 연속 decrease → 롤백 안 함"""

    def test_rollback_resets_to_3days_ago_weight(self):
        """롤백 시 3일 전 previous_weight로 복원"""

    # ── DB 연동 ──
    def test_save_history_creates_record(self):
        """_save_history() → ml_weight_history INSERT 확인"""

    def test_update_override_upserts(self):
        """_update_override() → ml_weight_overrides UPSERT 확인"""

    def test_get_current_weights_db_priority(self):
        """DB 값이 하드코딩보다 우선"""

    def test_get_current_weights_fallback(self):
        """DB 없으면 하드코딩 값 반환"""

    # ── improved_predictor 연동 ──
    def test_predictor_uses_db_override(self):
        """_get_calibrated_max_weight()가 DB 값 반환"""

    def test_predictor_fallback_to_hardcoded(self):
        """DB 없으면 ML_MAX_WEIGHT 딕셔너리 값 반환"""

    # ── API ──
    def test_api_performance_returns_200(self): ...
    def test_api_contribution_returns_200(self): ...
    def test_api_weight_history_returns_200(self): ...
    def test_api_effectiveness_returns_200(self): ...
    def test_api_current_weights_returns_200(self): ...

    # ── 스케줄러 통합 ──
    def test_phase_159_calls_calibrator(self):
        """daily_job Phase 1.59가 MLWeightCalibrator.calibrate() 호출"""

    def test_phase_159_stores_alerts(self):
        """alerts가 self._ml_cal_alerts에 저장됨"""

    def test_phase_159_exception_does_not_block(self):
        """예외 시 발주 플로우 계속"""
```

### 12.2 테스트 픽스처

```python
@pytest.fixture
def calibrator_db(tmp_path):
    """테스트용 매장 DB (prediction_logs + ML 테이블)"""
    db_path = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_path))
    # prediction_logs 생성 + rule_order_qty 컬럼 포함
    # ml_weight_history, ml_weight_overrides 생성
    # 14일치 prediction_logs 시드 데이터 삽입
    conn.close()
    return str(db_path)
```

---

## 13. 구현 순서 (체크리스트)

### Phase A: Core (MLWeightCalibrator + DB)

- [ ] A-1: `constants.py`에 ML_WEIGHT_CAL_* 상수 추가
- [ ] A-2: `schema.py`에 2개 테이블 DDL 추가
- [ ] A-3: `ml_weight_calibrator.py` 구현
  - [ ] A-3a: `calibrate()` 메인 로직
  - [ ] A-3b: `_analyze_group()` 판정
  - [ ] A-3c: `_check_rollback()` 3일 롤백
  - [ ] A-3d: `_save_history()` + `_update_override()` DB 저장
  - [ ] A-3e: `get_current_weights()` + `get_weight_history()` 조회
- [ ] A-4: `improved_predictor.py` `_get_calibrated_max_weight()` 추가
- [ ] A-5: 테스트 작성 (Calibrator 12개 + predictor 2개)

### Phase B: Integration (Scheduler + 알림)

- [ ] B-1: `daily_job.py` Phase 1.59 블록 추가
- [ ] B-2: `kakao_notifier.py` ML 섹션 추가
- [ ] B-3: daily_job Phase 3 리포트 dict에 ml_performance 추가
- [ ] B-4: 테스트 작성 (스케줄러 3개)

### Phase C: UI (API + 대시보드)

- [ ] C-1: `api_ml_performance.py` 5개 엔드포인트
- [ ] C-2: `routes/__init__.py` Blueprint 등록
- [ ] C-3: `index.html` ML 성능 탭 + 패널
- [ ] C-4: JavaScript 차트/테이블 로직
- [ ] C-5: 테스트 작성 (API 5개)

---

## 14. 데이터 흐름도

```
[매일 07:00 daily_job]
       │
       ├─ Phase 1.6: AccuracyTracker.update_actual_sales()
       │              └─ prediction_logs.actual_qty 갱신
       │
       ├─ Phase 1.59: MLWeightCalibrator.calibrate()
       │   ├─ AccuracyTracker.get_rule_vs_ml_accuracy(14d)
       │   │   └─ prediction_logs (rule_order_qty vs actual_qty)
       │   ├─ _analyze_group() × 5개 그룹
       │   ├─ _check_rollback() — ml_weight_history 최근 3건 조회
       │   ├─ _save_history() — ml_weight_history INSERT
       │   └─ _update_override() — ml_weight_overrides UPSERT
       │
       ├─ Phase 2: 예측 실행
       │   └─ ImprovedPredictor._get_ml_weight()
       │       └─ _get_calibrated_max_weight()
       │           └─ ml_weight_overrides 조회 (캐시)
       │
       └─ Phase 3: 카카오 리포트
           └─ send_report(ml_performance={...}, ml_alerts=[...])

[대시보드 접속 시]
       │
       └─ /api/ml/* → AccuracyTracker + MLWeightCalibrator → JSON → Chart.js
```

---

## 15. 주의사항

1. **Phase 1.59 vs Phase 1.6 순서**: Phase 1.6이 actual_qty를 갱신하므로, Phase 1.59는 **어제까지의 actual_qty**를 기반으로 분석. Phase 1.6 이전에 실행해도 무방 (어제 데이터는 이미 갱신됨)
2. **캐시 무효화**: `_ml_weight_override_cache`는 ImprovedPredictor 인스턴스 생성 시 1회 로드. 매 배치마다 새 인스턴스이므로 자동 갱신
3. **UNIQUE 제약**: ml_weight_history의 `(store_id, calibration_date, category_group)` 로 하루 1회만 보장
4. **기존 테스트 영향**: ML_MAX_WEIGHT 하드코딩을 참조하는 테스트는 DB 오버라이드 미존재 시 기존 값 유지이므로 영향 없음

---

## 16. Week 3: AI 요약 서비스 (토론 결과 반영, 2026-03-28)

> **원칙**: Week 3에서는 인터페이스와 저장 구조만 확보하고, 고도화는 후속 Week에 위임

### 16.1 확정된 설계 결정

| 항목 | 결정 | 근거 |
|------|------|------|
| 발주 요약 수준 | B안 (카테고리별 이상 강조) | 카카오톡 1000자 제한 + 점주 빠른 확인 |
| 주의품목 표시 | 최대 3개 + "외 N건" | 정보 과부하 방지 |
| 이상 감지 표시 | C안 (조건부) - 이상 있을 때만 [주의] 섹션 | 알림 피로 방지 |
| 발주 요약 | 항상 1~2줄 포함 (하이브리드) | 이상 없는 날에도 발주 확인 필요 |
| 트리거 타이밍 | Phase 3 send_report() 내 통합 | 별도 메시지 = 알림 피로 |
| action_proposals | Optional 파라미터만 확보 | 자동화 2단계는 Week 4+ |
| timeout 방어 | DB 조회 레벨에 timeout만 | rule-based는 0.5초 이내, 5초 걸리면 DB 문제 |
| ai_summaries 저장 | Week 3에서 INSERT만 | 매장 간 비교 조회는 Week 5+ |
| AI 역할 | 규칙 기반 템플릿 (AI 없이) | BGF 외부전송 제약 + 안정성 |

### 16.2 신규 파일

| # | 파일 | 계층 | 역할 | 예상 LOC |
|---|------|------|------|---------|
| 1 | `src/notification/summary_report_service.py` | Application | 발주/이상감지 요약 생성 + DB 저장 | ~150 |

### 16.3 수정 파일

| # | 파일 | 수정 내용 | 예상 변경량 |
|---|------|----------|-----------|
| 1 | `src/infrastructure/database/schema.py` | ai_summaries DDL (COMMON_SCHEMA) | +10줄 |
| 2 | `src/scheduler/daily_job.py` | Phase 3 send_report() 내 요약 연동 | +15줄 |
| 3 | `src/notification/kakao_notifier.py` | send_report()에 요약 섹션 조립 | +10줄 |

### 16.4 DB 스키마 (common.db)

```sql
-- COMMON_SCHEMA에 추가
CREATE TABLE IF NOT EXISTS ai_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    summary_type TEXT NOT NULL,   -- 'order' | 'integrity'
    report_text TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ai_summaries_store_type
    ON ai_summaries(store_id, summary_type, created_at);
```

### 16.5 SummaryReportService 클래스 설계

```python
# src/notification/summary_report_service.py

from typing import Dict, List, Optional, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SummaryReportService:
    """발주 결과 + 이상 감지 요약 생성 (규칙 기반 템플릿)

    Week 3: 인터페이스 + 저장 구조 확보
    Week 4+: action_proposals 연동, 매장 간 비교
    """

    def __init__(self, store_id: str):
        self.store_id = store_id

    def build_order_summary(self, order_results: Dict[str, Any]) -> str:
        """항상 표시. 1~2줄 발주 결과 요약.

        Args:
            order_results: {
                "total_items": int,         # 총 발주 품목 수
                "total_amount": int,        # 총 발주 금액
                "normal_count": int,        # 정상 발주 품목 수
                "warning_items": [          # 주의 품목 (과발주 의심 등)
                    {"item_nm": str, "reason": str, "rule_qty": int, "final_qty": int},
                    ...
                ],
            }

        Returns:
            "✅ 정상 21품목 | ⚠️ 주의 2품목 (컵라면 외 1건)"
        """

    def build_integrity_summary(
        self,
        integrity_results: Dict[str, Any],
        action_proposals: Optional[List[Dict]] = None,  # Week 4+ 확장용
    ) -> Optional[str]:
        """이상 있을 때만 반환. 없으면 None.

        Args:
            integrity_results: {
                "checks": {
                    "expired_batch_remaining": {"count": int, "severity": str},
                    "food_ghost_stock": {"count": int, "severity": str},
                    "expiry_time_mismatch": {"count": int, "severity": str},
                    "missing_delivery_type": {"count": int, "severity": str},
                    "past_expiry_active": {"count": int, "severity": str},
                    "unavailable_with_sales": {"count": int, "severity": str},
                },
                "dangerous_skips": [
                    {"item_cd": str, "item_nm": str, "skip_reason": str, "skip_detail": str},
                    ...
                ],
                "skip_stats": {
                    "SKIP_LOW_POPULARITY": int,
                    "SKIP_UNAVAILABLE": int,
                    "PASS_STOCK_SUFFICIENT": int,
                    "PASS_DATA_INSUFFICIENT": int,
                },
            }
            action_proposals: None (Week 3) | [{"action_type": str, "count": int}] (Week 4+)

        Returns:
            None (이상 없음) 또는 "[주의]\n🔴 ghost_inventory 3건\n🟡 zero_stock 1건"
        """

    def compose(
        self,
        order_results: Dict[str, Any],
        integrity_results: Dict[str, Any],
    ) -> str:
        """최종 문자열 조립. send_report()에서 이것만 호출.

        Returns:
            발주 요약 (항상) + [주의] 이상 감지 (조건부)
        """
        order_part = self.build_order_summary(order_results)
        integrity_part = self.build_integrity_summary(integrity_results)

        sections = [order_part]
        if integrity_part:
            sections.append(integrity_part)

        return "\n\n".join(sections)

    def save_summary(self, summary_type: str, report_text: str) -> None:
        """common.db ai_summaries에 INSERT (조회/비교는 Week 5+)"""
```

### 16.6 카카오톡 메시지 포맷

#### 정상 발주 (이상 없음)
```
[기존 리포트 내용]
...

📊 발주 요약
✅ 정상 21품목 / 147,800원
식품 8 | 음료 9 | 기타 4
```

#### 이상 발주 (주의품목 + integrity 이상)
```
[기존 리포트 내용]
...

📊 발주 요약
✅ 정상 19품목 | ⚠️ 주의 2품목 (컵라면 외 1건)

[주의] 이상 감지
🔴 ghost_inventory: 3건
🟡 unavailable_with_sales: 1건
→ 대시보드에서 상세 확인
```

### 16.7 daily_job.py 연동

```python
# Phase 3: send_report() 내부
def send_report(self):
    # 기존 리포트 생성
    existing_report = self._build_existing_report()

    # AI 요약 섹션 추가 (fail-safe)
    try:
        from src.notification.summary_report_service import SummaryReportService
        summary_svc = SummaryReportService(store_id=self.store_id)
        summary_text = summary_svc.compose(
            order_results=self._order_results,
            integrity_results=self._integrity_results,
        )
        full_report = f"{existing_report}\n\n{summary_text}"

        # common.db에 저장 (Week 3: INSERT만)
        summary_svc.save_summary("order", summary_text)
    except Exception as e:
        logger.warning(f"요약 생성 실패 (기존 리포트 유지): {e}")
        full_report = existing_report  # fallback

    self._send_kakao(full_report)
```

### 16.8 테스트 시나리오 (~10개)

```python
class TestSummaryReportService:
    """AI 요약 서비스 단위 테스트"""

    # ── build_order_summary() ──
    def test_order_summary_normal_only(self):
        """전체 정상 → ✅ 정상 N품목 / 금액"""

    def test_order_summary_with_warnings(self):
        """주의품목 2건 → ✅ 정상 N | ⚠️ 주의 2 (이름 외 1건)"""

    def test_order_summary_warnings_max_3(self):
        """주의품목 5건 → 최대 3개 표시 + 외 2건"""

    def test_order_summary_empty_results(self):
        """빈 결과 → 기본 메시지"""

    # ── build_integrity_summary() ──
    def test_integrity_no_issues_returns_none(self):
        """이상 없음 → None 반환"""

    def test_integrity_with_issues(self):
        """ghost 3건 + unavailable 1건 → [주의] 포맷"""

    def test_integrity_severity_ordering(self):
        """🔴 > 🟡 > 🟢 순서 정렬"""

    # ── compose() ──
    def test_compose_normal(self):
        """정상 → 발주 요약만 포함, [주의] 없음"""

    def test_compose_with_integrity(self):
        """이상 있음 → 발주 요약 + [주의] 모두 포함"""

    # ── save_summary() ──
    def test_save_summary_inserts_to_common_db(self):
        """common.db ai_summaries에 INSERT 확인"""

    # ── fail-safe ──
    def test_compose_exception_returns_empty(self):
        """내부 예외 시 빈 문자열 (daily_job fallback 가능)"""
```

### 16.9 구현 순서 (Week 3 체크리스트)

- [ ] W3-1: `schema.py` COMMON_SCHEMA에 ai_summaries DDL 추가
- [ ] W3-2: 3매장 + common.db 마이그레이션 실행
- [ ] W3-3: `summary_report_service.py` 생성
  - [ ] W3-3a: `build_order_summary()` 구현
  - [ ] W3-3b: `build_integrity_summary()` 구현
  - [ ] W3-3c: `compose()` 조립
  - [ ] W3-3d: `save_summary()` common.db INSERT
- [ ] W3-4: `daily_job.py` Phase 3 send_report() 연동 + fallback
- [ ] W3-5: `kakao_notifier.py` 요약 섹션 조립
- [ ] W3-6: 테스트 작성 (~10개)
- [ ] W3-7: 전체 테스트 스위트 통과 확인

### 16.10 향후 확장 (Week 4+)

| Week | 내용 | 비고 |
|------|------|------|
| Week 4 | action_proposals 연동 | compose()에 proposals 파라미터 전달 |
| Week 4 | BGF 외부전송 계약 확인 → api_based_summary | AI_SUMMARY_ENABLED 토글 |
| Week 5 | 매장 간 비교 요약 | ai_summaries 조회 로직 |
| Week 5 | 대시보드 AI 요약 패널 | /api/ai/summaries 엔드포인트 |
