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
from .data_pipeline import MLDataPipeline
from .feature_builder import MLFeatureBuilder, get_category_group, CATEGORY_GROUPS
from .model import MLPredictor

logger = get_logger(__name__)

# 학습 최소 데이터 수
MIN_TRAINING_SAMPLES = 50

# 카테고리 그룹별 비대칭 손실 계수 (quantile alpha)
# alpha > 0.5: 과소예측 패널티 높음 (넉넉하게 발주)
# alpha < 0.5: 과대예측 패널티 높음 (보수적 발주)
GROUP_QUANTILE_ALPHA = {
    "food_group": 0.6,        # 식품: 품절비용 > 폐기비용 → 넉넉히
    "perishable_group": 0.55,  # 소멸성: 폐기 위험도 중간
    "alcohol_group": 0.55,     # 주류: 품절 기회비용 높음
    "tobacco_group": 0.5,      # 담배: 대칭 (재고 안정)
    "general_group": 0.45,     # 일반: 장기유통 → 보수적
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

        for item_info in active_items:
            item_cd = item_info["item_cd"]
            mid_cd = item_info["mid_cd"]
            group = get_category_group(mid_cd)

            # 일별 판매 데이터 조회
            daily_sales = self.pipeline.get_item_daily_stats(item_cd, days)
            if len(daily_sales) < 14:
                continue

            # 상품 메타정보
            meta = item_meta.get(item_cd, {})

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
                    # lag_7: 7일 전 판매량
                    lag7_date = (target_dt - timedelta(days=7)).strftime("%Y-%m-%d")
                    if lag7_date in date_to_idx:
                        lag_7_val = daily_sales[date_to_idx[lag7_date]].get("sale_qty", 0) or 0
                    # lag_28: 28일 전 판매량
                    lag28_date = (target_dt - timedelta(days=28)).strftime("%Y-%m-%d")
                    if lag28_date in date_to_idx:
                        lag_28_val = daily_sales[date_to_idx[lag28_date]].get("sale_qty", 0) or 0
                    # week_over_week: (7일전 - 14일전) / 14일전
                    lag14_date = (target_dt - timedelta(days=14)).strftime("%Y-%m-%d")
                    if lag7_date in date_to_idx and lag14_date in date_to_idx:
                        qty_7 = daily_sales[date_to_idx[lag7_date]].get("sale_qty", 0) or 0
                        qty_14 = daily_sales[date_to_idx[lag14_date]].get("sale_qty", 0) or 0
                        if qty_14 > 0:
                            wow_val = round((qty_7 - qty_14) / qty_14, 3)
                        elif qty_7 > 0:
                            wow_val = 1.0
                        else:
                            wow_val = 0.0
                except (ValueError, KeyError):
                    pass

                sample = {
                    "item_cd": item_cd,
                    "daily_sales": history,
                    "target_date": target_date_str,
                    "mid_cd": mid_cd,
                    "actual_sale_qty": target_row.get("sale_qty", 0) or 0,
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
                }

                group_data[group].append(sample)

        # 결과 요약
        for group, samples in group_data.items():
            if samples:
                logger.info(f"  {group}: {len(samples)}개 샘플")

        return group_data

    def train_all_groups(self, days: int = 90) -> Dict[str, Any]:
        """
        전체 카테고리 그룹 모델 학습

        Args:
            days: 학습 데이터 기간

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
        logger.info("=" * 60)
        logger.info(f"ML 모델 학습 시작{store_label}: {datetime.now().isoformat()}")
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

                logger.info(
                    f"  MAE: {mae:.2f}, RMSE: {rmse:.2f}, "
                    f"MAPE: {mape:.1f}%, Pinball(α={alpha}): {pinball:.3f}, "
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
                    pass

                # 모델 저장 (이전 모델 백업 + 메타데이터 기록)
                save_metrics = {
                    "mae": round(float(mae), 2),
                    "rmse": round(float(rmse), 2),
                    "mape": round(float(mape), 1),
                    "pinball_loss": round(float(pinball), 3),
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
                    "mae": round(mae, 2),
                    "rmse": round(rmse, 2),
                    "mape": round(mape, 1),
                    "pinball_loss": round(pinball, 3),
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

        # 학습 지표 DB 저장
        self._save_training_metrics(results)

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
                        mae, rmse, mape, pinball_loss, quantile_alpha,
                        feature_importance
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.store_id, group_name, trained_at,
                    r.get("samples"), r.get("train_size"), r.get("test_size"),
                    r.get("mae"), r.get("rmse"), r.get("mape"),
                    r.get("pinball_loss"), r.get("quantile_alpha"),
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
