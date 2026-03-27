# -*- coding: utf-8 -*-
"""ds_orgMenu 전체 메뉴 트리 + TopFrame 데이터셋 추출"""
import sys, io, time, json
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer"):
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8",
                                                errors="replace", line_buffering=True))

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.sales_analyzer import SalesAnalyzer
from src.settings.timing import SA_LOGIN_WAIT, SA_POPUP_CLOSE_WAIT


def main():
    print("=" * 70)
    print("BGF ds_orgMenu + TopFrame 데이터셋 전체 추출")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    analyzer = SalesAnalyzer()
    try:
        analyzer.setup_driver()
        analyzer.connect()
        time.sleep(SA_LOGIN_WAIT)
        if not analyzer.do_login():
            print("[ERROR] 로그인 실패!")
            return
        print("[OK] 로그인 성공")
        time.sleep(SA_POPUP_CLOSE_WAIT * 2)
        analyzer.close_popup()
        time.sleep(SA_POPUP_CLOSE_WAIT)
        driver = analyzer.driver

        # 이름으로 직접 접근 (typename이 undefined로 반환되므로)
        result = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
                var output = {};

                // 알려진 데이터셋 이름 목록
                var dsNames = ['ds_output', 'Dataset00', 'Dataset01', 'Dataset02',
                              'ds_topMenu', 'ds_orgMenu', 'ds_depth1', 'ds_depth2',
                              'ds_myMenu', 'ds_depth1srn', 'ds_Permission',
                              'ds_weatherCond', 'ds_weatherToday', 'ds_weatherTomorrow',
                              'ds_urgeDeplReqMd', 'ds_UrgentImg',
                              'ds_businessAlarm', 'ds_businessAlarmMessage',
                              'ds_businessAlarmCnt', 'ds_mainAlarm', 'ds_cutvNotice'];

                for (var d = 0; d < dsNames.length; d++) {
                    var name = dsNames[d];
                    var ds = topForm[name];
                    if (!ds || typeof ds.getRowCount !== 'function') continue;

                    var rc = ds.getRowCount();
                    var cc = ds.getColCount();
                    var colNames = [];
                    for (var c = 0; c < cc; c++) colNames.push(ds.getColID(c));

                    var data = [];
                    for (var r = 0; r < rc; r++) {
                        var row = {};
                        for (var c = 0; c < cc; c++) {
                            var v = ds.getColumn(r, ds.getColID(c));
                            row[ds.getColID(c)] = v != null ? String(v) : '';
                        }
                        data.push(row);
                    }

                    output[name] = {rows: rc, cols: cc, columns: colNames, data: data};
                }

                // Global Variables
                var gvars = {};
                var gvNames = ['GV_CHANNELTYPE', 'GV_STORE_CD', 'GV_USER_ID',
                              'GV_USER_NM', 'GV_STORE_NM', 'GV_ORG_CD'];
                for (var i = 0; i < gvNames.length; i++) {
                    try { gvars[gvNames[i]] = app[gvNames[i]] || ''; } catch(e) {}
                }
                output['__globalVars'] = gvars;

                return output;
            } catch(e) {
                return {error: e.message};
            }
        """)

        if result.get("error"):
            print(f"ERROR: {result['error']}")
            return

        # 결과 저장
        out_path = project_root / "data" / "bgf_topframe_datasets.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n전체 데이터 저장: {out_path}")

        # 요약 출력
        for ds_name, ds_info in result.items():
            if ds_name == "__globalVars":
                print(f"\n  Global Variables:")
                for k, v in ds_info.items():
                    if v: print(f"    {k} = {v}")
                continue

            if not isinstance(ds_info, dict) or "rows" not in ds_info:
                continue
            print(f"\n  {ds_name}: {ds_info['rows']}행×{ds_info['cols']}열")
            if ds_info.get("columns"):
                print(f"    컬럼: {', '.join(ds_info['columns'][:15])}")

        # ds_orgMenu 메뉴 트리 출력
        org_menu = result.get("ds_orgMenu", {})
        if org_menu.get("data"):
            print("\n" + "=" * 70)
            print(f"전체 메뉴 트리 (ds_orgMenu {org_menu['rows']}행)")
            print("=" * 70)

            for row in org_menu["data"]:
                level = row.get("LEVEL", "0")
                menu_id = row.get("MENU_ID", "")
                menu_nm = row.get("MENU_NM", "")
                folder = row.get("FOLDER_YN", "")
                url = row.get("URL", "")

                indent = "  " * (int(level) if level.isdigit() else 1)
                marker = "[D]" if folder == "1" else "[S]"
                url_str = f" → {url}" if url else ""
                print(f"  {indent}{marker} {menu_nm} ({menu_id}){url_str}")

        print("\n탐색 완료!")

    finally:
        try:
            analyzer.close()
            print("브라우저 종료")
        except:
            pass


if __name__ == "__main__":
    main()
