#!/usr/bin/env python3
"""
골든 스냅샷 비교 유틸리티 — 리팩토링 전후 발주량 비교

사용법:
    python tests/golden/compare_snapshot.py before.json after.json
    python tests/golden/compare_snapshot.py before.json after.json --verbose
    python tests/golden/compare_snapshot.py before.json before.json  # 자체 검증 (PASS)

출력:
    PASS: 전부 동일
    DIFF: 다른 항목 있음 (상세 목록 출력)
"""
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class SnapshotDiff:
    """단일 SKU의 차이 정보"""
    def __init__(self, product_code: str, product_name: str, mid_cd: str,
                 before_qty: int, after_qty: int,
                 before_stages: dict, after_stages: dict):
        self.product_code = product_code
        self.product_name = product_name
        self.mid_cd = mid_cd
        self.before_qty = before_qty
        self.after_qty = after_qty
        self.before_stages = before_stages
        self.after_stages = after_stages
        self.changed_stage = self._find_changed_stage()

    def _find_changed_stage(self) -> str:
        """처음으로 값이 달라진 단계를 찾는다"""
        stage_order = [
            "after_rule", "after_rop", "after_promo", "after_ml",
            "after_diff", "after_promo_floor", "after_sub", "after_cap",
            "after_round", "final",
            "before_floor", "after_floor", "after_food_cap", "after_manual_deduct",
        ]
        for stage in stage_order:
            b_val = self.before_stages.get(stage)
            a_val = self.after_stages.get(stage)
            if b_val != a_val and b_val != "추적불가" and a_val != "추적불가":
                return stage
        return "unknown"

    def to_dict(self) -> dict:
        return {
            "product_code": self.product_code,
            "product_name": self.product_name,
            "mid_cd": self.mid_cd,
            "before_qty": self.before_qty,
            "after_qty": self.after_qty,
            "delta": self.after_qty - self.before_qty,
            "changed_stage": self.changed_stage,
        }


class CompareResult:
    """비교 결과"""
    def __init__(self):
        self.total_before: int = 0
        self.total_after: int = 0
        self.matched: int = 0
        self.diffs: List[SnapshotDiff] = []
        self.only_in_before: List[str] = []
        self.only_in_after: List[str] = []

    @property
    def total(self) -> int:
        return max(self.total_before, self.total_after)

    @property
    def match_rate(self) -> float:
        if self.total == 0:
            return 100.0
        return (self.matched / self.total) * 100

    @property
    def is_pass(self) -> bool:
        return len(self.diffs) == 0 and len(self.only_in_before) == 0 and len(self.only_in_after) == 0


def load_snapshot(path: str) -> dict:
    """JSON 스냅샷 파일 로드"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare_snapshots(before_path: str, after_path: str) -> CompareResult:
    """
    리팩토링 전후 스냅샷을 비교한다.

    Args:
        before_path: 리팩토링 전 스냅샷 파일 경로
        after_path: 리팩토링 후 스냅샷 파일 경로

    Returns:
        CompareResult 객체
    """
    before = load_snapshot(before_path)
    after = load_snapshot(after_path)

    result = CompareResult()

    # product_code를 키로 하는 dict 생성
    before_map: Dict[str, dict] = {
        p["product_code"]: p for p in before.get("products", [])
    }
    after_map: Dict[str, dict] = {
        p["product_code"]: p for p in after.get("products", [])
    }

    result.total_before = len(before_map)
    result.total_after = len(after_map)

    # before에만 있는 SKU
    result.only_in_before = [
        cd for cd in before_map if cd not in after_map
    ]

    # after에만 있는 SKU
    result.only_in_after = [
        cd for cd in after_map if cd not in before_map
    ]

    # 양쪽에 모두 있는 SKU 비교
    common_codes = set(before_map.keys()) & set(after_map.keys())
    for cd in sorted(common_codes):
        b = before_map[cd]
        a = after_map[cd]
        b_qty = b.get("final_qty", 0)
        a_qty = a.get("final_qty", 0)

        if b_qty == a_qty:
            result.matched += 1
        else:
            diff = SnapshotDiff(
                product_code=cd,
                product_name=b.get("product_name", a.get("product_name", "")),
                mid_cd=b.get("category_mid", a.get("category_mid", "")),
                before_qty=b_qty,
                after_qty=a_qty,
                before_stages=b.get("stages", {}),
                after_stages=a.get("stages", {}),
            )
            result.diffs.append(diff)

    return result


def print_report(result: CompareResult, verbose: bool = False):
    """비교 결과를 출력한다"""
    print()
    print("=" * 70)
    print("  골든 스냅샷 비교 결과")
    print("=" * 70)
    print(f"  Before: {result.total_before}개 SKU")
    print(f"  After:  {result.total_after}개 SKU")
    print(f"  일치:   {result.matched}개 / {result.total}개 ({result.match_rate:.1f}%)")
    print(f"  차이:   {len(result.diffs)}개")

    if result.only_in_before:
        print(f"  Before에만 있음: {len(result.only_in_before)}개")
    if result.only_in_after:
        print(f"  After에만 있음:  {len(result.only_in_after)}개")

    if result.diffs:
        print()
        print("-" * 70)
        print("  차이 목록 (발주량이 다른 SKU)")
        print("-" * 70)
        print(f"  {'상품코드':<12} {'상품명':<20} {'mid':<5} {'Before':>6} {'After':>6} {'Delta':>6} {'변경단계'}")
        print(f"  {'-'*12} {'-'*20} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*15}")
        for diff in result.diffs[:50]:  # 최대 50개만 출력
            delta_str = f"+{diff.after_qty - diff.before_qty}" if diff.after_qty > diff.before_qty else str(diff.after_qty - diff.before_qty)
            name = diff.product_name[:18] if diff.product_name else ""
            print(
                f"  {diff.product_code:<12} {name:<20} {diff.mid_cd:<5} "
                f"{diff.before_qty:>6} {diff.after_qty:>6} {delta_str:>6} {diff.changed_stage}"
            )
        if len(result.diffs) > 50:
            print(f"  ... 외 {len(result.diffs) - 50}개 생략")

        if verbose:
            print()
            print("-" * 70)
            print("  상세 stages 비교")
            print("-" * 70)
            for diff in result.diffs[:10]:
                print(f"\n  [{diff.product_code}] {diff.product_name}")
                all_stages = sorted(set(
                    list(diff.before_stages.keys()) +
                    list(diff.after_stages.keys())
                ))
                for stage in all_stages:
                    b_val = diff.before_stages.get(stage, "-")
                    a_val = diff.after_stages.get(stage, "-")
                    marker = " ★" if b_val != a_val else ""
                    print(f"    {stage:<25} {str(b_val):>8} → {str(a_val):>8}{marker}")

    if result.only_in_before and verbose:
        print()
        print(f"  Before에만 있는 SKU: {', '.join(result.only_in_before[:20])}")
    if result.only_in_after and verbose:
        print()
        print(f"  After에만 있는 SKU: {', '.join(result.only_in_after[:20])}")

    print()
    print("=" * 70)
    if result.is_pass:
        print("  결과: PASS (전부 동일)")
    else:
        print(f"  결과: DIFF ({len(result.diffs)}개 불일치, "
              f"{len(result.only_in_before)}개 삭제, "
              f"{len(result.only_in_after)}개 추가)")
    print("=" * 70)
    print()


def export_diff_json(result: CompareResult, output_path: str):
    """차이를 JSON으로 내보내기"""
    export = {
        "summary": {
            "total_before": result.total_before,
            "total_after": result.total_after,
            "matched": result.matched,
            "match_rate": round(result.match_rate, 2),
            "diff_count": len(result.diffs),
            "verdict": "PASS" if result.is_pass else "DIFF",
        },
        "diffs": [d.to_dict() for d in result.diffs],
        "only_in_before": result.only_in_before,
        "only_in_after": result.only_in_after,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    print(f"차이 JSON 저장: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="골든 스냅샷 비교기")
    parser.add_argument("before", help="리팩토링 전 스냅샷 JSON")
    parser.add_argument("after", help="리팩토링 후 스냅샷 JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 stages 비교 출력")
    parser.add_argument("--output", "-o", type=str, default=None, help="차이 JSON 출력 경로")
    args = parser.parse_args()

    if not Path(args.before).exists():
        print(f"오류: 파일 없음 — {args.before}")
        return 1
    if not Path(args.after).exists():
        print(f"오류: 파일 없음 — {args.after}")
        return 1

    result = compare_snapshots(args.before, args.after)
    print_report(result, verbose=args.verbose)

    if args.output:
        export_diff_json(result, args.output)

    return 0 if result.is_pass else 1


if __name__ == "__main__":
    sys.exit(main())
