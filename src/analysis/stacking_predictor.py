"""
C-2: ML Stacking 메타 학습기 (stacking_predictor.py)

목적:
  Rule 예측 + ML 예측을 고정 비율로 섞는 대신,
  "상황별(요일/카테고리/데이터량)로 어떻게 섞으면 가장 정확한지"를
  Ridge 메타 학습기가 prediction_logs 이력에서 자동으로 학습.

학습 데이터:
  prediction_logs (rule_pred, ml_pred) + daily_sales (실제 정답)
  → 최소 30일치 필요

메타 피처 (6개):
  rule_pred, ml_pred,
  weekday, month, mid_cd_encoded, data_days_ratio

통합 위치:
  improved_predictor.py 의 적응형 블렌딩 직후
  → StackingPredictor.blend(rule_pred, ml_pred, context) 호출
  → 실패 / 데이터 부족 / MAE 악화 시 기존 MAE 블렌딩 폴백

학습 스케줄:
  일요일 03:00 ML 전체 재학습 시 같이 실행 (train_stacking_model)

안전장치:
  - 학습 데이터 < MIN_TRAIN_SAMPLES → 폴백
  - 검증 MAE > 기존 MAE × 1.2 → 폴백 + 경고
  - 모델 파일 없으면 폴백 (초기 운영 기간 동안은 기존 방식 유지)
"""

from __future__ import annotations

import json
import pickle
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 상수 ───────────────────────────────────────────────────────────
MIN_TRAIN_SAMPLES  = 100   # 학습에 필요한 최소 (200→100, QW-2)
HOLDOUT_DAYS       = 7     # 검증용 holdout 기간
TRAIN_LOOKBACK     = 60    # 학습에 사용할 최대 일수
MAE_DEGRADATION_THRESHOLD = 1.20   # MAE 20% 이상 악화 시 폴백

# 모델 저장 경로
_PROJECT_ROOT = Path(__file__).parent.parent.parent
MODEL_DIR = _PROJECT_ROOT / "data" / "models" / "stacking"

# mid_cd 인코딩 (prediction_logs 에 저장된 값 기준)
# 없는 mid_cd는 0으로 폴백
_MID_CD_ENCODE: Dict[str, int] = {}   # 런타임에 동적으로 채워짐


# ── 데이터 클래스 ──────────────────────────────────────────────────

@dataclass
class StackingContext:
    """
    blend() 호출 시 함께 전달하는 컨텍스트.
    improved_predictor.py 에서 이미 알고 있는 값들.
    """
    weekday:         int    # 0(월) ~ 6(일)
    month:           int    # 1~12
    mid_cd:          str    # 카테고리 코드
    data_days_ratio: float  # min(1.0, data_days / 60)
    current_mae_weight: float  # 현재 MAE 기반 ml_weight (폴백용)


@dataclass
class StackingResult:
    """blend() 반환값"""
    final_pred:   float
    used_stacking: bool    # True: 메타 학습기 사용 / False: MAE 폴백
    meta_weight:  float    # 메타 학습기가 결정한 ml 비중 (참고용)
    reason:       str


# ── 모델 저장/로드 ─────────────────────────────────────────────────

def _model_path(store_id: str) -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    return MODEL_DIR / f"stacking_{store_id}.pkl"


def _meta_path(store_id: str) -> Path:
    """모델과 함께 저장하는 메타 정보 (MAE 등)"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    return MODEL_DIR / f"stacking_{store_id}_meta.json"


def _save_model(store_id: str, model, mae: float, trained_at: str) -> None:
    with open(_model_path(store_id), "wb") as f:
        pickle.dump(model, f)
    with open(_meta_path(store_id), "w") as f:
        json.dump({"mae": mae, "trained_at": trained_at}, f)
    logger.info(f"[Stacking] 모델 저장: store={store_id} MAE={mae:.4f}")


def _load_model(store_id: str):
    """저장된 모델 로드. 없으면 None 반환."""
    path = _model_path(store_id)
    if not path.exists():
        return None, None
    try:
        with open(path, "rb") as f:
            model = pickle.load(f)
        with open(_meta_path(store_id), "r") as f:
            meta = json.load(f)
        return model, meta
    except Exception as e:
        logger.warning(f"[Stacking] 모델 로드 실패: {e}")
        return None, None


# ── 학습 데이터 준비 ───────────────────────────────────────────────

def _encode_mid_cd(mid_cd: str) -> int:
    """mid_cd → 정수 인코딩. 없으면 0."""
    if not _MID_CD_ENCODE:
        # 첫 호출 시 common.db 에서 동적 로딩
        try:
            common_path = _PROJECT_ROOT / "data" / "common.db"
            if common_path.exists():
                conn = sqlite3.connect(str(common_path))
                rows = conn.execute(
                    "SELECT mid_cd, ROW_NUMBER() OVER (ORDER BY mid_cd) AS rn "
                    "FROM mid_categories"
                ).fetchall()
                conn.close()
                _MID_CD_ENCODE.update({row[0]: row[1] for row in rows})
        except Exception:
            pass
    return _MID_CD_ENCODE.get(mid_cd, 0)


def _load_training_data(store_id: str) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    prediction_logs + daily_sales 조인으로 학습 데이터 구성.

    반환: (X, y) 또는 (None, None) 데이터 부족 시
    X shape: (N, 6) — [rule_pred, ml_pred, weekday, month, mid_cd_enc, data_days_ratio]
    y shape: (N,)   — 실제 판매량
    """
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_store_connection(store_id)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        logger.error(f"[Stacking] DB 연결 실패: {e}")
        return None, None

    cutoff = (datetime.now() - timedelta(days=TRAIN_LOOKBACK)).strftime("%Y-%m-%d")

    try:
        rows = conn.execute("""
            SELECT
                pl.predicted_qty,
                COALESCE(pl.ml_order_qty, pl.predicted_qty) AS ml_order_qty,
                pl.mid_cd,
                pl.target_date,
                pl.ml_weight_used,
                COALESCE(ds.sale_qty, 0)  AS actual_qty,
                CAST(strftime('%w', pl.target_date) AS INTEGER) AS weekday_raw,
                CAST(strftime('%m', pl.target_date) AS INTEGER) AS month
            FROM prediction_logs pl
            JOIN daily_sales ds
              ON pl.item_cd    = ds.item_cd
             AND pl.target_date = ds.sales_date
            WHERE pl.prediction_date >= ?
              AND pl.predicted_qty   IS NOT NULL
              AND pl.predicted_qty   > 0
              AND COALESCE(pl.ml_order_qty, pl.predicted_qty) > 0
            ORDER BY pl.target_date ASC
        """, (cutoff,)).fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"[Stacking] 학습 데이터 조회 실패: {e}")
        conn.close()
        return None, None

    if len(rows) < MIN_TRAIN_SAMPLES:
        logger.info(
            f"[Stacking] 학습 데이터 부족: {len(rows)}행 < {MIN_TRAIN_SAMPLES} "
            f"(store={store_id})"
        )
        return None, None

    X_list, y_list = [], []
    for row in rows:
        rule_pred      = float(row["predicted_qty"])
        ml_pred        = float(row["ml_order_qty"])
        mid_cd_enc     = _encode_mid_cd(row["mid_cd"] or "")
        weekday        = int(row["weekday_raw"] or 0)
        month          = int(row["month"] or 1)
        ml_weight      = float(row["ml_weight_used"] or 0.3)
        # data_days_ratio 는 ml_weight_used 로 근사 (0.1~0.5 → 정규화)
        data_days_ratio = min(1.0, ml_weight / 0.5)
        actual         = float(row["actual_qty"])

        X_list.append([rule_pred, ml_pred, weekday, month, mid_cd_enc, data_days_ratio])
        y_list.append(actual)

    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.float32)


# ── 학습 ──────────────────────────────────────────────────────────

def train_stacking_model(store_id: str) -> Dict:
    """
    일요일 03:00 ML 재학습 시 함께 호출.

    1. prediction_logs + daily_sales → (X, y) 구성
    2. train/holdout 분리
    3. Ridge 학습
    4. holdout MAE 비교 (기존 MAE 블렌딩 대비)
    5. 개선됐으면 저장, 악화됐으면 폴백 유지

    반환: {"status": "saved"|"fallback"|"insufficient_data", "mae": ..., "baseline_mae": ...}
    """
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    logger.info(f"[Stacking] 학습 시작 (store={store_id})")

    X, y = _load_training_data(store_id)
    if X is None:
        return {"status": "insufficient_data", "mae": None, "baseline_mae": None}

    # holdout 분리 (마지막 HOLDOUT_DAYS 에 해당하는 행)
    # 날짜 순 정렬되어 있으므로 마지막 비율로 분리
    split = max(MIN_TRAIN_SAMPLES, len(X) - HOLDOUT_DAYS * 10)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    if len(X_val) == 0:
        return {"status": "insufficient_data", "mae": None, "baseline_mae": None}

    # Ridge 메타 학습기 (스케일링 포함)
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("ridge",  Ridge(alpha=1.0)),
    ])

    try:
        model.fit(X_train, y_train)
    except Exception as e:
        logger.error(f"[Stacking] 학습 실패: {e}")
        return {"status": "error", "error": str(e)}

    # holdout MAE
    y_pred_meta = model.predict(X_val)
    y_pred_meta = np.clip(y_pred_meta, 0, None)
    meta_mae    = float(np.mean(np.abs(y_pred_meta - y_val)))

    # 기존 MAE 블렌딩 baseline (ml_weight=0.3 고정)
    rule_val    = X_val[:, 0]
    ml_val      = X_val[:, 1]
    baseline_pred = rule_val * 0.7 + ml_val * 0.3
    baseline_mae  = float(np.mean(np.abs(baseline_pred - y_val)))

    logger.info(
        f"[Stacking] MAE 비교: meta={meta_mae:.4f} baseline={baseline_mae:.4f} "
        f"(store={store_id})"
    )

    # 안전장치: MAE 악화 시 저장 안 함
    if meta_mae > baseline_mae * MAE_DEGRADATION_THRESHOLD:
        logger.warning(
            f"[Stacking] MAE 악화 ({meta_mae:.4f} > {baseline_mae:.4f} × "
            f"{MAE_DEGRADATION_THRESHOLD}) → 폴백 유지"
        )
        return {
            "status": "fallback",
            "mae": meta_mae,
            "baseline_mae": baseline_mae,
        }

    _save_model(store_id, model, meta_mae, datetime.now().isoformat())

    return {
        "status": "saved",
        "mae": meta_mae,
        "baseline_mae": baseline_mae,
        "improvement_pct": round((1 - meta_mae / baseline_mae) * 100, 2),
        "train_samples": len(X_train),
        "val_samples": len(X_val),
    }


# ── 예측 (blend) ───────────────────────────────────────────────────

class StackingPredictor:
    """
    improved_predictor.py 에서 사용하는 메타 블렌더.

    사용법:
        from src.analysis.stacking_predictor import StackingPredictor
        _stacking = StackingPredictor(store_id)
        result = _stacking.blend(
            rule_pred = 5.0,
            ml_pred   = 7.0,
            context   = StackingContext(weekday=4, month=3,
                                        mid_cd="001", data_days_ratio=0.8,
                                        current_mae_weight=0.35)
        )
        final = result.final_pred
    """

    def __init__(self, store_id: str):
        self.store_id = store_id
        self._model   = None
        self._meta    = None
        self._loaded  = False

    def _ensure_loaded(self) -> bool:
        """모델 지연 로딩. 로드 성공 여부 반환."""
        if self._loaded:
            return self._model is not None
        self._model, self._meta = _load_model(self.store_id)
        self._loaded = True
        if self._model is None:
            logger.debug(f"[Stacking] 모델 없음 → MAE 폴백 (store={self.store_id})")
        return self._model is not None

    def blend(
        self,
        rule_pred: float,
        ml_pred:   float,
        context:   StackingContext,
    ) -> StackingResult:
        """
        rule_pred + ml_pred → 메타 학습기로 최종 예측 반환.
        모델 없거나 오류 시 current_mae_weight 기반 기존 블렌딩으로 폴백.
        """
        # 폴백 계산 (항상 준비)
        fallback = (rule_pred * (1 - context.current_mae_weight)
                    + ml_pred  * context.current_mae_weight)

        if not self._ensure_loaded():
            return StackingResult(
                final_pred    = fallback,
                used_stacking = False,
                meta_weight   = context.current_mae_weight,
                reason        = "모델 없음 → MAE 폴백",
            )

        try:
            mid_cd_enc = _encode_mid_cd(context.mid_cd)
            X = np.array([[
                rule_pred,
                ml_pred,
                context.weekday,
                context.month,
                mid_cd_enc,
                context.data_days_ratio,
            ]], dtype=np.float32)

            pred = float(self._model.predict(X)[0])
            pred = max(0.0, pred)   # 음수 방지

            # 메타 학습기가 부여한 실질 ml 비중 추정 (로깅용)
            if rule_pred != ml_pred:
                meta_weight = (pred - rule_pred) / (ml_pred - rule_pred)
                meta_weight = float(np.clip(meta_weight, 0.0, 1.0))
            else:
                meta_weight = context.current_mae_weight

            return StackingResult(
                final_pred    = pred,
                used_stacking = True,
                meta_weight   = meta_weight,
                reason        = "메타 학습기 적용",
            )

        except Exception as e:
            logger.warning(f"[Stacking] 예측 실패 → 폴백: {e}")
            return StackingResult(
                final_pred    = fallback,
                used_stacking = False,
                meta_weight   = context.current_mae_weight,
                reason        = f"예측 오류 → MAE 폴백: {e}",
            )

    def is_available(self) -> bool:
        """모델이 로드되어 있는지 확인 (로깅/대시보드용)"""
        return self._ensure_loaded()


# ── CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="C-2: Stacking 메타 학습기")
    parser.add_argument("--store", "-s", required=True)
    parser.add_argument("--train",  action="store_true", help="모델 학습 실행")
    parser.add_argument("--status", action="store_true", help="현재 모델 상태 출력")
    args = parser.parse_args()

    if args.train:
        result = train_stacking_model(args.store)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.status:
        model, meta = _load_model(args.store)
        if meta:
            print(json.dumps({
                "store_id":   args.store,
                "trained_at": meta.get("trained_at"),
                "mae":        meta.get("mae"),
                "available":  True,
            }, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"store_id": args.store, "available": False},
                              ensure_ascii=False, indent=2))
