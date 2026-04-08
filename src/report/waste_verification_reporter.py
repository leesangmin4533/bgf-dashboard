"""
폐기 검증 비교 보고서 생성기 (WasteVerificationReporter)

전표 상세 품목 (실제 폐기) vs 추적모듈 (daily_sales, order_tracking, inventory_batches)
비교 결과를 일별 텍스트 파일로 저장.

파일: data/logs/waste_verification_{store_id}_YYYY-MM-DD.txt
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, IO, List, Optional, Set, Tuple

from src.infrastructure.database.repos.waste_slip_repo import WasteSlipRepository
from src.infrastructure.database.repos import SalesRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WasteVerificationReporter:
    """폐기 검증 비교 보고서 생성기

    전표 데이터(공식)와 추적모듈 데이터를 비교하여
    일별 텍스트 보고서를 생성합니다.
    """

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.slip_repo = WasteSlipRepository(store_id=store_id)
        self.sales_repo = SalesRepository(store_id=store_id)
        self._log_dir = Path(__file__).parent.parent.parent / "data" / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ================================================================
    # 데이터 수집
    # ================================================================

    def _get_slip_items(
        self, target_date: str
    ) -> List[Dict[str, Any]]:
        """전표 상세 품목 조회 (상품코드별 합산)"""
        return self.slip_repo.get_waste_slip_items_summary(
            target_date, self.store_id
        )

    def _get_tracking_daily_sales(
        self, target_date: str
    ) -> List[Dict[str, Any]]:
        """daily_sales에서 disuse_qty > 0인 상품 조회"""
        try:
            conn = self.sales_repo._get_conn()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    item_cd,
                    COALESCE(disuse_qty, 0) as disuse_qty,
                    mid_cd
                FROM daily_sales
                WHERE sales_date = ?
                  AND COALESCE(disuse_qty, 0) > 0
                ORDER BY item_cd
                """,
                (target_date,),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            # item_nm은 daily_sales에 없으므로 item_cd를 대체로 사용
            for row in rows:
                row.setdefault("item_nm", row.get("item_cd", ""))
            return rows
        except Exception as e:
            logger.warning(f"daily_sales 조회 실패: {e}")
            return []

    def _get_tracking_order_tracking(
        self, target_date: str
    ) -> List[Dict[str, Any]]:
        """order_tracking에서 status=expired인 상품 조회"""
        try:
            conn = self.sales_repo._get_conn()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # order_tracking 테이블 존재 여부 확인
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='order_tracking'"
            )
            if not cursor.fetchone():
                return []

            cursor.execute(
                """
                SELECT
                    item_cd,
                    COALESCE(item_nm, item_cd) as item_nm,
                    COALESCE(remaining_qty, 0) as qty,
                    status
                FROM order_tracking
                WHERE expiry_time LIKE ? || '%'
                  AND status = 'expired'
                ORDER BY item_cd
                """,
                (target_date,),
            )
            return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"order_tracking 조회 실패: {e}")
            return []

    def _get_tracking_inventory_batches(
        self, target_date: str
    ) -> List[Dict[str, Any]]:
        """inventory_batches에서 status=expired인 상품 조회"""
        try:
            conn = self.sales_repo._get_conn()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # inventory_batches 테이블 존재 여부 확인
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='inventory_batches'"
            )
            if not cursor.fetchone():
                return []

            # waste-verification-slot-based (2026-04-07):
            # status='expired'만 보던 사각지대 해소 → 'consumed'/'disposed'/'expired' 모두 포함.
            # batch-sync-zero-sales-guard 사건처럼 잘못 consumed 마킹된 배치도 검증 base에 포함.
            #
            # waste-verification-historical-noise-filter (2026-04-08):
            # 47863 스키마 드리프트 조사 중 발견 — 백필 시점(2026-03-04)에 2025년 입고분이
            # 일괄 consumed로 생성되어 검증 오탐을 유발. 다음 두 가드 추가:
            #   1) expiration_days <= 30: 장기 유통기한(공산품/담배 등)은 일별 폐기 검증 대상 아님
            #   2) created_at >= 14일 이내: 백필 historical 레코드 제외
            # (waste_slips는 실시간 수집이라 이 필터가 불일치를 만들지 않음)
            cursor.execute(
                """
                SELECT
                    item_cd,
                    COALESCE(item_nm, item_cd) as item_nm,
                    COALESCE(remaining_qty, 0) as qty,
                    status
                FROM inventory_batches
                WHERE date(expiry_date) = ?
                  AND status != 'active'
                  AND COALESCE(expiration_days, 999) <= 30
                  AND date(COALESCE(created_at, '1970-01-01')) >= date(?, '-14 days')
                ORDER BY item_cd
                """,
                (target_date, target_date),
            )
            return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"inventory_batches 조회 실패: {e}")
            return []

    # ================================================================
    # 슬롯 기반 검증 (waste-verification-slot-based, 2026-04-07)
    # ================================================================

    @staticmethod
    def _classify_slot(cre_ymdhms: Optional[str]) -> str:
        """BGF 점주 입력 시각(cre_ymdhms 14자리) → 슬롯 분류.

        Returns:
            'slot_2am': 02:00 ~ 13:59 (1차 박스 폐기)
            'slot_2pm': 14:00 ~ 다음날 01:59 (2차 박스 폐기)
            'unclassified': 파싱 실패
        """
        if not cre_ymdhms or len(cre_ymdhms) != 14:
            return "unclassified"
        try:
            hh = int(cre_ymdhms[8:10])
        except ValueError:
            return "unclassified"
        if 2 <= hh <= 13:
            return "slot_2am"
        if hh >= 14 or hh < 2:
            return "slot_2pm"
        return "unclassified"

    def get_slot_comparison_data(self, target_date: str) -> Dict[str, Any]:
        """슬롯별(02시/14시) 폐기 추적 검증 데이터 조회.

        BGF 입력 시각으로 슬롯 자동 분류 + tracking base에 status!=active 적용.

        Returns:
            {date, store_id, slot_2am, slot_2pm, unclassified, summary}
        """
        try:
            conn = self.sales_repo._get_conn()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 1) BGF 폐기 (헤더 JOIN으로 cre_ymdhms 포함)
            cursor.execute(
                """
                SELECT wsi.item_cd,
                       COALESCE(wsi.item_nm, wsi.item_cd) AS item_nm,
                       wsi.qty, ws.cre_ymdhms
                FROM waste_slip_items wsi
                JOIN waste_slips ws
                  ON wsi.store_id = ws.store_id
                 AND wsi.chit_date = ws.chit_date
                 AND wsi.chit_no = ws.chit_no
                WHERE wsi.chit_date = ?
                """,
                (target_date,),
            )
            slip_rows = [dict(r) for r in cursor.fetchall()]

            # 슬롯별 BGF 폐기 분류
            slip_by_slot: Dict[str, set] = {
                "slot_2am": set(),
                "slot_2pm": set(),
                "unclassified": set(),
            }
            for row in slip_rows:
                slot = self._classify_slot(row.get("cre_ymdhms"))
                slip_by_slot[slot].add(row["item_cd"])

            # 2) Tracking base (02:00 / 14:00 만료 + status != active)
            cursor.execute(
                """
                SELECT DISTINCT item_cd
                FROM inventory_batches
                WHERE date(expiry_date) = ?
                  AND time(expiry_date) = '02:00:00'
                  AND status != 'active'
                """,
                (target_date,),
            )
            tracking_2am = {r["item_cd"] for r in cursor.fetchall()}

            cursor.execute(
                """
                SELECT DISTINCT item_cd
                FROM inventory_batches
                WHERE date(expiry_date) = ?
                  AND time(expiry_date) = '14:00:00'
                  AND status != 'active'
                """,
                (target_date,),
            )
            tracking_2pm = {r["item_cd"] for r in cursor.fetchall()}

            # 3) 슬롯별 매칭 계산
            def _slot_metrics(tracking: set, slip: set) -> Dict[str, Any]:
                matched = tracking & slip
                slip_only = slip - tracking
                tracking_only = tracking - slip
                base = len(tracking)
                return {
                    "tracking_base": base,
                    "slip_count": len(slip),
                    "matched": len(matched),
                    "slip_only": len(slip_only),
                    "tracking_only": len(tracking_only),
                    "match_rate": round(100 * len(matched) / base, 1) if base else 0.0,
                    "slip_only_items": sorted(slip_only),
                    "tracking_only_items": sorted(tracking_only),
                }

            slot_2am = _slot_metrics(tracking_2am, slip_by_slot["slot_2am"])
            slot_2pm = _slot_metrics(tracking_2pm, slip_by_slot["slot_2pm"])

            # 4) 종합 요약
            total_base = slot_2am["tracking_base"] + slot_2pm["tracking_base"]
            total_matched = slot_2am["matched"] + slot_2pm["matched"]
            false_negative = slot_2am["slip_only"] + slot_2pm["slip_only"]
            false_positive = slot_2am["tracking_only"] + slot_2pm["tracking_only"]

            return {
                "date": target_date,
                "store_id": self.store_id,
                "slot_2am": slot_2am,
                "slot_2pm": slot_2pm,
                "unclassified": len(slip_by_slot["unclassified"]),
                "summary": {
                    "overall_match_rate": (
                        round(100 * total_matched / total_base, 1)
                        if total_base
                        else 0.0
                    ),
                    "false_negative": false_negative,
                    "false_positive": false_positive,
                },
            }
        except Exception as e:
            logger.warning(f"[SlotVerify] {self.store_id} 슬롯 비교 실패: {e}")
            return {
                "date": target_date,
                "store_id": self.store_id,
                "error": str(e),
            }

    # ================================================================
    # 비교 로직
    # ================================================================

    def _compare(
        self,
        slip_items: List[Dict[str, Any]],
        tracking_items: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """전표 vs 추적모듈 비교

        Args:
            slip_items: 전표 상세 품목 (item_cd별 합산)
            tracking_items: 추적모듈 품목 {item_cd: {qty, source, item_nm}}

        Returns:
            {matched, slip_only, tracking_only}
        """
        slip_codes: Set[str] = {
            item["item_cd"] for item in slip_items
        }
        tracking_codes: Set[str] = set(tracking_items.keys())

        matched_codes = slip_codes & tracking_codes
        slip_only_codes = slip_codes - tracking_codes
        tracking_only_codes = tracking_codes - slip_codes

        # 매칭된 상품의 상세 비교
        matched = []
        for code in sorted(matched_codes):
            slip_item = next(
                i for i in slip_items if i["item_cd"] == code
            )
            track = tracking_items[code]
            slip_qty = slip_item.get("total_qty", 0) or 0
            track_qty = track.get("qty", 0) or 0
            matched.append({
                "item_cd": code,
                "item_nm": slip_item.get("item_nm", ""),
                "slip_qty": slip_qty,
                "tracking_qty": track_qty,
                "diff": slip_qty - track_qty,
                "source": track.get("source", ""),
            })

        # 전표에만 있는 상품
        slip_only = []
        for code in sorted(slip_only_codes):
            item = next(
                i for i in slip_items if i["item_cd"] == code
            )
            slip_only.append({
                "item_cd": code,
                "item_nm": item.get("item_nm", ""),
                "qty": item.get("total_qty", 0) or 0,
                "large_nm": item.get("large_nm", ""),
            })

        # 추적에만 있는 상품
        tracking_only = []
        for code in sorted(tracking_only_codes):
            track = tracking_items[code]
            tracking_only.append({
                "item_cd": code,
                "item_nm": track.get("item_nm", ""),
                "qty": track.get("qty", 0) or 0,
                "source": track.get("source", ""),
            })

        return {
            "matched": matched,
            "slip_only": slip_only,
            "tracking_only": tracking_only,
        }

    def _merge_tracking_data(
        self,
        daily_sales: List[Dict[str, Any]],
        order_tracking: List[Dict[str, Any]],
        inventory_batches: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """3개 추적 소스를 item_cd 기준으로 병합

        Returns:
            {item_cd: {qty, source, item_nm}}
        """
        merged: Dict[str, Dict[str, Any]] = {}

        for item in daily_sales:
            code = item.get("item_cd", "")
            if not code:
                continue
            if code not in merged:
                merged[code] = {
                    "qty": 0,
                    "source": "daily_sales",
                    "item_nm": item.get("item_nm", ""),
                }
            merged[code]["qty"] += item.get("disuse_qty", 0) or 0

        for item in order_tracking:
            code = item.get("item_cd", "")
            if not code:
                continue
            if code not in merged:
                merged[code] = {
                    "qty": 0,
                    "source": "order_tracking",
                    "item_nm": item.get("item_nm", ""),
                }
            else:
                merged[code]["source"] += "+order_tracking"
            merged[code]["qty"] += item.get("qty", 0) or 0

        for item in inventory_batches:
            code = item.get("item_cd", "")
            if not code:
                continue
            if code not in merged:
                merged[code] = {
                    "qty": 0,
                    "source": "inv_batches",
                    "item_nm": item.get("item_nm", ""),
                }
            else:
                merged[code]["source"] += "+inv_batches"
            merged[code]["qty"] += item.get("qty", 0) or 0

        return merged

    # ================================================================
    # 보고서 생성
    # ================================================================

    def generate_daily_report(
        self,
        target_date: str,
    ) -> Optional[str]:
        """일별 비교 보고서 생성

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD)

        Returns:
            생성된 파일 경로 또는 None
        """
        now = datetime.now()
        weekday_kr = ["월", "화", "수", "목", "금", "토", "일"]
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            day_name = weekday_kr[dt.weekday()]
        except ValueError:
            day_name = ""

        time_str = now.strftime("%H:%M:%S")

        # 1) 데이터 수집
        slip_items = self._get_slip_items(target_date)
        ds_data = self._get_tracking_daily_sales(target_date)
        ot_data = self._get_tracking_order_tracking(target_date)
        ib_data = self._get_tracking_inventory_batches(target_date)

        # 2) 추적 데이터 병합
        tracking_merged = self._merge_tracking_data(
            ds_data, ot_data, ib_data
        )

        # 3) 비교
        comparison = self._compare(slip_items, tracking_merged)

        # 4) 전표 전체 품목 조회 (보고서 섹션1용)
        all_slip_items = self.slip_repo.get_waste_slip_items(
            target_date, self.store_id
        )

        # 5) 보고서 작성
        filename = f"waste_verification_{self.store_id}_{target_date}.txt"
        filepath = self._log_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                self._write_header(
                    f, target_date, day_name, time_str
                )
                self._write_section1_slip(f, all_slip_items)
                self._write_section2_tracking(
                    f, ds_data, ot_data, ib_data, tracking_merged
                )
                self._write_section3_comparison(f, comparison)
                self._write_section4_summary(
                    f, slip_items, tracking_merged, comparison
                )
                self._write_footer(f)

            logger.info(f"폐기 검증 보고서 저장: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.warning(f"폐기 검증 보고서 저장 실패: {e}")
            return None

    # ================================================================
    # 보고서 섹션 작성
    # ================================================================

    def _write_header(
        self, f: IO[str], date_str: str, weekday: str, time_str: str
    ) -> None:
        """헤더"""
        f.write("=" * 72 + "\n")
        f.write("BGF 폐기 검증 보고서\n")
        f.write(
            f"날짜: {date_str} ({weekday})   "
            f"생성: {time_str}   "
            f"매장: {self.store_id or 'N/A'}\n"
        )
        f.write("=" * 72 + "\n\n")

    def _write_section1_slip(
        self, f: IO[str], items: List[Dict[str, Any]]
    ) -> None:
        """섹션 1: 전표 상세 품목 (실제 폐기)"""
        f.write("[1. 전표 상세 품목 (실제 폐기)]\n")
        f.write("-" * 72 + "\n")

        if not items:
            f.write("  (해당 없음)\n\n")
            return

        f.write(
            f"  {'No.':>4s} {'상품코드':<16s} "
            f"{'상품명':<20s} "
            f"{'수량':>4s} {'원가':>8s} {'매가':>8s} "
            f"{'대분류':<10s}\n"
        )
        f.write(
            f"  {'---':>4s} {'-'*14:<16s} "
            f"{'-'*18:<20s} "
            f"{'----':>4s} {'-------':>8s} {'-------':>8s} "
            f"{'-'*8:<10s}\n"
        )

        total_qty = 0
        total_wonga = 0
        total_maega = 0

        for i, item in enumerate(items, 1):
            qty = item.get("qty", 0) or 0
            wonga = item.get("wonga_amt", 0) or 0
            maega = item.get("maega_amt", 0) or 0
            total_qty += qty
            total_wonga += wonga
            total_maega += maega

            item_cd = str(item.get("item_cd", ""))
            item_nm = str(item.get("item_nm", ""))[:18]
            large_nm = str(item.get("large_nm", ""))[:8]

            f.write(
                f"  {i:4d} {item_cd:<16s} "
                f"{item_nm:<20s} "
                f"{qty:4d} {wonga:>8,.0f} {maega:>8,.0f} "
                f"{large_nm:<10s}\n"
            )

        f.write(
            f"\n  소계: {len(items)}건, "
            f"원가 {total_wonga:,.0f}원, "
            f"매가 {total_maega:,.0f}원\n\n"
        )

    def _write_section2_tracking(
        self,
        f: IO[str],
        ds_data: List[Dict[str, Any]],
        ot_data: List[Dict[str, Any]],
        ib_data: List[Dict[str, Any]],
        merged: Dict[str, Dict[str, Any]],
    ) -> None:
        """섹션 2: 추적모듈 데이터"""
        f.write("[2. 추적모듈 데이터]\n")
        f.write("-" * 72 + "\n")

        # 2-1. daily_sales
        f.write("2-1. daily_sales (disuse_qty > 0)\n")
        if ds_data:
            for i, item in enumerate(ds_data, 1):
                item_cd = str(item.get("item_cd", ""))
                item_nm = str(item.get("item_nm", ""))[:18]
                qty = item.get("disuse_qty", 0) or 0
                f.write(
                    f"  {i:4d} {item_cd:<16s} "
                    f"{item_nm:<20s} {qty:4d}\n"
                )
        else:
            f.write("  (해당 없음)\n")

        # 2-2. order_tracking
        f.write("\n2-2. order_tracking (status=expired)\n")
        if ot_data:
            for i, item in enumerate(ot_data, 1):
                item_cd = str(item.get("item_cd", ""))
                item_nm = str(item.get("item_nm", ""))[:18]
                qty = item.get("qty", 0) or 0
                f.write(
                    f"  {i:4d} {item_cd:<16s} "
                    f"{item_nm:<20s} {qty:4d}\n"
                )
        else:
            f.write("  (해당 없음)\n")

        # 2-3. inventory_batches
        f.write("\n2-3. inventory_batches (status=expired)\n")
        if ib_data:
            for i, item in enumerate(ib_data, 1):
                item_cd = str(item.get("item_cd", ""))
                item_nm = str(item.get("item_nm", ""))[:18]
                qty = item.get("qty", 0) or 0
                f.write(
                    f"  {i:4d} {item_cd:<16s} "
                    f"{item_nm:<20s} {qty:4d}\n"
                )
        else:
            f.write("  (해당 없음)\n")

        total_tracking = len(merged)
        f.write(f"\n  추적모듈 합계: {total_tracking}건\n\n")

    def _write_section3_comparison(
        self, f: IO[str], comparison: Dict[str, Any]
    ) -> None:
        """섹션 3: 비교 결과"""
        f.write("[3. 비교 결과]\n")
        f.write("-" * 72 + "\n")

        matched = comparison.get("matched", [])
        slip_only = comparison.get("slip_only", [])
        tracking_only = comparison.get("tracking_only", [])

        # 3-1. 매칭
        f.write("3-1. 양쪽 모두 존재 (매칭)\n")
        if matched:
            f.write(
                f"  {'상품코드':<16s} {'상품명':<20s} "
                f"{'전표':>4s} {'추적':>4s} {'차이':>4s} "
                f"{'소스':<16s}\n"
            )
            f.write(
                f"  {'-'*14:<16s} {'-'*18:<20s} "
                f"{'----':>4s} {'----':>4s} {'----':>4s} "
                f"{'-'*14:<16s}\n"
            )
            for item in matched:
                item_cd = str(item.get("item_cd", ""))
                item_nm = str(item.get("item_nm", ""))[:18]
                f.write(
                    f"  {item_cd:<16s} {item_nm:<20s} "
                    f"{item['slip_qty']:4d} "
                    f"{item['tracking_qty']:4d} "
                    f"{item['diff']:4d} "
                    f"{item['source']:<16s}\n"
                )
        else:
            f.write("  (해당 없음)\n")

        # 3-2. 전표에만 있음
        f.write("\n3-2. 전표에만 있는 상품 (추적 누락)\n")
        if slip_only:
            for item in slip_only:
                item_cd = str(item.get("item_cd", ""))
                item_nm = str(item.get("item_nm", ""))[:18]
                large_nm = str(item.get("large_nm", ""))[:8]
                f.write(
                    f"  {item_cd:<16s} {item_nm:<20s} "
                    f"{item['qty']:4d} ({large_nm})\n"
                )
        else:
            f.write("  (해당 없음)\n")

        # 3-3. 추적에만 있음
        f.write("\n3-3. 추적에만 있는 상품 (전표 미반영)\n")
        if tracking_only:
            for item in tracking_only:
                item_cd = str(item.get("item_cd", ""))
                item_nm = str(item.get("item_nm", ""))[:18]
                f.write(
                    f"  {item_cd:<16s} {item_nm:<20s} "
                    f"{item['qty']:4d} ({item['source']})\n"
                )
        else:
            f.write("  (해당 없음)\n")

        f.write("\n")

    def _write_section4_summary(
        self,
        f: IO[str],
        slip_items: List[Dict[str, Any]],
        tracking_merged: Dict[str, Dict[str, Any]],
        comparison: Dict[str, Any],
    ) -> None:
        """섹션 4: 요약"""
        f.write("[4. 요약]\n")
        f.write("-" * 72 + "\n")

        slip_count = len(slip_items)
        tracking_count = len(tracking_merged)
        matched_count = len(comparison.get("matched", []))
        slip_only_count = len(comparison.get("slip_only", []))
        tracking_only_count = len(comparison.get("tracking_only", []))

        miss_rate = (
            round(slip_only_count / slip_count * 100, 1)
            if slip_count > 0
            else 0
        )

        # 정밀도: 추적이 맞춘 비율 (False Positive 지표)
        precision = (
            round(matched_count / tracking_count * 100, 1)
            if tracking_count > 0
            else 0
        )
        # 재현율: 전표를 추적이 감지한 비율 (False Negative 지표)
        recall = (
            round(matched_count / slip_count * 100, 1)
            if slip_count > 0
            else 0
        )

        f.write(f"  전표 품목:      {slip_count}건\n")
        f.write(f"  추적모듈 품목:  {tracking_count}건\n")
        f.write(f"  매칭:           {matched_count}건\n")
        f.write(
            f"  전표에만 있음:  {slip_only_count}건 "
            f"(추적 누락율 {miss_rate}%)\n"
        )
        f.write(f"  추적에만 있음:  {tracking_only_count}건\n")
        f.write(f"  추적 정밀도:    {precision}% "
                f"(매칭/추적 -- 높을수록 False Positive 적음)\n")
        f.write(f"  추적 재현율:    {recall}% "
                f"(매칭/전표 -- 높을수록 False Negative 적음)\n")

    def _write_footer(self, f: IO[str]) -> None:
        """푸터"""
        f.write("=" * 72 + "\n")

    # ================================================================
    # 비교 결과 데이터 반환 (서비스용)
    # ================================================================

    def get_comparison_data(
        self, target_date: str
    ) -> Dict[str, Any]:
        """비교 결과 데이터 반환 (보고서 저장 없이)

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD)

        Returns:
            {slip_items, tracking_merged, comparison, summary}
        """
        slip_items = self._get_slip_items(target_date)
        ds_data = self._get_tracking_daily_sales(target_date)
        ot_data = self._get_tracking_order_tracking(target_date)
        ib_data = self._get_tracking_inventory_batches(target_date)

        tracking_merged = self._merge_tracking_data(
            ds_data, ot_data, ib_data
        )
        comparison = self._compare(slip_items, tracking_merged)

        slip_count = len(slip_items)
        tracking_count = len(tracking_merged)
        matched_count = len(comparison.get("matched", []))
        slip_only_count = len(comparison.get("slip_only", []))
        tracking_only_count = len(comparison.get("tracking_only", []))
        miss_rate = (
            round(slip_only_count / slip_count * 100, 1)
            if slip_count > 0
            else 0
        )

        precision = (
            round(matched_count / tracking_count * 100, 1)
            if tracking_count > 0
            else 0
        )
        recall = (
            round(matched_count / slip_count * 100, 1)
            if slip_count > 0
            else 0
        )

        return {
            "date": target_date,
            "slip_items": slip_items,
            "tracking_merged": tracking_merged,
            "comparison": comparison,
            "summary": {
                "slip_count": slip_count,
                "tracking_count": tracking_count,
                "matched": matched_count,
                "slip_only": slip_only_count,
                "tracking_only": tracking_only_count,
                "miss_rate": miss_rate,
                "precision": precision,
                "recall": recall,
            },
        }
