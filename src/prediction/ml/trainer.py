"""
ML 모델 학습/추론 파이프라인
- 주간 재학습 (매주 월요일 03:00)
- 카테고리 그룹별 개별 학습
- 모델 저장: data/models/ (joblib)
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.utils.logger import get_logger
from src.prediction.prediction_config import PREDICTION_PARAMS
from .data_pipeline import MLDataPipeline
from .feature_builder import MLFeatureBuilder, get_category_group, CATEGORY_GROUPS
from .model import MLPredictor

logger = get_logger(__name__)

# 학습 최소 데이터 수
MIN_TRAINING_SAMPLES = 50

# 그룹 모델 학습 설정 (food-ml-dual-model)
GROUP_MIN_SAMPLES = 30  # 그룹 모델 최소 샘플 (개별보다 낮음)
GROUP_TRAINING_DAYS = {
    "food_group": 30,  # 푸드: 회전 빠름 → 짧은 윈도우
    "default": 90,
}

# 카테고리 그룹별 비대칭 손실 계수 (quantile alpha)
# alpha > 0.5: 과소예측 패널티 높음 (넉넉하게 발주)
# alpha < 0.5: 과대예측 패널티 높음 (보수적 발주)
# ml-improvement Phase D: 도메인 정합 반영
GROUP_QUANTILE_ALPHA = {
    "food_group": 0.45,        # 보수적 (유통기한 짧음, 폐기비용 > 품절비용)
    "perishable_group": 0.48,  # 약간 보수적 (유통기한 1~3일)
    "alcohol_group": 0.55,     # 유지 (유통기한 길고 품절 기회비용 높음)
    "tobacco_group": 0.55,     # 상향 (품절 시 고객 이탈 높음, 폐기 없음)
    "general_group": 0.50,     # 중립 (장기 유통)
}


class MLTrainer:
    """ML 모델 학습기

    Args:
        db_path: DB 경로 (명시적 지정, 테스트용)
        store_id: 매장 ID (매장별 DB + 매장별 모델 디렉토리 사용)
    """

    def __init__(self, db_path: Optional[str] = None, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.pipeline = MLDataPipeline(db_path=db_path, store_id=store_id)
        self.predictor = MLPredictor(store_id=store_id)

    def _prepare_training_data(
        self, days: int = 90
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        학습 데이터 준비 (카테고리 그룹별)

        Args:
            days: 학습 데이터 기간

        Returns:
            {group_name: [{daily_sales, target_date, mid_cd, actual_sale_qty, ...}, ...]}
        """
        # 활성 상품 목록
        active_items = self.pipeline.get_active_items(min_days=14)
        if not active_items:
            logger.warning("활성 상품 없음. 학습 데이터 없음.")
            return {}

        logger.info(f"학습 대상 상품: {len(active_items)}개")

        # 상품별 메타정보 일괄 조회 (유통기한, 이익률, 폐기율)
        item_meta = self.pipeline.get_items_meta([i["item_cd"] for i in active_items])

        # 기간 내 기온 데이터 조회
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        weather_data = self.pipeline.get_external_factors(start_date, end_date)

        # 그룹별 학습 데이터 분류
        group_data: Dict[str, List[Dict[str, Any]]] = {
            group: [] for group in CATEGORY_GROUPS
        }

        # ML 품절일 imputation 설정
        _ml_so_cfg = PREDICTION_PARAMS.get("ml_stockout_filter", {})
        ml_stockout_enabled = _ml_so_cfg.get("enabled", False)
        ml_stockout_min_days = _ml_so_cfg.get("min_available_days", 3)

        # 행사 데이터 일괄 캐시 {item_cd: [(start_date, end_date), ...]}
        promo_cache: Dict[str, List[Tuple[str, str]]] = {}
        try:
            _conn = self.pipeline._get_conn()
            try:
                _cur = _conn.cursor()
                _cur.execute(
                    "SELECT item_cd, start_date, end_date FROM promotions WHERE is_active = 1"
                )
                for _r in _cur.fetchall():
                    promo_cache.setdefault(_r[0], []).append((_r[1], _r[2]))
            finally:
                _conn.close()
        except Exception:
            logger.debug("DB 테이블 미존재, False 폴백", exc_info=True)
            pass  # 테이블 미존재 등 → 전부 False 폴백

        # 입고 패턴 통계 배치 캐시 (receiving-pattern)
        receiving_stats_cache: Dict[str, Dict[str, float]] = {}
        try:
            from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
            from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository

            recv_repo = ReceivingRepository(store_id=self.store_id)
            ot_repo = OrderTrackingRepository(store_id=self.store_id)

            pattern_stats = recv_repo.get_receiving_pattern_stats_batch(
                store_id=self.store_id, days=days
            )
            pending_ages = ot_repo.get_pending_age_batch(store_id=self.store_id)

            all_items = set(pattern_stats.keys()) | set(pending_ages.keys())
            for _item_cd in all_items:
                stats = dict(pattern_stats.get(_item_cd, {}))
                stats["pending_age_days"] = pending_ages.get(_item_cd, 0)
                receiving_stats_cache[_item_cd] = stats

            logger.info(f"[학습] 입고패턴 캐시: {len(receiving_stats_cache)}개 상품")
        except Exception as e:
            logger.debug(f"[학습] 입고패턴 캐시 로드 실패 (무시): {e}")

        # 시간대 Feature 캐시 (item_cd별 1회 조회)
        _hourly_cache: Dict[str, dict] = {}

        for item_info in active_items:
            item_cd = item_info["item_cd"]
            mid_cd = item_info["mid_cd"]
            group = get_category_group(mid_cd)

            # 일별 판매 데이터 조회
            daily_sales = self.pipeline.get_item_daily_stats(item_cd, days)
            if len(daily_sales) < 14:
                continue

            # 시간대 Feature 조회 (캐시: item_cd별 1회)
            if item_cd not in _hourly_cache:
                _hourly_cache[item_cd] = MLFeatureBuilder.calc_hourly_ratios(
                    store_id=self.store_id or "",
                    item_cd=item_cd,
                    base_date=daily_sales[-1]["sales_date"],
                    days=14,
                )

            # 상품 메타정보
            meta = item_meta.get(item_cd, {})

            # A0: 품절일 imputation용 비품절 평균 (최근 14일 → 30일 폴백)
            _non_stockout_avg = None
            if ml_stockout_enabled:
                _avail_14 = [
                    (d.get("sale_qty", 0) or 0)
                    for d in daily_sales[-14:]
                    if d.get("stock_qty") is not None and d.get("stock_qty", 0) > 0
                ]
                if len(_avail_14) >= ml_stockout_min_days:
                    _non_stockout_avg = sum(_avail_14) / len(_avail_14)
                else:
                    _avail_30 = [
                        (d.get("sale_qty", 0) or 0)
                        for d in daily_sales[-30:]
                        if d.get("stock_qty") is not None and d.get("stock_qty", 0) > 0
                    ]
                    if len(_avail_30) >= ml_stockout_min_days:
                        _non_stockout_avg = sum(_avail_30) / len(_avail_30)

            # 학습 샘플 생성: 7일 이전까지의 각 날짜를 target으로
            # (최소 7일의 이전 데이터를 feature로 사용)
            # Lag 계산을 위한 판매량 인덱싱 (날짜→인덱스 맵)
            date_to_idx = {row["sales_date"]: idx for idx, row in enumerate(daily_sales)}

            for i in range(7, len(daily_sales)):
                target_row = daily_sales[i]
                history = daily_sales[:i]

                # 해당 날짜의 외부 요인 조회 (기온, 공휴일 등)
                target_date_str = target_row["sales_date"]
                day_factors = weather_data.get(target_date_str, {})
                temp_str = day_factors.get("temperature")
                temp_val = None
                if temp_str is not None:
                    try:
                        temp_val = float(temp_str)
                    except (ValueError, TypeError):
                        pass

                # 전일 기온 → 기온 변화량 계산
                temp_delta_val = None
                if temp_val is not None:
                    prev_date_str = (datetime.strptime(target_date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                    prev_factors = weather_data.get(prev_date_str, {})
                    prev_temp_str = prev_factors.get("temperature")
                    if prev_temp_str is not None:
                        try:
                            temp_delta_val = temp_val - float(prev_temp_str)
                        except (ValueError, TypeError):
                            pass

                # 공휴일 여부
                holiday_str = day_factors.get("is_holiday", "false")
                is_holiday = holiday_str.lower() in ("true", "1", "yes")

                # 연휴 맥락 (2026-02-18 추가)
                holiday_period = int(day_factors.get("holiday_period_days", "0") or "0")
                pre_holiday = day_factors.get("is_pre_holiday", "false").lower() in ("true", "1")
                post_holiday = day_factors.get("is_post_holiday", "false").lower() in ("true", "1")

                # Lag Features 계산 (학습 시점 기준)
                lag_7_val = None
                lag_28_val = None
                wow_val = None
                try:
                    target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
                    # lag_7: 7일 전 판매량 (A2: 품절일 imputation)
                    lag7_date = (target_dt - timedelta(days=7)).strftime("%Y-%m-%d")
                    if lag7_date in date_to_idx:
                        _lag7_row = daily_sales[date_to_idx[lag7_date]]
                        _lag7_sale = _lag7_row.get("sale_qty", 0) or 0
                        _lag7_stock = _lag7_row.get("stock_qty")
                        if (_non_stockout_avg is not None
                                and _lag7_stock is not None and _lag7_stock == 0
                                and _lag7_sale == 0):
                            lag_7_val = _non_stockout_avg
                        else:
                            lag_7_val = _lag7_sale
                    # lag_28: 28일 전 판매량 (A2: 품절일 imputation)
                    lag28_date = (target_dt - timedelta(days=28)).strftime("%Y-%m-%d")
                    if lag28_date in date_to_idx:
                        _lag28_row = daily_sales[date_to_idx[lag28_date]]
                        _lag28_sale = _lag28_row.get("sale_qty", 0) or 0
                        _lag28_stock = _lag28_row.get("stock_qty")
                        if (_non_stockout_avg is not None
                                and _lag28_stock is not None and _lag28_stock == 0
                                and _lag28_sale == 0):
                            lag_28_val = _non_stockout_avg
                        else:
                            lag_28_val = _lag28_sale
                    # week_over_week: (7일전 - 14일전) / 14일전 (A2: 품절일 imputation)
                    lag14_date = (target_dt - timedelta(days=14)).strftime("%Y-%m-%d")
                    if lag7_date in date_to_idx and lag14_date in date_to_idx:
                        _r7 = daily_sales[date_to_idx[lag7_date]]
                        _r14 = daily_sales[date_to_idx[lag14_date]]
                        qty_7 = _r7.get("sale_qty", 0) or 0
                        qty_14 = _r14.get("sale_qty", 0) or 0
                        if _non_stockout_avg is not None:
                            if (_r7.get("stock_qty") is not None and _r7["stock_qty"] == 0 and qty_7 == 0):
                                qty_7 = _non_stockout_avg
                            if (_r14.get("stock_qty") is not None and _r14["stock_qty"] == 0 and qty_14 == 0):
                                qty_14 = _non_stockout_avg
                        if qty_14 > 0:
                            wow_val = round((qty_7 - qty_14) / qty_14, 3)
                        elif qty_7 > 0:
                            wow_val = 1.0
                        else:
                            wow_val = 0.0
                except (ValueError, KeyError):
                    pass

                # A1: Y값 품절일 imputation
                _target_sale_qty = target_row.get("sale_qty", 0) or 0
                _target_stock = target_row.get("stock_qty")
                if (_non_stockout_avg is not None
                        and _target_stock is not None and _target_stock == 0
                        and _target_sale_qty == 0):
                    _target_sale_qty = _non_stockout_avg

                # 외부 환경 피처 (35→39 확장)
                _rain_qty_train = 0.0
                _sky_nm_train = ""
                _pm25_train = 0.0
                try:
                    _rain_qty_train = float(day_factors.get("rain_qty", 0) or 0)
                    _sky_nm_train = str(day_factors.get("weather_cd_nm", "") or "")
                    _pm25_train = float(day_factors.get("pm25", 0) or 0)
                except (ValueError, TypeError):
                    pass
                _target_dt_day = datetime.strptime(target_date_str, "%Y-%m-%d").day
                _is_payday_train = 1 if _target_dt_day in (10, 11, 12, 25, 26, 27) else 0

                sample = {
                    "item_cd": item_cd,
                    "daily_sales": history,
                    "target_date": target_date_str,
                    "mid_cd": mid_cd,
                    "actual_sale_qty": _target_sale_qty,
                    "stock_qty": daily_sales[i - 1].get("stock_qty", 0) or 0,  # 전일 마감 재고 (leakage 제거)
                    "pending_qty": 0,
                    "promo_active": any(
                        s <= target_date_str <= e for s, e in promo_cache.get(item_cd, [])
                    ),
                    "expiration_days": meta.get("expiration_days", 0),
                    "disuse_rate": meta.get("disuse_rate", 0.0),
                    "margin_rate": meta.get("margin_rate", 0.0),
                    "temperature": temp_val,
                    "temperature_delta": temp_delta_val,
                    "lag_7": lag_7_val,
                    "lag_28": lag_28_val,
                    "week_over_week": wow_val,
                    "is_holiday": is_holiday,
                    "holiday_period_days": holiday_period,
                    "is_pre_holiday": pre_holiday,
                    "is_post_holiday": post_holiday,
                    "receiving_stats": receiving_stats_cache.get(item_cd),
                    "large_cd": meta.get("large_cd"),
                    "rain_qty": _rain_qty_train,
                    "sky_nm": _sky_nm_train,
                    "is_payday": _is_payday_train,
                    "pm25": _pm25_train,
                    "hourly_ratios": _hourly_cache.get(item_cd, {}),
                }

                group_data[group].append(sample)

        # 결과 요약
        for group, samples in group_data.items():
            if samples:
                logger.info(f"  {group}: {len(samples)}개 샘플")

        return group_data

    # 성능 보호 게이트 임계값 (MAE 악화 허용 비율)
    PERFORMANCE_GATE_THRESHOLD = 0.2  # 20%

    # Accuracy@1 하락 허용 임계값 (ml-improvement Phase E)
    ACCURACY_GATE_THRESHOLD = 0.05  # 5%p

    def _check_performance_gate(self, group_name: str, new_mae: float,
                                accuracy_at_1: Optional[float] = None) -> bool:
        """기존 모델 대비 성능 악화 여부 확인.

        Returns:
            True: 신규 모델 수용 (개선 또는 허용 범위 내)
            False: 롤백 필요 (MAE 20% 이상 악화 OR Accuracy@1 5%p 하락)
        """
        meta = self.predictor._load_meta()
        group_meta = meta.get("groups", {}).get(group_name, {}).get("metrics", {})
        prev_mae = group_meta.get("mae")

        if prev_mae is None or prev_mae <= 0:
            return True  # 기존 기록 없음 → 수용

        degradation = (new_mae - prev_mae) / prev_mae
        if degradation > self.PERFORMANCE_GATE_THRESHOLD:
            logger.warning(
                f"[{group_name}] 성능 게이트 FAIL (MAE): "
                f"기존 MAE={prev_mae:.2f} -> 신규 MAE={new_mae:.2f} "
                f"(악화 {degradation:.1%}). 이전 모델 유지."
            )
            return False

        # Accuracy@1 게이트 (ml-improvement Phase E)
        if accuracy_at_1 is not None:
            prev_acc = group_meta.get("accuracy_at_1")
            if prev_acc is not None and prev_acc > 0:
                acc_drop = prev_acc - accuracy_at_1
                if acc_drop > self.ACCURACY_GATE_THRESHOLD:
                    logger.warning(
                        f"[{group_name}] 성능 게이트 FAIL (Acc@1): "
                        f"기존 Acc@1={prev_acc:.1%} -> 신규 Acc@1={accuracy_at_1:.1%} "
                        f"(하락 {acc_drop:.1%}p). 이전 모델 유지."
                    )
                    return False

        improvement = "개선" if degradation < 0 else "허용"
        logger.info(
            f"[{group_name}] 성능 게이트 PASS: "
            f"기존 MAE={prev_mae:.2f} -> 신규 MAE={new_mae:.2f} "
            f"({improvement} {degradation:+.1%})"
        )
        return True

    def _rollback_model(self, group_name: str) -> bool:
        """_prev 모델로 롤백."""
        import shutil as _shutil
        prev_path = self.predictor.model_dir / f"model_{group_name}_prev.joblib"
        model_path = self.predictor.model_dir / f"model_{group_name}.joblib"

        if not prev_path.exists():
            logger.warning(f"[{group_name}] 이전 모델 없음. 롤백 불가.")
            return False

        _shutil.copy2(prev_path, model_path)
        logger.info(f"[{group_name}] 이전 모델로 롤백 완료")
        return True

    def train_all_groups(self, days: int = 90, incremental: bool = False) -> Dict[str, Any]:
        """
        전체 카테고리 그룹 모델 학습

        Args:
            days: 학습 데이터 기간
            incremental: True면 증분학습 (성능 보호 게이트 적용)

        Returns:
            학습 결과 {group: {success, samples, metrics}}
        """
        try:
            from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import mean_absolute_error, mean_squared_error
        except ImportError:
            logger.warning("scikit-learn 미설치. pip install scikit-learn")
            return {"error": "scikit-learn not installed"}

        store_label = f" (store={self.store_id})" if self.store_id else " (글로벌)"
        mode_label = f"증분({days}일)" if incremental else f"전체({days}일)"
        logger.info("=" * 60)
        logger.info(f"ML 모델 학습 시작 [{mode_label}]{store_label}: {datetime.now().isoformat()}")
        logger.info("=" * 60)

        # 학습 데이터 준비
        group_data = self._prepare_training_data(days)
        results = {}

        for group_name, samples in group_data.items():
            if len(samples) < MIN_TRAINING_SAMPLES:
                logger.info(f"[{group_name}] 샘플 부족 ({len(samples)} < {MIN_TRAINING_SAMPLES}). 스킵.")
                results[group_name] = {
                    "success": False,
                    "reason": f"insufficient_samples ({len(samples)})",
                }
                continue

            logger.info(f"[{group_name}] 학습 시작: {len(samples)}개 샘플")

            # Feature 배열 생성 + 날짜 추적 (시계열 분할용)
            X_list, y_list, valid_dates = [], [], []
            for s in samples:
                feat = MLFeatureBuilder.build_features(
                    daily_sales=s.get("daily_sales", []),
                    target_date=s.get("target_date", ""),
                    mid_cd=s.get("mid_cd", ""),
                    stock_qty=s.get("stock_qty", 0),
                    pending_qty=s.get("pending_qty", 0),
                    promo_active=s.get("promo_active", False),
                    expiration_days=s.get("expiration_days", 0),
                    disuse_rate=s.get("disuse_rate", 0.0),
                    margin_rate=s.get("margin_rate", 0.0),
                    temperature=s.get("temperature"),
                    temperature_delta=s.get("temperature_delta"),
                    lag_7=s.get("lag_7"),
                    lag_28=s.get("lag_28"),
                    week_over_week=s.get("week_over_week"),
                    is_holiday=s.get("is_holiday", False),
                    association_score=s.get("association_score", 0.0),
                    holiday_period_days=s.get("holiday_period_days", 0),
                    is_pre_holiday=s.get("is_pre_holiday", False),
                    is_post_holiday=s.get("is_post_holiday", False),
                    receiving_stats=s.get("receiving_stats"),
                    large_cd=s.get("large_cd"),
                    rain_qty=s.get("rain_qty", 0.0),
                    sky_nm=s.get("sky_nm", ""),
                    is_payday=s.get("is_payday", 0),
                    pm25=s.get("pm25", 0.0),
                    hourly_ratios=s.get("hourly_ratios"),
                )
                if feat is not None:
                    X_list.append(feat)
                    y_list.append(float(s.get("actual_sale_qty", 0)))
                    valid_dates.append(s["target_date"])

            if len(X_list) < MIN_TRAINING_SAMPLES:
                results[group_name] = {
                    "success": False,
                    "reason": "feature_build_failed",
                }
                continue

            X = np.vstack(X_list)
            y = np.array(y_list, dtype=np.float32)

            # 날짜 기준 시계열 분할 (시계열 leakage 방지)
            unique_dates = sorted(set(valid_dates))
            cutoff_idx = max(1, int(len(unique_dates) * 0.8))
            cutoff_date = unique_dates[cutoff_idx - 1]

            train_mask = np.array([d <= cutoff_date for d in valid_dates])
            test_mask = ~train_mask

            if test_mask.sum() < 5:
                # 폴백: 마지막 20% 인덱스 (날짜 cutoff로 test가 너무 적으면)
                split = max(1, int(len(X) * 0.8))
                X_train, X_test = X[:split], X[split:]
                y_train, y_test = y[:split], y[split:]
            else:
                X_train, X_test = X[train_mask], X[test_mask]
                y_train, y_test = y[train_mask], y[test_mask]

            # 앙상블: RandomForest + GradientBoosting (비대칭 손실)
            try:
                # 카테고리별 비대칭 alpha 결정
                alpha = GROUP_QUANTILE_ALPHA.get(group_name, 0.5)

                rf = RandomForestRegressor(
                    n_estimators=100,
                    max_depth=10,
                    min_samples_leaf=5,
                    random_state=42,
                    n_jobs=-1,
                )
                rf.fit(X_train, y_train)

                # GradientBoosting: quantile 손실로 비대칭 학습
                gb = GradientBoostingRegressor(
                    n_estimators=100,
                    max_depth=5,
                    learning_rate=0.1,
                    min_samples_leaf=5,
                    random_state=42,
                    loss="quantile",
                    alpha=alpha,
                )
                gb.fit(X_train, y_train)

                # 앙상블 모델 래퍼
                ensemble = _EnsembleModel(rf, gb)

                # 테스트 성능 평가 (MAE + 비대칭 Pinball Loss)
                y_pred = ensemble.predict(X_test)
                mae = mean_absolute_error(y_test, y_pred)
                rmse = np.sqrt(mean_squared_error(y_test, y_pred))
                mean_y = np.mean(y_test)
                mape = (mae / mean_y * 100) if mean_y > 0 else 0

                # Pinball Loss (비대칭 손실 지표)
                errors = y_test - y_pred
                pinball = np.mean(np.where(errors >= 0, alpha * errors, (alpha - 1) * errors))

                # Accuracy@N 메트릭 (ml-improvement Phase E)
                # 편의점 저판매량에서 ±1/2개 이내 정확도가 실질적 지표
                accuracy_at_1 = float(np.mean(np.abs(y_test - y_pred) <= 1.0))
                accuracy_at_2 = float(np.mean(np.abs(y_test - y_pred) <= 2.0))

                logger.info(
                    f"  MAE: {mae:.2f}, RMSE: {rmse:.2f}, "
                    f"MAPE: {mape:.1f}%, Pinball(α={alpha}): {pinball:.3f}, "
                    f"Acc@1: {accuracy_at_1:.1%}, Acc@2: {accuracy_at_2:.1%}, "
                    f"Mean: {mean_y:.2f}"
                )

                # Feature Importance 기록
                avg_imp: Dict[str, float] = {}
                try:
                    feat_names = MLFeatureBuilder.FEATURE_NAMES
                    rf_imp = dict(zip(feat_names, rf.feature_importances_.tolist()))
                    gb_imp = dict(zip(feat_names, gb.feature_importances_.tolist()))
                    avg_imp = {n: round((rf_imp[n] + gb_imp[n]) / 2, 4) for n in feat_names}
                    top5 = sorted(avg_imp.items(), key=lambda x: -x[1])[:5]
                    logger.info(f"  Feature Top5: {[(n, f'{v:.4f}') for n, v in top5]}")
                except Exception:
                    logger.debug("Feature Top5 로깅 실패, 학습 결과 영향 없음", exc_info=True)

                # 성능 보호 게이트 (증분학습 시)
                # ml-improvement Phase E: MAE 20% 악화 OR Accuracy@1 5%p 하락 시 롤백
                if incremental and not self._check_performance_gate(
                    group_name, mae, accuracy_at_1=accuracy_at_1
                ):
                    self._rollback_model(group_name)
                    results[group_name] = {
                        "success": False,
                        "gated": True,
                        "reason": "performance_gate_failed",
                        "samples": len(X),
                        "mae": round(float(mae), 2),
                        "accuracy_at_1": round(float(accuracy_at_1), 3),
                    }
                    continue

                # 모델 저장 (이전 모델 백업 + 메타데이터 기록)
                save_metrics = {
                    "mae": round(float(mae), 2),
                    "rmse": round(float(rmse), 2),
                    "mape": round(float(mape), 1),
                    "pinball_loss": round(float(pinball), 3),
                    "accuracy_at_1": round(float(accuracy_at_1), 3),
                    "accuracy_at_2": round(float(accuracy_at_2), 3),
                    "samples": len(X),
                    "train_size": len(X_train),
                    "test_size": len(X_test),
                }
                self.predictor.save_model(group_name, ensemble, metrics=save_metrics)

                results[group_name] = {
                    "success": True,
                    "samples": len(X),
                    "train_size": len(X_train),
                    "test_size": len(X_test),
                    "mae": round(float(mae), 2),
                    "rmse": round(float(rmse), 2),
                    "mape": round(float(mape), 1),
                    "pinball_loss": round(float(pinball), 3),
                    "accuracy_at_1": round(float(accuracy_at_1), 3),
                    "accuracy_at_2": round(float(accuracy_at_2), 3),
                    "quantile_alpha": alpha,
                    "feature_importance": avg_imp,
                }

            except Exception as e:
                logger.warning(f"  [{group_name}] 학습 실패: {e}")
                results[group_name] = {
                    "success": False,
                    "reason": str(e),
                }

        logger.info("=" * 60)
        logger.info("ML 모델 학습 완료")
        success_count = sum(1 for r in results.values() if r.get("success"))
        logger.info(f"성공: {success_count}/{len(results)} 그룹")
        logger.info("=" * 60)

        # 그룹 모델 학습 (food-ml-dual-model)
        try:
            group_days = GROUP_TRAINING_DAYS.get("food_group", days)
            group_results = self.train_group_models(days=group_days)
            results["_group_models"] = group_results
        except Exception as e:
            logger.warning(f"그룹 모델 학습 실패 (무시): {e}")
            results["_group_models"] = {"error": str(e)}

        # 학습 지표 DB 저장
        self._save_training_metrics(results)

        return results

    def train_group_models(self, days: int = 30) -> Dict[str, Any]:
        """small_cd 기반 그룹 모델 학습 (food_group 전용)

        Args:
            days: 학습 데이터 기간

        Returns:
            {key: {success, samples, mae, ...}}
        """
        try:
            from sklearn.ensemble import GradientBoostingRegressor
            from sklearn.metrics import mean_absolute_error
        except ImportError:
            return {"error": "scikit-learn not installed"}

        logger.info(f"[그룹모델] 학습 시작 ({days}일 윈도우)")

        # food 상품만 조회 (min_days=3으로 낮춤)
        food_mids = CATEGORY_GROUPS.get("food_group", [])
        if not food_mids:
            return {}

        active_items = self.pipeline.get_active_items(min_days=3)
        food_items = [i for i in active_items if i["mid_cd"] in food_mids]

        if not food_items:
            logger.info("[그룹모델] 푸드 상품 없음")
            return {}

        # small_cd 매핑 조회
        item_smallcd = self.pipeline.get_item_smallcd_map()
        peer_avgs = self.pipeline.get_smallcd_peer_avg_batch()
        lifecycle_stages = self.pipeline.get_lifecycle_stages_batch()
        item_meta = self.pipeline.get_items_meta([i["item_cd"] for i in food_items])

        # 기온/외부요인
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        weather_data = self.pipeline.get_external_factors(start_date, end_date)

        # small_cd별로 학습 데이터 그룹화
        smallcd_data: Dict[str, List[Dict]] = {}
        # 시간대 Feature 캐시 (그룹 모델용)
        _grp_hourly_cache: Dict[str, dict] = {}

        for item_info in food_items:
            item_cd = item_info["item_cd"]
            mid_cd = item_info["mid_cd"]
            small_cd = item_smallcd.get(item_cd, "")
            if not small_cd:
                small_cd = f"mid_{mid_cd}"  # small_cd 없으면 mid_cd 그룹

            daily_sales = self.pipeline.get_item_daily_stats(item_cd, days)
            if len(daily_sales) < 3:
                continue

            # 시간대 Feature 조회 (캐시: item_cd별 1회)
            if item_cd not in _grp_hourly_cache:
                _grp_hourly_cache[item_cd] = MLFeatureBuilder.calc_hourly_ratios(
                    store_id=self.store_id or "",
                    item_cd=item_cd,
                    base_date=daily_sales[-1]["sales_date"],
                    days=14,
                )

            meta = item_meta.get(item_cd, {})
            _peer_avg = peer_avgs.get(item_smallcd.get(item_cd, ""), 0.0)
            _lifecycle = lifecycle_stages.get(item_cd, 1.0)
            _data_days = item_info.get("data_days", len(daily_sales))

            # A3: 품절일 imputation용 비품절 평균 (최근 14일 → 30일 폴백)
            _grp_non_stockout_avg = None
            _grp_ml_so_cfg = PREDICTION_PARAMS.get("ml_stockout_filter", {})
            if _grp_ml_so_cfg.get("enabled", False):
                _grp_min_days = _grp_ml_so_cfg.get("min_available_days", 3)
                _grp_avail_14 = [
                    (d.get("sale_qty", 0) or 0)
                    for d in daily_sales[-14:]
                    if d.get("stock_qty") is not None and d.get("stock_qty", 0) > 0
                ]
                if len(_grp_avail_14) >= _grp_min_days:
                    _grp_non_stockout_avg = sum(_grp_avail_14) / len(_grp_avail_14)
                else:
                    _grp_avail_30 = [
                        (d.get("sale_qty", 0) or 0)
                        for d in daily_sales[-30:]
                        if d.get("stock_qty") is not None and d.get("stock_qty", 0) > 0
                    ]
                    if len(_grp_avail_30) >= _grp_min_days:
                        _grp_non_stockout_avg = sum(_grp_avail_30) / len(_grp_avail_30)

            for i in range(min(7, len(daily_sales)), len(daily_sales)):
                target_row = daily_sales[i]
                history = daily_sales[:i]
                target_date_str = target_row["sales_date"]

                day_factors = weather_data.get(target_date_str, {})
                temp_val = None
                temp_str = day_factors.get("temperature")
                if temp_str:
                    try:
                        temp_val = float(temp_str)
                    except (ValueError, TypeError):
                        pass

                is_holiday = day_factors.get("is_holiday", "false").lower() in ("true", "1")

                # A3: Y값 품절일 imputation
                _grp_target_sale = target_row.get("sale_qty", 0) or 0
                _grp_target_stock = target_row.get("stock_qty")
                if (_grp_non_stockout_avg is not None
                        and _grp_target_stock is not None and _grp_target_stock == 0
                        and _grp_target_sale == 0):
                    _grp_target_sale = _grp_non_stockout_avg

                # 외부 환경 피처 (35→39 확장)
                _grp_rain_qty = 0.0
                _grp_sky_nm = ""
                _grp_pm25 = 0.0
                try:
                    _grp_rain_qty = float(day_factors.get("rain_qty", 0) or 0)
                    _grp_sky_nm = str(day_factors.get("weather_cd_nm", "") or "")
                    _grp_pm25 = float(day_factors.get("pm25", 0) or 0)
                except (ValueError, TypeError):
                    pass
                _grp_target_day = datetime.strptime(target_date_str, "%Y-%m-%d").day
                _grp_is_payday = 1 if _grp_target_day in (10, 11, 12, 25, 26, 27) else 0

                sample = {
                    "item_cd": item_cd,
                    "daily_sales": history,
                    "target_date": target_date_str,
                    "mid_cd": mid_cd,
                    "actual_sale_qty": _grp_target_sale,
                    "stock_qty": daily_sales[i - 1].get("stock_qty", 0) or 0,
                    "pending_qty": 0,
                    "promo_active": False,
                    "expiration_days": meta.get("expiration_days", 0),
                    "disuse_rate": meta.get("disuse_rate", 0.0),
                    "margin_rate": meta.get("margin_rate", 0.0),
                    "temperature": temp_val,
                    "temperature_delta": None,
                    "is_holiday": is_holiday,
                    "large_cd": meta.get("large_cd"),
                    "data_days": _data_days,
                    "smallcd_peer_avg": _peer_avg,
                    "lifecycle_stage": _lifecycle,
                    "rain_qty": _grp_rain_qty,
                    "sky_nm": _grp_sky_nm,
                    "is_payday": _grp_is_payday,
                    "pm25": _grp_pm25,
                    "hourly_ratios": _grp_hourly_cache.get(item_cd, {}),
                }
                smallcd_data.setdefault(small_cd, []).append(sample)

        # small_cd별 학습
        results: Dict[str, Any] = {}
        alpha = GROUP_QUANTILE_ALPHA.get("food_group", 0.45)

        # 샘플 부족 small_cd → mid_cd로 합치기
        mid_fallback: Dict[str, List[Dict]] = {}
        trainable_keys: List[str] = []

        for key, samples in smallcd_data.items():
            if len(samples) >= GROUP_MIN_SAMPLES:
                trainable_keys.append(key)
            else:
                # mid_cd 폴백으로 합침
                mid_key = f"mid_{key[:3]}" if not key.startswith("mid_") else key
                mid_fallback.setdefault(mid_key, []).extend(samples)

        # mid_cd 폴백도 trainable에 추가
        for mid_key, samples in mid_fallback.items():
            if len(samples) >= GROUP_MIN_SAMPLES:
                smallcd_data[mid_key] = samples
                trainable_keys.append(mid_key)

        for key in trainable_keys:
            samples = smallcd_data[key]
            # Feature 배열 생성
            X_list, y_list = [], []
            for s in samples:
                feat = MLFeatureBuilder.build_features(
                    daily_sales=s.get("daily_sales", []),
                    target_date=s.get("target_date", ""),
                    mid_cd=s.get("mid_cd", ""),
                    stock_qty=s.get("stock_qty", 0),
                    pending_qty=0,
                    expiration_days=s.get("expiration_days", 0),
                    disuse_rate=s.get("disuse_rate", 0.0),
                    margin_rate=s.get("margin_rate", 0.0),
                    temperature=s.get("temperature"),
                    is_holiday=s.get("is_holiday", False),
                    large_cd=s.get("large_cd"),
                    data_days=s.get("data_days", 0),
                    smallcd_peer_avg=s.get("smallcd_peer_avg", 0.0),
                    lifecycle_stage=s.get("lifecycle_stage", 1.0),
                    rain_qty=s.get("rain_qty", 0.0),
                    sky_nm=s.get("sky_nm", ""),
                    is_payday=s.get("is_payday", 0),
                    pm25=s.get("pm25", 0.0),
                    hourly_ratios=s.get("hourly_ratios"),
                )
                if feat is not None:
                    X_list.append(feat)
                    y_list.append(float(s.get("actual_sale_qty", 0)))

            if len(X_list) < GROUP_MIN_SAMPLES:
                continue

            X = np.vstack(X_list)
            y = np.array(y_list, dtype=np.float32)

            # 80/20 분할
            split = max(1, int(len(X) * 0.8))
            X_train, X_test = X[:split], X[split:]
            y_train, y_test = y[:split], y[split:]

            if len(X_test) < 3:
                continue

            try:
                gb = GradientBoostingRegressor(
                    n_estimators=80,
                    max_depth=4,
                    learning_rate=0.1,
                    min_samples_leaf=3,
                    random_state=42,
                    loss="quantile",
                    alpha=alpha,
                )
                gb.fit(X_train, y_train)

                y_pred = gb.predict(X_test)
                mae = mean_absolute_error(y_test, y_pred)

                # 모델 키 정리 (small_cd or mid_cd)
                model_key = f"small_{key}" if not key.startswith("mid_") else key

                metrics = {
                    "mae": round(float(mae), 2),
                    "samples": len(X),
                    "train_size": len(X_train),
                    "test_size": len(X_test),
                }
                self.predictor.save_group_model(model_key, gb, metrics=metrics)

                logger.info(f"  [그룹모델] {model_key}: MAE={mae:.2f}, 샘플={len(X)}")
                results[model_key] = {"success": True, **metrics}

            except Exception as e:
                logger.warning(f"  [그룹모델] {key} 학습 실패: {e}")
                results[key] = {"success": False, "reason": str(e)}

        logger.info(f"[그룹모델] 학습 완료: {sum(1 for r in results.values() if r.get('success'))}개 성공")
        return results

    def _save_training_metrics(self, results: Dict[str, Any]) -> None:
        """학습 지표를 ml_training_logs 테이블에 저장"""
        try:
            conn = sqlite3.connect(self.pipeline.db_path, timeout=30)
            cursor = conn.cursor()

            # 테이블 존재 확인 (없으면 생성)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ml_training_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id TEXT,
                    group_name TEXT NOT NULL,
                    trained_at TEXT NOT NULL,
                    samples INTEGER,
                    train_size INTEGER,
                    test_size INTEGER,
                    mae REAL,
                    rmse REAL,
                    mape REAL,
                    pinball_loss REAL,
                    accuracy_at_1 REAL,
                    accuracy_at_2 REAL,
                    quantile_alpha REAL,
                    feature_importance TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            trained_at = datetime.now().isoformat()
            for group_name, r in results.items():
                if not r.get("success"):
                    continue
                fi_json = json.dumps(r.get("feature_importance", {}), ensure_ascii=False)
                cursor.execute("""
                    INSERT INTO ml_training_logs (
                        store_id, group_name, trained_at,
                        samples, train_size, test_size,
                        mae, rmse, mape, pinball_loss,
                        accuracy_at_1, accuracy_at_2,
                        quantile_alpha, feature_importance
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.store_id, group_name, trained_at,
                    r.get("samples"), r.get("train_size"), r.get("test_size"),
                    r.get("mae"), r.get("rmse"), r.get("mape"),
                    r.get("pinball_loss"),
                    r.get("accuracy_at_1"), r.get("accuracy_at_2"),
                    r.get("quantile_alpha"),
                    fi_json,
                ))

            conn.commit()
            conn.close()
            logger.info(f"학습 지표 저장 완료 ({sum(1 for r in results.values() if r.get('success'))}건)")
        except Exception as e:
            logger.debug(f"학습 지표 저장 실패 (무시): {e}")


class _EnsembleModel:
    """RandomForest + GradientBoosting 앙상블 래퍼

    joblib 직렬화 가능하도록 독립 클래스로 분리.
    """

    def __init__(self, rf_model: Any, gb_model: Any, rf_weight: float = 0.5) -> None:
        self.rf_model = rf_model
        self.gb_model = gb_model
        self.rf_weight = rf_weight
        self.gb_weight = 1.0 - rf_weight

    def predict(self, X: np.ndarray) -> np.ndarray:
        """앙상블 예측"""
        rf_pred = self.rf_model.predict(X)
        gb_pred = self.gb_model.predict(X)
        return self.rf_weight * rf_pred + self.gb_weight * gb_pred

    def __repr__(self) -> str:
        return f"EnsembleModel(rf_weight={self.rf_weight}, gb_weight={self.gb_weight})"
