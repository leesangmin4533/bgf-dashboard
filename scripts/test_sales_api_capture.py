"""
매출 화면(STMB011_M0) API 엔드포인트 캡처 스크립트

로그인 → 매출분석 메뉴 이동 → 인터셉터 설치 → 날짜 조회 → 카테고리 클릭
각 단계에서 발생하는 네트워크 요청(gfn_transaction + XHR)을 캡처하여 출력/저장합니다.

사용법:
    python scripts/test_sales_api_capture.py                    # 기본 (오늘-1일)
    python scripts/test_sales_api_capture.py --date 20260226    # 특정 날짜
    python scripts/test_sales_api_capture.py --categories 5     # 클릭할 카테고리 수
"""

import sys
import io
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Windows CP949 콘솔 -> UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# 프로젝트 루트
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.sales_analyzer import SalesAnalyzer
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────
# gfn_transaction + XHR 인터셉터 (body + response 캡처)
# ──────────────────────────────────────────────────────────────────
INTERCEPTOR_JS = """
(function() {
    if (window._salesInterceptorInstalled) return 'already_installed';
    window._salesCaptures = [];

    // ─── 1) gfn_transaction 오버라이드 ───
    // 넥사크로의 내부 서버 통신 함수를 래핑하여 호출 파라미터를 캡처
    try {
        if (typeof gfn_transaction === 'function') {
            window._origGfnTransaction = gfn_transaction;
            window.gfn_transaction = function(txId, svcURL, inDS, outDS, args, callback, isAsync) {
                window._salesCaptures.push({
                    type: 'gfn_transaction',
                    txId: txId || '',
                    serviceURL: svcURL || '',
                    inputDS: inDS || '',
                    outputDS: outDS || '',
                    args: args || '',
                    timestamp: new Date().toISOString()
                });
                console.log('[CAPTURE] gfn_transaction:', txId, svcURL);
                return window._origGfnTransaction.apply(this, arguments);
            };
            console.log('[INTERCEPT] gfn_transaction 오버라이드 성공');
        } else {
            console.log('[INTERCEPT] gfn_transaction 함수 없음 - XHR만 캡처');
        }
    } catch(e) {
        console.warn('[INTERCEPT] gfn_transaction 오버라이드 실패:', e.message);
    }

    // ─── 2) XMLHttpRequest body + response 캡처 ───
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this._capMethod = method;
        this._capUrl = url;
        return origOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        var xhr = this;
        xhr._capBody = body;
        xhr.addEventListener('load', function() {
            try {
                var entry = {
                    type: 'xhr',
                    method: xhr._capMethod || '',
                    url: xhr._capUrl || '',
                    bodyLength: (xhr._capBody || '').length,
                    bodyPreview: (xhr._capBody || '').substring(0, 3000),
                    status: xhr.status,
                    responseLength: (xhr.responseText || '').length,
                    responsePreview: (xhr.responseText || '').substring(0, 1000),
                    timestamp: new Date().toISOString()
                };
                window._salesCaptures.push(entry);
                console.log('[CAPTURE] XHR:', xhr._capMethod, xhr._capUrl, 'body:', (xhr._capBody||'').length, 'resp:', (xhr.responseText||'').length);
            } catch(e) {}
        });
        return origSend.apply(this, arguments);
    };

    // ─── 3) fetch() 인터셉터 (일부 넥사크로 버전은 fetch 사용) ───
    var origFetch = window.fetch;
    window.fetch = function(input, init) {
        var url = typeof input === 'string' ? input : (input && input.url) || '';
        var body = (init && init.body) || '';
        var entry = {
            type: 'fetch',
            url: url,
            method: (init && init.method) || 'GET',
            bodyLength: (typeof body === 'string') ? body.length : 0,
            bodyPreview: (typeof body === 'string') ? body.substring(0, 3000) : '',
            timestamp: new Date().toISOString()
        };

        return origFetch.apply(this, arguments).then(function(resp) {
            var cloned = resp.clone();
            cloned.text().then(function(text) {
                entry.status = resp.status;
                entry.responseLength = text.length;
                entry.responsePreview = text.substring(0, 1000);
                window._salesCaptures.push(entry);
                console.log('[CAPTURE] fetch:', url, 'resp:', text.length);
            }).catch(function(){});
            return resp;
        });
    };

    window._salesInterceptorInstalled = true;
    console.log('[INTERCEPT] 인터셉터 설치 완료');
    return 'installed';
})();
"""


def get_captures(driver):
    """캡처된 요청 목록 조회"""
    try:
        return driver.execute_script("return window._salesCaptures || []")
    except Exception as e:
        logger.error(f"캡처 조회 실패: {e}")
        return []


def clear_captures(driver):
    """캡처 목록 초기화"""
    driver.execute_script("window._salesCaptures = []")


def print_captures(captures, title=""):
    """캡처된 요청 출력"""
    print(f"\n{'='*70}")
    print(f" {title}")
    print(f"{'='*70}")
    print(f" 총 {len(captures)}건 캡처됨\n")

    for i, cap in enumerate(captures):
        cap_type = cap.get('type', '?')
        ts = cap.get('timestamp', '')

        if cap_type == 'gfn_transaction':
            print(f"  [{i+1}] gfn_transaction @ {ts}")
            print(f"      txId       : {cap.get('txId', '')}")
            print(f"      serviceURL : {cap.get('serviceURL', '')}")
            print(f"      inputDS    : {cap.get('inputDS', '')}")
            print(f"      outputDS   : {cap.get('outputDS', '')}")
            print(f"      args       : {cap.get('args', '')}")

        elif cap_type in ('xhr', 'fetch'):
            print(f"  [{i+1}] {cap_type.upper()} {cap.get('method', '')} {cap.get('url', '')} @ {ts}")
            print(f"      status     : {cap.get('status', '')}")
            print(f"      body       : {cap.get('bodyLength', 0)} bytes")
            if cap.get('bodyPreview'):
                preview = cap['bodyPreview'][:200]
                print(f"      bodyPreview: {preview}...")
            print(f"      response   : {cap.get('responseLength', 0)} bytes")
            if cap.get('responsePreview'):
                resp_preview = cap['responsePreview'][:200]
                print(f"      respPreview: {resp_preview}...")

        print()


def save_captures(captures, filepath):
    """캡처 결과를 JSON 파일로 저장"""
    # responsePreview 를 좀 더 길게 저장
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(captures, f, ensure_ascii=False, indent=2)

    print(f"\n캡처 결과 저장: {filepath} ({len(captures)}건)")


def main():
    parser = argparse.ArgumentParser(description="STMB011 API 엔드포인트 캡처")
    parser.add_argument('--date', '-d', type=str, default=None,
                        help='조회 날짜 (YYYYMMDD). 기본: 어제')
    parser.add_argument('--categories', '-c', type=int, default=3,
                        help='클릭할 카테고리 수 (기본: 3)')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='결과 저장 경로 (기본: data/captures/sales_api_capture.json)')
    args = parser.parse_args()

    # 날짜 기본값: 어제
    if args.date:
        target_date = args.date
    else:
        yesterday = datetime.now() - timedelta(days=1)
        target_date = yesterday.strftime('%Y%m%d')

    output_path = args.output or str(project_root / 'data' / 'captures' / 'sales_api_capture.json')
    num_categories = args.categories

    print("=" * 70)
    print("STMB011_M0 (중분류 매출 구성비) API 캡처")
    print(f"날짜: {target_date}, 카테고리 클릭 수: {num_categories}")
    print("=" * 70)

    analyzer = None
    all_captures = {
        'metadata': {
            'date': target_date,
            'categories_clicked': num_categories,
            'started_at': datetime.now().isoformat(),
        },
        'phase_1_after_search': [],
        'phase_2_after_clicks': [],
        'all_captures': [],
    }

    try:
        # ──────────────────────────────────────────────────
        # 1. 로그인
        # ──────────────────────────────────────────────────
        print("\n[1/5] SalesAnalyzer 초기화 + 로그인...")
        analyzer = SalesAnalyzer()
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return

        print("[OK] 로그인 성공")

        # ──────────────────────────────────────────────────
        # 2. 매출분석 메뉴 이동
        # ──────────────────────────────────────────────────
        print("\n[2/5] 매출분석 > 중분류별 매출 구성비 메뉴 이동...")
        if not analyzer.navigate_to_sales_menu():
            print("[ERROR] 메뉴 이동 실패")
            return

        print("[OK] STMB011_M0 화면 로딩됨")
        time.sleep(2)

        # ──────────────────────────────────────────────────
        # 3. 인터셉터 설치
        # ──────────────────────────────────────────────────
        print("\n[3/5] 네트워크 인터셉터 설치...")
        result = analyzer.driver.execute_script(INTERCEPTOR_JS)
        print(f"[OK] 인터셉터: {result}")
        time.sleep(0.5)

        # ──────────────────────────────────────────────────
        # 4. 날짜 설정 + F_10 조회 → 캡처 Phase 1
        # ──────────────────────────────────────────────────
        print(f"\n[4/5] 날짜 {target_date} 설정 + 조회(F_10)...")
        clear_captures(analyzer.driver)

        analyzer.set_date_and_search(target_date)

        # dsList 로딩 대기
        if analyzer.wait_for_dataset(timeout=30):
            print("[OK] dsList 로딩 완료")
        else:
            print("[WARN] dsList 로딩 타임아웃 - 계속 진행")

        time.sleep(1)  # 추가 대기 (비동기 응답 대기)

        captures_phase1 = get_captures(analyzer.driver)
        all_captures['phase_1_after_search'] = captures_phase1
        print_captures(captures_phase1, "Phase 1: F_10 검색 후 캡처")

        # ──────────────────────────────────────────────────
        # 5. 카테고리 클릭 → 캡처 Phase 2
        # ──────────────────────────────────────────────────
        print(f"\n[5/5] 중분류 카테고리 {num_categories}개 클릭...")
        clear_captures(analyzer.driver)

        categories = analyzer.get_all_mid_categories()
        print(f"  총 {len(categories)}개 중분류 발견")

        clicked = 0
        for cat in categories[:num_categories]:
            idx = cat['index']
            mid_cd = cat.get('MID_CD', '?')
            mid_nm = cat.get('MID_NM', '?')
            print(f"  [{clicked+1}/{num_categories}] 클릭: [{mid_cd}] {mid_nm}")

            row_count = analyzer.click_mid_category_and_wait(idx, timeout=15)
            if row_count >= 0:
                print(f"    → dsDetail {row_count}행 로딩됨")
            else:
                print(f"    → 로딩 실패")

            time.sleep(0.5)
            clicked += 1

        captures_phase2 = get_captures(analyzer.driver)
        all_captures['phase_2_after_clicks'] = captures_phase2
        print_captures(captures_phase2, f"Phase 2: 카테고리 {clicked}개 클릭 후 캡처")

        # ──────────────────────────────────────────────────
        # 결과 저장
        # ──────────────────────────────────────────────────
        all_captures['all_captures'] = captures_phase1 + captures_phase2
        all_captures['metadata']['completed_at'] = datetime.now().isoformat()
        all_captures['metadata']['total_captures'] = len(all_captures['all_captures'])
        all_captures['metadata']['categories_found'] = len(categories)

        save_captures(all_captures, output_path)

        # ──────────────────────────────────────────────────
        # 요약
        # ──────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print(" 캡처 요약")
        print("=" * 70)
        print(f"  Phase 1 (검색): {len(captures_phase1)}건")
        print(f"  Phase 2 (클릭): {len(captures_phase2)}건")
        print(f"  합계          : {len(all_captures['all_captures'])}건")
        print(f"  저장 위치     : {output_path}")

        # 엔드포인트 분석
        urls = set()
        for cap in all_captures['all_captures']:
            url = cap.get('url') or cap.get('serviceURL') or ''
            if url:
                urls.add(url)

        if urls:
            print(f"\n  발견된 엔드포인트:")
            for url in sorted(urls):
                print(f"    - {url}")

        # gfn_transaction 호출 분석
        gfn_calls = [c for c in all_captures['all_captures'] if c.get('type') == 'gfn_transaction']
        if gfn_calls:
            print(f"\n  gfn_transaction 호출 ({len(gfn_calls)}건):")
            for g in gfn_calls:
                print(f"    txId={g.get('txId')}, svcURL={g.get('serviceURL')}")
                print(f"    inDS={g.get('inputDS')}, outDS={g.get('outputDS')}")

        print("\n" + "=" * 70)
        print(" 다음 단계: captures JSON 분석 후 Direct API 구현")
        print("=" * 70)

    except KeyboardInterrupt:
        print("\n[중단] 사용자 중단")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        if analyzer and analyzer.driver:
            print("\n브라우저 종료...")
            try:
                analyzer.driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
