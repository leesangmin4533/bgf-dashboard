"""
폐기 위험 알림 실행 스크립트

사용법:
    # 현황 확인만
    python scripts/run_expiry_alert.py

    # 카카오톡 발송
    python scripts/run_expiry_alert.py --send
"""

import sys
import io
import argparse
from pathlib import Path

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alert.expiry_checker import ExpiryChecker, run_expiry_check_and_alert


def main() -> None:
    parser = argparse.ArgumentParser(description='폐기 위험 알림')
    parser.add_argument('--send', action='store_true', help='카카오톡 발송')
    parser.add_argument('--quiet', action='store_true', help='콘솔 출력 없이 발송만')
    args = parser.parse_args()

    # 폐기 위험 체크
    checker = ExpiryChecker()

    if not args.quiet:
        checker.print_status()

    # 카카오톡 발송
    if args.send:
        print("\n카카오톡 발송 중...")
        result = checker.send_kakao_alert()
        if result:
            print("✓ 발송 완료!")
        else:
            print("✗ 발송 실패 또는 알림 대상 없음")

    checker.close()


if __name__ == "__main__":
    main()
