"""
자동발주 vs 확정발주 비교 분석 (순수 로직)

I/O 없이 두 데이터 세트를 비교하여 차이(diff)를 생성한다.
- auto_snapshot: 자동발주 스냅샷 (order_snapshots 레코드)
- receiving_data: 센터매입 입고 데이터 (receiving_history 레코드)
"""

from typing import Any, Dict, List


class OrderDiffAnalyzer:
    """자동발주 vs 확정발주 비교 분석"""

    @staticmethod
    def compare(
        auto_snapshot: List[Dict[str, Any]],
        receiving_data: List[Dict[str, Any]],
        store_id: str = "",
        order_date: str = "",
        receiving_date: str = "",
    ) -> Dict[str, Any]:
        """스냅샷과 입고 데이터를 비교하여 diff + summary 생성

        Args:
            auto_snapshot: order_snapshots 레코드 리스트
            receiving_data: receiving_history 레코드 리스트
            store_id: 매장 코드 (diff 레코드에 포함)
            order_date: 발주일
            receiving_date: 입고일

        Returns:
            {
                'diffs': List[Dict],      # 차이 상세
                'summary': Dict,          # 일별 요약
                'unchanged_count': int,
            }
        """
        # item_cd 기준 인덱싱
        # 1차/2차/신선 배송 상품은 receiving_history에 기록되지 않으므로 비교 제외
        # "일반"(비푸드 센터매입)과 "ambient"는 비교 대상
        _NON_COMPARABLE = {"1차", "2차", "신선"}
        auto_map: Dict[str, Dict] = {}
        not_comparable_count = 0
        for item in auto_snapshot:
            ic = item.get("item_cd")
            if not ic:
                continue
            dt = item.get("delivery_type", "")
            if dt in _NON_COMPARABLE:
                not_comparable_count += 1
                continue
            auto_map[ic] = item

        recv_map: Dict[str, Dict] = {}
        for item in receiving_data:
            ic = item.get("item_cd")
            if ic:
                # 동일 상품 여러 전표 → 수량 합산
                if ic in recv_map:
                    recv_map[ic]["order_qty"] = (
                        recv_map[ic].get("order_qty", 0)
                        + _to_int(item.get("order_qty", 0))
                    )
                    recv_map[ic]["receiving_qty"] = (
                        recv_map[ic].get("receiving_qty", 0)
                        + _to_int(item.get("receiving_qty", 0))
                    )
                else:
                    recv_map[ic] = {
                        "item_cd": ic,
                        "item_nm": item.get("item_nm"),
                        "mid_cd": item.get("mid_cd"),
                        "order_qty": _to_int(item.get("order_qty", 0)),
                        "receiving_qty": _to_int(item.get("receiving_qty", 0)),
                        "receiving_date": item.get("receiving_date", receiving_date),
                    }

        all_items = set(auto_map.keys()) | set(recv_map.keys())

        diffs: List[Dict[str, Any]] = []
        unchanged_count = 0
        total_auto_qty = 0
        total_confirmed_qty = 0
        total_receiving_qty = 0
        items_qty_changed = 0
        items_added = 0
        items_removed = 0

        for ic in sorted(all_items):
            auto = auto_map.get(ic)
            recv = recv_map.get(ic)

            auto_qty = auto.get("final_order_qty", 0) if auto else 0
            confirmed_qty = recv.get("order_qty", 0) if recv else 0
            recv_qty = recv.get("receiving_qty", 0) if recv else 0

            total_auto_qty += auto_qty
            total_confirmed_qty += confirmed_qty
            total_receiving_qty += recv_qty

            recv_date = (
                recv.get("receiving_date", receiving_date) if recv else receiving_date
            )

            diff_type = OrderDiffAnalyzer.classify_diff(
                auto_qty, confirmed_qty, recv_qty, auto is not None, recv is not None
            )

            if diff_type == "unchanged":
                unchanged_count += 1
                continue

            item_nm = (
                (auto or {}).get("item_nm")
                or (recv or {}).get("item_nm")
                or ""
            )
            mid_cd = (
                (auto or {}).get("mid_cd")
                or (recv or {}).get("mid_cd")
                or ""
            )

            diff_record = {
                "store_id": store_id,
                "order_date": order_date,
                "receiving_date": recv_date,
                "item_cd": ic,
                "item_nm": item_nm,
                "mid_cd": mid_cd,
                "diff_type": diff_type,
                "auto_order_qty": auto_qty,
                "predicted_qty": auto.get("predicted_qty", 0) if auto else 0,
                "eval_decision": auto.get("eval_decision") if auto else None,
                "confirmed_order_qty": confirmed_qty,
                "receiving_qty": recv_qty,
                "qty_diff": confirmed_qty - auto_qty,
                "receiving_diff": recv_qty - confirmed_qty,
            }
            diffs.append(diff_record)

            if diff_type == "qty_changed":
                items_qty_changed += 1
            elif diff_type == "added":
                items_added += 1
            elif diff_type == "removed":
                items_removed += 1

        total_auto_items = len(auto_map)
        total_confirmed_items = len(recv_map)
        total_items = total_auto_items + items_added
        match_rate = (
            unchanged_count / total_items if total_items > 0 else 0.0
        )

        # 입고 데이터 유무 판단: confirmed_items가 0이면 입고 기록 없음
        has_receiving_data = 1 if total_confirmed_items > 0 else 0

        summary = {
            "store_id": store_id,
            "order_date": order_date,
            "receiving_date": receiving_date,
            "total_auto_items": total_auto_items,
            "total_confirmed_items": total_confirmed_items,
            "items_unchanged": unchanged_count,
            "items_qty_changed": items_qty_changed,
            "items_added": items_added,
            "items_removed": items_removed,
            "items_not_comparable": not_comparable_count,
            "total_auto_qty": total_auto_qty,
            "total_confirmed_qty": total_confirmed_qty,
            "total_receiving_qty": total_receiving_qty,
            "match_rate": round(match_rate, 4),
            "has_receiving_data": has_receiving_data,
        }

        return {
            "diffs": diffs,
            "summary": summary,
            "unchanged_count": unchanged_count,
        }

    @staticmethod
    def classify_diff(
        auto_qty: int,
        confirmed_qty: int,
        receiving_qty: int,
        in_auto: bool,
        in_recv: bool,
    ) -> str:
        """차이 유형 분류

        Args:
            auto_qty: 시스템 발주량
            confirmed_qty: BGF 확정 발주수량
            receiving_qty: 실제 입고수량
            in_auto: 자동발주 스냅샷에 존재
            in_recv: 입고 데이터에 존재

        Returns:
            'unchanged' | 'qty_changed' | 'added' | 'removed' | 'receiving_diff'
        """
        if in_auto and not in_recv:
            return "removed"

        if not in_auto and in_recv:
            return "added"

        # 양쪽 모두 존재
        if auto_qty != confirmed_qty:
            return "qty_changed"

        if receiving_qty != confirmed_qty:
            return "receiving_diff"

        return "unchanged"


def _to_int(val) -> int:
    """안전한 int 변환"""
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0
