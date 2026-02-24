"""기존 waste_slip_items의 item_nm을 products/daily_sales 마스터로 보정"""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STORE_IDS = ["46513", "46704"]


def fix_store(store_id: str):
    store_db = f"data/stores/{store_id}.db"
    common_db = "data/common.db"

    conn = sqlite3.connect(store_db)
    conn.row_factory = sqlite3.Row

    # 1) products 마스터 (common.db) 로드
    conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
    products = {}
    for row in conn.execute("SELECT item_cd, item_nm FROM common.products"):
        products[row["item_cd"]] = row["item_nm"]

    # 2) order_tracking / inventory_batches에서 item_nm 로드 (products에 없는 상품 대비)
    sales_names = {}
    for tbl in ["order_tracking", "inventory_batches"]:
        for row in conn.execute(f"""
            SELECT item_cd, item_nm FROM {tbl}
            WHERE store_id = ? AND item_nm IS NOT NULL AND item_nm != ''
            GROUP BY item_cd
        """, (store_id,)):
            if row["item_cd"] not in products and row["item_cd"] not in sales_names:
                sales_names[row["item_cd"]] = row["item_nm"]

    # 3) waste_slip_items 전체 조회
    items = conn.execute("""
        SELECT id, item_cd, item_nm FROM waste_slip_items
        WHERE store_id = ?
    """, (store_id,)).fetchall()

    fixed = 0
    missing = 0
    for item in items:
        item_cd = item["item_cd"]
        current_nm = item["item_nm"] or ""

        # 정확한 이름 찾기: products 우선, 없으면 daily_sales
        correct_nm = products.get(item_cd) or sales_names.get(item_cd)

        if not correct_nm:
            missing += 1
            continue

        if correct_nm != current_nm:
            conn.execute(
                "UPDATE waste_slip_items SET item_nm = ? WHERE id = ?",
                (correct_nm, item["id"]),
            )
            fixed += 1
            print(f"  [{store_id}] {item_cd}: [{current_nm}] -> [{correct_nm}]")

    conn.commit()
    conn.execute("DETACH DATABASE common")
    conn.close()

    print(f"  [{store_id}] 총 {len(items)}건 중 보정 {fixed}건, 마스터 미등록 {missing}건")
    return fixed


def main():
    print("=== waste_slip_items item_nm 보정 ===")
    total_fixed = 0
    for store_id in STORE_IDS:
        print(f"\n--- {store_id} ---")
        total_fixed += fix_store(store_id)

    print(f"\n=== 완료: 총 {total_fixed}건 보정 ===")


if __name__ == "__main__":
    main()
