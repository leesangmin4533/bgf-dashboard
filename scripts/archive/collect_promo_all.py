# -*- coding: utf-8 -*-
"""
전체 상품 행사 정보 수집 스크립트
- 최근 7일 판매 상품 대상
- 상품 상세 팝업에서 행사 정보 수집
- DB에 저장
"""

import sys
sys.path.insert(0, 'src')

import time
import sqlite3
from datetime import datetime
from typing import List, Tuple
from sales_analyzer import SalesAnalyzer
from collectors.product_info_collector import ProductInfoCollector

def get_recent_items(days: int = 7) -> List[Tuple[str, str]]:
    """최근 N일 판매된 상품 코드 목록 (담배 제외)"""
    conn = sqlite3.connect('data/bgf_sales.db')
    cursor = conn.cursor()

    # 담배(072), 전자담배(073) 제외
    cursor.execute(f'''
        SELECT DISTINCT ds.item_cd, p.item_nm
        FROM daily_sales ds
        JOIN products p ON ds.item_cd = p.item_cd
        WHERE ds.sales_date >= date('now', '-{days} days')
        AND p.mid_cd NOT IN ('072', '073')
        ORDER BY ds.item_cd
    ''')

    items = cursor.fetchall()
    conn.close()
    return items

def main() -> None:
    print('=' * 60)
    print('전체 상품 행사 정보 수집')
    print('=' * 60)

    # 1. 수집 대상 상품 목록
    items = get_recent_items(7)
    print(f'\n수집 대상: {len(items)}개 상품')
    print(f'예상 소요 시간: 약 {len(items) * 4 // 60}분')

    # 2. BGF 로그인
    print('\n[1] BGF 시스템 로그인...')
    analyzer = SalesAnalyzer()
    analyzer.setup_driver()
    analyzer.connect()

    if not analyzer.do_login():
        print('[ERROR] 로그인 실패')
        analyzer.close()
        return

    print('[OK] 로그인 성공')
    time.sleep(2)
    analyzer.close_popup()
    time.sleep(1)

    # 3. 상품 정보 수집기
    collector = ProductInfoCollector(driver=analyzer.driver)

    # 4. 행사 정보 수집
    print(f'\n[2] 행사 정보 수집 시작...')

    success_count = 0
    promo_count = 0
    fail_count = 0
    start_time = time.time()

    for i, (item_cd, item_nm) in enumerate(items):
        try:
            # 진행 상황 출력
            elapsed = time.time() - start_time
            if i > 0:
                eta = (elapsed / i) * (len(items) - i)
                eta_min = int(eta // 60)
                eta_sec = int(eta % 60)
                print(f'\n[{i+1}/{len(items)}] {item_cd} ({item_nm[:20]}...) - 남은시간: {eta_min}분 {eta_sec}초')
            else:
                print(f'\n[{i+1}/{len(items)}] {item_cd} ({item_nm[:20]}...)')

            # 상품 정보 수집
            detail = collector._fetch_product_detail_via_popup(item_cd)

            if detail:
                # DB 저장
                collector.save_to_db(item_cd, detail)
                success_count += 1

                promo = detail.get('promo_type')
                if promo:
                    promo_count += 1
                    print(f'  -> 행사: {promo}')
                else:
                    print(f'  -> 행사 없음')
            else:
                fail_count += 1
                print(f'  -> [FAIL] 수집 실패')

            # 너무 빠르면 서버 부하
            time.sleep(0.5)

        except Exception as e:
            fail_count += 1
            print(f'  -> [ERROR] {e}')

            # Alert 처리
            try:
                alert = analyzer.driver.switch_to.alert
                alert.accept()
            except:
                pass

    # 5. 결과 출력
    elapsed = time.time() - start_time
    print('\n' + '=' * 60)
    print('수집 완료')
    print('=' * 60)
    print(f'전체: {len(items)}개')
    print(f'성공: {success_count}개')
    print(f'행사 상품: {promo_count}개')
    print(f'실패: {fail_count}개')
    print(f'소요 시간: {int(elapsed // 60)}분 {int(elapsed % 60)}초')

    # 6. 종료
    analyzer.close()
    print('\n브라우저 종료')

if __name__ == '__main__':
    main()
