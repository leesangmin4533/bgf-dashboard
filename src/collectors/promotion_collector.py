"""
행사 정보 크롤링 모듈

BGF 발주사이트 → 품목 상세정보 팝업에서 행사 정보 추출

사용법:
    collector = PromotionCollector(driver)

    # 단일 상품 행사 정보 조회
    promo_info = collector.get_item_promotion("8801234567890")

    # 전체 상품 행사 정보 수집
    collector.collect_all_promotions()
"""

import sqlite3
import time
from typing import Any, Optional, List, Dict
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PromotionInfo:
    """행사 정보"""
    item_cd: str                        # 상품코드
    item_nm: str                        # 상품명
    promo_type: Optional[str]           # 행사 유형: '1+1', '2+1', None
    start_date: Optional[str]           # 시작일 (YYYY-MM-DD)
    end_date: Optional[str]             # 종료일 (YYYY-MM-DD)
    next_promo_type: Optional[str]      # 다음 달 행사 유형
    next_start_date: Optional[str]      # 다음 달 시작일
    next_end_date: Optional[str]        # 다음 달 종료일

    def has_current_promo(self) -> bool:
        """현재 행사 중인지"""
        return self.promo_type is not None

    def has_next_promo(self) -> bool:
        """다음 달 행사 예정인지"""
        return self.next_promo_type is not None

    def is_ending_soon(self, days: int = 3) -> bool:
        """행사 종료 임박 여부

        Args:
            days: 종료 임박 기준 일수 (기본 3일)

        Returns:
            종료일까지 남은 일수가 days 이하이면 True
        """
        if not self.end_date:
            return False

        end = datetime.strptime(self.end_date, '%Y-%m-%d').date()
        today = date.today()
        remaining = (end - today).days

        return 0 <= remaining <= days

    def will_promo_change(self) -> bool:
        """행사 변경 예정 여부 (종료 또는 변경)

        Returns:
            현재 행사가 종료 예정이거나 다음 달 행사가 다르면 True
        """
        # 현재 행사 있는데 다음 행사 없음 = 종료 예정
        if self.has_current_promo() and not self.has_next_promo():
            return True

        # 현재 행사와 다음 행사가 다름 = 변경 예정
        if self.promo_type != self.next_promo_type:
            return True

        return False


class PromotionCollector:
    """
    행사 정보 수집기

    BGF 발주사이트의 품목 상세 팝업에서 행사 정보를 크롤링
    """

    def __init__(self, driver: WebDriver, db_path: Optional[str] = None, store_id: Optional[str] = None) -> None:
        """
        Args:
            driver: Selenium WebDriver 객체 (로그인된 상태)
            db_path: 데이터베이스 경로
            store_id: 매장 ID (None이면 전체)
        """
        self.driver = driver
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "bgf_sales.db"
        self.db_path = str(db_path)
        self.store_id = store_id

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    # =========================================================================
    # 크롤링 메서드
    # =========================================================================

    def get_item_promotion(self, item_cd: str) -> Optional[PromotionInfo]:
        """
        단일 상품의 행사 정보 조회

        Args:
            item_cd: 상품코드

        Returns:
            PromotionInfo 또는 None

        동작:
            1. 품목 상세정보 팝업 열기
            2. 행사 정보 textarea에서 텍스트 추출
            3. 당월/익월 행사 정보 파싱
            4. 팝업 닫기
        """
        try:
            # 1. 품목 상세 팝업 열기
            if not self._open_item_detail_popup(item_cd):
                logger.warning(f"품목 상세 팝업 열기 실패: {item_cd}")
                return None

            time.sleep(0.5)  # 팝업 로딩 대기

            # 2. 상품명 추출
            item_nm = self._extract_item_name()

            # 3. 행사 정보 추출 (당월 + 익월 한번에)
            promo_data = self._extract_all_promotions()
            current_promo = promo_data.get('current', {})
            next_promo = promo_data.get('next', {})

            # 4. 팝업 닫기
            self._close_popup()

            # 5. 결과 반환
            return PromotionInfo(
                item_cd=item_cd,
                item_nm=item_nm,
                promo_type=current_promo.get('type'),
                start_date=current_promo.get('start_date'),
                end_date=current_promo.get('end_date'),
                next_promo_type=next_promo.get('type'),
                next_start_date=next_promo.get('start_date'),
                next_end_date=next_promo.get('end_date'),
            )

        except Exception as e:
            logger.error(f"행사 정보 조회 실패 ({item_cd}): {e}")
            self._close_popup()  # 에러 시에도 팝업 닫기
            return None

    def _open_item_detail_popup(self, item_cd: str) -> bool:
        """
        품목 상세정보 팝업 열기

        단품별 발주 화면에서 상품 코드 입력 후 상세 정보 팝업 열기
        """
        try:
            # 넥사크로 JavaScript로 팝업 열기
            js_code = f"""
            (function() {{
                try {{
                    var app = nexacro.getApplication();
                    var mainFrame = app.mainframe;
                    var workFrame = mainFrame.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    var form = workFrame.form;

                    // 상품 코드로 상세 팝업 호출
                    // (실제 BGF 시스템의 팝업 호출 함수에 맞게 수정 필요)
                    if (form.fn_ItemDetail) {{
                        form.fn_ItemDetail('{item_cd}');
                        return true;
                    }} else if (form.gfn_ItemDetail) {{
                        form.gfn_ItemDetail('{item_cd}');
                        return true;
                    }}

                    return false;
                }} catch(e) {{
                    return false;
                }}
            }})()
            """
            result = self.driver.execute_script(js_code)
            return result == True

        except Exception as e:
            logger.error(f"팝업 열기 실패: {e}")
            return False

    def _extract_item_name(self) -> str:
        """팝업에서 상품명 추출"""
        try:
            js_code = """
            (function() {
                try {
                    var app = nexacro.getApplication();
                    var popups = app.popupframes;
                    if (popups) {
                        var keys = Object.keys(popups);
                        for (var i = keys.length - 1; i >= 0; i--) {
                            var popup = popups[keys[i]];
                            if (popup && popup.form) {
                                // 상품명 찾기 (다양한 컴포넌트 이름 시도)
                                var nmFields = ['stItemNm', 'sta_itemNm', 'edtItemNm', 'st_ItemNm'];
                                for (var j = 0; j < nmFields.length; j++) {
                                    var comp = popup.form[nmFields[j]];
                                    if (comp && comp.text) {
                                        return comp.text;
                                    }
                                }
                            }
                        }
                    }
                    return '';
                } catch(e) { return ''; }
            })()
            """
            return self.driver.execute_script(js_code) or ""
        except Exception:
            return ""

    def _extract_all_promotions(self) -> Dict[str, Any]:
        """
        행사 정보 추출 (당월 + 익월)

        DOM 구조:
            <textarea id="...stEvt01:textarea" class="nexatextarea" readonly="">
            당월 : 1+1 | 26.01.01~26.01.31 | 행사 요일 : 매일 | 방식 : 판촉비
            익월 : 1+1 | 26.02.01~26.02.28 | 행사 요일 : 매일 | 방식 : 판촉비
            </textarea>

        Returns:
            {
                'current': {'type': '1+1', 'start_date': '2026-01-01', 'end_date': '2026-01-31'},
                'next': {'type': '1+1', 'start_date': '2026-02-01', 'end_date': '2026-02-28'}
            }
        """
        result = {
            'current': {'type': None, 'start_date': None, 'end_date': None},
            'next': {'type': None, 'start_date': None, 'end_date': None}
        }

        try:
            # 넥사크로 객체에서 행사 정보 추출
            js_code = """
            (function() {
                try {
                    var app = nexacro.getApplication();
                    var popups = app.popupframes;
                    if (popups) {
                        var keys = Object.keys(popups);
                        for (var i = keys.length - 1; i >= 0; i--) {
                            var popup = popups[keys[i]];
                            if (popup && popup.form) {
                                // 행사 정보 textarea 찾기
                                var evtFields = ['stEvt01', 'sta_evt01', 'txtEvt01', 'taEvt'];
                                for (var j = 0; j < evtFields.length; j++) {
                                    var comp = popup.form[evtFields[j]];
                                    if (comp) {
                                        // textarea의 값 가져오기
                                        if (comp.value) return comp.value;
                                        if (comp.text) return comp.text;
                                    }
                                }
                            }
                        }
                    }
                    return '';
                } catch(e) { return ''; }
            })()
            """
            text = self.driver.execute_script(js_code)

            if not text or text.strip() == "":
                return result

            # 줄바꿈으로 분리하여 각 행 파싱
            lines = text.strip().split('\n')

            for line in lines:
                parsed = self._parse_promotion_line(line)
                if not parsed:
                    continue

                if parsed['period'] == '당월':
                    result['current'] = {
                        'type': parsed['promo_type'],
                        'start_date': parsed['start_date'],
                        'end_date': parsed['end_date']
                    }
                elif parsed['period'] == '익월':
                    result['next'] = {
                        'type': parsed['promo_type'],
                        'start_date': parsed['start_date'],
                        'end_date': parsed['end_date']
                    }

        except Exception as e:
            logger.error(f"행사 정보 추출 실패: {e}")

        return result

    def _parse_promotion_line(self, line: str) -> Optional[Dict[str, Any]]:
        """
        행사 정보 한 줄 파싱

        입력: "당월 : 1+1 | 26.01.01~26.01.31 | 행사 요일 : 매일 | 방식 : 판촉비"

        출력: {
            'period': '당월',
            'promo_type': '1+1',
            'start_date': '2026-01-01',
            'end_date': '2026-01-31'
        }
        """
        if not line or line.strip() == "":
            return None

        result = {
            'period': None,
            'promo_type': None,
            'start_date': None,
            'end_date': None,
        }

        # | 로 분리
        parts = [p.strip() for p in line.split("|")]

        for part in parts:
            # "당월 : 1+1" 또는 "익월 : 2+1" 형식
            if part.startswith("당월") or part.startswith("익월"):
                if " : " in part:
                    period, promo_type = part.split(" : ", 1)
                    result['period'] = period.strip()
                    result['promo_type'] = promo_type.strip()

            # "26.01.01~26.01.31" 형식 (날짜 범위)
            elif "~" in part and "." in part:
                dates = part.split("~")
                if len(dates) == 2:
                    result['start_date'] = self._convert_date(dates[0].strip())
                    result['end_date'] = self._convert_date(dates[1].strip())

        # 필수 값 체크 (행사 유형과 시작일이 있어야 유효)
        if result['promo_type'] and result['start_date']:
            return result

        return None

    def _convert_date(self, short_date: str) -> str:
        """
        짧은 날짜 형식을 표준 형식으로 변환

        입력: "26.01.01"
        출력: "2026-01-01"
        """
        try:
            parts = short_date.split(".")
            if len(parts) == 3:
                year = "20" + parts[0]  # 26 → 2026
                month = parts[1].zfill(2)
                day = parts[2].zfill(2)
                return f"{year}-{month}-{day}"
        except Exception as e:
            logger.warning(f"날짜 변환 실패 ({short_date}): {e}")

        return short_date

    def _close_popup(self) -> None:
        """팝업 닫기"""
        try:
            js_code = """
            (function() {
                try {
                    var app = nexacro.getApplication();
                    var popups = app.popupframes;
                    if (popups) {
                        var keys = Object.keys(popups);
                        for (var i = keys.length - 1; i >= 0; i--) {
                            var popup = popups[keys[i]];
                            if (popup && popup.form) {
                                // 닫기 버튼 찾기
                                var closeNames = ['btn_close', 'btnClose', 'Button00', 'btn_exit'];
                                for (var j = 0; j < closeNames.length; j++) {
                                    var btn = popup.form[closeNames[j]];
                                    if (btn && btn.click) {
                                        btn.click();
                                        return true;
                                    }
                                }
                                // 버튼 못 찾으면 직접 닫기
                                popup.close();
                                return true;
                            }
                        }
                    }
                    return false;
                } catch(e) { return false; }
            })()
            """
            self.driver.execute_script(js_code)
            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"팝업 닫기 실패: {e}")

    # =========================================================================
    # 일괄 수집
    # =========================================================================

    def collect_all_promotions(
        self,
        item_list: Optional[List[str]] = None,
        save_to_db: bool = True
    ) -> List[PromotionInfo]:
        """
        전체 상품 행사 정보 수집

        Args:
            item_list: 수집할 상품코드 리스트 (None이면 전체)
            save_to_db: DB 저장 여부

        Returns:
            수집된 PromotionInfo 리스트
        """
        if item_list is None:
            item_list = self._get_all_item_codes()

        results = []
        total = len(item_list)

        logger.info(f"행사 정보 수집 시작: {total}개 상품")

        for idx, item_cd in enumerate(item_list):
            try:
                promo_info = self.get_item_promotion(item_cd)

                if promo_info:
                    results.append(promo_info)

                    if save_to_db:
                        self._save_promotion(promo_info)

                # 진행률 로그
                if (idx + 1) % 50 == 0:
                    logger.info(f"진행률: {idx + 1}/{total}")

                # 요청 간 딜레이
                time.sleep(0.3)

            except Exception as e:
                logger.error(f"상품 {item_cd} 수집 실패: {e}")
                continue

        logger.info(f"행사 정보 수집 완료: {len(results)}/{total}")

        return results

    def _get_all_item_codes(self) -> List[str]:
        """전체 상품 코드 조회"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # 최근 판매 이력이 있는 상품만
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(f"""
                SELECT DISTINCT item_cd
                FROM daily_sales
                WHERE sales_date >= date('now', '-14 days')
                AND sale_qty > 0
                {store_filter}
            """, store_params)

            items = [row[0] for row in cursor.fetchall()]
            return items
        finally:
            conn.close()

    # =========================================================================
    # DB 저장
    # =========================================================================

    def _save_promotion(self, promo: PromotionInfo) -> None:
        """행사 정보 DB 저장"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # 현재 행사 저장
            if promo.has_current_promo():
                cursor.execute("""
                    INSERT OR REPLACE INTO promotions
                    (item_cd, item_nm, promo_type, start_date, end_date, is_active, store_id, collected_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """, (
                    promo.item_cd,
                    promo.item_nm,
                    promo.promo_type,
                    promo.start_date,
                    promo.end_date,
                    self.store_id,
                    now,
                    now
                ))

            # 다음 달 행사 저장
            if promo.has_next_promo():
                cursor.execute("""
                    INSERT OR REPLACE INTO promotions
                    (item_cd, item_nm, promo_type, start_date, end_date, is_active, store_id, collected_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """, (
                    promo.item_cd,
                    promo.item_nm,
                    promo.next_promo_type,
                    promo.next_start_date,
                    promo.next_end_date,
                    self.store_id,
                    now,
                    now
                ))

            # 행사 변경 감지 및 저장
            if promo.will_promo_change():
                self._save_promotion_change(promo, cursor)

            conn.commit()
        finally:
            conn.close()

    def _save_promotion_change(self, promo: PromotionInfo, cursor: sqlite3.Cursor) -> None:
        """행사 변경 이력 저장"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 변경 유형 결정
        if promo.has_current_promo() and not promo.has_next_promo():
            change_type = 'end'
            change_date = promo.end_date
        elif not promo.has_current_promo() and promo.has_next_promo():
            change_type = 'start'
            change_date = promo.next_start_date
        else:
            change_type = 'change'
            change_date = promo.end_date

        cursor.execute("""
            INSERT OR IGNORE INTO promotion_changes
            (item_cd, item_nm, change_type, change_date, prev_promo_type, next_promo_type, store_id, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            promo.item_cd,
            promo.item_nm,
            change_type,
            change_date,
            promo.promo_type,
            promo.next_promo_type,
            self.store_id,
            now
        ))


# =============================================================================
# 테스트
# =============================================================================
if __name__ == "__main__":
    # 파싱 테스트
    collector = PromotionCollector.__new__(PromotionCollector)

    # 테스트 케이스 1: 당월 + 익월
    line1 = "당월 : 1+1 | 26.01.01~26.01.31 | 행사 요일 : 매일 | 방식 : 판촉비"
    result1 = collector._parse_promotion_line(line1)
    print(f"테스트 1: {result1}")

    # 테스트 케이스 2: 익월만
    line2 = "익월 : 2+1 | 26.02.01~26.02.28 | 행사 요일 : 매일 | 방식 : 행사원가"
    result2 = collector._parse_promotion_line(line2)
    print(f"테스트 2: {result2}")

    # 날짜 변환 테스트
    print(f"날짜 변환: 26.01.01 -> {collector._convert_date('26.01.01')}")
