"""BGF 계정 비밀번호 Fernet 암복호화 유틸리티

온보딩 STEP 3에서 BGF 로그인 정보를 암호화하여 DB에 저장.
복호화는 발주 실행 시에만 사용.
"""

import os
from cryptography.fernet import Fernet, InvalidToken

KEY_VERSION = "v1"


def _get_fernet():
    """Fernet 인스턴스 반환. 환경변수에서 키 로드."""
    key = os.environ.get("ORDERFIT_SECRET_KEY")
    if not key:
        raise ValueError("ORDERFIT_SECRET_KEY 환경변수가 설정되지 않았습니다")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_password(plain_text):
    """평문 → 'v1:<encrypted>' 형태 문자열 반환"""
    f = _get_fernet()
    encrypted = f.encrypt(plain_text.encode()).decode()
    return "{}:{}".format(KEY_VERSION, encrypted)


def decrypt_password(encrypted_text):
    """'v1:<encrypted>' → 평문 반환. 실패 시 ValueError."""
    try:
        if ":" in encrypted_text:
            _version, token = encrypted_text.split(":", 1)
        else:
            token = encrypted_text
        f = _get_fernet()
        return f.decrypt(token.encode()).decode()
    except (InvalidToken, Exception):
        raise ValueError("복호화 실패")


def validate_secret_key():
    """서버 시작 시 호출. 키가 유효한지 검증."""
    key = os.environ.get("ORDERFIT_SECRET_KEY")
    if not key:
        raise ValueError(
            "ORDERFIT_SECRET_KEY 환경변수가 설정되지 않았습니다. "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())" 로 생성하세요.'
        )
    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        raise ValueError("ORDERFIT_SECRET_KEY 형식이 올바르지 않습니다 (Fernet 키 필요)")
