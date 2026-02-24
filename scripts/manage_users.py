"""대시보드 사용자 관리 CLI

Usage:
    python scripts/manage_users.py create --username admin --password admin1234 --store-id 46513 --role admin
    python scripts/manage_users.py create --username store46704 --password pass1234 --store-id 46704
    python scripts/manage_users.py list
    python scripts/manage_users.py reset-password --username admin --password newpass
    python scripts/manage_users.py delete --username user01
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.database.repos.user_repo import DashboardUserRepository
from src.infrastructure.database.schema import init_common_db


def cmd_create(args):
    init_common_db()
    repo = DashboardUserRepository()

    if repo.get_by_username(args.username):
        print(f"[ERROR] '{args.username}' 아이디가 이미 존재합니다.")
        return 1

    user_id = repo.create_user(
        username=args.username,
        password=args.password,
        store_id=args.store_id,
        role=args.role,
        full_name=args.full_name,
    )
    print(f"[OK] 사용자 생성 완료: id={user_id}, username={args.username}, "
          f"store_id={args.store_id}, role={args.role}")
    return 0


def cmd_list(args):
    init_common_db()
    repo = DashboardUserRepository()
    users = repo.list_users()

    if not users:
        print("등록된 사용자가 없습니다.")
        return 0

    header = f"{'ID':>4}  {'아이디':<15} {'매장':>6}  {'역할':<8} {'활성':>4}  {'이름':<15} {'마지막 로그인':<20}"
    print(header)
    print("-" * len(header))
    for u in users:
        active = "Y" if u["is_active"] else "N"
        last = u.get("last_login_at") or "-"
        if len(last) > 19:
            last = last[:19]
        name = u.get("full_name") or "-"
        print(f"{u['id']:>4}  {u['username']:<15} {u['store_id']:>6}  {u['role']:<8} {active:>4}  {name:<15} {last:<20}")
    return 0


def cmd_reset_password(args):
    init_common_db()
    repo = DashboardUserRepository()
    user = repo.get_by_username(args.username)
    if not user:
        print(f"[ERROR] '{args.username}' 사용자를 찾을 수 없습니다.")
        return 1

    repo.update_user(user["id"], password=args.password)
    print(f"[OK] '{args.username}' 비밀번호 변경 완료")
    return 0


def cmd_delete(args):
    init_common_db()
    repo = DashboardUserRepository()
    user = repo.get_by_username(args.username)
    if not user:
        print(f"[ERROR] '{args.username}' 사용자를 찾을 수 없습니다.")
        return 1

    repo.delete_user(user["id"])
    print(f"[OK] '{args.username}' 삭제 완료")
    return 0


def main():
    parser = argparse.ArgumentParser(description="BGF 대시보드 사용자 관리")
    sub = parser.add_subparsers(dest="command", help="명령어")
    sub.required = True

    # create
    p_create = sub.add_parser("create", help="사용자 생성")
    p_create.add_argument("--username", required=True, help="로그인 아이디")
    p_create.add_argument("--password", required=True, help="비밀번호")
    p_create.add_argument("--store-id", required=True, help="매장 코드")
    p_create.add_argument("--role", default="viewer", choices=["admin", "viewer"], help="역할 (기본: viewer)")
    p_create.add_argument("--full-name", default=None, help="표시 이름")
    p_create.set_defaults(func=cmd_create)

    # list
    p_list = sub.add_parser("list", help="사용자 목록")
    p_list.set_defaults(func=cmd_list)

    # reset-password
    p_reset = sub.add_parser("reset-password", help="비밀번호 변경")
    p_reset.add_argument("--username", required=True, help="대상 아이디")
    p_reset.add_argument("--password", required=True, help="새 비밀번호")
    p_reset.set_defaults(func=cmd_reset_password)

    # delete
    p_del = sub.add_parser("delete", help="사용자 삭제")
    p_del.add_argument("--username", required=True, help="대상 아이디")
    p_del.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
