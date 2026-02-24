"""order_tracking expiry_time 보정 + 유령 항목 정리

문제:
1. 수집 실패 기간(2/8~2/14) order_tracking에 expiry_time이 '23:59:59' 기본값으로 저장됨
   → 실제 폐기 시간(02:00, 10:00, 14:00, 22:00, 00:00)으로 보정
2. realtime_inventory.stock_qty=0인데 order_tracking.remaining_qty>0인 유령 항목
   → remaining_qty=0, status='consumed'으로 정리

보정 규칙:
  - 1차(상품명 끝 '1'): 도시락/주먹밥/김밥 → order_date+1일 02:00, 샌드위치/햄버거 → order_date 22:00
  - 2차(상품명 끝 '2'): 도시락/주먹밥/김밥 → order_date 14:00, 샌드위치/햄버거 → order_date+1일 10:00
  - 빵(012): order_date + expiration_days일 00:00
  - 기타: product_details.expiration_days 기반 계산
"""

import sqlite3
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

sys.stdout.reconfigure(encoding='utf-8')


# 카테고리별 폐기 시간 규칙
FOOD_EXPIRY_RULES = {
    # mid_cd → {차수: (day_offset, hour)}
    # 1차: 당일 20:00 도착
    # 2차: 익일 07:00 도착
    "001": {"1": (1, 2), "2": (0, 14)},    # 도시락: 1차→익일02:00, 2차→당일14:00
    "002": {"1": (1, 2), "2": (0, 14)},    # 주먹밥
    "003": {"1": (1, 2), "2": (0, 14)},    # 김밥
    "004": {"1": (0, 22), "2": (1, 10)},   # 샌드위치: 1차→당일22:00, 2차→익일10:00
    "005": {"1": (0, 22), "2": (1, 10)},   # 햄버거
}


def get_delivery_suffix(item_nm: str) -> str:
    """상품명에서 차수 추출 (끝자리 1 or 2)"""
    if not item_nm:
        return ""
    last = item_nm.strip()[-1]
    return last if last in ("1", "2") else ""


def calc_correct_expiry(item_nm: str, mid_cd: str, order_date: str,
                        expiration_days: int = 0) -> str:
    """정확한 expiry_time 계산

    Args:
        item_nm: 상품명
        mid_cd: 중분류 코드
        order_date: 발주일 (YYYY-MM-DD)
        expiration_days: product_details의 유통기한(일)

    Returns:
        'YYYY-MM-DD HH:MM' 형식의 정확한 폐기 시간
    """
    try:
        od = datetime.strptime(order_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""

    suffix = get_delivery_suffix(item_nm)

    # 푸드류 (001~005): 차수별 규칙
    if mid_cd in FOOD_EXPIRY_RULES and suffix in ("1", "2"):
        day_offset, hour = FOOD_EXPIRY_RULES[mid_cd][suffix]
        # 1차: 당일 도착이므로 order_date 기준
        # 2차: 익일 도착이므로 order_date+1 기준
        if suffix == "1":
            base = od  # 1차 발주일 당일 도착
        else:
            base = od  # 2차도 order_date 기준 (이미 도착일=order_date+1이 반영된 규칙)
        expiry_dt = base + timedelta(days=day_offset)
        return expiry_dt.strftime(f"%Y-%m-%d {hour:02d}:00")

    # 빵(012): order_date + expiration_days
    if mid_cd == "012":
        days = expiration_days if expiration_days > 0 else 3
        expiry_dt = od + timedelta(days=days)
        return expiry_dt.strftime("%Y-%m-%d 00:00")

    # 기타: expiration_days 기반
    if expiration_days > 0:
        expiry_dt = od + timedelta(days=expiration_days)
        return expiry_dt.strftime("%Y-%m-%d 00:00")

    return ""


def patch_store(store_id: str, dry_run: bool = True):
    """단일 매장 패치"""
    db_path = project_root / "data" / "stores" / f"{store_id}.db"
    common_path = project_root / "data" / "common.db"

    if not db_path.exists():
        print(f"[{store_id}] DB 없음: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # common.db에서 product_details 조회용
    common_conn = sqlite3.connect(str(common_path))
    common_conn.row_factory = sqlite3.Row

    print(f"\n{'='*60}")
    print(f"[{store_id}] 패치 시작 (dry_run={dry_run})")
    print(f"{'='*60}")

    # ── 1단계: expiry_time 23:59:59 보정 (모든 status 대상) ──
    print(f"\n--- 1단계: expiry_time 23:59:59 보정 (모든 status) ---")

    bad_rows = conn.execute("""
        SELECT id, item_cd, item_nm, mid_cd, order_date, expiry_time, status
        FROM order_tracking
        WHERE store_id = ?
          AND expiry_time LIKE '%23:59:59%'
        ORDER BY order_date, mid_cd
    """, (store_id,)).fetchall()

    fixed_count = 0
    skip_count = 0

    for row in bad_rows:
        ot_id = row['id']
        item_cd = row['item_cd']
        item_nm = row['item_nm']
        mid_cd = row['mid_cd']
        order_date = row['order_date']
        old_expiry = row['expiry_time']

        # product_details에서 expiration_days 조회
        pd_row = common_conn.execute(
            "SELECT expiration_days FROM product_details WHERE item_cd = ?",
            (item_cd,)
        ).fetchone()
        exp_days = pd_row['expiration_days'] if pd_row and pd_row['expiration_days'] else 0

        new_expiry = calc_correct_expiry(item_nm, mid_cd, order_date, exp_days)

        if not new_expiry:
            skip_count += 1
            print(f"  SKIP: {item_nm} ({mid_cd}) - 보정 규칙 없음")
            continue

        fixed_count += 1
        status = row['status']
        print(f"  FIX: {item_nm} | {old_expiry} -> {new_expiry} [{status}]")

        if not dry_run:
            conn.execute(
                "UPDATE order_tracking SET expiry_time = ? WHERE id = ?",
                (new_expiry, ot_id)
            )

    print(f"  결과: {fixed_count}건 보정, {skip_count}건 SKIP")

    # ── 2단계: 유령 항목 정리 (stock=0, remaining>0) ──
    print(f"\n--- 2단계: 유령 항목 정리 (stock=0, remaining>0) ---")

    ghost_rows = conn.execute("""
        SELECT ot.id, ot.item_cd, ot.item_nm, ot.mid_cd,
               ot.remaining_qty, ot.status, ot.order_date,
               COALESCE(ri.stock_qty, 0) as stock
        FROM order_tracking ot
        LEFT JOIN realtime_inventory ri
            ON ot.item_cd = ri.item_cd AND ot.store_id = ri.store_id
        WHERE ot.status IN ('ordered', 'arrived', 'selling')
          AND ot.remaining_qty > 0
          AND ot.store_id = ?
          AND (ri.stock_qty IS NULL OR ri.stock_qty = 0)
        ORDER BY ot.order_date
    """, (store_id,)).fetchall()

    consumed_count = 0
    for row in ghost_rows:
        consumed_count += 1
        if consumed_count <= 10:  # 처음 10건만 출력
            print(f"  CONSUME: {row['item_nm']} | qty={row['remaining_qty']} "
                  f"| stock=0 | order={row['order_date']}")

    if consumed_count > 10:
        print(f"  ... 외 {consumed_count - 10}건")

    print(f"  결과: {consumed_count}건 → status='consumed', remaining_qty=0")

    if not dry_run:
        ghost_ids = [r['id'] for r in ghost_rows]
        if ghost_ids:
            placeholders = ','.join('?' * len(ghost_ids))
            now = datetime.now().isoformat()
            conn.execute(
                f"""
                UPDATE order_tracking
                SET remaining_qty = 0, status = 'consumed', updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [now] + ghost_ids
            )

    # ── 3단계: stock>0인 항목도 remaining_qty를 실재고 기준으로 보정 ──
    print(f"\n--- 3단계: remaining_qty > stock_qty 보정 ---")

    over_rows = conn.execute("""
        SELECT ot.id, ot.item_cd, ot.item_nm,
               ot.remaining_qty, ri.stock_qty,
               ot.order_date
        FROM order_tracking ot
        JOIN realtime_inventory ri
            ON ot.item_cd = ri.item_cd AND ot.store_id = ri.store_id
        WHERE ot.status IN ('ordered', 'arrived', 'selling')
          AND ot.remaining_qty > ri.stock_qty
          AND ri.stock_qty > 0
          AND ot.store_id = ?
        ORDER BY ot.order_date
    """, (store_id,)).fetchall()

    adjusted_count = 0
    for row in over_rows:
        adjusted_count += 1
        if adjusted_count <= 10:
            print(f"  ADJUST: {row['item_nm']} | remaining={row['remaining_qty']}"
                  f" -> stock={row['stock_qty']} | order={row['order_date']}")

        if not dry_run:
            conn.execute(
                "UPDATE order_tracking SET remaining_qty = ? WHERE id = ?",
                (row['stock_qty'], row['id'])
            )

    if adjusted_count > 10:
        print(f"  ... 외 {adjusted_count - 10}건")
    print(f"  결과: {adjusted_count}건 보정")

    if not dry_run:
        conn.commit()
        print(f"\n[{store_id}] 커밋 완료")
    else:
        print(f"\n[{store_id}] DRY RUN - 변경 없음")

    # 최종 상태 확인
    remaining_bad = conn.execute("""
        SELECT COUNT(*) FROM order_tracking
        WHERE store_id = ?
          AND expiry_time LIKE '%23:59:59%'
    """, (store_id,)).fetchone()[0]

    remaining_ghost = conn.execute("""
        SELECT COUNT(*) FROM order_tracking ot
        LEFT JOIN realtime_inventory ri ON ot.item_cd = ri.item_cd AND ot.store_id = ri.store_id
        WHERE ot.status IN ('ordered','arrived','selling')
          AND ot.remaining_qty > 0
          AND ot.store_id = ?
          AND (ri.stock_qty IS NULL OR ri.stock_qty = 0)
    """, (store_id,)).fetchone()[0]

    print(f"\n[{store_id}] 패치 후: 23:59:59={remaining_bad}건, 유령={remaining_ghost}건")

    conn.close()
    common_conn.close()


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv

    if dry_run:
        print("*** DRY RUN 모드 (실제 적용: --apply) ***")

    for sid in ["46513", "46704"]:
        patch_store(sid, dry_run=dry_run)

    if dry_run:
        print("\n*** 실제 적용하려면: python scripts/patch_order_tracking_expiry.py --apply ***")
