"""
Croston/TSB alpha·beta 최적화 모듈 (C-1)

일요일 03:00 배치에서 실행.
간헐 수요(intermittent) 상품에 대해 24개 (alpha × beta) 조합을 그리드 서치하여
상품별 최적 파라미터를 croston_params 테이블에 저장한다.

base_predictor.py 연결:
    from src.analysis.croston_optimizer import get_croston_params
    alpha, beta = get_croston_params(store_id, item_cd)  # 없으면 (0.15, 0.10) 반환

DB:
    store DB → croston_params 테이블 (자동 생성)
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 그리드 서치 파라미터 ────────────────────────────────────────────
ALPHA_CANDIDATES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]   # 수요 수준 평활 (6개)
BETA_CANDIDATES  = [0.05, 0.10, 0.15, 0.20]                # 수요 간격 평활 (4개)
# 총 24개 조합

# 평가에 사용할 holdout 기간 (일)
HOLDOUT_DAYS = 7

# 학습에 사용할 최소/최대 데이터 기간 (일)
MIN_TRAIN_DAYS  = 30
MAX_TRAIN_DAYS  = 90

# 기본값 (DB에 데이터 없거나 데이터 부족 시 폴백)
DEFAULT_ALPHA = 0.15
DEFAULT_BETA  = 0.10

# 재최적화 주기 (일) — 마지막 최적화 이후 이 기간이 지나면 재실행
REOPTIMIZE_INTERVAL_DAYS = 28


# ── DB 유틸리티 ────────────────────────────────────────────────────

def _get_store_db_path(store_id: str) -> Path:
    """매장 DB 경로 반환"""
    return Path(f"data/stores/{store_id}.db")


def _ensure_table(conn: sqlite3.Connection) -> None:
    """croston_params 테이블 보장 (없으면 생성, 구 스키마면 재생성)"""
    # 구 스키마 감지: optimized_at 컬럼 없으면 재생성
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(croston_params)").fetchall()]
        if cols and "optimized_at" not in cols:
            logger.warning("[Croston] 구 스키마 감지 → croston_params 재생성")
            conn.execute("DROP TABLE croston_params")
            conn.commit()
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS croston_params (
            item_cd      TEXT PRIMARY KEY,
            alpha        REAL NOT NULL,
            beta         REAL NOT NULL,
            rmse         REAL,           -- holdout RMSE (참고용)
            data_days    INTEGER,        -- 학습에 사용된 일수
            sell_days    INTEGER,        -- 실제 판매일수
            optimized_at TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
    """)
    conn.commit()


# ── Croston/TSB 예측 함수 ───────────────────────────────────────────

def _croston_tsb_forecast(
    demands: List[float],
    alpha: float,
    beta: float,
) -> List[float]:
    """
    TSB(Teunter-Syntetos-Babai) 방식 Croston 예측.
    demands: 시계열 수요 (0 포함, 날짜 순서)
    반환: 동일 길이의 예측값 리스트 (1-step ahead)
    """
    if not demands:
        return []

    n = len(demands)
    forecasts = [0.0] * n

    # 초기값: 첫 번째 비영 값 또는 1.0
    first_nonzero = next((d for d in demands if d > 0), 1.0)
    z = first_nonzero   # 수요 수준 (demand level)
    p = 0.5             # 수요 발생 확률

    for t in range(n):
        d = demands[t]

        if t == 0:
            forecasts[t] = z * p
            continue

        # TSB 업데이트
        if d > 0:
            z = alpha * d + (1 - alpha) * z
            p = beta  * 1 + (1 - beta)  * p
        else:
            p = beta  * 0 + (1 - beta)  * p
            # z는 변경 없음 (TSB 방식)

        forecasts[t] = z * p

    return forecasts


def _evaluate(
    train: List[float],
    holdout: List[float],
    alpha: float,
    beta: float,
) -> float:
    """
    holdout RMSE 계산.
    train 으로 상태를 업데이트한 뒤 holdout 기간 예측값과 실제값 비교.
    """
    import math

    if not holdout:
        return float("inf")

    # train 으로 내부 상태 워밍업
    all_data = train + holdout
    all_forecasts = _croston_tsb_forecast(all_data, alpha, beta)

    # holdout 구간만 RMSE 계산
    start = len(train)
    errors = [
        (all_forecasts[start + i] - holdout[i]) ** 2
        for i in range(len(holdout))
    ]
    return math.sqrt(sum(errors) / len(errors))


# ── 핵심 최적화 로직 ───────────────────────────────────────────────

def _optimize_item(
    item_cd: str,
    demands: List[float],
) -> Optional[Dict]:
    """
    단일 상품에 대해 그리드 서치 실행.
    반환: {alpha, beta, rmse, data_days, sell_days} 또는 None (데이터 부족)
    """
    total_days = len(demands)
    if total_days < MIN_TRAIN_DAYS + HOLDOUT_DAYS:
        return None

    sell_days = sum(1 for d in demands if d > 0)
    sell_ratio = sell_days / total_days if total_days > 0 else 0

    # 간헐 수요 범위 확인 (0.05 ~ 0.40 사이)
    # 너무 자주 팔리면 Croston 쓸 필요 없고,
    # 거의 안 팔리면 데이터가 너무 희박해 의미 없음
    if sell_ratio < 0.05 or sell_ratio > 0.40:
        return None

    # train / holdout 분리
    train   = demands[:-HOLDOUT_DAYS]
    holdout = demands[-HOLDOUT_DAYS:]

    best_alpha = DEFAULT_ALPHA
    best_beta  = DEFAULT_BETA
    best_rmse  = float("inf")

    for alpha in ALPHA_CANDIDATES:
        for beta in BETA_CANDIDATES:
            rmse = _evaluate(train, holdout, alpha, beta)
            if rmse < best_rmse:
                best_rmse  = rmse
                best_alpha = alpha
                best_beta  = beta

    return {
        "alpha":     best_alpha,
        "beta":      best_beta,
        "rmse":      round(best_rmse, 4),
        "data_days": total_days,
        "sell_days": sell_days,
    }


# ── 매장 단위 실행 ─────────────────────────────────────────────────

def run_optimization(store_id: str) -> Dict:
    """
    매장의 간헐 수요 상품 전체에 대해 Croston 파라미터 최적화 실행.

    Args:
        store_id: 매장 코드

    Returns:
        {
            "total": 처리 시도 상품 수,
            "optimized": 최적화 성공 수,
            "skipped": 데이터 부족/범위 외 수,
            "unchanged": 기존값과 동일해 저장 생략 수,
            "errors": 오류 수,
        }
    """
    db_path = _get_store_db_path(store_id)
    if not db_path.exists():
        logger.error(f"[Croston] DB 없음: {db_path}")
        return {"error": "db_not_found"}

    now_str = datetime.now().isoformat()
    cutoff  = (datetime.now() - timedelta(days=MAX_TRAIN_DAYS)).strftime("%Y-%m-%d")

    stats = {"total": 0, "optimized": 0, "skipped": 0, "unchanged": 0, "errors": 0}

    try:
        # ── Step 1: common.db 에서 intermittent 상품 목록 조회 ──────────
        # product_details 는 common.db 에 있으므로 store DB 와 별도 연결
        common_db_path = Path("data/common.db")
        intermittent_set: set = set()

        if common_db_path.exists():
            try:
                cconn = sqlite3.connect(str(common_db_path))
                cconn.row_factory = sqlite3.Row
                rows = cconn.execute(
                    "SELECT item_cd FROM product_details WHERE demand_pattern = 'intermittent'"
                ).fetchall()
                intermittent_set = {row["item_cd"] for row in rows}
                cconn.close()
                logger.info(f"[Croston] common.db intermittent 상품: {len(intermittent_set)}개")
            except Exception as e:
                logger.warning(f"[Croston] common.db 조회 실패, 전체 상품 대상으로 진행: {e}")
        else:
            logger.warning("[Croston] common.db 없음, 전체 상품 대상으로 진행")

        # ── Step 2: store DB 연결 ────────────────────────────────────────
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)

        # 판매 이력이 있는 상품 목록 (store DB)
        all_item_rows = conn.execute("""
            SELECT DISTINCT item_cd
            FROM daily_sales
            WHERE sales_date >= ?
            ORDER BY item_cd
        """, (cutoff,)).fetchall()
        all_items = [row["item_cd"] for row in all_item_rows]

        # intermittent_set 비어있으면 전체 허용 (common.db 조회 실패 폴백)
        items = [i for i in all_items if i in intermittent_set] if intermittent_set else all_items

        logger.info(
            f"[Croston] 대상 상품 {len(items)}개 (store={store_id}, "
            f"filter={'intermittent' if intermittent_set else 'all'})"
        )

        for item_cd in items:
            stats["total"] += 1
            try:
                # 재최적화 필요 여부 확인
                existing = conn.execute(
                    "SELECT optimized_at, alpha, beta FROM croston_params WHERE item_cd = ?",
                    (item_cd,)
                ).fetchone()

                if existing:
                    last_opt = datetime.fromisoformat(existing["optimized_at"])
                    days_since = (datetime.now() - last_opt).days
                    if days_since < REOPTIMIZE_INTERVAL_DAYS:
                        stats["skipped"] += 1
                        continue

                # 판매 데이터 로딩 (날짜 순서)
                sales_rows = conn.execute("""
                    SELECT sales_date, COALESCE(sale_qty, 0) AS qty
                    FROM daily_sales
                    WHERE item_cd = ? AND sales_date >= ?
                    ORDER BY sales_date ASC
                """, (item_cd, cutoff)).fetchall()

                if len(sales_rows) < MIN_TRAIN_DAYS + HOLDOUT_DAYS:
                    stats["skipped"] += 1
                    continue

                demands = [float(row["qty"]) for row in sales_rows]

                # 최적화 실행
                result = _optimize_item(item_cd, demands)
                if result is None:
                    stats["skipped"] += 1
                    continue

                # 기존값과 동일하면 저장 생략
                if (existing
                        and existing["alpha"] == result["alpha"]
                        and existing["beta"]  == result["beta"]):
                    stats["unchanged"] += 1
                    continue

                # 저장 (UPSERT)
                conn.execute("""
                    INSERT INTO croston_params
                        (item_cd, alpha, beta, rmse, data_days, sell_days,
                         optimized_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_cd) DO UPDATE SET
                        alpha        = excluded.alpha,
                        beta         = excluded.beta,
                        rmse         = excluded.rmse,
                        data_days    = excluded.data_days,
                        sell_days    = excluded.sell_days,
                        optimized_at = excluded.optimized_at,
                        updated_at   = excluded.updated_at
                """, (
                    item_cd,
                    result["alpha"],
                    result["beta"],
                    result["rmse"],
                    result["data_days"],
                    result["sell_days"],
                    now_str,
                    now_str,
                ))
                conn.commit()

                stats["optimized"] += 1
                logger.debug(
                    f"[Croston] {item_cd}: "
                    f"alpha={result['alpha']} beta={result['beta']} "
                    f"rmse={result['rmse']} "
                    f"(sell_ratio={result['sell_days']}/{result['data_days']})"
                )

            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"[Croston] {item_cd} 최적화 실패: {e}")

        conn.close()

    except Exception as e:
        logger.error(f"[Croston] 전체 실행 실패 (store={store_id}): {e}")
        stats["error"] = str(e)

    logger.info(
        f"[Croston] 완료 store={store_id} | "
        f"total={stats['total']} optimized={stats['optimized']} "
        f"skipped={stats['skipped']} unchanged={stats['unchanged']} "
        f"errors={stats['errors']}"
    )
    return stats


# ── base_predictor.py 연결 인터페이스 ──────────────────────────────

def get_croston_params(
    store_id: str,
    item_cd: str,
) -> Tuple[float, float]:
    """
    상품별 최적 (alpha, beta) 반환.
    DB에 없거나 오류 시 기본값 (DEFAULT_ALPHA, DEFAULT_BETA) 반환.

    base_predictor.py 에서 이렇게 사용:
        from src.analysis.croston_optimizer import get_croston_params
        alpha, beta = get_croston_params(self.store_id, item_cd)
    """
    try:
        db_path = _get_store_db_path(store_id)
        if not db_path.exists():
            return DEFAULT_ALPHA, DEFAULT_BETA

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT alpha, beta FROM croston_params WHERE item_cd = ?",
            (item_cd,)
        ).fetchone()
        conn.close()

        if row:
            return float(row[0]), float(row[1])
        return DEFAULT_ALPHA, DEFAULT_BETA

    except Exception:
        return DEFAULT_ALPHA, DEFAULT_BETA


# ── 통계 조회 (대시보드용) ─────────────────────────────────────────

def get_optimization_summary(store_id: str) -> Dict:
    """
    최적화 결과 요약 반환.
    {total_items, avg_alpha, avg_beta, avg_rmse, last_run}
    """
    try:
        db_path = _get_store_db_path(store_id)
        if not db_path.exists():
            return {}

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("""
            SELECT
                COUNT(*)        AS total_items,
                ROUND(AVG(alpha), 3)  AS avg_alpha,
                ROUND(AVG(beta),  3)  AS avg_beta,
                ROUND(AVG(rmse),  4)  AS avg_rmse,
                MAX(optimized_at)     AS last_run
            FROM croston_params
        """).fetchone()
        conn.close()

        if row:
            return {
                "total_items": row[0],
                "avg_alpha":   row[1],
                "avg_beta":    row[2],
                "avg_rmse":    row[3],
                "last_run":    row[4],
            }
        return {}

    except Exception as e:
        logger.debug(f"[Croston] summary 조회 실패: {e}")
        return {}


# ── CLI (직접 실행 테스트용) ───────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Croston alpha/beta 최적화")
    parser.add_argument("--store", "-s", required=True, help="매장 코드")
    parser.add_argument("--summary", action="store_true", help="결과 요약 출력")
    args = parser.parse_args()

    if args.summary:
        summary = get_optimization_summary(args.store)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        result = run_optimization(args.store)
        print(json.dumps(result, ensure_ascii=False, indent=2))
