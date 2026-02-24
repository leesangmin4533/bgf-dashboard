"""스마트 라디오 버튼 디버그 - rdGubun 구성 확인"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.order_status_collector import OrderStatusCollector
from src.config.ui_config import FRAME_IDS, DS_PATHS


def main():
    print("=" * 60)
    print("스마트 라디오 디버그")
    print("=" * 60)

    # 로그인
    analyzer = SalesAnalyzer()
    analyzer.setup_driver()
    analyzer.connect()

    if not analyzer.do_login():
        print("로그인 실패!")
        analyzer.driver.quit()
        return

    print("로그인 성공!")
    time.sleep(2)

    # 발주현황조회 메뉴 이동 (최대 2회 시도)
    collector = OrderStatusCollector(analyzer.driver)
    menu_ok = False
    for attempt in range(2):
        if collector.navigate_to_order_status_menu():
            menu_ok = True
            break
        print(f"  메뉴 이동 시도 {attempt+1} 실패, 재시도...")
        time.sleep(3)
    if not menu_ok:
        print("메뉴 이동 실패!")
        analyzer.driver.quit()
        return

    print("메뉴 이동 성공!")
    time.sleep(2)

    frame_id = FRAME_IDS["ORDER_STATUS"]
    ds_path = DS_PATHS["ORDER_STATUS"]

    # 자동 라디오 먼저 클릭하여 데이터셋 로딩 확인
    print("\n[0] 자동 라디오 먼저 클릭 (데이터셋 로딩 확인)...")
    if collector.click_auto_radio():
        print("  자동 라디오 클릭 성공")
        time.sleep(2)
    else:
        print("  자동 라디오 클릭 실패")

    # 1. rdGubun 라디오 컴포넌트 전체 분석
    print("\n[1] rdGubun 라디오 컴포넌트 분석...")
    result = analyzer.driver.execute_script(f"""
        try {{
            const app = nexacro.getApplication();
            const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id};
            const wf = frame.form.{ds_path};

            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (!radio) return {{error: 'rdGubun not found'}};

            var info = {{
                exists: true,
                currentValue: radio.value,
                hasSetValue: !!radio.set_value,
                hasGetCount: !!radio.getCount,
                hasGetItemText: !!radio.getItemText,
                hasGetItemValue: !!radio.getItemValue,
                innerdataset: null,
                innerdatasetType: typeof radio.innerdataset,
                items: [],
                itemCount: 0
            }};

            // innerdataset 분석
            var ds = radio.innerdataset;
            if (ds) {{
                if (typeof ds === 'string') {{
                    info.innerdataset = 'string: ' + ds;
                    // 문자열이면 실제 데이터셋을 가져와야 함
                    var actualDs = wf.Div21.form.objects[ds] || wf.Div21.form[ds];
                    if (actualDs && actualDs.getRowCount) {{
                        info.innerdataset = 'resolved from string: ' + ds;
                        info.resolvedRowCount = actualDs.getRowCount();
                    }}
                }} else if (ds.getRowCount) {{
                    info.innerdataset = 'dataset object';
                    info.innerdatasetRowCount = ds.getRowCount();
                }} else {{
                    info.innerdataset = 'object without getRowCount';
                    info.innerdatasetKeys = Object.keys(ds).slice(0, 20);
                }}
            }}

            // getCount/getItemText로 아이템 나열
            if (radio.getCount) {{
                info.itemCount = radio.getCount();
                for (var i = 0; i < radio.getCount(); i++) {{
                    var text = radio.getItemText ? radio.getItemText(i) : '(no getItemText)';
                    var val = radio.getItemValue ? radio.getItemValue(i) : '(no getItemValue)';
                    info.items.push({{index: i, text: text, value: val}});
                }}
            }}

            return info;
        }} catch(e) {{
            return {{error: e.message, stack: e.stack}};
        }}
    """)

    if result and result.get('error'):
        print(f"  오류: {result['error']}")
    elif result:
        print(f"  존재: {result.get('exists')}")
        print(f"  현재 값: {result.get('currentValue')}")
        print(f"  innerdataset 타입: {result.get('innerdatasetType')}")
        print(f"  innerdataset: {result.get('innerdataset')}")
        print(f"  아이템 수 (getCount): {result.get('itemCount')}")
        print(f"  아이템 목록:")
        for item in result.get('items', []):
            marker = " ← 현재" if str(item['value']) == str(result.get('currentValue')) else ""
            print(f"    [{item['index']}] value={item['value']}, text={item['text']}{marker}")
    else:
        print("  결과 없음")

    # 2. "스마트" 텍스트가 실제로 존재하는지 DOM 검색
    print(f"\n[2] 프레임 내 '스마트' 텍스트 검색...")
    result2 = analyzer.driver.execute_script(f"""
        try {{
            var found = [];
            var allEls = document.querySelectorAll('[id*="{frame_id}"] span, [id*="{frame_id}"] div');
            for (var el of allEls) {{
                var txt = el.textContent.trim();
                if (txt === '스마트' || txt.indexOf('스마트') >= 0) {{
                    found.push({{
                        tag: el.tagName,
                        text: txt.substring(0, 50),
                        id: el.id || '',
                        visible: el.offsetParent !== null,
                        parentId: (el.parentElement && el.parentElement.id) || ''
                    }});
                }}
            }}
            return {{count: found.length, elements: found.slice(0, 20)}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)

    if result2 and result2.get('error'):
        print(f"  오류: {result2['error']}")
    elif result2:
        print(f"  '스마트' 포함 요소: {result2.get('count')}개")
        for el in result2.get('elements', []):
            vis = "보임" if el['visible'] else "숨김"
            print(f"    <{el['tag']}> text='{el['text']}' id='{el['id']}' parent='{el['parentId']}' [{vis}]")
    else:
        print("  결과 없음")

    # 3. rdGubun 영역 내 텍스트 검색
    print(f"\n[3] rdGubun 영역 내 텍스트 검색...")
    result3 = analyzer.driver.execute_script("""
        try {
            var found = [];
            var rdEls = document.querySelectorAll('div[id*="rdGubun"] span, div[id*="rdGubun"] div');
            for (var el of rdEls) {
                var txt = el.textContent.trim();
                if (txt.length > 0 && txt.length < 20) {
                    found.push({tag: el.tagName, text: txt, id: el.id || '', visible: el.offsetParent !== null});
                }
            }
            return {count: found.length, elements: found.slice(0, 30)};
        } catch(e) {
            return {error: e.message};
        }
    """)

    if result3 and result3.get('error'):
        print(f"  오류: {result3['error']}")
    elif result3:
        print(f"  rdGubun 내 텍스트 요소: {result3.get('count')}개")
        for el in result3.get('elements', []):
            vis = "보임" if el['visible'] else "숨김"
            print(f"    <{el['tag']}> text='{el['text']}' [{vis}]")
    else:
        print("  결과 없음")

    analyzer.driver.quit()
    print("\n드라이버 종료")


if __name__ == "__main__":
    main()
