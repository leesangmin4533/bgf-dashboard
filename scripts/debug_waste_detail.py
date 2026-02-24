"""폐기전표 상세 품목 확인 - 2/6 전표 더블클릭"""
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

# 날짜 설정 + 조회
ws._set_date_range_and_filter('20260206', '20260206')
time.sleep(1)
ws._execute_search()
time.sleep(3)

# dsList 확인 - 실제 데이터셋 이름 찾기
ds_names = driver.execute_script("""
    var frame = nexacro.getApplication().getActiveFrame();
    var form = frame.form;
    var datasets = [];
    for (var key in form) {
        try {
            if (form[key] && form[key]._type_name === 'Dataset') {
                datasets.push({id: key, rows: form[key].getRowCount(), cols: form[key].getColCount()});
            }
        } catch(e) {}
    }
    return JSON.stringify(datasets);
""")
print("=== 데이터셋 목록 ===")
ds_list = json.loads(ds_names)
for ds in ds_list:
    print(f"  {ds['id']}: {ds['rows']}행 x {ds['cols']}열")

# 메인 데이터셋 내용 확인 (rows > 0인 것)
for ds in ds_list:
    if ds['rows'] > 0 and ds['rows'] < 100:
        ds_data = driver.execute_script(f"""
            var frame = nexacro.getApplication().getActiveFrame();
            var ds = frame.form.lookup('{ds['id']}');
            if (!ds) return 'null';
            var rows = [];
            for (var i = 0; i < ds.getRowCount(); i++) {{
                var row = {{}};
                for (var j = 0; j < ds.getColCount(); j++) {{
                    var colId = ds.getColID(j);
                    var val = ds.getColumn(i, colId);
                    if (val !== null && val !== undefined && typeof val === 'object' && val.hi !== undefined) {{
                        row[colId] = Number(val.hi);
                    }} else {{
                        row[colId] = val;
                    }}
                }}
                rows.push(row);
            }}
            return JSON.stringify(rows);
        """)
        print(f"\n=== {ds['id']} 데이터 ===")
        if ds_data and ds_data != 'null':
            parsed = json.loads(ds_data)
            for i, row in enumerate(parsed):
                print(f"  [{i}] {json.dumps(row, ensure_ascii=False)}")

# 첫 번째 전표 상세 열기
print("\n=== 전표 상세 열기 ===")
detail_items = ws._collect_slip_details(0)
print(f"상세 품목: {detail_items}")

time.sleep(2)
driver.save_screenshot('data/screenshots/2026-02-19/waste_0206_detail_popup.png')
print("상세 스크린샷 저장")

# 두 번째 전표 상세
detail_items2 = ws._collect_slip_details(1)
print(f"\n두 번째 전표 품목: {detail_items2}")

time.sleep(2)
driver.save_screenshot('data/screenshots/2026-02-19/waste_0206_detail2.png')

driver.quit()
print('\n완료')
