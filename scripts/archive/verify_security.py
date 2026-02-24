# -*- coding: utf-8 -*-
"""보안 검증 스크립트 - stores.json에 민감 정보가 없는지 확인"""
import sys
import json
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))


def verify_security():
    """보안 검증"""
    print("=" * 60)
    print("보안 검증 스크립트")
    print("=" * 60)

    project_root = Path(__file__).parent.parent
    stores_json = project_root / "config" / "stores.json"

    # stores.json 로드
    with open(stores_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    issues = []

    # 1. 점포 데이터에 민감 정보 체크
    print("\n[1] stores.json 민감 정보 체크")
    sensitive_keys = ['bgf_user_id', 'bgf_password', 'password', 'user_id']

    for store in data.get('stores', []):
        for key in sensitive_keys:
            if key in store:
                value = store[key]
                # None이 아니고 빈 문자열도 아닌 경우
                if value is not None and value != "":
                    issues.append(f"점포 {store['store_id']}: {key} 필드에 값이 있음 ('{value}')")

    if issues:
        print("[FAIL] 발견된 보안 문제:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("[PASS] stores.json에 민감 정보 없음")

    # 2. .env.example 체크 (실제 값이 없는지)
    print("\n[2] .env.example 체크")
    env_example = project_root / ".env.example"

    if env_example.exists():
        with open(env_example, 'r', encoding='utf-8') as f:
            content = f.read()

        # 실제 값처럼 보이는 패턴 감지 (숫자만으로 구성된 비밀번호 등)
        suspicious_patterns = [
            ('1113', '실제 비밀번호로 보이는 값'),
            ('46704', '점포 ID가 사용자 ID로 사용됨'),
        ]

        for pattern, desc in suspicious_patterns:
            if pattern in content and 'your_' not in content[:content.index(pattern)]:
                issues.append(f".env.example: {desc} ('{pattern}')")

        if not any(desc for _, desc in suspicious_patterns):
            print("[PASS] .env.example에 실제 값 없음")
    else:
        print("[FAIL] .env.example 파일이 없음")

    # 3. 결과 요약
    print("\n" + "=" * 60)
    if issues:
        print(f"[FAIL] 보안 문제 발견: {len(issues)}건")
        print("\n조치 필요:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    else:
        print("[PASS] 모든 보안 검증 통과")
        print("\n확인 사항:")
        print("  1. stores.json에 인증 정보 없음")
        print("  2. .env.example은 템플릿으로만 사용")
        print("  3. 실제 .env 파일은 .gitignore에 포함되어야 함")
        return True
    print("=" * 60)


if __name__ == "__main__":
    success = verify_security()
    sys.exit(0 if success else 1)
