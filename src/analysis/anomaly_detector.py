"""
D-2: 이상치 탐지 + 카카오 알림 (anomaly_detector.py)

실행 시점: Phase 1.0 (매출 수집) 직후
목적: 판매량/재고 이상 징후를 당일 감지 → 카카오 알림

감지 조건 3가지:
  1. 판매 급증  — sale_qty > 평균 + 3σ  (30일 이력 기준)
  2. 연속 미판매 — 비푸드 상품 연속 3일 sale_qty = 0  (재고 있는데 안 팔림)
  3. 재고 급감  — stock_qty 전일 대비 50%+ 감소  (예기치 않은 재고 소진)

사용법 (daily_job.py Phase 1.0 직후):
    from src.analysis.anomaly_detector import run_anomaly_detection
    anomaly_result = run_anomaly_detection(self.store_id, today_str)
    if anomaly_result.has_alerts:
        notifier.send_message(anomaly_result.format_kakao())
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 감지 파라미터 ──────────────────────────────────────────────────
SPIKE_SIGMA          = 3.0    # 판매 급증: 평균 + N × σ 초과
SPIKE_MIN_MEAN       = 1.0    # 평균 1개 미만 상품은 급증 감지 제외 (노이즈 방지)
SPIKE_LOOKBACK_DAYS  = 30     # 기준 이력 일수

ZERO_CONSECUTIVE_DAYS = 3     # 연속 미판매 감지 일수
ZERO_MIN_STOCK        = 1     # 재고 있는데 안 팔릴 때만 감지 (재고 0이면 품절이므로 제외)

STOCK_DROP_RATE      = 0.50   # 재고 급감: 전일 대비 N% 이상 감소
STOCK_DROP_MIN_QTY   = 5      # 전일 재고 최소 N개 이상일 때만 감지 (소량은 노이즈)

# 푸드 mid_cd (연속 미판매 감지 제외 — 유통기한 관리로 0일이 자연스러움)
FOOD_MID_CDS = {"001", "002", "003", "004", "005", "012"}

_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ── 데이터 클래스 ──────────────────────────────────────────────────

@dataclass
class AnomalyAlert:
    """개별 이상치 알림"""
    alert_type: str          # "spike" | "zero_streak" | "stock_drop"
    item_cd:    str
    item_nm:    str
    mid_cd:     str
    detail:     str          # 사람이 읽을 수 있는 설명
    severity:   str = "warn" # "warn" | "info"


@dataclass
class AnomalyResult:
    """run_anomaly_detection() 반환값"""
    run_at:     str = ""
    store_id:   str = ""
    target_date: str = ""
    spike_count:  int = 0
    zero_count:   int = 0
    stock_count:  int = 0
    error_count:  int = 0
    alerts: List[AnomalyAlert] = field(default_factory=list)
    error_msg: str = ""

    @property
    def has_alerts(self) -> bool:
        return len(self.alerts) > 0

    def format_kakao(self) -> str:
        """카카오톡 전송용 메시지 포맷"""
        total = len(self.alerts)
        lines = [f"[이상치 탐지] {total}건 — {self.target_date}\n"]
        for a in self.alerts:
            icon = "\U0001f534" if a.severity == "warn" else "\U0001f7e1"
            lines.append(f"{icon} {a.detail}")
        lines.append(f"\n※ 발주 전 확인 권장")
        return "\n".join(lines)


# ── DB 유틸리티 ────────────────────────────────────────────────────

def _get_conn(store_id: str):
    from src.infrastructure.database.connection import DBRouter
    conn = DBRouter.get_store_connection(store_id)
    conn.row_factory = sqlite3.Row
    return conn


def _attach_common(conn) -> None:
    common_path = _PROJECT_ROOT / "data" / "common.db"
    conn.execute(f"ATTACH DATABASE '{common_path}' AS common")


def _detach_common(conn) -> None:
    try:
        conn.execute("DETACH DATABASE common")
    except Exception:
        pass


# ── 감지 함수 ──────────────────────────────────────────────────────

def _detect_spikes(conn, target_date: str) -> List[AnomalyAlert]:
    """
    판매 급증 감지.
    target_date 판매량 > 과거 30일 평균 + 3σ
    평균 < SPIKE_MIN_MEAN 인 상품은 제외 (저판매 노이즈 방지)
    """
    cutoff = (datetime.strptime(target_date, "%Y-%m-%d")
              - timedelta(days=SPIKE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # 과거 이력 집계
    hist_rows = conn.execute("""
        SELECT item_cd, mid_cd,
               AVG(CAST(sale_qty AS REAL))  AS avg_qty,
               -- SQLite에는 STDDEV 없음 → 분산 수동 계산
               AVG(CAST(sale_qty AS REAL) * CAST(sale_qty AS REAL))
                   - AVG(CAST(sale_qty AS REAL)) * AVG(CAST(sale_qty AS REAL))
                                                AS variance
        FROM daily_sales
        WHERE sales_date >= ? AND sales_date < ?
          AND sale_qty IS NOT NULL
        GROUP BY item_cd, mid_cd
        HAVING COUNT(*) >= 7   -- 최소 7일 이력 있어야 통계 의미 있음
    """, (cutoff, target_date)).fetchall()

    if not hist_rows:
        return []

    # 오늘 판매량
    today_rows = conn.execute("""
        SELECT item_cd, sale_qty
        FROM daily_sales
        WHERE sales_date = ?
          AND sale_qty   IS NOT NULL
    """, (target_date,)).fetchall()

    today_map = {row["item_cd"]: int(row["sale_qty"]) for row in today_rows}

    # 상품명 조회
    item_nms = _get_item_names(conn, list(today_map.keys()))

    alerts = []
    for h in hist_rows:
        item_cd  = h["item_cd"]
        avg_qty  = float(h["avg_qty"] or 0)
        variance = float(h["variance"] or 0)

        if avg_qty < SPIKE_MIN_MEAN:
            continue
        if item_cd not in today_map:
            continue

        sigma    = math.sqrt(max(variance, 0))
        today_qty = today_map[item_cd]
        threshold = avg_qty + SPIKE_SIGMA * sigma

        if today_qty > threshold:
            item_nm = item_nms.get(item_cd, item_cd)
            alerts.append(AnomalyAlert(
                alert_type = "spike",
                item_cd    = item_cd,
                item_nm    = item_nm,
                mid_cd     = h["mid_cd"] or "",
                detail     = (
                    f"판매 급증: {item_nm} "
                    f"{today_qty}개 (평균 {avg_qty:.1f}개, "
                    f"{(today_qty - avg_qty) / sigma:.1f}σ)"
                ) if sigma > 0 else (
                    f"판매 급증: {item_nm} {today_qty}개 (평균 {avg_qty:.1f}개)"
                ),
                severity = "warn",
            ))

    return alerts


def _detect_zero_streak(conn, target_date: str) -> List[AnomalyAlert]:
    """
    연속 미판매 감지 (비푸드만).
    최근 ZERO_CONSECUTIVE_DAYS 일 연속 sale_qty = 0
    단, 재고가 있는데 안 팔리는 경우만 (재고 0이면 품절 → 정상)
    """
    start = (datetime.strptime(target_date, "%Y-%m-%d")
             - timedelta(days=ZERO_CONSECUTIVE_DAYS - 1)).strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT item_cd, mid_cd,
               COUNT(*)                          AS day_count,
               SUM(CASE WHEN sale_qty = 0 THEN 1 ELSE 0 END) AS zero_days,
               MAX(stock_qty)                    AS max_stock
        FROM daily_sales
        WHERE sales_date >= ? AND sales_date <= ?
          AND sale_qty IS NOT NULL
        GROUP BY item_cd, mid_cd
        HAVING day_count = ?
           AND zero_days = ?
           AND max_stock >= ?
    """, (start, target_date,
          ZERO_CONSECUTIVE_DAYS, ZERO_CONSECUTIVE_DAYS,
          ZERO_MIN_STOCK)).fetchall()

    item_nms = _get_item_names(conn, [r["item_cd"] for r in rows])

    alerts = []
    for row in rows:
        mid_cd = row["mid_cd"] or ""
        if mid_cd in FOOD_MID_CDS:
            continue   # 푸드류는 제외

        item_cd = row["item_cd"]
        item_nm = item_nms.get(item_cd, item_cd)
        alerts.append(AnomalyAlert(
            alert_type = "zero_streak",
            item_cd    = item_cd,
            item_nm    = item_nm,
            mid_cd     = mid_cd,
            detail     = (
                f"연속 미판매: {item_nm} "
                f"{ZERO_CONSECUTIVE_DAYS}일째 0개 "
                f"(재고 {row['max_stock']}개 있음)"
            ),
            severity = "warn",
        ))

    return alerts


def _detect_stock_drop(conn, target_date: str) -> List[AnomalyAlert]:
    """
    재고 급감 감지.
    전일 stock_qty 대비 당일 stock_qty 가 STOCK_DROP_RATE 이상 감소
    전일 재고 STOCK_DROP_MIN_QTY 개 이상일 때만 감지
    """
    yesterday = (datetime.strptime(target_date, "%Y-%m-%d")
                 - timedelta(days=1)).strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT t.item_cd,  t.mid_cd,
               y.stock_qty AS prev_stock,
               t.stock_qty AS curr_stock
        FROM daily_sales t
        JOIN daily_sales y
          ON t.item_cd    = y.item_cd
         AND y.sales_date = ?
        WHERE t.sales_date = ?
          AND t.stock_qty IS NOT NULL
          AND y.stock_qty IS NOT NULL
          AND y.stock_qty >= ?
          AND t.stock_qty < y.stock_qty * (1 - ?)
    """, (yesterday, target_date,
          STOCK_DROP_MIN_QTY, STOCK_DROP_RATE)).fetchall()

    item_nms = _get_item_names(conn, [r["item_cd"] for r in rows])

    alerts = []
    for row in rows:
        item_cd    = row["item_cd"]
        item_nm    = item_nms.get(item_cd, item_cd)
        prev_stock = int(row["prev_stock"])
        curr_stock = int(row["curr_stock"])
        drop_pct   = int((prev_stock - curr_stock) / prev_stock * 100)

        alerts.append(AnomalyAlert(
            alert_type = "stock_drop",
            item_cd    = item_cd,
            item_nm    = item_nm,
            mid_cd     = row["mid_cd"] or "",
            detail     = (
                f"재고 급감: {item_nm} "
                f"{prev_stock}→{curr_stock}개 (-{drop_pct}%)"
            ),
            severity = "warn",
        ))

    return alerts


def _get_item_names(conn, item_cds: List[str]) -> Dict[str, str]:
    """common.products 에서 상품명 조회"""
    if not item_cds:
        return {}

    placeholders = ",".join("?" * len(item_cds))
    # common.products 에서 우선 조회
    try:
        rows = conn.execute(
            f"SELECT item_cd, item_nm FROM common.products "
            f"WHERE item_cd IN ({placeholders})",
            item_cds
        ).fetchall()
        return {row["item_cd"]: row["item_nm"] for row in rows}
    except Exception:
        # ATTACH 안 된 경우 폴백
        return {icd: icd for icd in item_cds}


# ── 메인 실행 ──────────────────────────────────────────────────────

def run_anomaly_detection(store_id: str, target_date: str) -> AnomalyResult:
    """
    Phase 1.0 직후 호출.
    3가지 조건 탐지 후 AnomalyResult 반환.

    Args:
        store_id:    매장 코드
        target_date: 탐지 기준 날짜 (YYYY-MM-DD), 보통 today_str

    Returns:
        AnomalyResult — has_alerts, format_kakao() 포함
    """
    result = AnomalyResult(
        run_at      = datetime.now().isoformat(),
        store_id    = store_id,
        target_date = target_date,
    )

    try:
        conn = _get_conn(store_id)
        _attach_common(conn)

        # 1. 판매 급증
        try:
            spikes = _detect_spikes(conn, target_date)
            result.alerts.extend(spikes)
            result.spike_count = len(spikes)
        except Exception as e:
            result.error_count += 1
            logger.warning(f"[D-2] spike 감지 실패: {e}")

        # 2. 연속 미판매
        try:
            zeros = _detect_zero_streak(conn, target_date)
            result.alerts.extend(zeros)
            result.zero_count = len(zeros)
        except Exception as e:
            result.error_count += 1
            logger.warning(f"[D-2] zero_streak 감지 실패: {e}")

        # 3. 재고 급감
        try:
            drops = _detect_stock_drop(conn, target_date)
            result.alerts.extend(drops)
            result.stock_count = len(drops)
        except Exception as e:
            result.error_count += 1
            logger.warning(f"[D-2] stock_drop 감지 실패: {e}")

        _detach_common(conn)
        conn.close()

    except Exception as e:
        result.error_msg = str(e)
        logger.error(f"[D-2] 전체 실행 실패 (store={store_id}): {e}")

    logger.info(
        f"[D-2] 완료 | store={store_id} date={target_date} "
        f"spike={result.spike_count} zero={result.zero_count} "
        f"stock={result.stock_count} alerts={len(result.alerts)}"
    )

    return result
