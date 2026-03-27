"""초대 코드 발급 CLI

사용법:
    python scripts/generate_invite_code.py                    # 범용 코드 1개
    python scripts/generate_invite_code.py --store 46513      # 특정 매장용
    python scripts/generate_invite_code.py --count 5          # 5개 일괄
    python scripts/generate_invite_code.py --expires 7d       # 7일 후 만료
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infrastructure.database.repos.onboarding_repo import OnboardingRepository


def parse_expires(value):
    """만료 기간 파싱. 7d → 7일 후, 24h → 24시간 후."""
    if not value:
        return None
    value = value.strip().lower()
    if value.endswith("d"):
        days = int(value[:-1])
        return (datetime.now() + timedelta(days=days)).isoformat()
    elif value.endswith("h"):
        hours = int(value[:-1])
        return (datetime.now() + timedelta(hours=hours)).isoformat()
    else:
        return None


def main():
    parser = argparse.ArgumentParser(description="발주핏 초대 코드 발급")
    parser.add_argument("--store", type=str, default=None, help="특정 매장용 코드 (매장 코드)")
    parser.add_argument("--count", type=int, default=1, help="발급 개수 (기본: 1)")
    parser.add_argument("--expires", type=str, default=None, help="만료 기간 (예: 7d, 24h)")
    parser.add_argument("--admin-id", type=int, default=1, help="발급자 admin user_id (기본: 1)")
    args = parser.parse_args()

    expires_at = parse_expires(args.expires)
    repo = OnboardingRepository()

    print("=" * 40)
    print("  발주핏 초대 코드 발급")
    print("=" * 40)

    codes = []
    for _ in range(args.count):
        code = repo.create_invite_code(
            store_id=args.store,
            created_by=args.admin_id,
            expires_at=expires_at,
        )
        codes.append(code)

    for code in codes:
        print("  코드: {}".format(code))

    print("-" * 40)
    if args.store:
        print("  매장: {}".format(args.store))
    if expires_at:
        print("  만료: {}".format(expires_at))
    print("  발급 수: {}개".format(len(codes)))
    print("=" * 40)


if __name__ == "__main__":
    main()
