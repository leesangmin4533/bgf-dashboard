"""
fix_expired_batch_remaining.py
──────────────────────────────
inventory_batches 테이블에서 status='expired' 인데
remaining_qty > 0 인 유령 배치를 정리.

daily_job.py Phase 1.65 (Stale Stock Cleanup) 직후에 호출 권장.

사용법 (단독 실행):
    python fix_expired_batch_remaining.py
    python fix_expired_batch_remaining.py --dry-run
    python fix_expired_batch_remaining.py --store-id 46513

daily_job.py 연결:
    from fix_expired_batch_remaining import cleanup_expired_batches
    cleaned = cleanup_expired_batches(store_id)
    logger.info(f"[Phase 1.65] 만료배치 정리: {cleaned}건")
"""

import sqlite3
import argparse
import logging
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_DIR   = BASE_DIR / "data" / "stores"


def cleanup_expired_batches(store_id: str, dry_run: bool = False) -> int:
    """
    status='expired' AND remaining_qty > 0 인 배치 → remaining_qty = 0

    Returns:
        정리된 행 수
    """
    db_path = DB_DIR / f"{store_id}.db"
    if not db_path.exists():
        log.error(f"[{store_id}] DB 없음: {db_path}")
        return 0

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        # 정리 대상 확인
        cur = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(remaining_qty), 0)
            FROM inventory_batches
            WHERE status = 'expired'
              AND remaining_qty > 0
        """)
        count, total_qty = cur.fetchone()

        if count == 0:
            log.info(f"[{store_id}] 만료 배치 유령 재고 없음")
            return 0

        log.info(
            f"[{store_id}] 만료 배치 정리 대상: {count}건 "
            f"(잔여수량 합계: {int(total_qty)})"
        )

        if dry_run:
            log.info(f"[{store_id}] DRY-RUN: 실제 변경 없음")
            return count

        conn.execute("""
            UPDATE inventory_batches
            SET remaining_qty = 0,
                updated_at    = datetime('now', 'localtime')
            WHERE status = 'expired'
              AND remaining_qty > 0
        """)
        conn.commit()
        log.info(f"[{store_id}] 만료 배치 정리 완료: {count}건")
        return count

    except Exception as e:
        log.error(f"[{store_id}] 만료 배치 정리 실패: {e}")
        conn.rollback()
        return 0

    finally:
        conn.close()


# ── 단독 실행 ────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="만료 배치 유령 재고 정리")
    parser.add_argument("--store-id", help="특정 매장만 (미지정 시 전체)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.store_id:
        targets = [args.store_id]
    else:
        targets = [p.stem for p in DB_DIR.glob("*.db")
                   if not p.stem.startswith("common")
                   and "_backup" not in p.stem]

    total = 0
    for sid in targets:
        total += cleanup_expired_batches(sid, dry_run=args.dry_run)

    log.info(f"=== 전체 정리: {total}건 ===")


if __name__ == "__main__":
    main()
