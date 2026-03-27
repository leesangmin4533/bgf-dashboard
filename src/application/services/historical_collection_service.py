"""
HistoricalCollectionService -- 과거 매출 데이터 수집 서비스

새 매장 온보딩 후 과거 6개월 매출 데이터를 BGF 사이트에서 수집합니다.
archive/collect_6months_dongyang.py 를 범용화한 버전입니다.

주요 특징:
- store_id를 파라미터로 받음 (하드코딩 없음)
- SalesCollector + SalesRepository 패턴 (레거시 미사용)
- 중복 날짜 자동 스킵 (filter_already_collected)
- 30일 배치 단위, 배치 간 30초 대기
- 백그라운드 스레드에서 안전하게 실행 가능
"""

import time
from datetime import datetime, timedelta
from typing import List, Dict, Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 설정 상수
DEFAULT_MONTHS = 6
BATCH_SIZE = 30          # 한 번에 수집할 날짜 수
BATCH_DELAY_SECONDS = 30  # 배치 간 대기 시간


def collect_historical_sales(store_id: str, months: int = DEFAULT_MONTHS) -> Dict[str, Any]:
    """과거 매출 데이터 수집 (범용)

    BGF 사이트에 로그인하여 과거 N개월 매출 데이터를 수집하고 DB에 저장합니다.
    이미 수집된 날짜는 자동으로 스킵합니다.

    백그라운드 스레드에서 호출해도 안전합니다.

    Args:
        store_id: 매장 코드 (예: "46513")
        months: 수집할 과거 개월 수 (기본 6개월)

    Returns:
        수집 결과 통계 {total_dates, success, failed, skipped, success_rate, elapsed_minutes}
    """
    logger.info(
        "[HISTORICAL] 과거 데이터 수집 시작: store=%s, months=%d",
        store_id, months,
    )
    start_time = time.time()

    try:
        # 1. 수집 대상 날짜 생성
        all_dates = _generate_date_list(months)

        # 2. 이미 수집된 날짜 제외
        uncollected = _filter_already_collected(all_dates, store_id)
        skipped = len(all_dates) - len(uncollected)

        if not uncollected:
            logger.info("[HISTORICAL] store=%s 모든 날짜 수집 완료 (스킵 %d일)", store_id, skipped)
            return {
                "total_dates": len(all_dates),
                "success": 0,
                "failed": 0,
                "skipped": skipped,
                "success_rate": 100.0,
                "elapsed_minutes": 0.0,
            }

        logger.info(
            "[HISTORICAL] store=%s 수집 대상: %d일 (스킵: %d일, 예상 소요: %.1f분)",
            store_id, len(uncollected), skipped, len(uncollected) * 2 / 60,
        )

        # 3. 배치 수집
        success, failed = _collect_in_batches(uncollected, store_id)

        elapsed = (time.time() - start_time) / 60
        total = len(all_dates)
        result = {
            "total_dates": total,
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "success_rate": (success / len(uncollected) * 100) if uncollected else 100.0,
            "elapsed_minutes": round(elapsed, 1),
        }

        logger.info(
            "[HISTORICAL] store=%s 수집 완료: 성공=%d, 실패=%d, 스킵=%d (%.1f분)",
            store_id, success, failed, skipped, elapsed,
        )
        return result

    except Exception as e:
        elapsed = (time.time() - start_time) / 60
        logger.error(
            "[HISTORICAL] store=%s 수집 중 오류: %s (%.1f분 경과)",
            store_id, e, elapsed,
            exc_info=True,
        )
        return {
            "total_dates": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "success_rate": 0.0,
            "elapsed_minutes": round(elapsed, 1),
            "error": str(e),
        }


def _generate_date_list(months: int) -> List[str]:
    """수집할 날짜 리스트 생성 (어제부터 과거 N개월)

    Args:
        months: 수집할 개월 수

    Returns:
        날짜 리스트 (YYYY-MM-DD 형식, 과거→현재 순서)
    """
    end_date = datetime.now().date() - timedelta(days=1)  # 어제까지
    start_date = end_date - timedelta(days=30 * months)

    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    logger.info("[HISTORICAL] 날짜 범위: %s ~ %s (%d일)", start_date, end_date, len(dates))
    return dates


def _filter_already_collected(dates: List[str], store_id: str) -> List[str]:
    """이미 수집된 날짜 제외

    Args:
        dates: 전체 날짜 리스트
        store_id: 매장 코드

    Returns:
        아직 수집되지 않은 날짜 리스트
    """
    from src.infrastructure.database.repos.sales_repo import SalesRepository

    repo = SalesRepository(store_id=store_id)
    collected = set(repo.get_collected_dates(days=365, store_id=store_id))
    uncollected = [d for d in dates if d not in collected]

    logger.info(
        "[HISTORICAL] store=%s 미수집: %d일 / 전체 %d일",
        store_id, len(uncollected), len(dates),
    )
    return uncollected


def _make_save_callback(store_id: str):
    """배치 저장 콜백 생성

    Args:
        store_id: 매장 코드

    Returns:
        save_callback(date_str, data) -> stats
    """
    from src.infrastructure.database.repos.sales_repo import SalesRepository

    def save_batch_data(date_str: str, data: List[Dict[str, Any]]) -> Dict[str, int]:
        """수집 데이터를 매장 DB에 저장"""
        repo = SalesRepository(store_id=store_id)

        # 컬럼명 대문자 정규화 (SalesCollector 출력 호환)
        formatted = []
        for item in data:
            formatted.append({
                "ITEM_CD": item.get("item_cd") or item.get("ITEM_CD", ""),
                "ITEM_NM": item.get("item_nm") or item.get("ITEM_NM", ""),
                "MID_CD": item.get("mid_cd") or item.get("MID_CD", ""),
                "MID_NM": item.get("mid_nm") or item.get("MID_NM", ""),
                "SALE_QTY": item.get("sale_qty") or item.get("SALE_QTY", 0),
                "ORD_QTY": item.get("ord_qty") or item.get("ORD_QTY", 0),
                "BUY_QTY": item.get("buy_qty") or item.get("BUY_QTY", 0),
                "DISUSE_QTY": item.get("disuse_qty") or item.get("DISUSE_QTY", 0),
                "STOCK_QTY": item.get("stock_qty") or item.get("STOCK_QTY", 0),
            })

        stats = repo.save_daily_sales(
            sales_data=formatted,
            sales_date=date_str,
            store_id=store_id,
            collected_at=datetime.now().isoformat(),
        )
        return stats

    return save_batch_data


def _collect_in_batches(dates: List[str], store_id: str) -> tuple:
    """배치 단위로 데이터 수집

    Args:
        dates: 수집할 날짜 리스트
        store_id: 매장 코드

    Returns:
        (success_count, failed_count) 튜플
    """
    from src.collectors.sales_collector import SalesCollector

    total_success = 0
    total_failed = 0
    save_callback = _make_save_callback(store_id)

    # 배치로 분할
    batches = [dates[i:i + BATCH_SIZE] for i in range(0, len(dates), BATCH_SIZE)]

    logger.info(
        "[HISTORICAL] store=%s %d개 배치 시작 (배치당 최대 %d일)",
        store_id, len(batches), BATCH_SIZE,
    )

    for batch_idx, batch_dates in enumerate(batches, 1):
        logger.info(
            "[HISTORICAL] store=%s 배치 %d/%d (%d일: %s ~ %s)",
            store_id, batch_idx, len(batches),
            len(batch_dates), batch_dates[0], batch_dates[-1],
        )

        collector = SalesCollector(store_id=store_id)
        try:
            results = collector.collect_multiple_dates(
                dates=batch_dates,
                save_callback=save_callback,
            )

            batch_success = sum(1 for r in results.values() if r.get("success"))
            batch_failed = len(batch_dates) - batch_success
            total_success += batch_success
            total_failed += batch_failed

            logger.info(
                "[HISTORICAL] store=%s 배치 %d 완료: 성공=%d, 실패=%d",
                store_id, batch_idx, batch_success, batch_failed,
            )

            # 배치 간 대기 (마지막 배치 제외)
            if batch_idx < len(batches):
                logger.info(
                    "[HISTORICAL] store=%s 다음 배치 전 %d초 대기",
                    store_id, BATCH_DELAY_SECONDS,
                )
                time.sleep(BATCH_DELAY_SECONDS)

        except Exception as e:
            logger.error(
                "[HISTORICAL] store=%s 배치 %d 실패: %s",
                store_id, batch_idx, e,
                exc_info=True,
            )
            total_failed += len(batch_dates)

        finally:
            collector.close()

    return total_success, total_failed
