"""폐기전표 페이지 구조 디버깅"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.waste_slip_collector import WasteSlipCollector

# 로그인
analyzer = SalesAnalyzer(store_id='46513')
analyzer.setup_driver()
analyzer.connect()
analyzer.do_login()
analyzer.close_popup()
driver = analyzer.driver

ws = WasteSlipCollector(driver=driver, store_id='46513')

# 메뉴 이동
ws.navigate_to_waste_slip_menu()
time.sleep(2)

# 날짜 설정
ws._set_date_range_and_filter('20260206', '20260206')
time.sleep(1)

# 스크린샷
driver.save_screenshot('data/screenshots/2026-02-19/waste_0206_before_search.png')
print('스크린샷 저장: waste_0206_before_search.png')

# 조회 실행 (WasteSlipCollector의 실제 메서드 사용)
ws._execute_search()
time.sleep(3)

# 스크린샷
driver.save_screenshot('data/screenshots/2026-02-19/waste_0206_after_search.png')
print('스크린샷 저장: waste_0206_after_search.png')

# dsList 데이터 확인
JS_GET_DSLIST = """
var frame = nexacro.getApplication().getActiveFrame();
if (!frame) return JSON.stringify({error: 'no frame'});
var form = frame.form;
var ds = form.lookup('dsList');
if (!ds) return JSON.stringify({error: 'no dsList'});
var rows = [];
for (var i = 0; i < ds.getRowCount(); i++) {
    var row = {};
    for (var j = 0; j < ds.getColCount(); j++) {
        var colId = ds.getColID(j);
        var val = ds.getColumn(i, colId);
        if (val !== null && val !== undefined && typeof val === 'object' && val.hi !== undefined) {
            row[colId] = Number(val.hi);
        } else {
            row[colId] = val;
        }
    }
    rows.push(row);
}
return JSON.stringify(rows);
"""
data = driver.execute_script(JS_GET_DSLIST)
print()
print('=== dsList 헤더 데이터 ===')
parsed = json.loads(data) if isinstance(data, str) else data
if isinstance(parsed, list):
    print(f'전표 수: {len(parsed)}')
    for i, row in enumerate(parsed):
        print(f'  [{i}] {json.dumps(row, ensure_ascii=False)}')
else:
    print(parsed)

# 첫 번째 전표 상세 페이지 열기 시도
if isinstance(parsed, list) and len(parsed) > 0:
    print()
    print('=== 첫 번째 전표 상세 열기 ===')

    # 더블클릭으로 상세 열기
    JS_OPEN_DETAIL = """
    var frame = nexacro.getApplication().getActiveFrame();
    var form = frame.form;
    var grid = form.lookup('grd_list') || form.lookup('Grid00') || form.lookup('Grid01');
    if (!grid) {
        // 그리드 찾기
        var grids = [];
        for (var key in form) {
            if (form[key] && form[key]._type_name === 'Grid') {
                grids.push(key);
            }
        }
        return JSON.stringify({error: 'no grid found', available_grids: grids});
    }

    // 더블클릭 이벤트 발생
    grid.set_clickrow(0);
    if (grid.oncelldblclick) {
        var e = {};
        e.row = 0;
        e.cell = 0;
        grid.oncelldblclick.fireEvent(grid, e);
    }
    return JSON.stringify({grid_id: grid.id, action: 'dblclick_row_0'});
    """
    detail_result = driver.execute_script(JS_OPEN_DETAIL)
    print(detail_result)
    time.sleep(3)

    # 상세 팝업 스크린샷
    driver.save_screenshot('data/screenshots/2026-02-19/waste_0206_detail.png')
    print('상세 스크린샷 저장: waste_0206_detail.png')

    # 상세 팝업의 데이터셋 확인
    JS_DETAIL_DS = """
    var result = {};
    // 팝업 프레임 찾기
    var frames = nexacro.getApplication()._frames;
    var frameIds = [];
    for (var key in frames) {
        if (frames[key]) frameIds.push(key);
    }
    result.frames = frameIds;

    // 활성 프레임의 모든 데이터셋
    var frame = nexacro.getApplication().getActiveFrame();
    if (frame && frame.form) {
        var datasets = [];
        for (var key in frame.form) {
            try {
                if (frame.form[key] && frame.form[key]._type_name === 'Dataset') {
                    var ds = frame.form[key];
                    datasets.push({
                        id: key,
                        rowCount: ds.getRowCount(),
                        colCount: ds.getColCount()
                    });
                }
            } catch(e) {}
        }
        result.datasets = datasets;
    }
    return JSON.stringify(result);
    """
    ds_info = driver.execute_script(JS_DETAIL_DS)
    print()
    print('=== 데이터셋 정보 ===')
    print(json.dumps(json.loads(ds_info), indent=2, ensure_ascii=False))

# 브라우저 유지 (5초 후 닫기)
time.sleep(2)
driver.quit()
print('\n완료')
