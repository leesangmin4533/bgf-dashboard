"""
신규 점포 추가 자동화 스크립트
=============================
사용법:
    python tools/add_store.py --store_id 12345 --store_name "이천신규점"
선택 옵션:
    --location   "경기 이천시"  (기본값)
    --type       "일반점"       (기본값)
    --desc       "설명"
    --password   "초기비밀번호" (대시보드 viewer 계정용, 기본값: store_id)
    --no-account  viewer 계정 생성 생략
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash
# ─────────────────────────────────────────────
# 경로 설정 (프로젝트 루트 기준)
# ─────────────────────────────────────────────
ROOT = Path(__file__).parent.parent          # tools/ 의 상위 = 프로젝트 루트
STORES_JSON  = ROOT / "config" / "stores.json"
COMMON_DB    = ROOT / "data" / "common.db"
STORE_DB_DIR = ROOT / "data" / "stores"
ENV_FILE     = ROOT / ".env"
# ─────────────────────────────────────────────
# 헬퍼 함수들
# ─────────────────────────────────────────────
def print_step(num: int, title: str):
    print(f"\n{'─'*50}")
    print(f"  STEP {num}: {title}")
    print(f"{'─'*50}")
def print_ok(msg: str):
    print(f"  ✅  {msg}")
def print_skip(msg: str):
    print(f"  ⏭️   {msg}")
def print_warn(msg: str):
    print(f"  ⚠️   {msg}")
def print_fail(msg: str):
    print(f"  ❌  {msg}")
# ─────────────────────────────────────────────
# STEP 1: stores.json 업데이트
# ─────────────────────────────────────────────
def update_stores_json(store_id: str, store_name: str,
                       location: str, store_type: str, desc: str) -> bool:
    print_step(1, "stores.json 업데이트")
    if not STORES_JSON.exists():
        print_fail(f"파일 없음: {STORES_JSON}")
        return False
    with open(STORES_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 중복 확인
    existing_ids = [s["store_id"] for s in data.get("stores", [])]
    if store_id in existing_ids:
        print_skip(f"이미 등록된 점포: {store_id}")
        return True
    # 새 점포 항목 추가
    new_store = {
        "store_id":   store_id,
        "store_name": store_name,
        "location":   location,
        "type":       store_type,
        "is_active":  True,
        "description": desc,
        "added_date": datetime.now().strftime("%Y-%m-%d")
    }
    data["stores"].append(new_store)
    # 메타데이터 갱신
    if "_metadata" in data:
        data["_metadata"]["last_updated"] = datetime.now().isoformat()
        data["_metadata"]["total_stores"] = len(data["stores"])
    with open(STORES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print_ok(f"stores.json에 추가 완료 → {store_name} ({store_id})")
    return True
# ─────────────────────────────────────────────
# STEP 2: .env 파일 안내 + 자동 추가 (값이 없을 때)
# ─────────────────────────────────────────────
def update_env_file(store_id: str) -> bool:
    print_step(2, ".env 환경변수 설정")
    uid_key = f"BGF_USER_ID_{store_id}"
    pw_key  = f"BGF_PASSWORD_{store_id}"
    # .env 파일 읽기
    env_lines = []
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            env_lines = f.readlines()
    existing_keys = set()
    for line in env_lines:
        if "=" in line and not line.strip().startswith("#"):
            existing_keys.add(line.split("=")[0].strip())
    # 이미 있으면 스킵
    if uid_key in existing_keys and pw_key in existing_keys:
        print_skip(f"{uid_key}, {pw_key} 이미 존재")
        return True
    # 빈 플레이스홀더 추가
    added = []
    with open(ENV_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n# 신규 점포 {store_id} — {datetime.now().strftime('%Y-%m-%d')} 추가\n")
        if uid_key not in existing_keys:
            f.write(f"{uid_key}=\n")
            added.append(uid_key)
        if pw_key not in existing_keys:
            f.write(f"{pw_key}=\n")
            added.append(pw_key)
    for key in added:
        print_warn(f"{key} 가 .env에 추가됨 — 직접 값을 입력하세요!")
    print(f"\n  📝 .env 파일을 열어서 아래 항목에 실제 값을 입력하세요:")
    print(f"      {uid_key}=<BGF사이트 아이디>")
    print(f"      {pw_key}=<BGF사이트 비밀번호>")
    return True
# ─────────────────────────────────────────────
# STEP 3: 대시보드 viewer 계정 생성
# ─────────────────────────────────────────────
def create_dashboard_account(store_id: str, password: str) -> bool:
    print_step(3, "대시보드 viewer 계정 생성")
    if not COMMON_DB.exists():
        print_fail(f"common.db 없음: {COMMON_DB}")
        return False
    conn = sqlite3.connect(str(COMMON_DB))
    try:
        # 중복 확인
        existing = conn.execute(
            "SELECT id FROM dashboard_users WHERE username = ? OR store_id = ?",
            (store_id, store_id)
        ).fetchone()
        if existing:
            print_skip(f"store_id={store_id} 계정이 이미 존재합니다")
            return True
        now = datetime.now().isoformat()
        pw_hash = generate_password_hash(password)
        conn.execute(
            """INSERT INTO dashboard_users
               (username, password_hash, store_id, role, is_active, created_at, updated_at)
               VALUES (?, ?, ?, 'viewer', 1, ?, ?)""",
            (store_id, pw_hash, store_id, now, now)
        )
        conn.commit()
        print_ok(f"계정 생성 완료 → ID: {store_id} / PW: {password}")
        print_warn("초기 비밀번호를 반드시 변경하세요!")
        return True
    except sqlite3.Error as e:
        print_fail(f"DB 오류: {e}")
        return False
    finally:
        conn.close()
# ─────────────────────────────────────────────
# STEP 4: 매장 DB 파일 사전 생성 (선택)
# ─────────────────────────────────────────────
def ensure_store_db(store_id: str) -> bool:
    print_step(4, "매장 DB 파일 확인")
    db_path = STORE_DB_DIR / f"{store_id}.db"
    if db_path.exists() and db_path.stat().st_size > 0:
        print_skip(f"이미 존재: {db_path}")
        return True
    # 스케줄러 첫 실행 시 자동 생성되므로 안내만
    print_ok(f"DB는 스케줄러 첫 실행 시 자동 생성됩니다")
    print(f"      경로: {db_path}")
    # 디렉토리만 미리 생성
    STORE_DB_DIR.mkdir(parents=True, exist_ok=True)
    return True
# ─────────────────────────────────────────────
# STEP 5: 최종 체크리스트 출력
# ─────────────────────────────────────────────
def print_checklist(store_id: str, store_name: str):
    print(f"\n{'═'*50}")
    print(f"  🎉 {store_name} ({store_id}) 추가 완료!")
    print(f"{'═'*50}")
    print("""
  ✅ stores.json 업데이트        → 스케줄러/대시보드 자동 인식
  ✅ .env 플레이스홀더 추가      → 직접 값 입력 필요!
  ✅ 대시보드 viewer 계정 생성   → 초기 PW 변경 권장
  ✅ 매장 DB                     → 스케줄러 첫 실행 시 자동 생성
  📌 남은 작업:
     1. .env 파일에서 BGF 로그인 정보 입력
     2. 스케줄러 재시작 (새 점포 자동 포함)
     3. 대시보드에서 점포 드롭다운 확인
""")
# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="신규 점포 추가 스크립트")
    parser.add_argument("--store_id",   required=True,  help="점포 코드 (예: 12345)")
    parser.add_argument("--store_name", required=True,  help="점포명 (예: 이천신규점)")
    parser.add_argument("--location",   default="경기 이천시", help="위치")
    parser.add_argument("--type",       default="일반점",      help="매장 유형")
    parser.add_argument("--desc",       default="",            help="설명")
    parser.add_argument("--password",   default=None,          help="대시보드 초기 비밀번호 (기본: store_id)")
    parser.add_argument("--no-account", action="store_true",   help="viewer 계정 생성 생략")
    args = parser.parse_args()
    # 기본 비밀번호 = store_id
    password = args.password or args.store_id
    print(f"\n  🚀 신규 점포 추가 시작: {args.store_name} ({args.store_id})")
    results = []
    results.append(update_stores_json(
        args.store_id, args.store_name, args.location, args.type, args.desc
    ))
    results.append(update_env_file(args.store_id))
    if not args.no_account:
        results.append(create_dashboard_account(args.store_id, password))
    else:
        print_step(3, "대시보드 계정 생성")
        print_skip("--no-account 옵션으로 생략")
    results.append(ensure_store_db(args.store_id))
    if all(results):
        print_checklist(args.store_id, args.store_name)
    else:
        print_fail("\n일부 단계에서 오류가 발생했습니다. 위 메시지를 확인하세요.")
        sys.exit(1)
if __name__ == "__main__":
    main()
