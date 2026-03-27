"""
PaydayAnalyzer — 매출 데이터 기반 급여일 효과 구간 자동 감지

Phase A-3 개선: 하드코딩(10일/25일) → 실제 매출에서 통계적으로 튀는 구간 자동 탐지

동작 방식:
  1. daily_sales 에서 90일치 일별 총매출 집계
  2. 휴일·프로모션 날짜 제외 (외부 요인 오염 제거)
  3. 월의 '일(day)' 기준으로 그룹화 → 평균·표준편차 계산
  4. 평균 + 1σ 이상인 날짜 = 고매출 구간(boost)
  5. 평균 - 0.5σ 이하인 날짜 = 저매출 구간(decline)
  6. 연속된 날짜를 하나의 구간으로 묶기
  7. 결과를 external_factors 에 저장 (factor_type='payday')

저장 스키마:
  factor_date = 분석 실행월 1일 (예: '2026-03-01')
  factor_type = 'payday'
  factor_key  = 'boost_days'    → JSON 리스트 "[10, 11, 25, 26]"
  factor_key  = 'decline_days'  → JSON 리스트 "[28, 29, 30, 31]"
  factor_key  = 'analyzed_at'   → ISO 타임스탬프

실행 주기: 주 1회 (일요일 배치) — 패턴은 빠르게 변하지 않음
"""

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple

from src.utils.logger import get_logger
from src.infrastructure.database.repos import ExternalFactorRepository

logger = get_logger(__name__)

# 분석에 필요한 최소 데이터 일수 (부족하면 폴백)
MIN_DAYS_REQUIRED = 60

# 고매출 구간 감지 임계값 (평균 + N*σ)
BOOST_SIGMA_THRESHOLD = 1.0
# 저매출 구간 감지 임계값 (평균 - N*σ)
DECLINE_SIGMA_THRESHOLD = 0.5


class PaydayAnalyzer:
    """매출 패턴 기반 급여일 효과 구간 자동 감지 및 저장"""

    def __init__(self, store_id: str, db_path: str):
        """
        Args:
            store_id : 점포 코드
            db_path  : 매장 DB 경로 (daily_sales 가 있는 store DB)
        """
        self.store_id = store_id
        self.db_path = db_path

    # =========================================================================
    # Public API
    # =========================================================================

    def analyze(self, lookback_days: int = 90) -> Dict:
        """분석 실행 및 external_factors 저장

        Args:
            lookback_days: 분석에 사용할 과거 일수 (기본 90일)

        Returns:
            {
                "boost_days":   [10, 11, 25, 26],   # 고매출 구간
                "decline_days": [28, 29, 30, 31],   # 저매출 구간
                "data_days":    87,                  # 실제 사용된 데이터 일수
                "status":       "ok" | "fallback"   # 결과 상태
            }
        """
        try:
            daily_totals = self._load_daily_totals(lookback_days)
            holiday_dates = self._load_holiday_dates(lookback_days)

            # 휴일·프로모션 날짜 제외
            clean_totals = {
                d: v for d, v in daily_totals.items()
                if d not in holiday_dates
            }

            if len(clean_totals) < MIN_DAYS_REQUIRED:
                logger.warning(
                    f"[PaydayAnalyzer] 데이터 부족 ({len(clean_totals)}일 < "
                    f"{MIN_DAYS_REQUIRED}일) — 폴백 유지"
                )
                return {"boost_days": [], "decline_days": [],
                        "data_days": len(clean_totals), "status": "fallback"}

            day_groups = self._group_by_day_of_month(clean_totals)
            day_avgs = self._calc_day_averages(day_groups)
            boost, decline = self._detect_payday_windows(day_avgs)

            self._save_to_db(boost, decline, len(clean_totals))

            logger.info(
                f"[PaydayAnalyzer] 완료 — "
                f"boost={boost}, decline={decline}, "
                f"data={len(clean_totals)}일"
            )
            return {
                "boost_days": boost,
                "decline_days": decline,
                "data_days": len(clean_totals),
                "status": "ok",
            }

        except Exception as e:
            logger.error(f"[PaydayAnalyzer] 분석 실패: {e}", exc_info=True)
            return {"boost_days": [], "decline_days": [],
                    "data_days": 0, "status": "error"}

    # =========================================================================
    # Step 1: 데이터 로드
    # =========================================================================

    def _load_daily_totals(self, lookback_days: int) -> Dict[date, float]:
        """daily_sales 에서 일별 총매출(sale_qty 합산) 집계"""
        cutoff = (datetime.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        result: Dict[date, float] = {}

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            rows = conn.execute("""
                SELECT sales_date, SUM(sale_qty) AS total_qty
                FROM   daily_sales
                WHERE  sales_date >= ?
                  AND  sale_qty   > 0
                GROUP  BY sales_date
                ORDER  BY sales_date
            """, (cutoff,)).fetchall()

            conn.close()

            for row in rows:
                d = datetime.strptime(row["sales_date"], "%Y-%m-%d").date()
                result[d] = float(row["total_qty"])

        except Exception as e:
            logger.error(f"[PaydayAnalyzer] daily_sales 조회 실패: {e}")

        return result

    def _load_holiday_dates(self, lookback_days: int) -> set:
        """external_factors 에서 휴일 날짜 집합 반환"""
        holiday_dates = set()
        try:
            repo = ExternalFactorRepository()

            check_date = datetime.today() - timedelta(days=lookback_days)
            today = datetime.today()

            while check_date <= today:
                date_str = check_date.strftime("%Y-%m-%d")
                factors = repo.get_factors(date_str, factor_type='calendar')
                if factors:
                    fmap = {f['factor_key']: f['factor_value'] for f in factors}
                    if fmap.get('is_holiday', 'false').lower() in ('true', '1'):
                        holiday_dates.add(check_date.date())
                    if fmap.get('is_pre_holiday', 'false').lower() in ('true', '1'):
                        holiday_dates.add(check_date.date())
                check_date += timedelta(days=1)

        except Exception as e:
            logger.warning(f"[PaydayAnalyzer] 휴일 로드 실패 (제외 없이 진행): {e}")

        return holiday_dates

    # =========================================================================
    # Step 2: 그룹화 및 통계
    # =========================================================================

    def _group_by_day_of_month(
        self, daily_totals: Dict[date, float]
    ) -> Dict[int, List[float]]:
        """날짜 → {일(1~31): [매출값, ...]} 그룹화"""
        groups: Dict[int, List[float]] = defaultdict(list)
        for d, val in daily_totals.items():
            groups[d.day].append(val)
        return dict(groups)

    def _calc_day_averages(
        self, day_groups: Dict[int, List[float]]
    ) -> Dict[int, float]:
        """각 일(day)의 평균 매출 계산"""
        return {
            day: sum(vals) / len(vals)
            for day, vals in day_groups.items()
            if vals  # 빈 리스트 방어
        }

    # =========================================================================
    # Step 3: 고매출 / 저매출 구간 감지
    # =========================================================================

    def _detect_payday_windows(
        self, day_avgs: Dict[int, float]
    ) -> Tuple[List[int], List[int]]:
        """평균·표준편차 기반으로 boost / decline 날짜 목록 반환

        Returns:
            (boost_days, decline_days) — 각각 정렬된 일(day) 리스트
        """
        if not day_avgs:
            return [], []

        values = list(day_avgs.values())
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        sigma = variance ** 0.5

        boost_threshold = mean + BOOST_SIGMA_THRESHOLD * sigma
        decline_threshold = mean - DECLINE_SIGMA_THRESHOLD * sigma

        raw_boost = sorted(d for d, v in day_avgs.items() if v >= boost_threshold)
        raw_decline = sorted(d for d, v in day_avgs.items() if v <= decline_threshold)

        # 연속 날짜 묶음 로그 (디버깅용)
        logger.debug(
            f"[PaydayAnalyzer] mean={mean:.1f}, σ={sigma:.1f}, "
            f"boost_thr={boost_threshold:.1f}, decline_thr={decline_threshold:.1f}"
        )
        logger.debug(f"[PaydayAnalyzer] raw_boost={raw_boost}, raw_decline={raw_decline}")

        return raw_boost, raw_decline

    # =========================================================================
    # Step 4: DB 저장
    # =========================================================================

    def _save_to_db(
        self, boost_days: List[int], decline_days: List[int], data_days: int
    ) -> None:
        """external_factors 에 분석 결과 저장

        factor_date = 현재 월의 1일 (예: 2026-03-01)
        factor_type = 'payday'
        """
        # 분석 결과는 '이번 달' 기준으로 저장 — 매월 갱신
        analysis_month = datetime.today().replace(day=1).strftime("%Y-%m-%d")

        try:
            repo = ExternalFactorRepository()

            entries = [
                ("boost_days", json.dumps(boost_days)),
                ("decline_days", json.dumps(decline_days)),
                ("data_days", str(data_days)),
                ("analyzed_at", datetime.now().isoformat()),
            ]

            for key, value in entries:
                repo.save_factor(
                    factor_date=analysis_month,
                    factor_type='payday',
                    factor_key=key,
                    factor_value=value,
                    store_id=self.store_id,
                )

            logger.info(
                f"[PaydayAnalyzer] 저장 완료 — "
                f"date={analysis_month}, boost={boost_days}, decline={decline_days}"
            )

        except Exception as e:
            logger.error(f"[PaydayAnalyzer] DB 저장 실패: {e}")
