# -*- coding: utf-8 -*-
"""
팝업 진단 스크립트
실제 브라우저에서 열린 팝업을 확인하고 닫기 테스트
"""

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from src.sales_analyzer import SalesAnalyzer
from src.utils.popup_manager import close_all_popups, close_alerts
from src.utils.logger import get_logger

logger = get_logger(__name__)

def diagnose_popups(driver):
    """
    현재 열린 팝업 진단
    """
    print("\n" + "=" * 70)
    print("POPUP DIAGNOSIS")
    print("=" * 70)

    result = driver.execute_script("""
        const diagnosis = {
            nexacroFrames: [],
            domPopups: [],
            errors: []
        };

        // 1. 넥사크로 프레임 탐색
        try {
            const app = nexacro.getApplication();
            if (app && app.mainframe && app.mainframe.all) {
                for (let i = 0; i < app.mainframe.all.length; i++) {
                    const frame = app.mainframe.all[i];
                    const frameId = frame.id || frame.name || '';
                    const visible = frame.visible !== false;

                    diagnosis.nexacroFrames.push({
                        id: frameId,
                        visible: visible,
                        type: frame.constructor ? frame.constructor.name : 'unknown'
                    });
                }
            } else {
                diagnosis.errors.push('nexacro.getApplication() 접근 실패');
            }
        } catch(e) {
            diagnosis.errors.push('넥사크로 API 에러: ' + e.message);
        }

        // 2. DOM 팝업 탐색
        try {
            const selectors = [
                '[id*="popupframe"]',
                '[id*="PopupFrame"]',
                '[id*="Popup"]',
                '[id*="Modal"]',
                '[id*="Dialog"]'
            ];

            selectors.forEach(selector => {
                const elements = document.querySelectorAll(selector);
                elements.forEach(el => {
                    if (el.offsetParent !== null) {
                        diagnosis.domPopups.push({
                            id: el.id || el.className || 'no-id',
                            selector: selector,
                            visible: true
                        });
                    }
                });
            });
        } catch(e) {
            diagnosis.errors.push('DOM 탐색 에러: ' + e.message);
        }

        return diagnosis;
    """)

    print("\n[1] 넥사크로 프레임 ({0}개)".format(len(result['nexacroFrames'])))
    for frame in result['nexacroFrames'][:10]:
        status = "VISIBLE" if frame['visible'] else "HIDDEN"
        print("  - {0} [{1}] ({2})".format(frame['id'], status, frame['type']))
    if len(result['nexacroFrames']) > 10:
        print("  ... 외 {0}개".format(len(result['nexacroFrames']) - 10))

    print("\n[2] DOM 팝업 요소 ({0}개)".format(len(result['domPopups'])))
    for popup in result['domPopups'][:10]:
        print("  - {0} (selector: {1})".format(popup['id'], popup['selector']))
    if len(result['domPopups']) > 10:
        print("  ... 외 {0}개".format(len(result['domPopups']) - 10))

    print("\n[3] 에러")
    if result['errors']:
        for err in result['errors']:
            print("  ! {0}".format(err))
    else:
        print("  (없음)")

    # 팝업으로 보이는 프레임 필터링
    popup_frames = [f for f in result['nexacroFrames']
                   if f['visible'] and any(keyword in f['id'].lower()
                   for keyword in ['popup', 'callitem', 'modal', 'dialog'])]

    print("\n[4] 닫아야 할 팝업 ({0}개)".format(len(popup_frames)))
    for frame in popup_frames:
        print("  * {0}".format(frame['id']))

    return popup_frames


def main():
    print("BGF 로그인 및 팝업 진단 시작...\n")

    analyzer = None
    driver = None

    try:
        # SalesAnalyzer 생성 및 초기화
        analyzer = SalesAnalyzer()
        analyzer.setup_driver()
        analyzer.connect()

        # BGF 로그인
        print("[Step 1] BGF 로그인...")
        if not analyzer.do_login():
            print("[FAIL] 로그인 실패")
            return

        print("[OK] 로그인 성공\n")
        time.sleep(2)

        # analyzer의 드라이버 사용
        driver = analyzer.driver

        # 초기 상태 진단
        print("[Step 2] 초기 팝업 상태 확인...")
        initial_popups = diagnose_popups(driver)

        # 팝업 닫기 테스트
        print("\n[Step 3] 팝업 닫기 실행...")
        print("-" * 70)

        alert_count = close_alerts(driver, max_attempts=3, silent=False)
        print("Alert 처리: {0}개".format(alert_count))

        popup_count = close_all_popups(driver, silent=False)
        print("팝업 닫기: {0}개".format(popup_count))

        time.sleep(1)

        # 종료 후 상태 진단
        print("\n[Step 4] 팝업 닫기 후 상태 확인...")
        final_popups = diagnose_popups(driver)

        # 결과 비교
        print("\n" + "=" * 70)
        print("RESULT")
        print("=" * 70)
        print("초기 팝업: {0}개".format(len(initial_popups)))
        print("최종 팝업: {0}개".format(len(final_popups)))
        print("닫은 개수: {0}개 (reported)".format(popup_count))

        if len(final_popups) > 0:
            print("\n[경고] 아직 열려있는 팝업:")
            for popup in final_popups:
                print("  ! {0}".format(popup['id']))
        else:
            print("\n[OK] 모든 팝업이 정상적으로 닫혔습니다.")

        print("\n계속하려면 Enter를 누르세요...")
        input()

    except Exception as e:
        print("[ERROR] {0}".format(e))
        import traceback
        traceback.print_exc()

    finally:
        print("\n브라우저 종료 중...")
        if analyzer and analyzer.driver:
            analyzer.close()


if __name__ == "__main__":
    main()
