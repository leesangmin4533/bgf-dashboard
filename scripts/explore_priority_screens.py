# -*- coding: utf-8 -*-
"""
BGF 리테일 우선순위 화면 상세 분석 - 타겟 5개 + 기존 5개
핵심: 각 화면 분석 후 브라우저 리프레시로 탭 리셋
"""
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

TOP_PREFIX = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.div_topMenu.form."
SUB_PREFIX = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.pdiv_topMenu_"


def dismiss_alert(driver):
    try:
        alert = driver.switch_to.alert
        alert.accept()
        time.sleep(0.3)
        return True
    except:
        return False


def click_dom(driver, dom_id, wait=0.3):
    try:
        r = driver.execute_script(f"""
            var el = document.getElementById('{dom_id}');
            if (!el) return 'not_found';
            if (el.offsetParent === null) return 'hidden';
            el.scrollIntoView({{block:'center', inline:'center'}});
            var r = el.getBoundingClientRect();
            var o = {{bubbles:true, cancelable:true, view:window,
                      clientX:r.left+r.width/2, clientY:r.top+r.height/2}};
            el.dispatchEvent(new MouseEvent('mousedown', o));
            el.dispatchEvent(new MouseEvent('mouseup', o));
            el.dispatchEvent(new MouseEvent('click', o));
            return 'ok';
        """)
        if wait > 0:
            time.sleep(wait)
        return r
    except:
        dismiss_alert(driver)
        return "error"


def navigate_to_screen(driver, parent_id, sub_id):
    """상위→서브 클릭"""
    top_dom = f"{TOP_PREFIX}{parent_id}:icontext"
    click_dom(driver, top_dom, wait=0.8)
    dismiss_alert(driver)

    for suffix in [":text", ":icontext", ""]:
        sub_dom = f"{SUB_PREFIX}{parent_id}.form.{sub_id}{suffix}"
        r = click_dom(driver, sub_dom, wait=0.3)
        dismiss_alert(driver)
        if r == "ok":
            return True
    return False


def install_interceptor(driver):
    """XHR body+response 캡처 인터셉터 설치"""
    driver.execute_script("""
        window.__xhrLog = [];
        var oO = XMLHttpRequest.prototype.open, oS = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function(m,u) { this.__u=u; this.__m=m; return oO.apply(this,arguments); };
        XMLHttpRequest.prototype.send = function(b) {
            var self = this;
            var u = self.__u || '';
            if (u && u.indexOf('.xfdl') < 0 && u.indexOf('.js') < 0
                && (u.indexOf('/st') >= 0 || u.indexOf('/ST') >= 0
                    || u.indexOf('/ss') >= 0 || u.indexOf('/SS') >= 0)) {
                var entry = {url: u, method: self.__m,
                    body: b ? b.substring(0, 2000) : '', ts: new Date().toISOString()};
                self.addEventListener('load', function() {
                    entry.status = self.status;
                    entry.respLen = self.responseText ? self.responseText.length : 0;
                    entry.respPreview = self.responseText ? self.responseText.substring(0, 500) : '';
                });
                window.__xhrLog.push(entry);
            }
            return oS.apply(this, arguments);
        };
    """)


def explore_workframe_deep(driver):
    """WorkFrame 상세 분석 (데이터셋 내용 샘플 포함)"""
    for _ in range(16):
        check = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                return !!(wf && wf.form);
            } catch(e) { return false; }
        """)
        if check:
            break
        time.sleep(0.5)

    return driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var info = {frameId: '', datasets: [], grids: [], buttons: [], combos: []};
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            if (!wf || !wf.form) return info;
            info.frameId = wf.formurl || '';

            function scanForm(f, prefix) {
                if (!f) return;
                // objects 배열에서 Dataset 찾기 (duck typing)
                if (f.objects) {
                    for (var i = 0; i < f.objects.length; i++) {
                        var obj = f.objects[i];
                        if (!obj || !obj.name) continue;
                        if (typeof obj.getRowCount === 'function') {
                            var cc = obj.getColCount(), rc = obj.getRowCount();
                            var cn = [];
                            for (var c = 0; c < Math.min(cc, 60); c++) cn.push(obj.getColID(c));

                            // 샘플 데이터 (최대 3행)
                            var sample = [];
                            for (var r = 0; r < Math.min(rc, 3); r++) {
                                var row = {};
                                for (var c = 0; c < Math.min(cc, 20); c++) {
                                    var v = obj.getColumn(r, obj.getColID(c));
                                    if (v != null) row[obj.getColID(c)] = String(v);
                                }
                                sample.push(row);
                            }

                            info.datasets.push({
                                name: prefix+obj.name, cols: cc, rows: rc,
                                columns: cn, sample: sample
                            });
                        }
                    }
                }
                // 이름으로 직접 접근 시도 (objects에 안 나올 수 있음)
                var knownDs = ['dsSearch', 'dsList', 'dsDetail', 'dsResult', 'dsGrid',
                              'dsMaster', 'dsItem', 'dsLarge', 'dsMid', 'dsSmall',
                              'ds_list', 'ds_detail', 'ds_search', 'ds_result'];
                for (var d = 0; d < knownDs.length; d++) {
                    var ds = f[knownDs[d]];
                    if (ds && typeof ds.getRowCount === 'function') {
                        // 이미 추가됐는지 확인
                        var exists = info.datasets.some(function(x) { return x.name === prefix+knownDs[d]; });
                        if (!exists) {
                            var cc = ds.getColCount(), rc = ds.getRowCount();
                            var cn = [];
                            for (var c = 0; c < Math.min(cc, 60); c++) cn.push(ds.getColID(c));
                            info.datasets.push({
                                name: prefix+knownDs[d]+' (직접접근)', cols: cc, rows: rc, columns: cn
                            });
                        }
                    }
                }

                if (f.components) {
                    for (var i = 0; i < f.components.length; i++) {
                        var comp = f.components[i];
                        if (!comp) continue;
                        if (comp.typename === 'Grid')
                            info.grids.push({name: prefix+comp.name, bind: comp.binddataset||''});
                        if (comp.typename === 'Button' && comp.name)
                            info.buttons.push({name: prefix+comp.name, text: comp.text||''});
                        if (comp.typename === 'Combo' && comp.name)
                            info.combos.push({name: prefix+comp.name, bind: comp.binddataset||''});
                        if (comp.form) scanForm(comp.form, prefix+comp.name+'.');
                    }
                }
            }
            scanForm(wf.form, '');
            return info;
        } catch(e) {
            return {error: e.message};
        }
    """) or {}


def reset_tabs(driver):
    """페이지 리프레시로 탭 리셋 (7개 제한 우회)"""
    driver.refresh()
    time.sleep(3)
    # 넥사크로 재로딩 대기
    for _ in range(20):
        try:
            ready = driver.execute_script("""
                try { return !!nexacro.getApplication().mainframe; } catch(e) { return false; }
            """)
            if ready:
                break
        except:
            pass
        time.sleep(0.5)
    dismiss_alert(driver)
    # 팝업 닫기
    try:
        driver.execute_script("""
            var app = nexacro.getApplication();
            var form = app.mainframe.HFrameSet00.LoginFrame.form.div_login.form;
            if (form && form.btn_close) form.btn_close.click();
        """)
    except:
        pass
    time.sleep(0.5)
    dismiss_alert(driver)
    install_interceptor(driver)


def main():
    print("=" * 70)
    print("BGF 우선순위 화면 상세 분석")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 분석 대상 화면 (우선순위순)
    targets = [
        # Tier 1: 즉시 적용 가능
        ("STJK000_M0", "STJK010_M0", "현재고 조회"),
        ("STJK000_M0", "STJK030_M0", "일자별 재고추이"),
        ("STGJ000_M0", "STGJ300_M0", "입고예정 내역 조회"),
        ("STMB000_M0", "STMB010_M0", "시간대별 매출 정보"),
        ("STCM000_M0", "STCM130_M0", "상품 유통기한 관리"),
        # Tier 2: 발주 최적화
        ("STBJ000_M0", "STBJ330_M0", "발주정지상품조회"),
        ("STBJ000_M0", "STBJ490_M0", "품절상품현황"),
        ("STBJ000_M0", "STBJ080_M0", "상품별 발주 카렌더"),
        # 기존 화면 재분석
        ("STBJ000_M0", "STBJ030_M0", "[기존] 단품별 발주"),
        ("STMB000_M0", "STMB011_M0", "[기존] 중분류별 매출"),
        ("STGJ000_M0", "STGJ010_M0", "[기존] 센터매입 조회"),
        ("STBJ000_M0", "STBJ070_M0", "[기존] 발주현황조회"),
    ]

    analyzer = SalesAnalyzer()
    results = {}

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
        install_interceptor(driver)

        batch_count = 0
        for i, (parent, sub, name) in enumerate(targets):
            # 5개마다 리셋
            if batch_count >= 5:
                print("\n  [리셋: 페이지 리프레시...]")
                reset_tabs(driver)
                batch_count = 0
                time.sleep(1)

            print(f"\n  [{i+1}/{len(targets)}] {name} ({sub})")
            driver.execute_script("window.__xhrLog = [];")
            dismiss_alert(driver)

            ok = navigate_to_screen(driver, parent, sub)
            if not ok:
                print(f"    [SKIP] 네비게이션 실패")
                continue

            batch_count += 1
            time.sleep(4)

            # WorkFrame 분석
            wf = explore_workframe_deep(driver)
            if wf.get("error"):
                print(f"    ERROR: {wf['error']}")
                continue

            frame_url = wf.get("frameId", "")
            print(f"    화면URL: {frame_url}")

            if wf.get("datasets"):
                print(f"    데이터셋 {len(wf['datasets'])}개:")
                for ds in wf["datasets"]:
                    cols = ", ".join(ds["columns"][:15])
                    extra = "..." if len(ds["columns"]) > 15 else ""
                    print(f"      {ds['name']}: {ds['rows']}행×{ds['cols']}열")
                    if cols:
                        print(f"        [{cols}{extra}]")
                    if ds.get("sample"):
                        for row in ds["sample"][:2]:
                            vals = [f"{k}={v}" for k, v in list(row.items())[:8] if v]
                            if vals:
                                print(f"        → {', '.join(vals)}")

            if wf.get("grids"):
                for g in wf["grids"]:
                    print(f"    그리드: {g['name']} → {g['bind']}")

            if wf.get("buttons"):
                btns = [f"{b['name']}({b['text']})" for b in wf["buttons"][:8]]
                print(f"    버튼: {', '.join(btns)}")

            if wf.get("combos"):
                for c in wf["combos"]:
                    print(f"    콤보: {c['name']} → {c['bind']}")

            # XHR 캡처
            time.sleep(1)
            xhr = driver.execute_script("return window.__xhrLog || [];") or []
            api_calls = [x for x in xhr if '/log/' not in x.get('url', '') and '/search/' not in x.get('url', '')]
            if api_calls:
                print(f"    API 호출 {len(api_calls)}건:")
                for x in api_calls[:5]:
                    print(f"      {x['method']} {x['url']}")
                    if x.get('body'):
                        print(f"        body({len(x['body'])}자): {x['body'][:200]}")
                    if x.get('respPreview'):
                        print(f"        resp({x.get('respLen', 0)}자): {x['respPreview'][:150]}")

            results[sub] = {
                "name": name,
                "frameUrl": frame_url,
                "datasets": wf.get("datasets", []),
                "grids": wf.get("grids", []),
                "buttons": wf.get("buttons", []),
                "combos": wf.get("combos", []),
                "apiCalls": [{"url": x["url"], "method": x["method"],
                             "bodyLen": len(x.get("body", "")),
                             "respLen": x.get("respLen", 0)} for x in api_calls[:10]]
            }

        # 저장
        output = {"timestamp": datetime.now().isoformat(), "screens": results}
        out_path = project_root / "data" / "bgf_priority_screens.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n\n결과 저장: {out_path}")

        print("\n" + "=" * 70)
        print("분석 완료!")
        print("=" * 70)

    finally:
        try:
            analyzer.close()
            print("브라우저 종료")
        except:
            pass


if __name__ == "__main__":
    main()
