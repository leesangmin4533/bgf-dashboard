"""
hourly_sales_detail 데이터 정리 스크립트
- 공병/봉투 노이즈 삭제
- 묶음상품 -> 낱개 바코드 변환 (엄격한 매칭)
- 중복 레코드 병합

Usage:
    python scripts/cleanup_hsd_data.py --store 46704
    python scripts/cleanup_hsd_data.py --store 47863
    python scripts/cleanup_hsd_data.py --all  # 전체 매장
    python scripts/cleanup_hsd_data.py --store 46704 --dry-run  # 시뮬레이션
"""

import argparse
import sqlite3
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def get_store_db(store_id):
    return os.path.join(
        os.path.dirname(__file__), '..', 'data', 'stores', f'{store_id}.db'
    )


def get_common_db():
    return os.path.join(os.path.dirname(__file__), '..', 'data', 'common.db')


# ────────────────────────────────────────
# 브랜드 접두어 제거
# ────────────────────────────────────────
BRAND_PREFIX_RE = re.compile(
    r'^(CJ\)|신\)|동원\)|오뚜기\)|하이트\)|롯데\)|삼양\)|대상\)|농심\)|풀무원\)|'
    r'오비\)|크라운\)|해태\)|빙그레\)|남양\)|매일\)|서울\)|연세\)|'
    r'MIIX\)|LIL\)|핏\)|'
    r'[A-Za-z]{2,6}\)|'  # 영문 2~6자 + )
    r'[가-힣]{1,4}\))'
)

# HSD 약칭 → products 정식 명칭 매핑
NAME_SYNONYMS = {
    '팔리아': '팔리아멘트',
    '팔리아아쿠아': '팔리아멘트아쿠아',
    '스카이블루팩': '메비우스스카이블루팩',
    '스카이블루곽': '메비우스스카이블루곽',
    '말보로화이트': '말보로아이스버스트화이트',
    '말)하이브리드': '말보로하이브리드',
    '팔)하이브리드': '팔리아멘트하이브리드',
}


def strip_brand(name):
    """브랜드 접두어 제거: CJ)햇반210g → 햇반210g, MIIX)믹스 → 믹스"""
    return BRAND_PREFIX_RE.sub('', name).strip()


# ────────────────────────────────────────
# 묶음 배수 파싱
# ────────────────────────────────────────
def parse_multiplier(item_nm):
    """상품명에서 묶음 배수 파싱

    Returns:
        (multiplier, base_name) or (None, None) if not a multipack
    """
    # 빵 이름에 '소보루' 포함 → 묶음이 아님
    if '소보루' in item_nm:
        return None, None

    # PBICK(PB상품), *PBICK, 득템 등 PB 번들은 묶음이 아님
    if 'PBICK' in item_nm:
        return None, None

    # '발효카스타드소보루' 같은 빵 이름 → 보루로 시작하지 않으면 스킵
    # 보루 = 담배 10갑 묶음 (mg/㎎ 포함 확인)
    if (item_nm.endswith('보루') or item_nm.endswith('보루신')
            or item_nm.endswith('보루N')):
        # 담배 보루 확인: mg, ㎎, 또는 알려진 담배 브랜드 포함
        tobacco_keywords = [
            'mg', '㎎', '에쎄', '말보로', '던힐', '보헴', '레종', '메비우스',
            '카멜', 'MIIX', 'LIL', '디스', '팔리아', '핏)', '시즌', '타임',
            '스카이블루', '테리아', '비스타', '그래', '심플', '클라우드',
            '하이퍼', '네오', '센티아', '몽스', '아이스볼트', '한라산',
            '트로피컬', '리얼', 'LBS', 'E스타일', '버진',
        ]
        if any(kw in item_nm for kw in tobacco_keywords):
            base = (item_nm
                    .replace('보루신', '')
                    .replace('보루N', '')
                    .replace('보루', '')
                    .strip())
            return 10, base
        # 담배가 아닌 '보루' 포함 제품 (예: 발효카스타드소보루)
        return None, None

    # 매 (sheets) 패턴 제외 — *30매, 15매 등은 묶음이 아님
    if re.search(r'\d+매', item_nm):
        return None, None

    # *숫자 끝 (카스캔500ml*4, 칭따오캔500ml*6, 칭따오생*4)
    m = re.search(r'\*(\d+)$', item_nm)
    if m:
        mult = int(m.group(1))
        if mult > 12:  # 12 초과 배수는 묶음이 아닐 가능성 높음
            return None, None
        base = item_nm[:m.start()].strip()
        return mult, base

    # 숫자입) or 숫자입 끝 (삼양1963(4입), 코젤화이트500ml*4입, 햇반210g*8입)
    m = re.search(r'\*?(\d+)입\)?$', item_nm)
    if m:
        mult = int(m.group(1))
        if mult > 12:
            return None, None
        base = re.sub(r'\*?\d+입\)?$', '', item_nm).strip()
        base = base.rstrip('(').strip()
        return mult, base

    # *숫자 중간 (통그릴비엔나180*2)
    m = re.search(r'(\d+)\*(\d+)$', item_nm)
    if m:
        mult = int(m.group(2))
        if mult > 12:
            return None, None
        base = item_nm[:m.start()] + m.group(1)
        return mult, base

    return None, None


# ────────────────────────────────────────
# 낱개 바코드 매칭 (엄격)
# ────────────────────────────────────────
def find_single_item(common_conn, base_name, ds_items_set):
    """낱개 바코드 찾기 — 엄격한 매칭만 수행

    Args:
        common_conn: common.db 연결
        base_name: 묶음 제거 후 기본 상품명
        ds_items_set: daily_sales에 있는 item_cd 집합

    Returns:
        (item_cd, item_nm) or (None, None)
    """
    if not common_conn or len(base_name) < 2:
        return None, None

    # products 테이블에서 후보 검색 (첫 3~4글자 LIKE)
    search_key = strip_brand(base_name)
    like_len = min(4, len(search_key))
    if like_len < 2:
        return None, None
    like_pattern = f'%{search_key[:like_len]}%'

    candidates = common_conn.execute('''
        SELECT DISTINCT item_cd, item_nm FROM products
        WHERE item_nm LIKE ?
    ''', (like_pattern,)).fetchall()

    if not candidates:
        return None, None

    # daily_sales에 있는 상품 우선
    candidates.sort(key=lambda x: (0 if x[0] in ds_items_set else 1))

    stripped_base = strip_brand(base_name)

    # === Match Level 1: 정확 일치 ===
    for cd, nm in candidates:
        if '보루' in nm or '*' in nm:
            continue
        if nm == base_name or nm == stripped_base:
            return cd, nm

    # === Match Level 1.5: ) 제거 후 정확 일치 ===
    # MIIX)믹스 → MIIX믹스, LIL)액상카트리지 → LIL액상카트리지
    clean_base = base_name.replace(')', '')
    if clean_base != base_name:
        for cd, nm in candidates:
            if '보루' in nm or '*' in nm:
                continue
            if nm == clean_base:
                return cd, nm

    # === Match Level 2: 브랜드 제거 후 정확 일치 ===
    for cd, nm in candidates:
        if '보루' in nm or '*' in nm:
            continue
        stripped_nm = strip_brand(nm)
        if stripped_nm == stripped_base or stripped_nm == base_name:
            return cd, nm
        # clean_base와도 비교
        if clean_base != base_name and stripped_nm == strip_brand(clean_base):
            return cd, nm

    # === Match Level 3: 동의어 매핑 후 재시도 ===
    synonym_base = stripped_base
    for abbr, full in NAME_SYNONYMS.items():
        if synonym_base.startswith(abbr):
            synonym_base = synonym_base.replace(abbr, full, 1)
            break

    if synonym_base != stripped_base:
        for cd, nm in candidates:
            if '보루' in nm or '*' in nm:
                continue
            stripped_nm = strip_brand(nm)
            if stripped_nm == synonym_base:
                return cd, nm
            if stripped_nm.startswith(synonym_base):
                remainder = stripped_nm[len(synonym_base):]
                if not remainder or re.match(r'^[\d]*[gmlLkK㎎mg]+$', remainder):
                    return cd, nm

    # === Match Level 4: startsWith 매칭 ===
    # 나머지가 단위(g, ml, mg, ㎎) 또는 숫자+단위일 때만 허용
    UNIT_SUFFIX_RE = re.compile(r'^[\d]*[gmlLkK㎎]+[\d]*[gmlLkK㎎]*$')
    for cd, nm in candidates:
        if '보루' in nm or '*' in nm:
            continue
        stripped_nm = strip_brand(nm)
        if len(stripped_base) >= 3 and len(stripped_nm) >= 3:
            if stripped_nm.startswith(stripped_base):
                remainder = stripped_nm[len(stripped_base):]
                if not remainder or UNIT_SUFFIX_RE.match(remainder):
                    return cd, nm
            if stripped_base.startswith(stripped_nm):
                remainder = stripped_base[len(stripped_nm):]
                if not remainder or UNIT_SUFFIX_RE.match(remainder):
                    return cd, nm

    # === Match Level 5: 용량 정규화 매칭 ===
    norm_base = re.sub(r'(\d+)(ml|ML|g|G|l|L)', lambda m: m.group(1) + m.group(2).lower(), stripped_base)
    for cd, nm in candidates:
        if '보루' in nm or '*' in nm:
            continue
        stripped_nm = strip_brand(nm)
        norm_nm = re.sub(r'(\d+)(ml|ML|g|G|l|L)', lambda m: m.group(1) + m.group(2).lower(), stripped_nm)
        if norm_nm == norm_base:
            return cd, nm

    # === Match Level 6: 동의어 + startsWith 확장 ===
    if synonym_base != stripped_base:
        for cd, nm in candidates:
            if '보루' in nm or '*' in nm:
                continue
            stripped_nm = strip_brand(nm)
            if stripped_nm.startswith(synonym_base) or synonym_base.startswith(stripped_nm):
                return cd, nm

    # 매칭 실패 — 퍼지 매칭은 하지 않음 (오탐 방지)
    return None, None


# ────────────────────────────────────────
# 매장 정리 메인
# ────────────────────────────────────────
def cleanup_store(store_id, dry_run=False):
    """매장 HSD 데이터 정리"""
    db_path = get_store_db(store_id)
    common_path = get_common_db()

    if not os.path.exists(db_path):
        print(f"  [SKIP] DB 없음: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    common_conn = sqlite3.connect(common_path) if os.path.exists(common_path) else None

    try:
        # 정리 전 통계
        before_count = conn.execute(
            'SELECT COUNT(*) FROM hourly_sales_detail'
        ).fetchone()[0]
        print(f"  정리 전: {before_count:,}건")

        # daily_sales item_cd 집합 (매칭 우선순위용)
        ds_items_set = set()
        try:
            rows = conn.execute(
                'SELECT DISTINCT item_cd FROM daily_sales WHERE store_id = ?',
                (store_id,)
            ).fetchall()
            ds_items_set = {r[0] for r in rows}
        except Exception:
            pass

        # ──── Phase 1: 공병/봉투 삭제 ────
        print(f"\n  Phase 1: 공병/봉투 삭제")

        noise_count = conn.execute('''
            SELECT COUNT(*) FROM hourly_sales_detail
            WHERE item_nm LIKE '%공병%' OR item_nm LIKE '%재활용봉투%'
               OR item_nm LIKE '%친환경봉투%'
        ''').fetchone()[0]

        if noise_count > 0:
            if not dry_run:
                conn.execute('''
                    DELETE FROM hourly_sales_detail
                    WHERE item_nm LIKE '%공병%' OR item_nm LIKE '%재활용봉투%'
                       OR item_nm LIKE '%친환경봉투%'
                ''')
                conn.commit()
            print(f"    삭제: {noise_count}건 {'(DRY RUN)' if dry_run else ''}")
        else:
            print(f"    노이즈 없음")

        # ──── Phase 2: 묶음 → 낱개 변환 ────
        print(f"\n  Phase 2: 묶음 → 낱개 변환")

        multipack_items = conn.execute('''
            SELECT DISTINCT item_cd, item_nm FROM hourly_sales_detail
            WHERE item_nm LIKE '%*%' OR item_nm LIKE '%보루%'
               OR item_nm LIKE '%입)%' OR item_nm LIKE '%입 %'
        ''').fetchall()

        converted = 0
        unmatched = []
        skipped_non_multi = 0
        converted_records = 0

        for item_cd, item_nm in multipack_items:
            mult, base_name = parse_multiplier(item_nm)
            if mult is None:
                skipped_non_multi += 1
                continue

            single_cd, single_nm = find_single_item(
                common_conn, base_name, ds_items_set
            )

            if single_cd is None:
                rec_count = conn.execute(
                    'SELECT COUNT(*) FROM hourly_sales_detail WHERE item_cd=?',
                    (item_cd,)
                ).fetchone()[0]
                unmatched.append((item_cd, item_nm, mult, rec_count))
                continue

            rec_count = conn.execute(
                'SELECT COUNT(*) FROM hourly_sales_detail WHERE item_cd=?',
                (item_cd,)
            ).fetchone()[0]

            if not dry_run:
                # 기존 낱개 레코드 존재 여부 확인
                existing = conn.execute('''
                    SELECT sales_date, hour, sale_qty FROM hourly_sales_detail
                    WHERE item_cd = ? AND (sales_date, hour) IN (
                        SELECT sales_date, hour FROM hourly_sales_detail
                        WHERE item_cd = ?
                    )
                ''', (single_cd, item_cd)).fetchall()

                existing_map = {(d, h): q for d, h, q in existing}

                multi_rows = conn.execute('''
                    SELECT rowid, sales_date, hour, sale_qty, sale_amt
                    FROM hourly_sales_detail WHERE item_cd = ?
                ''', (item_cd,)).fetchall()

                for rowid, sales_date, hour, sale_qty, sale_amt in multi_rows:
                    new_qty = sale_qty * mult
                    key = (sales_date, hour)

                    if key in existing_map:
                        conn.execute('''
                            UPDATE hourly_sales_detail
                            SET sale_qty = sale_qty + ?, sale_amt = sale_amt + ?
                            WHERE item_cd = ? AND sales_date = ? AND hour = ?
                        ''', (new_qty, sale_amt, single_cd, sales_date, hour))
                        conn.execute(
                            'DELETE FROM hourly_sales_detail WHERE rowid = ?',
                            (rowid,)
                        )
                    else:
                        conn.execute('''
                            UPDATE hourly_sales_detail
                            SET item_cd = ?, item_nm = ?, sale_qty = ?
                            WHERE rowid = ?
                        ''', (single_cd, single_nm, new_qty, rowid))

                conn.commit()

            converted += 1
            converted_records += rec_count
            print(f"    OK  {item_nm} (x{mult}) → {single_nm} [{rec_count}건]")

        print(f"\n    변환: {converted}개 상품, {converted_records}건")
        print(f"    비묶음 스킵: {skipped_non_multi}개")

        # ──── Phase 3: 매칭 실패 상품 삭제 ────
        if unmatched:
            print(f"\n  Phase 3: 매칭 실패 → 삭제 ({len(unmatched)}개)")
            total_del = 0
            for item_cd, item_nm, mult, cnt in sorted(
                unmatched, key=lambda x: -x[3]
            ):
                print(f"    DEL {item_nm} (x{mult}): {cnt}건")
                total_del += cnt
                if not dry_run:
                    conn.execute(
                        'DELETE FROM hourly_sales_detail WHERE item_cd = ?',
                        (item_cd,)
                    )

            if not dry_run:
                conn.commit()
            print(f"    총 삭제: {total_del}건 {'(DRY RUN)' if dry_run else ''}")

        # ──── 최종 통계 ────
        final_count = conn.execute(
            'SELECT COUNT(*) FROM hourly_sales_detail'
        ).fetchone()[0]
        final_items = conn.execute(
            'SELECT COUNT(DISTINCT item_cd) FROM hourly_sales_detail'
        ).fetchone()[0]

        removed = before_count - final_count if not dry_run else noise_count
        print(f"\n  {'='*50}")
        print(f"  최종: {final_count:,}건, {final_items:,}개 상품")
        print(f"  제거: {removed:,}건 ({removed/before_count*100:.1f}%)")
        print(f"  {'='*50}")

    finally:
        conn.close()
        if common_conn:
            common_conn.close()


def main():
    parser = argparse.ArgumentParser(description="HSD 데이터 정리")
    parser.add_argument("--store", type=str, help="매장 코드")
    parser.add_argument("--all", action="store_true", help="전체 매장")
    parser.add_argument("--dry-run", action="store_true", help="시뮬레이션 모드")
    args = parser.parse_args()

    if args.all:
        stores = ['46704', '47863']  # 46513은 이미 정리 완료
    elif args.store:
        stores = [args.store]
    else:
        print("--store 또는 --all 필요")
        return

    for store_id in stores:
        print(f"\n{'='*60}")
        print(f"  매장 {store_id} 정리 {'(DRY RUN)' if args.dry_run else ''}")
        print(f"{'='*60}")
        cleanup_store(store_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
