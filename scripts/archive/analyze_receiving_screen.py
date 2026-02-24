"""
센터매입 조회/확정 화면 구조 상세 분석 스크립트

목적:
- dsListPopup (전표 목록) 구조 파악
- dsList (상품 목록) 구조 파악
- 전표별 상품 수집 완전성 검증
- 5일자 실제 데이터로 정확성 확인

Usage:
    python scripts/analyze_receiving_screen.py --date 20260205
    python scripts/analyze_receiving_screen.py --date 20260205 --screenshot
    python scripts/analyze_receiving_screen.py --date 20260205 --validate
"""

import sys
import time
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

# 프로젝트 경로 설정
sys.path.insert(0, str(Path(__file__).parent.parent))

from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from src.sales_analyzer import SalesAnalyzer
from src.collectors.receiving_collector import ReceivingCollector
from src.config.ui_config import FRAME_IDS
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 대기 시간 설정
RECEIVING_DATA_LOAD_WAIT = 2.0


def set_date_and_search(driver, frame_id: str, dgfw_ymd: str) -> Dict[str, Any]:
    """날짜 설정 및 조회 실행

    Args:
        driver: Selenium WebDriver
        frame_id: 프레임 ID
        dgfw_ymd: 입고일자 (YYYYMMDD)

    Returns:
        {"success": True} or {"error": "..."}
    """
    script = f"""
        try {{
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            const wf = form.div_workForm?.form;

            if (!wf || !wf.dsAcpYmd) {{
                return {{error: 'dsAcpYmd not found'}};
            }}

            // 날짜 설정 (YYYYMMDD → YYYY-MM-DD)
            const dateStr = "{dgfw_ymd}";
            const formatted = dateStr.substring(0,4) + "-" + dateStr.substring(4,6) + "-" + dateStr.substring(6,8);

            // dsAcpYmd 데이터셋에 날짜 설정
            if (wf.dsAcpYmd.getRowCount() > 0) {{
                wf.dsAcpYmd.setColumn(0, 'DGFW_YMD', dateStr);
            }}

            // 조회 버튼 클릭 시뮬레이션 (필요 시)
            // TODO: 조회 버튼 로직 추가

            return {{success: true, date: dateStr}};

        }} catch(e) {{
            return {{error: e.message}};
        }}
    """

    return driver.execute_script(script)


def close_popups(driver, frame_id: str = None) -> Dict[str, Any]:
    """팝업 자동 닫기 (Selenium native 방법)

    JavaScript를 우회하고 Selenium의 네이티브 요소 찾기로 "닫기" 버튼 클릭

    Args:
        driver: Selenium WebDriver
        frame_id: 사용 안 함 (하위 호환성 유지)

    Returns:
        {"success": True, "closed": N, "debug": {...}} or {"error": "..."}
    """
    debug_info = {
        "method": "selenium_native",
        "found_elements": [],
        "attempts": []
    }
    closed = 0

    try:
        # Strategy 1: XPath로 "닫기" 텍스트를 가진 모든 요소 찾기
        xpaths = [
            "//*[text()='닫기']",  # 정확히 "닫기"인 요소
            "//button[contains(text(), '닫기')]",  # 버튼 중 "닫기" 포함
            "//div[contains(text(), '닫기')]",  # div 중 "닫기" 포함
            "//*[text()='×']",  # X 버튼
            "//*[@id[contains(., 'btn_close')]]",  # ID에 btn_close 포함
            "//*[@class[contains(., 'btn_close')]]"  # class에 btn_close 포함
        ]

        for xpath in xpaths:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                debug_info["found_elements"].append({
                    "xpath": xpath,
                    "count": len(elements)
                })

                for elem in elements:
                    # 보이는 요소만 클릭
                    if elem.is_displayed():
                        try:
                            text = elem.text[:20] if elem.text else ""
                            tag = elem.tag_name
                            elem_id = elem.get_attribute('id') or ""

                            debug_info["attempts"].append(f"clicking_{tag}_{text}_{elem_id[:30]}")

                            elem.click()
                            closed += 1
                            debug_info["attempts"].append(f"SUCCESS_{tag}_{elem_id[:30]}")

                            # 첫 번째 성공 후 즉시 종료
                            return {"success": True, "closed": closed, "debug": debug_info}

                        except Exception as e:
                            debug_info["attempts"].append(f"click_failed_{str(e)[:50]}")
                            continue

            except NoSuchElementException:
                continue

        # 아무것도 클릭하지 못함
        return {"success": True, "closed": 0, "debug": debug_info}

    except Exception as e:
        return {"success": False, "error": str(e), "debug": debug_info}


def get_chit_list(driver, frame_id: str) -> Dict[str, Any]:
    """dsListPopup에서 전표 목록 조회

    Args:
        driver: Selenium WebDriver
        frame_id: 프레임 ID (STGJ010_M0)

    Returns:
        {
            "success": True,
            "count": N,
            "chits": [
                {
                    "ROW_INDEX": 0,
                    "CHIT_NO": "12345678",
                    "DGFW_YMD": "20260205",
                    "DGFW_TIME": "0730",
                    "CENTER_NM": "저온센터",
                    "CENTER_CD": "C001",
                    "ORD_DATE": "20260204",
                    "TOTAL_QTY": 250
                },
                ...
            ]
        }
    """
    script = f"""
        try {{
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            const wf = form.div_workForm?.form;

            if (!wf || !wf.dsListPopup) {{
                return {{error: 'dsListPopup not found'}};
            }}

            const ds = wf.dsListPopup;
            const count = ds.getRowCount();
            const chits = [];

            // Decimal 타입 변환 헬퍼
            function getVal(ds, row, col) {{
                let val = ds.getColumn(row, col);
                // nexacro는 큰 숫자를 {{hi, lo}} 객체로 반환
                if (val && typeof val === 'object' && val.hi !== undefined) {{
                    val = val.hi;
                }}
                return val;
            }}

            for (let i = 0; i < count; i++) {{
                const chit = {{
                    ROW_INDEX: i,
                    CHIT_NO: ds.getColumn(i, 'CHIT_NO'),
                    DGFW_YMD: ds.getColumn(i, 'DGFW_YMD'),
                    DGFW_TIME: ds.getColumn(i, 'DGFW_TIME'),
                    CENTER_NM: ds.getColumn(i, 'CENTER_NM'),
                    CENTER_CD: ds.getColumn(i, 'CENTER_CD'),
                    ORD_DATE: ds.getColumn(i, 'ORD_DATE'),
                    TOTAL_QTY: getVal(ds, i, 'TOTAL_QTY')
                }};
                chits.push(chit);
            }}

            return {{success: true, count: count, chits: chits}};

        }} catch(e) {{
            return {{error: e.message}};
        }}
    """

    return driver.execute_script(script)


def select_chit(driver, frame_id: str, row_index: int) -> Dict[str, Any]:
    """전표 선택 (dsListPopup.set_rowposition)

    Args:
        driver: Selenium WebDriver
        frame_id: 프레임 ID
        row_index: 선택할 전표 행 인덱스

    Returns:
        {"success": True} or {"error": "..."}
    """
    script = f"""
        try {{
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            const wf = form.div_workForm?.form;

            if (!wf || !wf.dsListPopup) {{
                return {{error: 'dsListPopup not found'}};
            }}

            // 전표 선택
            wf.dsListPopup.set_rowposition({row_index});

            return {{success: true, rowIndex: {row_index}}};

        }} catch(e) {{
            return {{error: e.message}};
        }}
    """

    return driver.execute_script(script)


def get_items_for_current_chit(driver, frame_id: str) -> Dict[str, Any]:
    """현재 선택된 전표의 상품 목록 조회 (dsList)

    Args:
        driver: Selenium WebDriver
        frame_id: 프레임 ID

    Returns:
        {
            "success": True,
            "count": M,
            "items": [
                {
                    "ITEM_CD": "8801234567890",
                    "ITEM_NM": "CJ 도시락1호",
                    "CUST_NM": "CJ프레시웨이",
                    "MID_CD": "001",
                    "ORD_QTY": 10,
                    "NAP_QTY": 10,
                    "UNIT_PRICE": 3500,
                    "TOTAL_AMT": 35000
                },
                ...
            ]
        }
    """
    script = f"""
        try {{
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            const wf = form.div_workForm?.form;

            if (!wf || !wf.dsList) {{
                return {{error: 'dsList not found'}};
            }}

            const ds = wf.dsList;
            const count = ds.getRowCount();
            const items = [];

            // Decimal 타입 변환 헬퍼
            function getVal(ds, row, col) {{
                let val = ds.getColumn(row, col);
                if (val && typeof val === 'object' && val.hi !== undefined) {{
                    val = val.hi;
                }}
                return val;
            }}

            for (let i = 0; i < count; i++) {{
                const item = {{
                    ITEM_CD: ds.getColumn(i, 'ITEM_CD'),
                    ITEM_NM: ds.getColumn(i, 'ITEM_NM'),
                    CUST_NM: ds.getColumn(i, 'CUST_NM'),
                    MID_CD: ds.getColumn(i, 'MID_CD'),
                    ORD_QTY: getVal(ds, i, 'ORD_QTY'),
                    NAP_QTY: getVal(ds, i, 'NAP_QTY'),
                    UNIT_PRICE: getVal(ds, i, 'UNIT_PRICE'),
                    TOTAL_AMT: getVal(ds, i, 'TOTAL_AMT')
                }};
                items.push(item);
            }}

            return {{success: true, count: count, items: items}};

        }} catch(e) {{
            return {{error: e.message}};
        }}
    """

    return driver.execute_script(script)


def format_date(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    if not yyyymmdd or len(yyyymmdd) != 8:
        return ""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def format_time(hhmm: str) -> str:
    """HHMM → HH:MM"""
    if not hhmm:
        return ""
    if len(hhmm) == 4:
        return f"{hhmm[:2]}:{hhmm[2:4]}"
    elif len(hhmm) == 3:
        return f"0{hhmm[0]}:{hhmm[1:3]}"
    return hhmm


def capture_screenshots(driver, dgfw_ymd: str, output_dir: Path) -> Dict[str, str]:
    """분석용 스크린샷 캡처

    Args:
        driver: Selenium WebDriver
        dgfw_ymd: 입고일자
        output_dir: 저장 디렉토리

    Returns:
        {"full": "path/to/full.png", ...}
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_dir = output_dir / dgfw_ymd
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    screenshots = {}

    # 전체 화면
    full_path = screenshot_dir / f"{timestamp}_01_full.png"
    driver.save_screenshot(str(full_path))
    screenshots["full"] = str(full_path)
    logger.info(f"[SCREENSHOT] {full_path}")

    return screenshots


def analyze_receiving_screen(
    dgfw_ymd: str = "20260205",
    capture_screenshot: bool = False,
    validate: bool = False
) -> Dict[str, Any]:
    """센터매입 조회/확정 화면 상세 분석

    Args:
        dgfw_ymd: 입고일자 (YYYYMMDD)
        capture_screenshot: 스크린샷 캡처 여부
        validate: 검증 모드 활성화

    Returns:
        {
            "success": True,
            "chits": [...],
            "items_by_chit": {...},
            "all_items": [...],
            "stats": {...},
            "screenshots": {...}
        }
    """
    analyzer = SalesAnalyzer()

    try:
        # 1. 로그인 및 화면 이동
        logger.info("=" * 80)
        logger.info(f"센터매입 화면 분석 시작 - {dgfw_ymd}")
        logger.info("=" * 80)

        logger.info("크롬 드라이버 설정 중...")
        analyzer.setup_driver()

        logger.info("BGF 사이트 접속 중...")
        analyzer.connect()

        logger.info("로그인 중...")
        if not analyzer.do_login():
            raise Exception("로그인 실패")

        logger.info("로그인 성공!")

        frame_id = FRAME_IDS["RECEIVING"]

        # 센터매입 화면 이동
        logger.info("센터매입 조회/확정 화면 이동 중...")
        collector = ReceivingCollector(analyzer.driver)

        if not collector.navigate_to_receiving_menu():
            raise Exception("메뉴 이동 실패")

        logger.info("화면 이동 완료")
        time.sleep(RECEIVING_DATA_LOAD_WAIT)

        # 팝업 닫기 (Selenium native)
        logger.info("팝업 닫기 시도 (Selenium XPath 검색)...")
        popup_result = close_popups(analyzer.driver)
        if popup_result.get('success'):
            closed_count = popup_result.get('closed', 0)
            debug_info = popup_result.get('debug', {})

            # 디버그 정보 출력
            logger.info(f"  [DEBUG] 방법: {debug_info.get('method')}")
            if debug_info.get('found_elements'):
                for elem_info in debug_info['found_elements'][:5]:
                    logger.info(f"    - {elem_info.get('xpath')}: {elem_info.get('count')}개")
            if debug_info.get('attempts'):
                logger.info(f"  [DEBUG] 시도: {debug_info['attempts'][:5]}")

            if closed_count > 0:
                logger.info(f"[OK] 팝업 닫기 성공: {closed_count}개")
                time.sleep(2.0)  # 팝업 닫힌 후 충분한 대기
            else:
                logger.warning("[WARNING] 닫기 버튼을 찾지 못함 또는 클릭 실패")
        else:
            logger.warning(f"[ERROR] 팝업 닫기 오류: {popup_result.get('error')} - 계속 진행")

        # 날짜 설정 및 조회
        logger.info(f"날짜 설정 중: {dgfw_ymd}")
        set_date_result = set_date_and_search(analyzer.driver, frame_id, dgfw_ymd)
        if not set_date_result.get('success'):
            logger.warning(f"날짜 설정 실패: {set_date_result.get('error')} - 기본값 사용")

        time.sleep(RECEIVING_DATA_LOAD_WAIT)

        # 스크린샷 캡처
        screenshots = {}
        if capture_screenshot:
            screenshot_dir = Path(__file__).parent.parent / "data" / "screenshots" / "receiving_analysis"
            screenshots = capture_screenshots(analyzer.driver, dgfw_ymd, screenshot_dir)

        # 2. 전표 목록 조회
        logger.info("\n" + "=" * 80)
        logger.info("Step 1: 전표 목록 조회 (dsListPopup)")
        logger.info("=" * 80)

        chit_result = get_chit_list(analyzer.driver, frame_id)

        if not chit_result.get('success'):
            raise Exception(f"전표 목록 조회 실패: {chit_result.get('error')}")

        chits = chit_result['chits']
        chit_count = len(chits)
        logger.info(f"\n[OK] 전표 수: {chit_count}개")

        for i, chit in enumerate(chits):
            logger.info(f"  [{i}] {chit['CHIT_NO']} - {chit['CENTER_NM']} "
                       f"({format_date(chit['DGFW_YMD'])} {format_time(chit['DGFW_TIME'])})")

        # 3. 전표별 상품 수집
        logger.info("\n" + "=" * 80)
        logger.info("Step 2: 전표별 상품 수집 (dsList)")
        logger.info("=" * 80)

        items_by_chit = {}
        all_items = []
        items_per_chit = []

        for i, chit in enumerate(chits):
            chit_no = chit['CHIT_NO']
            logger.info(f"\n[{i+1}/{chit_count}] 전표 {chit_no} 처리 중...")

            # 전표 선택
            select_result = select_chit(analyzer.driver, frame_id, i)
            if not select_result.get('success'):
                logger.error(f"  [ERROR] 전표 선택 실패: {select_result.get('error')}")
                continue

            logger.info(f"  전표 선택 완료 (rowIndex={i})")

            # dsList 로딩 대기
            time.sleep(RECEIVING_DATA_LOAD_WAIT)

            # 상품 목록 조회
            items_result = get_items_for_current_chit(analyzer.driver, frame_id)
            if not items_result.get('success'):
                logger.error(f"  [ERROR] 상품 조회 실패: {items_result.get('error')}")
                continue

            items = items_result['items']
            item_count = len(items)
            logger.info(f"  [OK] 상품 수: {item_count}개")

            # 전표 정보와 상품 정보 병합
            for item in items:
                merged = {
                    # 전표 정보
                    'receiving_date': format_date(chit['DGFW_YMD']),
                    'receiving_time': format_time(chit['DGFW_TIME']),
                    'chit_no': chit['CHIT_NO'],
                    'center_nm': chit['CENTER_NM'],
                    'center_cd': chit.get('CENTER_CD'),
                    'order_date': format_date(chit.get('ORD_DATE', '')),

                    # 상품 정보
                    'item_cd': item['ITEM_CD'],
                    'item_nm': item['ITEM_NM'],
                    'cust_nm': item.get('CUST_NM'),
                    'mid_cd': item.get('MID_CD'),
                    'order_qty': item.get('ORD_QTY', 0),
                    'receiving_qty': item.get('NAP_QTY', 0),
                    'unit_price': item.get('UNIT_PRICE'),
                    'total_amt': item.get('TOTAL_AMT'),
                }
                all_items.append(merged)

            items_by_chit[chit_no] = items
            items_per_chit.append(item_count)

            # 샘플 출력 (첫 3개 상품)
            for j, item in enumerate(items[:3]):
                logger.info(f"    [{j+1}] {item['ITEM_NM'][:20]} (수량: {item.get('NAP_QTY', 0)})")
            if item_count > 3:
                logger.info(f"    ... 외 {item_count - 3}개")

        # 4. 통계
        total_items = len(all_items)
        logger.info("\n" + "=" * 80)
        logger.info("수집 완료")
        logger.info("=" * 80)
        logger.info(f"전표 수: {chit_count}개")
        logger.info(f"총 상품 수: {total_items}개")
        logger.info(f"전표당 평균: {total_items / chit_count:.1f}개" if chit_count > 0 else "전표당 평균: 0개")
        logger.info(f"전표별 상품 수: {items_per_chit}")

        # 5. 검증 (옵션)
        validation_result = None
        if validate:
            logger.info("\n" + "=" * 80)
            logger.info("검증 모드")
            logger.info("=" * 80)
            validation_result = validate_completeness(chit_count, total_items, all_items)

        # 6. 결과 저장
        result = {
            "success": True,
            "date": dgfw_ymd,
            "formatted_date": format_date(dgfw_ymd),
            "chits": chits,
            "items_by_chit": items_by_chit,
            "all_items": all_items,
            "stats": {
                "chit_count": chit_count,
                "total_items": total_items,
                "items_per_chit": items_per_chit,
                "avg_items_per_chit": total_items / chit_count if chit_count > 0 else 0
            },
            "screenshots": screenshots,
            "validation": validation_result
        }

        # JSON 저장
        output_dir = Path(__file__).parent.parent / "data" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"receiving_{dgfw_ymd}_analysis.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"\n[OK] 분석 결과 저장: {output_file}")

        return result

    except Exception as e:
        logger.error(f"\n[ERROR] 오류 발생: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }

    finally:
        try:
            analyzer.close()
            logger.info("\n브라우저 종료")
        except Exception as e:
            logger.debug(f"브라우저 종료 실패: {e}")


def validate_completeness(
    actual_chit_count: int,
    actual_item_count: int,
    all_items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """수집 완전성 검증

    Args:
        actual_chit_count: 실제 수집된 전표 수
        actual_item_count: 실제 수집된 상품 수
        all_items: 모든 상품 데이터

    Returns:
        {
            "chit_count_match": True/False,
            "total_items_match": True/False,
            "required_fields_complete": True/False,
            "issues": [...]
        }
    """
    validation = {
        "chit_count_match": None,
        "total_items_match": None,
        "required_fields_complete": True,
        "issues": []
    }

    # 1. 전표 수 확인
    print(f"\n화면에 표시된 전표 수: (실제 수집: {actual_chit_count}개)")
    try:
        expected_chit_count = int(input("예상 전표 수를 입력하세요 (Enter=스킵): ") or actual_chit_count)
        validation["chit_count_match"] = (actual_chit_count == expected_chit_count)
        if not validation["chit_count_match"]:
            validation["issues"].append(f"전표 수 불일치: 예상={expected_chit_count}, 실제={actual_chit_count}")
    except:
        validation["chit_count_match"] = None

    # 2. 총 상품 수 확인
    print(f"\n화면에 표시된 총 상품 수: (실제 수집: {actual_item_count}개)")
    try:
        expected_item_count = int(input("예상 상품 수를 입력하세요 (Enter=스킵): ") or actual_item_count)
        validation["total_items_match"] = (actual_item_count == expected_item_count)
        if not validation["total_items_match"]:
            validation["issues"].append(f"상품 수 불일치: 예상={expected_item_count}, 실제={actual_item_count}")
    except:
        validation["total_items_match"] = None

    # 3. 필수 필드 검증
    required_fields = [
        'receiving_date', 'receiving_time', 'chit_no',
        'item_cd', 'item_nm', 'receiving_qty'
    ]

    for item in all_items:
        for field in required_fields:
            if not item.get(field):
                validation["required_fields_complete"] = False
                validation["issues"].append(
                    f"필수 필드 누락: {field} in {item.get('item_cd', 'UNKNOWN')}"
                )
                break

    # 4. 결과 출력
    print("\n" + "=" * 80)
    print("검증 결과")
    print("=" * 80)
    chit_match_str = '[OK]' if validation['chit_count_match'] else '[ERROR]' if validation['chit_count_match'] is not None else '[SKIP]'
    item_match_str = '[OK]' if validation['total_items_match'] else '[ERROR]' if validation['total_items_match'] is not None else '[SKIP]'
    field_complete_str = '[OK]' if validation['required_fields_complete'] else '[ERROR]'

    print(f"전표 수 일치: {chit_match_str}")
    print(f"상품 수 일치: {item_match_str}")
    print(f"필수 필드 완전: {field_complete_str}")

    if validation["issues"]:
        print(f"\n[ERROR] 발견된 이슈: {len(validation['issues'])}건")
        for issue in validation["issues"]:
            print(f"  - {issue}")
    else:
        print("\n[OK] 이슈 없음")

    return validation


def main():
    """메인 실행"""
    parser = argparse.ArgumentParser(
        description="센터매입 조회/확정 화면 구조 분석"
    )
    parser.add_argument(
        "--date", "-d",
        type=str,
        default="20260205",
        help="입고일자 (YYYYMMDD 형식, 기본값: 20260205)"
    )
    parser.add_argument(
        "--screenshot", "-s",
        action="store_true",
        help="스크린샷 캡처"
    )
    parser.add_argument(
        "--validate", "-v",
        action="store_true",
        help="검증 모드 활성화"
    )

    args = parser.parse_args()

    result = analyze_receiving_screen(
        dgfw_ymd=args.date,
        capture_screenshot=args.screenshot,
        validate=args.validate
    )

    if result["success"]:
        print("\n" + "=" * 80)
        print("[OK] 분석 완료!")
        print("=" * 80)
        sys.exit(0)
    else:
        print("\n" + "=" * 80)
        print("[ERROR] 분석 실패!")
        print("=" * 80)
        sys.exit(1)


if __name__ == "__main__":
    main()
