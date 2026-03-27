"""
발주 현황 조회 > 일반 탭 구조 탐색 스크립트

사용법:
    cd bgf_auto
    python scripts/explore_normal_tab.py [--store-id 46513]

확인 항목:
    1. rdGubun='1' (일반) 라디오 전환 동작 여부
    2. dsResult 컬럼 목록 + 샘플 데이터
    3. dsOrderSale 컬럼 목록 + 샘플 데이터
    4. ORD_YMD 포맷, ORD_CNT/ORD_UNIT_QTY 값 확인
    5. 각 탭별(전체/일반/자동/스마트) 행 수 비교
"""

import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sales_analyzer import SalesAnalyzer
from src.settings.constants import DEFAULT_STORE_ID
from src.settings.ui_config import FRAME_IDS, DS_PATHS
from src.utils.nexacro_helpers import (
    click_menu_by_text, click_submenu_by_text, wait_for_frame,
    close_tab_by_frame_id, JS_CLICK_HELPER,
)

FRAME_ID = FRAME_IDS["ORDER_STATUS"]
DS_PATH = DS_PATHS["ORDER_STATUS"]


def dump_dataset_structure(driver, ds_name: str, max_rows: int = 5) -> dict:
    """넥사크로 Dataset의 컬럼 목록 + 샘플 데이터 덤프"""
    result = driver.execute_script(f"""
        try {{
            const app = nexacro.getApplication();
            const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                .{FRAME_ID}.form.{DS_PATH};
            const ds = wf?.{ds_name};
            if (!ds) return {{error: '{ds_name} not found'}};

            // 1. 컬럼 목록 추출
            const colInfos = ds._colinfos || ds.colinfos || [];
            const columns = [];
            if (colInfos && colInfos.length) {{
                for (let c = 0; c < colInfos.length; c++) {{
                    const ci = colInfos[c];
                    columns.push({{
                        name: ci.id || ci.name || ('col_' + c),
                        type: ci.type || 'unknown',
                        size: ci.size || 0
                    }});
                }}
            }}

            // 1-b. 컬럼 정보가 비어있으면 첫 행에서 추출 시도
            if (columns.length === 0 && ds.getRowCount() > 0) {{
                // getColCount + getColID 방식
                if (ds.getColCount) {{
                    for (let c = 0; c < ds.getColCount(); c++) {{
                        columns.push({{
                            name: ds.getColID ? ds.getColID(c) : ('col_' + c),
                            type: 'inferred',
                            size: 0
                        }});
                    }}
                }}
            }}

            // 2. 샘플 데이터 (최대 max_rows개)
            function getVal(row, col) {{
                let val = ds.getColumn(row, col);
                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                return val !== null && val !== undefined ? String(val) : null;
            }}

            const colNames = columns.map(c => c.name);
            const samples = [];
            const rowCount = ds.getRowCount();
            const limit = Math.min(rowCount, {max_rows});

            for (let r = 0; r < limit; r++) {{
                const row = {{}};
                for (const cn of colNames) {{
                    row[cn] = getVal(r, cn);
                }}
                samples.push(row);
            }}

            return {{
                dataset: '{ds_name}',
                row_count: rowCount,
                col_count: columns.length,
                columns: columns,
                samples: samples
            }};
        }} catch(e) {{
            return {{error: e.message, stack: e.stack}};
        }}
    """)
    return result or {"error": "no result"}


def click_radio_and_get_count(driver, value: str, label: str) -> dict:
    """라디오 전환 후 dsResult 행 수 반환"""
    result = driver.execute_script(JS_CLICK_HELPER + f"""
        try {{
            const app = nexacro.getApplication();
            const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                .{FRAME_ID}.form.{DS_PATH};

            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (!radio || !radio.set_value) {{
                return {{error: 'rdGubun not found'}};
            }}

            // 현재 값 기록
            const before = radio.value;

            // 라디오 전환
            radio.set_value('{value}');
            if (radio.on_fire_onitemchanged) {{
                radio.on_fire_onitemchanged(radio, {{}});
            }}

            return {{
                success: true,
                label: '{label}',
                value: '{value}',
                before_value: before
            }};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    return result or {"error": "no result"}


def get_ds_row_count(driver, ds_name: str) -> int:
    """Dataset의 현재 행 수"""
    result = driver.execute_script(f"""
        try {{
            const wf = nexacro.getApplication().mainframe.HFrameSet00
                .VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            const ds = wf?.{ds_name};
            return ds ? ds.getRowCount() : -1;
        }} catch(e) {{ return -2; }}
    """)
    return result if isinstance(result, int) else -3


def get_known_columns_sample(driver, ds_name: str, columns: list, max_rows: int = 3) -> list:
    """알려진 컬럼으로 직접 샘플 추출 (colinfos 실패 시 폴백)"""
    cols_js = json.dumps(columns)
    result = driver.execute_script(f"""
        try {{
            const wf = nexacro.getApplication().mainframe.HFrameSet00
                .VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
            const ds = wf?.{ds_name};
            if (!ds) return {{error: '{ds_name} not found'}};

            const cols = {cols_js};
            function getVal(row, col) {{
                let val = ds.getColumn(row, col);
                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                return val !== null && val !== undefined ? String(val) : null;
            }}

            const samples = [];
            const limit = Math.min(ds.getRowCount(), {max_rows});
            for (let r = 0; r < limit; r++) {{
                const row = {{}};
                for (const c of cols) {{
                    row[c] = getVal(r, c);
                }}
                samples.push(row);
            }}

            return {{
                dataset: '{ds_name}',
                row_count: ds.getRowCount(),
                samples: samples
            }};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    return result or {"error": "no result"}


def explore(store_id: str = DEFAULT_STORE_ID):
    """메인 탐색"""
    print(f"\n{'='*60}")
    print(f"  발주 현황 조회 > 일반 탭 구조 탐색")
    print(f"  store_id: {store_id}")
    print(f"  time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    analyzer = SalesAnalyzer(store_id=store_id)

    try:
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] Login failed")
            return

        analyzer.close_popup()
        print("[OK] Login success\n")

        driver = analyzer.driver

        # 1. 발주 현황 조회 메뉴 이동
        print("[Step 1] 발주 현황 조회 메뉴 이동...")
        if not click_menu_by_text(driver, '발주'):
            print("[ERROR] 발주 메뉴 클릭 실패")
            return
        time.sleep(1)

        if not click_submenu_by_text(driver, '발주 현황 조회'):
            print("[ERROR] 발주 현황 조회 서브메뉴 클릭 실패")
            return
        time.sleep(2)

        if not wait_for_frame(driver, FRAME_ID):
            print("[ERROR] 프레임 로딩 실패")
            return
        print("[OK] 발주 현황 조회 화면 로딩 완료\n")

        # 기존 코드에서 사용하는 dsResult 컬럼 목록 (검증용)
        known_ds_result_cols = [
            "ORD_YMD", "ITEM_CD", "ITEM_NM", "MID_CD", "MID_NM",
            "ORD_CNT", "ORD_UNIT_QTY", "ITEM_WONGA", "NOW_QTY",
            "NAP_NEXTORD", "ORD_INPUT_ID", "ORD_PSS_ID", "ORD_PSS_NM"
        ]
        known_ds_order_sale_cols = [
            "ORD_YMD", "ITEM_CD", "ORD_QTY", "BUY_QTY",
            "SALE_QTY", "DISUSE_QTY"
        ]

        # 2. 각 탭별 행 수 비교
        print(f"{'='*60}")
        print("  [Step 2] 탭별 dsResult 행 수 비교")
        print(f"{'='*60}")

        tab_info = [
            ("0", "전체"),
            ("1", "일반"),
            ("2", "자동"),
            ("3", "스마트"),
        ]

        tab_counts = {}
        for val, label in tab_info:
            click_result = click_radio_and_get_count(driver, val, label)
            if click_result.get("error"):
                print(f"  [{label}(rdGubun={val})] ERROR: {click_result['error']}")
                tab_counts[label] = -1
                continue

            time.sleep(1.5)  # 데이터 갱신 대기

            count = get_ds_row_count(driver, "dsResult")
            tab_counts[label] = count
            print(f"  [{label}(rdGubun={val})] dsResult 행 수: {count}")

        print()

        # 3. 일반 탭 상세 구조 탐색
        print(f"{'='*60}")
        print("  [Step 3] 일반 탭(rdGubun=1) dsResult 상세 구조")
        print(f"{'='*60}")

        # 일반 탭으로 전환
        click_radio_and_get_count(driver, "1", "일반")
        time.sleep(1.5)

        # 3-a. colinfos 기반 컬럼 구조 덤프
        ds_result_info = dump_dataset_structure(driver, "dsResult", max_rows=5)
        if ds_result_info.get("error"):
            print(f"  [dsResult colinfos] ERROR: {ds_result_info['error']}")
            print("  -> 알려진 컬럼으로 직접 추출 시도...")
            ds_result_info = get_known_columns_sample(
                driver, "dsResult", known_ds_result_cols, max_rows=5
            )

        print(f"\n  dsResult 구조:")
        print(f"    행 수: {ds_result_info.get('row_count', '?')}")
        print(f"    컬럼 수: {ds_result_info.get('col_count', '?')}")

        if ds_result_info.get("columns"):
            print(f"\n    컬럼 목록:")
            for col in ds_result_info["columns"]:
                print(f"      - {col['name']} (type={col.get('type','?')}, size={col.get('size','?')})")

        if ds_result_info.get("samples"):
            print(f"\n    샘플 데이터 ({len(ds_result_info['samples'])}건):")
            for i, row in enumerate(ds_result_info["samples"]):
                print(f"\n    --- Row {i} ---")
                for k, v in row.items():
                    print(f"      {k}: {v}")
        print()

        # 3-b. dsOrderSale 구조 덤프
        print(f"{'='*60}")
        print("  [Step 4] 일반 탭(rdGubun=1) dsOrderSale 상세 구조")
        print(f"{'='*60}")

        ds_order_sale_info = dump_dataset_structure(driver, "dsOrderSale", max_rows=3)
        if ds_order_sale_info.get("error"):
            print(f"  [dsOrderSale colinfos] ERROR: {ds_order_sale_info['error']}")
            print("  -> 알려진 컬럼으로 직접 추출 시도...")
            ds_order_sale_info = get_known_columns_sample(
                driver, "dsOrderSale", known_ds_order_sale_cols, max_rows=3
            )

        print(f"\n  dsOrderSale 구조:")
        print(f"    행 수: {ds_order_sale_info.get('row_count', '?')}")

        if ds_order_sale_info.get("columns"):
            print(f"\n    컬럼 목록:")
            for col in ds_order_sale_info["columns"]:
                print(f"      - {col['name']} (type={col.get('type','?')})")

        if ds_order_sale_info.get("samples"):
            print(f"\n    샘플 데이터 ({len(ds_order_sale_info['samples'])}건):")
            for i, row in enumerate(ds_order_sale_info["samples"]):
                print(f"\n    --- Row {i} ---")
                for k, v in row.items():
                    print(f"      {k}: {v}")
        print()

        # 4. 알려진 컬럼으로 직접 값 확인 (가장 신뢰할 수 있는 방법)
        print(f"{'='*60}")
        print("  [Step 5] 일반 탭 알려진 컬럼 직접 값 확인")
        print(f"{'='*60}")

        direct_result = get_known_columns_sample(
            driver, "dsResult", known_ds_result_cols, max_rows=5
        )
        if direct_result.get("error"):
            print(f"  ERROR: {direct_result['error']}")
        else:
            print(f"\n  dsResult (알려진 컬럼 직접 조회): {direct_result.get('row_count', '?')}행")
            for i, row in enumerate(direct_result.get("samples", [])):
                print(f"\n  --- Row {i} ---")
                for k, v in row.items():
                    marker = ""
                    if v is None:
                        marker = " <-- NULL!"
                    print(f"    {k}: {v}{marker}")

        direct_sale = get_known_columns_sample(
            driver, "dsOrderSale", known_ds_order_sale_cols, max_rows=3
        )
        if not direct_sale.get("error"):
            print(f"\n  dsOrderSale (알려진 컬럼 직접 조회): {direct_sale.get('row_count', '?')}행")
            for i, row in enumerate(direct_sale.get("samples", [])):
                print(f"\n  --- Row {i} ---")
                for k, v in row.items():
                    marker = ""
                    if v is None:
                        marker = " <-- NULL!"
                    print(f"    {k}: {v}{marker}")
        print()

        # 5. ORD_INPUT_ID 분석 (수동 vs 시스템 구분 가능 여부)
        print(f"{'='*60}")
        print("  [Step 6] ORD_INPUT_ID 분포 분석 (전체 탭)")
        print(f"{'='*60}")

        click_radio_and_get_count(driver, "0", "전체")
        time.sleep(1.5)

        input_id_result = driver.execute_script(f"""
            try {{
                const wf = nexacro.getApplication().mainframe.HFrameSet00
                    .VFrameSet00.FrameSet.{FRAME_ID}.form.{DS_PATH};
                const ds = wf?.dsResult;
                if (!ds) return {{error: 'dsResult not found'}};

                function getVal(row, col) {{
                    let val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val !== null && val !== undefined ? String(val) : '';
                }}

                // ORD_INPUT_ID 별 건수 + ORD_PSS_ID/ORD_PSS_NM 분포
                const inputIdCounts = {{}};
                const pssIdCounts = {{}};
                for (let i = 0; i < ds.getRowCount(); i++) {{
                    const inputId = getVal(i, 'ORD_INPUT_ID') || '(empty)';
                    const pssId = getVal(i, 'ORD_PSS_ID') || '(empty)';
                    const pssNm = getVal(i, 'ORD_PSS_NM') || '(empty)';
                    inputIdCounts[inputId] = (inputIdCounts[inputId] || 0) + 1;
                    const key = pssId + ':' + pssNm;
                    pssIdCounts[key] = (pssIdCounts[key] || 0) + 1;
                }}

                return {{
                    total: ds.getRowCount(),
                    ord_input_id_dist: inputIdCounts,
                    ord_pss_dist: pssIdCounts
                }};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if input_id_result and not input_id_result.get("error"):
            print(f"\n  전체 행 수: {input_id_result.get('total', '?')}")
            print(f"\n  ORD_INPUT_ID 분포:")
            for k, v in input_id_result.get("ord_input_id_dist", {}).items():
                print(f"    {k}: {v}건")
            print(f"\n  ORD_PSS_ID:ORD_PSS_NM 분포:")
            for k, v in input_id_result.get("ord_pss_dist", {}).items():
                print(f"    {k}: {v}건")
        else:
            print(f"  ERROR: {input_id_result}")
        print()

        # 6. 결과 요약
        print(f"{'='*60}")
        print("  [Summary] 탐색 결과 요약")
        print(f"{'='*60}")
        print(f"\n  탭별 행 수:")
        for label, count in tab_counts.items():
            print(f"    {label}: {count}건")
        if tab_counts.get("전체", 0) > 0:
            total = tab_counts["전체"]
            parts = tab_counts.get("일반", 0) + tab_counts.get("자동", 0) + tab_counts.get("스마트", 0)
            print(f"\n  전체({total}) vs 일반+자동+스마트({parts})")
            if total == parts:
                print("  => 정확히 일치 (3개 탭 = 전체의 부분집합)")
            else:
                print(f"  => 차이 {total - parts}건 (기타 유형 존재 가능)")
        print()

        # JSON으로 전체 결과 저장
        output = {
            "timestamp": datetime.now().isoformat(),
            "store_id": store_id,
            "tab_counts": tab_counts,
            "dsResult_normal_tab": ds_result_info,
            "dsOrderSale_normal_tab": ds_order_sale_info,
            "dsResult_direct_sample": direct_result,
            "dsOrderSale_direct_sample": direct_sale,
            "input_id_distribution": input_id_result,
        }
        output_path = PROJECT_ROOT / "data" / "explore_normal_tab_result.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"  결과 저장: {output_path}")
        print()

    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()

    finally:
        if analyzer.driver:
            try:
                close_tab_by_frame_id(analyzer.driver, FRAME_ID)
            except Exception:
                pass
            analyzer.driver.quit()
            print("[OK] 브라우저 종료")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="발주 현황 조회 일반 탭 구조 탐색")
    parser.add_argument("--store-id", default=DEFAULT_STORE_ID, help="매장 코드")
    args = parser.parse_args()
    explore(store_id=args.store_id)
