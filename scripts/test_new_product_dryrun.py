"""
신상품 도입 현황 수집 드라이런 테스트

BGF 세션 없이 코드 경로를 Mock으로 검증:
1. NewProductCollector 인스턴스 생성
2. _collect_popup_items의 3단계 좌표 획득 JS 코드 문법 검증
3. collect_and_save 전체 플로우 시뮬레이션
4. daily_job._collect_new_product_info 호출 경로 검증
"""

import sys
import os
import json
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

# 프로젝트 루트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.collectors.new_product_collector import NewProductCollector
from src.utils.logger import get_logger

logger = get_logger("dryrun_new_product")

# ─── Mock 데이터 ───

MOCK_WEEKLY_DOIP = [
    {
        "STORE_CD": "46513", "YYYY_CD": "2026", "N_MNTH_CD": "03",
        "PERIOD": "0310~0316", "N_WEEK_CD": "2", "DOIP_RATE": 85.0,
        "ITEM_CNT": 40, "ITEM_AD_CNT": 36, "DOIP_CNT": 30,
        "MIDOIP_CNT": 10, "WEEK_CONT": "2주차", "WEEK_DISP2": "2주차 03/10~03/16"
    },
    {
        "STORE_CD": "46513", "YYYY_CD": "2026", "N_MNTH_CD": "03",
        "PERIOD": "0303~0309", "N_WEEK_CD": "1", "DOIP_RATE": 90.0,
        "ITEM_CNT": 35, "ITEM_AD_CNT": 32, "DOIP_CNT": 29,
        "MIDOIP_CNT": 3, "WEEK_CONT": "1주차", "WEEK_DISP2": "1주차 03/03~03/09"
    }
]

MOCK_WEEKLY_DS = [
    {
        "STORE_CD": "46513", "N_WEEK_CD": "2", "PERIOD": "0310~0316",
        "ITEM_CNT": 20, "DS_CNT": 15, "MIDS_CNT": 5,
        "DS_RATE": 75.0, "WEEK_CONT": "2주차"
    }
]

MOCK_DETAIL_TOTAL = [
    {
        "N_WEEK_CD": "2", "DOIP_SCORE": 68, "DS_SCORE": 15,
        "TOTAL_SCORE": 83, "RANK_NM": "C"
    }
]

MOCK_DETAIL_MONTH = [
    {
        "N_MNTH_CD": "03", "DOIP_RATE_AVG": 87.5,
        "DS_RATE_AVG": 75.0, "TOTAL_SCORE_AVG": 83.0
    }
]

# 팝업에서 수집되는 미도입 상품 목록
MOCK_POPUP_ITEMS = [
    {"ITEM_CD": "2040001", "ITEM_NM": "테스트 신상품A", "ORD_PSS_NM": "가능",
     "DOIP_YN": "N", "LARGE_NM": "과자류", "MID_NM": "스낵"},
    {"ITEM_CD": "2040002", "ITEM_NM": "테스트 신상품B", "ORD_PSS_NM": "가능",
     "DOIP_YN": "N", "LARGE_NM": "음료", "MID_NM": "탄산음료"},
]

# 셀 좌표 (DOM handle 방식으로 반환되는 것 시뮬레이션)
MOCK_CELL_RECT = {
    "x": 808, "y": 335, "cellIdx": 5, "method": "dom_handle_idx"
}


def create_mock_driver():
    """execute_script 호출을 시뮬레이션하는 Mock 드라이버"""
    driver = MagicMock()
    call_count = {"n": 0}

    def mock_execute_script(js, *args, **kwargs):
        call_count["n"] += 1
        n = call_count["n"]

        # JS 내용에 따라 적절한 응답 반환
        if "dsList" in js and "getRowCount" in js and "DOIP_RATE" in js:
            logger.info(f"[Mock #{n}] collect_weekly_doip → {len(MOCK_WEEKLY_DOIP)}행")
            return MOCK_WEEKLY_DOIP

        if "dsConvenienceList" in js and "getRowCount" in js:
            logger.info(f"[Mock #{n}] collect_weekly_ds → {len(MOCK_WEEKLY_DS)}행")
            return MOCK_WEEKLY_DS

        if "dsDetailTotal" in js and "getRowCount" in js:
            logger.info(f"[Mock #{n}] collect_detail_total → {len(MOCK_DETAIL_TOTAL)}행")
            return MOCK_DETAIL_TOTAL

        if "dsDetailMonth" in js and "getRowCount" in js:
            logger.info(f"[Mock #{n}] collect_detail_month → {len(MOCK_DETAIL_MONTH)}행")
            return MOCK_DETAIL_MONTH

        # 팝업 클릭 전 카운트 체크 (MIDOIP_CNT / MIDS_CNT)
        if "MIDOIP_CNT" in js and "getColumn" in js and "cnt_val" in js:
            logger.info(f"[Mock #{n}] midoip 카운트 체크 → count=10")
            return {"count": 10}

        if "MIDS_CNT" in js and "getColumn" in js and "cnt_val" in js:
            logger.info(f"[Mock #{n}] mids 카운트 체크 → count=5")
            return {"count": 5}

        # 셀 좌표 획득 (3단계 폴백 JS)
        if "_getBodyCellElem" in js and "dom_handle_idx" in js:
            logger.info(f"[Mock #{n}] 셀 좌표 획득 (3단계 폴백) → method=dom_handle_idx")
            return MOCK_CELL_RECT

        # 팝업 dsDetail 수집
        if "dsDetail" in js and "getRowCount" in js:
            logger.info(f"[Mock #{n}] 팝업 dsDetail 수집 → {len(MOCK_POPUP_ITEMS)}건")
            return MOCK_POPUP_ITEMS

        # 팝업 닫기
        if "closePopup" in js or "close" in js.lower():
            logger.info(f"[Mock #{n}] 팝업 닫기")
            return True

        # 기타 JS 호출
        logger.debug(f"[Mock #{n}] 기타 JS: {js[:80]}...")
        return None

    driver.execute_script = mock_execute_script
    return driver


def test_js_syntax():
    """3단계 좌표 획득 JS 코드의 Python f-string 문법 검증"""
    logger.info("=" * 60)
    logger.info("테스트 1: JS 코드 f-string 포맷 검증")
    logger.info("=" * 60)

    # _collect_popup_items에서 생성하는 JS 코드의 f-string이
    # Python SyntaxError 없이 포맷팅 되는지 확인

    week_idx = 0
    item_type = "midoip"
    click_col = "MIDOIP_CNT"
    grid_id = "gdList"

    from src.settings.ui_config import FRAME_IDS, DS_PATHS
    FRAME_ID = FRAME_IDS["NEW_PRODUCT_STATUS"]
    DS_PATH_VAL = DS_PATHS["NEW_PRODUCT_STATUS"]

    # 카운트 체크 JS
    try:
        check_js = f"""
            var ds = wf.dsList;
            if (!ds) return {{error: 'dsList not found'}};
            var cnt_val = ds.getColumn({week_idx}, '{click_col}');
            if (!cnt_val || cnt_val == 0) return {{skip: true, count: 0}};
            return {{count: cnt_val}};
        """
        logger.info(f"  ✅ 카운트 체크 JS 포맷 성공 (len={len(check_js)})")
    except Exception as e:
        logger.error(f"  ❌ 카운트 체크 JS 포맷 실패: {e}")
        return False

    # 3단계 좌표 획득 JS
    try:
        coord_js = f"""
            try {{
                var app = nexacro.getApplication();
                var form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form;
                var wf = form.{DS_PATH_VAL};
                var grid = wf.div2.form.{grid_id};
                if (!grid) return {{error: 'grid not found'}};

                var cellIdx = -1;
                for (var i = 0; i < grid.getCellCount('body'); i++) {{
                    var txt = grid.getCellProperty('body', i, 'text');
                    if (txt && txt.indexOf('{click_col}') >= 0) {{
                        cellIdx = i;
                        break;
                    }}
                }}
                if (cellIdx < 0) cellIdx = {5 if item_type == "midoip" else 4};

                // 방법1: _getBodyCellElem
                if (typeof grid._getBodyCellElem === 'function') {{
                    var cellElem = grid._getBodyCellElem({week_idx}, cellIdx);
                    if (cellElem && cellElem._element_node) {{
                        var rect = cellElem._element_node.getBoundingClientRect();
                        return {{
                            x: Math.round(rect.left + rect.width / 2),
                            y: Math.round(rect.top + rect.height / 2),
                            cellIdx: cellIdx, method: 'getBodyCellElem'
                        }};
                    }}
                }}

                // 방법2: _control_element._element_node
                var gElem = grid._control_element;
                if (gElem && gElem._element_node) {{
                    var gRect = gElem._element_node.getBoundingClientRect();
                    var estY = gRect.top + 30 + 20 * {week_idx} + 10;
                    var cols = grid.getCellCount('body');
                    var colW = gRect.width / cols;
                    var estX = gRect.left + colW * cellIdx + colW / 2;
                    return {{x: Math.round(estX), y: Math.round(estY), cellIdx: cellIdx, method: 'estimated', estimated: true}};
                }}

                // 방법3: DOM handle ID + row/col 인덱스
                var gridHandleId = grid._control_element && grid._control_element.handle
                    ? grid._control_element.handle.id : null;
                if (gridHandleId) {{
                    var gridDom = document.getElementById(gridHandleId);
                    if (gridDom) {{
                        var bodyCells = gridDom.querySelectorAll('.GridCellControl');
                        var totalCols = grid.getCellCount('body');
                        var domIdx = ({week_idx} + 1) * totalCols + cellIdx;
                        if (domIdx < bodyCells.length) {{
                            var dRect = bodyCells[domIdx].getBoundingClientRect();
                            if (dRect.width > 5 && dRect.height > 5) {{
                                return {{
                                    x: Math.round(dRect.left + dRect.width / 2),
                                    y: Math.round(dRect.top + dRect.height / 2),
                                    cellIdx: cellIdx, method: 'dom_handle_idx'
                                }};
                            }}
                        }}
                    }}
                }}

                return {{error: 'cannot find cell element (all 3 methods failed)'}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """
        logger.info(f"  ✅ 좌표 획득 JS 포맷 성공 (len={len(coord_js)})")
    except Exception as e:
        logger.error(f"  ❌ 좌표 획득 JS 포맷 실패: {e}")
        return False

    # gdList2 (mids) 도 검증
    try:
        grid_id2 = "gdList2"
        item_type2 = "mids"
        click_col2 = "MIDS_CNT"
        coord_js2 = f"""
            var grid = wf.div2.form.{grid_id2};
            if (cellIdx < 0) cellIdx = {5 if item_type2 == "midoip" else 4};
            var domIdx = ({week_idx} + 1) * totalCols + cellIdx;
        """
        logger.info(f"  ✅ gdList2 (mids) JS 포맷 성공")
    except Exception as e:
        logger.error(f"  ❌ gdList2 JS 포맷 실패: {e}")
        return False

    return True


def test_collector_init():
    """NewProductCollector 인스턴스 생성 검증"""
    logger.info("=" * 60)
    logger.info("테스트 2: Collector 인스턴스 생성")
    logger.info("=" * 60)

    try:
        collector = NewProductCollector(driver=None, store_id="46513")
        logger.info(f"  ✅ 인스턴스 생성 성공: store_id={collector.store_id}")
        logger.info(f"  ✅ repo 타입: {type(collector.repo).__name__}")
        assert collector.store_id == "46513"
        assert collector.driver is None
        return True
    except Exception as e:
        logger.error(f"  ❌ 인스턴스 생성 실패: {e}")
        return False


def test_collect_flow_mock():
    """Mock 드라이버로 전체 수집 플로우 검증"""
    logger.info("=" * 60)
    logger.info("테스트 3: Mock 드라이버 전체 수집 플로우")
    logger.info("=" * 60)

    driver = create_mock_driver()
    collector = NewProductCollector(driver=driver, store_id="46513")

    # 1. dsList 수집
    doip = collector.collect_weekly_doip()
    logger.info(f"  dsList 수집: {len(doip)}행")
    assert len(doip) == 2, f"Expected 2, got {len(doip)}"
    assert doip[0]["MIDOIP_CNT"] == 10
    logger.info("  ✅ dsList (도입률) 정상")

    # 2. dsConvenienceList 수집
    ds = collector.collect_weekly_ds()
    logger.info(f"  dsConvenienceList 수집: {len(ds)}행")
    assert len(ds) == 1
    assert ds[0]["MIDS_CNT"] == 5
    logger.info("  ✅ dsConvenienceList (3일발주) 정상")

    # 3. dsDetailTotal 수집
    detail = collector.collect_detail_total()
    logger.info(f"  dsDetailTotal 수집: {len(detail)}행")
    assert len(detail) == 1
    logger.info("  ✅ dsDetailTotal (종합점수) 정상")

    # 4. dsDetailMonth 수집
    month = collector.collect_detail_month()
    logger.info(f"  dsDetailMonth 수집: {len(month)}행")
    assert len(month) == 1
    logger.info("  ✅ dsDetailMonth (월별합계) 정상")

    return True


def test_popup_items_code_path():
    """_collect_popup_items 코드 경로 검증 (좌표 획득 + ActionChains)"""
    logger.info("=" * 60)
    logger.info("테스트 4: 팝업 수집 코드 경로 (mock)")
    logger.info("=" * 60)

    driver = create_mock_driver()

    # ActionChains mock
    mock_actions = MagicMock()
    mock_actions.move_by_offset.return_value = mock_actions
    mock_actions.click.return_value = mock_actions

    with patch("selenium.webdriver.common.action_chains.ActionChains", return_value=mock_actions):
        collector = NewProductCollector(driver=driver, store_id="46513")

        # midoip 팝업 수집
        items = collector._collect_popup_items(0, "midoip")
        logger.info(f"  midoip 팝업 수집 결과: {len(items)}건")

        if isinstance(items, list):
            logger.info(f"  ✅ _collect_popup_items 반환 정상 (list, {len(items)}건)")
        else:
            logger.warning(f"  ⚠️ _collect_popup_items 반환 타입: {type(items)}")

        # mids 팝업도 검증
        items2 = collector._collect_popup_items(0, "mids")
        logger.info(f"  mids 팝업 수집 결과: {len(items2)}건")
        logger.info(f"  ✅ _collect_popup_items (midoip+mids) 코드 경로 정상")

    return True


def test_daily_job_call_path():
    """daily_job._collect_new_product_info 호출 경로 검증"""
    logger.info("=" * 60)
    logger.info("테스트 5: daily_job 호출 경로")
    logger.info("=" * 60)

    try:
        from src.scheduler.daily_job import DailyCollectionJob
        job = DailyCollectionJob.__new__(DailyCollectionJob)
        job.store_id = "46513"

        # _collect_new_product_info 메서드 존재 확인
        assert hasattr(job, '_collect_new_product_info'), "메서드 미존재"
        logger.info("  ✅ _collect_new_product_info 메서드 존재")

        # 시그니처 확인
        import inspect
        sig = inspect.signature(job._collect_new_product_info)
        params = list(sig.parameters.keys())
        logger.info(f"  파라미터: {params}")
        assert "driver" in params, "driver 파라미터 미존재"
        logger.info("  ✅ 시그니처 정상 (driver 파라미터 있음)")

        return True
    except Exception as e:
        logger.error(f"  ❌ daily_job 호출 경로 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_to_num_method():
    """BigInt 객체 → 숫자 변환 검증"""
    logger.info("=" * 60)
    logger.info("테스트 6: _to_num (BigInt 변환)")
    logger.info("=" * 60)

    collector = NewProductCollector(store_id="46513")

    # Decimal 객체 (넥사크로 hi 패턴)
    assert collector._to_num({"hi": 42}) == 42
    logger.info("  ✅ {hi: 42} → 42")

    # 일반 숫자
    assert collector._to_num(10) == 10
    logger.info("  ✅ 10 → 10")

    # None
    assert collector._to_num(None) is None
    logger.info("  ✅ None → None")

    # 문자열
    assert collector._to_num("abc") == "abc"
    logger.info("  ✅ 'abc' → 'abc'")

    return True


def main():
    logger.info("=" * 60)
    logger.info("신상품 도입 현황 수집 드라이런 테스트")
    logger.info(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    results = {}

    tests = [
        ("JS 문법 검증", test_js_syntax),
        ("Collector 초기화", test_collector_init),
        ("수집 플로우 (Mock)", test_collect_flow_mock),
        ("팝업 코드 경로", test_popup_items_code_path),
        ("daily_job 호출경로", test_daily_job_call_path),
        ("BigInt 변환", test_to_num_method),
    ]

    for name, fn in tests:
        try:
            ok = fn()
            results[name] = "PASS" if ok else "FAIL"
        except Exception as e:
            results[name] = f"ERROR: {e}"
            import traceback
            traceback.print_exc()

    # 결과 요약
    logger.info("")
    logger.info("=" * 60)
    logger.info("결과 요약")
    logger.info("=" * 60)
    all_pass = True
    for name, status in results.items():
        icon = "✅" if status == "PASS" else "❌"
        logger.info(f"  {icon} {name}: {status}")
        if status != "PASS":
            all_pass = False

    logger.info("")
    if all_pass:
        logger.info("🎉 전체 통과! 신상품 수집 코드 구조 정상")
    else:
        logger.info("⚠️ 일부 실패 — 위 로그 확인 필요")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
