"""
selSearch SSV 응답 전체 컬럼 덤프 스크립트

STBJ030_M0 단품별 발주 화면에서 selSearch 호출 후
응답에 포함된 모든 데이터셋(dsItem, dsOrderSale, gdList, dsWeek 등)의
컬럼명과 샘플값을 전부 덤프합니다.

목적: SUGGEST_QTY, DISPLAY_QTY 등 미발견 컬럼이 실제 존재하는지 검증

Usage:
    python scripts/dump_selsearch_columns.py
    python scripts/dump_selsearch_columns.py --items 8801043015653,8800279670889
    python scripts/dump_selsearch_columns.py --save   # JSON 파일로 저장
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sales_analyzer import SalesAnalyzer
from src.order.order_executor import OrderExecutor
from src.collectors.direct_api_fetcher import (
    parse_ssv_dataset,
    ssv_row_to_dict,
    DirectApiFetcher,
)
from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 카테고리별 대표 상품
DEFAULT_ITEMS = [
    "8801043015653",  # 농심 육개장사발면 (라면 032)
    "8800279670889",  # 3XL베이컨햄마요김치 (도시락 002)
    "0000088014463",  # 팔리아멘트아쿠아3mg (담배 072)
    "8801056191115",  # 코카콜라600ML (음료 043)
]

# SSV 구분자
RS = '\u001e'
US = '\u001f'


def discover_all_datasets(ssv_text: str) -> list:
    """SSV 응답에서 모든 데이터셋(헤더+데이터)을 자동 발견

    Returns:
        [
            {
                'index': 0,
                'columns': ['_RowType_', 'COL1', 'COL2', ...],
                'column_types': ['STRING:256', 'INT:0', ...],
                'rows': [{'COL1': 'val1', ...}, ...],
                'row_count': 3,
                'marker_columns': ['COL1', 'COL2'],  # _RowType_ 제외
            },
            ...
        ]
    """
    if not ssv_text:
        return []

    records = ssv_text.split(RS)
    datasets = []

    i = 0
    while i < len(records):
        record = records[i].strip()
        if '_RowType_' in record:
            # 헤더 레코드 발견
            cols_raw = record.split(US)
            columns = [c.split(':')[0] for c in cols_raw]
            col_types = [c for c in cols_raw]  # 타입 정보 포함 (COL:TYPE:SIZE)

            # 데이터 행 수집
            rows = []
            j = i + 1
            while j < len(records):
                row_text = records[j].strip()
                if not row_text or '_RowType_' in row_text:
                    break
                vals = row_text.split(US)
                row_dict = {}
                for idx, col in enumerate(columns):
                    row_dict[col] = vals[idx] if idx < len(vals) else ''
                rows.append(row_dict)
                j += 1

            # _RowType_ 제외한 의미있는 컬럼만
            marker_cols = [c for c in columns if c != '_RowType_']

            datasets.append({
                'index': len(datasets),
                'columns': columns,
                'column_types': col_types,
                'rows': rows,
                'row_count': len(rows),
                'marker_columns': marker_cols,
            })

            i = j  # 데이터 행 다음으로 점프
        else:
            i += 1

    return datasets


def identify_dataset_name(columns: list) -> str:
    """컬럼 목록으로 데이터셋 이름 추정"""
    col_set = set(columns)

    if 'ITEM_NM' in col_set and 'NOW_QTY' in col_set:
        return 'dsItem (= dsGeneralGrid)'
    if 'ORD_QTY' in col_set and 'BUY_QTY' in col_set:
        return 'dsOrderSale'
    if 'MONTH_EVT' in col_set and 'PROFIT_RATE' in col_set:
        return 'gdList'
    if 'ORD_YMD' in col_set and 'ORD_QTY' not in col_set:
        return 'dsWeek'
    if 'ITEM_CD' in col_set:
        return 'ds??? (ITEM_CD 포함)'

    return f'ds??? (첫 컬럼: {columns[1] if len(columns) > 1 else "?"})'


def fetch_raw_ssv(fetcher: DirectApiFetcher, item_cd: str) -> str:
    """상품코드로 selSearch 호출, 원본 SSV 텍스트 반환"""
    if not fetcher._request_template:
        logger.error("요청 템플릿 없음")
        return ""

    try:
        ssv_text = fetcher.driver.execute_script("""
            var body = arguments[0].replace(/strItemCd=[^\\u001e]*/, 'strItemCd=' + arguments[1]);
            var resp = await fetch('/stbj030/selSearch', {
                method: 'POST',
                headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                body: body,
                signal: AbortSignal.timeout(arguments[2])
            });
            if (!resp.ok) return null;
            return await resp.text();
        """, fetcher._request_template, item_cd, 10000)
        return ssv_text or ""
    except Exception as e:
        logger.error(f"selSearch 호출 실패 ({item_cd}): {e}")
        return ""


def dump_dataset_columns_via_nexacro(driver) -> dict:
    """넥사크로 dsGeneralGrid 객체에서 직접 컬럼 정보 추출

    SSV 파싱과 별개로, 넥사크로가 실제 인식하는 컬럼 목록을 가져옵니다.
    SUGGEST_QTY 같은 컬럼이 SSV에는 없지만 dataset 정의에는 있을 수 있습니다.
    """
    return driver.execute_script("""
    try {
        var app = nexacro.getApplication();
        var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
        var stbjForm = null;

        // 폼 탐색
        try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}
        if (!stbjForm || !stbjForm.div_workForm) {
            var fKeys = Object.keys(frameSet);
            for (var i = 0; i < fKeys.length; i++) {
                try {
                    var f = frameSet[fKeys[i]];
                    if (f && f.form && f.form.div_workForm &&
                        f.form.div_workForm.form.div_work_01 &&
                        f.form.div_workForm.form.div_work_01.form.gdList) {
                        stbjForm = f.form;
                        break;
                    }
                } catch(e) {}
            }
        }
        if (!stbjForm) return JSON.stringify({error: 'form_not_found'});

        var workForm = stbjForm.div_workForm.form.div_work_01.form;
        if (!workForm) return JSON.stringify({error: 'workForm_not_found'});

        var result = { datasets: {}, grids: [] };

        // 1. 모든 dataset 객체 탐색
        var allKeys = Object.keys(workForm);
        for (var ki = 0; ki < allKeys.length; ki++) {
            var key = allKeys[ki];
            try {
                var obj = workForm[key];
                if (obj && typeof obj.getColCount === 'function') {
                    // Dataset 객체 발견
                    var colCount = obj.getColCount();
                    var cols = [];
                    for (var ci = 0; ci < colCount; ci++) {
                        var colId = obj.getColID(ci);
                        var info = obj.getColumnInfo(ci) || {};
                        cols.push({
                            name: colId,
                            type: String(info.type || ''),
                            size: String(info.size || ''),
                        });
                    }

                    // 첫 번째 행 샘플값 (있으면)
                    var sampleRow = {};
                    if (obj.getRowCount() > 0) {
                        for (var si = 0; si < Math.min(colCount, 100); si++) {
                            var scol = obj.getColID(si);
                            try {
                                var val = obj.getColumn(0, scol);
                                sampleRow[scol] = val !== null ? String(val) : null;
                            } catch(e) {}
                        }
                    }

                    result.datasets[key] = {
                        columnCount: colCount,
                        rowCount: obj.getRowCount(),
                        columns: cols,
                        sampleRow: sampleRow,
                    };
                }
            } catch(e) {}
        }

        // 2. Grid 객체 탐색 (바인딩된 dataset 확인)
        for (var gi = 0; gi < allKeys.length; gi++) {
            var gk = allKeys[gi];
            try {
                var gobj = workForm[gk];
                if (gobj && gobj._type_name && gobj._type_name.indexOf('Grid') >= 0) {
                    var bindDs = '';
                    try { bindDs = gobj._binddataset ? gobj._binddataset._id : ''; } catch(e) {}
                    result.grids.push({
                        id: gk,
                        type: gobj._type_name,
                        bindDataset: bindDs,
                    });
                }
            } catch(e) {}
        }

        return JSON.stringify(result);
    } catch(e) {
        return JSON.stringify({error: e.message});
    }
    """)


def main():
    parser = argparse.ArgumentParser(description='selSearch SSV 컬럼 덤프')
    parser.add_argument('--items', type=str, default=None,
                        help='쉼표 구분 상품코드 (기본: 대표상품 4개)')
    parser.add_argument('--save', action='store_true',
                        help='결과를 JSON 파일로 저장')
    parser.add_argument('--nexacro-only', action='store_true',
                        help='넥사크로 dataset 정보만 덤프 (selSearch 미호출)')
    args = parser.parse_args()

    item_codes = args.items.split(',') if args.items else DEFAULT_ITEMS

    # ── 1. 로그인 ──
    print("=" * 70)
    print("selSearch SSV 컬럼 덤프 스크립트")
    print("=" * 70)
    print(f"대상 상품: {item_codes}")
    print()

    sa = SalesAnalyzer()
    sa.setup_driver()
    sa.connect()
    time.sleep(SA_LOGIN_WAIT)

    if not sa.do_login():
        print("❌ 로그인 실패")
        return
    print("✅ 로그인 성공")

    time.sleep(SA_POPUP_CLOSE_WAIT * 2)
    try:
        sa.close_popup()
    except Exception:
        pass
    time.sleep(SA_POPUP_CLOSE_WAIT)

    # ── 2. STBJ030 발주 화면 이동 ──
    oe = OrderExecutor(sa.driver, store_id=sa.store_id)
    if not oe.navigate_to_single_order():
        print("❌ 단품별 발주 메뉴 이동 실패")
        sa.driver.quit()
        return
    print("✅ 단품별 발주 화면 이동 성공")
    time.sleep(2)

    all_results = {
        'timestamp': datetime.now().isoformat(),
        'items': {},
        'nexacro_datasets': None,
        'summary': {},
    }

    # ── 3. 넥사크로 dataset 정보 직접 덤프 ──
    print("\n" + "=" * 70)
    print("Phase 1: 넥사크로 dataset 객체 컬럼 덤프")
    print("=" * 70)

    nx_raw = dump_dataset_columns_via_nexacro(sa.driver)
    if nx_raw:
        try:
            nx_data = json.loads(nx_raw) if isinstance(nx_raw, str) else nx_raw
        except json.JSONDecodeError:
            nx_data = {'error': 'JSON 파싱 실패', 'raw': str(nx_raw)[:500]}

        all_results['nexacro_datasets'] = nx_data

        if 'error' not in nx_data:
            for ds_name, ds_info in nx_data.get('datasets', {}).items():
                col_names = [c['name'] for c in ds_info.get('columns', [])]
                print(f"\n📦 {ds_name} ({ds_info['columnCount']}컬럼, {ds_info['rowCount']}행)")
                for ci, col in enumerate(ds_info.get('columns', []), 1):
                    sample = ds_info.get('sampleRow', {}).get(col['name'], '')
                    sample_str = f' = "{sample}"' if sample else ''
                    print(f"  {ci:3d}. {col['name']:<25s} {col['type']:>10s}:{col['size']}{sample_str}")

                # ★ SUGGEST_QTY 검색
                for target in ['SUGGEST_QTY', 'DISPLAY_QTY', 'RCOM', 'AUTO']:
                    found = [c for c in col_names if target in c.upper()]
                    if found:
                        print(f"  ⭐ '{target}' 매칭 컬럼 발견: {found}")

            for grid in nx_data.get('grids', []):
                print(f"\n🔲 Grid: {grid.get('id', '?')} → bind={grid.get('bindDataset', '?')}")
        else:
            print(f"❌ 넥사크로 접근 실패: {nx_data.get('error')}")

    if args.nexacro_only:
        if args.save:
            _save_results(all_results)
        sa.driver.quit()
        return

    # ── 4. DirectAPI로 selSearch SSV 원본 덤프 ──
    print("\n" + "=" * 70)
    print("Phase 2: selSearch SSV 응답 원본 컬럼 덤프")
    print("=" * 70)

    fetcher = DirectApiFetcher(sa.driver, concurrency=1, timeout_ms=10000)

    # 템플릿 캡처: 먼저 한 상품 UI 검색 실행
    print("\n🔄 요청 템플릿 캡처 중...")
    fetcher.capture_request_template()

    if not fetcher._request_template:
        # UI에서 수동 검색 트리거
        print("  → 인터셉터 설치됨, UI 검색 트리거...")
        _trigger_search_ui(sa.driver, item_codes[0])
        time.sleep(2)
        fetcher.capture_request_template()

    if not fetcher._request_template:
        print("❌ 템플릿 캡처 실패 — 수동으로 한 상품 검색 후 재실행하세요")
        if args.save:
            _save_results(all_results)
        sa.driver.quit()
        return

    print("✅ 템플릿 캡처 성공")

    # ── 5. 각 상품별 SSV 덤프 ──
    all_columns_union = {}  # 데이터셋별 전체 컬럼 합집합

    for item_cd in item_codes:
        print(f"\n{'─' * 50}")
        print(f"상품: {item_cd}")
        print(f"{'─' * 50}")

        ssv_text = fetch_raw_ssv(fetcher, item_cd)
        if not ssv_text:
            print(f"  ❌ 응답 없음")
            all_results['items'][item_cd] = {'error': 'no_response'}
            continue

        print(f"  응답 크기: {len(ssv_text):,} bytes")

        datasets = discover_all_datasets(ssv_text)
        item_result = {
            'response_size': len(ssv_text),
            'dataset_count': len(datasets),
            'datasets': [],
        }

        for ds in datasets:
            ds_name = identify_dataset_name(ds['columns'])
            print(f"\n  📦 {ds_name} ({len(ds['columns'])}컬럼, {ds['row_count']}행)")

            # 컬럼 출력
            for ci, col_type in enumerate(ds['column_types']):
                col_name = ds['columns'][ci]
                sample = ''
                if ds['rows']:
                    sample = ds['rows'][0].get(col_name, '')
                    if len(sample) > 50:
                        sample = sample[:50] + '...'
                sample_str = f' = "{sample}"' if sample and col_name != '_RowType_' else ''
                print(f"    {ci + 1:3d}. {col_name:<25s} ({col_type}){sample_str}")

            # ★ SUGGEST_QTY / DISPLAY_QTY 검색
            for target in ['SUGGEST', 'DISPLAY_QTY', 'RCOM', 'AUTO_ORD', 'NEED']:
                found = [c for c in ds['columns'] if target in c.upper()]
                if found:
                    print(f"    ⭐ '{target}' 매칭 컬럼 발견: {found}")

            # 합집합 추적
            if ds_name not in all_columns_union:
                all_columns_union[ds_name] = set()
            all_columns_union[ds_name].update(ds['columns'])

            # 결과 저장 (JSON 직렬화용)
            ds_serializable = {
                'name': ds_name,
                'columns': ds['columns'],
                'column_types': ds['column_types'],
                'row_count': ds['row_count'],
                'sample_row': ds['rows'][0] if ds['rows'] else {},
            }
            item_result['datasets'].append(ds_serializable)

        all_results['items'][item_cd] = item_result

    # ── 6. 요약 ──
    print("\n" + "=" * 70)
    print("요약: 전 상품 컬럼 합집합")
    print("=" * 70)

    summary = {}
    for ds_name, cols in all_columns_union.items():
        sorted_cols = sorted(cols - {'_RowType_'})
        summary[ds_name] = sorted_cols
        print(f"\n📦 {ds_name} (총 {len(sorted_cols)}컬럼)")
        for ci, col in enumerate(sorted_cols, 1):
            print(f"  {ci:3d}. {col}")

    # ★ 최종 검색
    print("\n" + "=" * 70)
    print("★ 핵심 검색 결과")
    print("=" * 70)

    search_targets = ['SUGGEST', 'DISPLAY', 'RCOM', 'AUTO_ORD', 'NEED', 'SAFE',
                       'MIN_QTY', 'MAX_QTY', 'AVG', 'PRED']
    found_any = False
    for target in search_targets:
        for ds_name, cols in all_columns_union.items():
            matches = [c for c in cols if target in c.upper()]
            if matches:
                print(f"  ✅ '{target}' → {ds_name}: {matches}")
                found_any = True

    if not found_any:
        print("  ❌ SUGGEST_QTY, DISPLAY_QTY 등 추천/진열 관련 컬럼 미발견")
        print("  → BGF 단품별 발주 화면(STBJ030)에는 추천수량 컬럼이 없습니다.")
        print("  → 스마트발주 화면 또는 다른 화면에서 별도 확인이 필요합니다.")

    all_results['summary'] = summary

    # ── 7. 저장 ──
    if args.save:
        _save_results(all_results)

    sa.driver.quit()
    print("\n✅ 완료")


def _trigger_search_ui(driver, item_cd: str):
    """UI에서 상품 검색을 트리거하여 selSearch 요청 생성"""
    try:
        driver.execute_script("""
            var app = nexacro.getApplication();
            var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var stbjForm = null;
            try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}
            if (!stbjForm || !stbjForm.div_workForm) {
                var fKeys = Object.keys(frameSet);
                for (var i = 0; i < fKeys.length; i++) {
                    try {
                        var f = frameSet[fKeys[i]];
                        if (f && f.form && f.form.div_workForm) { stbjForm = f.form; break; }
                    } catch(e) {}
                }
            }
            if (!stbjForm) return;

            var workForm = stbjForm.div_workForm.form.div_work_01.form;
            if (!workForm) return;

            // 바코드 입력 후 검색 트리거
            if (workForm.edtBarcode) {
                workForm.edtBarcode.set_value(arguments[0]);
            }
            if (typeof workForm.fn_search === 'function') {
                workForm.fn_search();
            }
        """, item_cd)
    except Exception as e:
        logger.warning(f"UI 검색 트리거 실패: {e}")


def _save_results(results: dict):
    """결과를 JSON 파일로 저장"""
    # set → list 변환 (JSON 직렬화)
    def convert_sets(obj):
        if isinstance(obj, set):
            return sorted(list(obj))
        if isinstance(obj, dict):
            return {k: convert_sets(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [convert_sets(v) for v in obj]
        return obj

    results = convert_sets(results)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'captures',
        f'selsearch_columns_{ts}.json',
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 저장: {path}")


if __name__ == '__main__':
    main()
