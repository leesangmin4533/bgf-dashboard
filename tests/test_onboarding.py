"""온보딩 플로우 테스트"""

import os
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from cryptography.fernet import Fernet


# ── 테스트용 DB 스키마 ──────────────────────────────────────

def _create_test_db(db_path):
    """온보딩 테스트용 common.db (v58 스키마)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE dashboard_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            store_id TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'viewer',
            is_active INTEGER DEFAULT 1,
            full_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT,
            bgf_id TEXT,
            bgf_password_enc TEXT,
            store_code TEXT,
            store_name TEXT,
            onboarding_step INTEGER DEFAULT 0,
            active_categories TEXT DEFAULT '001,002,003,004,005,012',
            kakao_connected INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE invite_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            store_id TEXT,
            created_by INTEGER,
            used_by INTEGER,
            is_used INTEGER DEFAULT 0,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            used_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invite_codes_code ON invite_codes(code)")
    conn.execute("""
        CREATE TABLE onboarding_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            step INTEGER NOT NULL,
            action TEXT NOT NULL,
            error_code TEXT,
            duration_sec REAL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_onboarding_events_user ON onboarding_events(user_id)")
    conn.execute("""
        CREATE TABLE stores (
            store_id TEXT PRIMARY KEY,
            store_name TEXT NOT NULL,
            location TEXT,
            type TEXT,
            is_active INTEGER DEFAULT 1,
            bgf_user_id TEXT,
            bgf_password TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # 테스트 매장 등록
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO stores (store_id, store_name, location, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("46513", "CU 동양대점", "경북 영주시", now, now),
    )
    conn.commit()
    conn.close()
    return db_path


# ── 픽스처 ──────────────────────────────────────────────────

@pytest.fixture
def onboarding_db(tmp_path):
    """온보딩 테스트용 DB."""
    return _create_test_db(tmp_path / "test_common.db")


@pytest.fixture
def repo(onboarding_db):
    """OnboardingRepository with test DB."""
    from src.infrastructure.database.repos.onboarding_repo import OnboardingRepository
    return OnboardingRepository(db_path=onboarding_db)


@pytest.fixture
def user_repo(onboarding_db):
    """DashboardUserRepository with test DB."""
    from src.infrastructure.database.repos.user_repo import DashboardUserRepository
    return DashboardUserRepository(db_path=onboarding_db)


@pytest.fixture
def store_repo(onboarding_db):
    """StoreRepository with test DB."""
    from src.infrastructure.database.repos.store_repo import StoreRepository
    return StoreRepository(db_path=onboarding_db)


@pytest.fixture
def test_user(onboarding_db):
    """테스트용 사용자 (onboarding_step=0)."""
    conn = sqlite3.connect(str(onboarding_db))
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO dashboard_users
           (username, password_hash, store_id, role, is_active, full_name,
            created_at, updated_at, onboarding_step)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?, 0)""",
        ("testuser", "hash123", "", "viewer", "테스트", now, now),
    )
    conn.commit()
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return user_id


@pytest.fixture
def env_secret_key(monkeypatch):
    """ORDERFIT_SECRET_KEY 환경변수 설정."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ORDERFIT_SECRET_KEY", key)
    return key


# ── crypto 테스트 ──────────────────────────────────────────

class TestCrypto:

    def test_encrypt_decrypt_roundtrip(self, env_secret_key):
        """암호화 → 복호화 = 원문"""
        from src.utils.crypto import encrypt_password, decrypt_password
        plain = "my_secret_pass"
        encrypted = encrypt_password(plain)
        decrypted = decrypt_password(encrypted)
        assert decrypted == plain

    def test_encrypt_no_plaintext(self, env_secret_key):
        """암호화 결과에 원문 미포함"""
        from src.utils.crypto import encrypt_password
        plain = "visible_password"
        encrypted = encrypt_password(plain)
        assert plain not in encrypted

    def test_encrypt_key_version_prefix(self, env_secret_key):
        """'v1:' 프리픽스 확인"""
        from src.utils.crypto import encrypt_password
        encrypted = encrypt_password("test123")
        assert encrypted.startswith("v1:")

    def test_decrypt_invalid_token(self, env_secret_key):
        """잘못된 토큰 → ValueError"""
        from src.utils.crypto import decrypt_password
        with pytest.raises(ValueError, match="복호화 실패"):
            decrypt_password("v1:invalid_garbage_token")

    def test_validate_secret_key_missing(self, monkeypatch):
        """키 없으면 ValueError"""
        monkeypatch.delenv("ORDERFIT_SECRET_KEY", raising=False)
        from src.utils.crypto import validate_secret_key
        with pytest.raises(ValueError, match="ORDERFIT_SECRET_KEY"):
            validate_secret_key()


# ── invite code 테스트 ──────────────────────────────────────

class TestInviteCode:

    def test_create_invite_code(self, repo):
        """코드 생성 (16자리)"""
        code = repo.create_invite_code()
        assert len(code) == 16
        assert code.isalnum()

    def test_validate_valid_code(self, repo):
        """유효 코드 검증 성공"""
        code = repo.create_invite_code(store_id="46513")
        info = repo.validate_invite_code(code)
        assert info is not None
        assert info["code"] == code
        assert info["store_id"] == "46513"
        assert info["is_used"] == 0

    def test_validate_used_code(self, repo, test_user):
        """사용 완료 코드 → None"""
        code = repo.create_invite_code()
        repo.use_invite_code(code, test_user)
        assert repo.validate_invite_code(code) is None

    def test_validate_expired_code(self, repo):
        """만료 코드 → None"""
        expired = (datetime.now() - timedelta(hours=1)).isoformat()
        code = repo.create_invite_code(expires_at=expired)
        assert repo.validate_invite_code(code) is None

    def test_use_invite_code(self, repo, test_user):
        """사용 처리 (is_used=1, used_by 설정)"""
        code = repo.create_invite_code()
        result = repo.use_invite_code(code, test_user)
        assert result is True
        # 재사용 불가 확인
        result2 = repo.use_invite_code(code, test_user)
        assert result2 is False


# ── onboarding repo 테스트 ──────────────────────────────────

class TestOnboardingRepo:

    def test_save_store_info(self, repo, test_user):
        """매장 저장 + step=2"""
        repo.save_store_info(test_user, "46513", "CU 동양대점")
        status = repo.get_onboarding_status(test_user)
        assert status["step"] == 2
        assert status["store_code"] == "46513"
        assert status["store_name"] == "CU 동양대점"

    def test_save_bgf_credentials_encrypted(self, repo, test_user, env_secret_key, onboarding_db):
        """DB에 평문 미저장"""
        from src.utils.crypto import encrypt_password
        repo.update_onboarding_step(test_user, 2)  # step 2 선행
        enc = encrypt_password("mypassword")
        repo.save_bgf_credentials(test_user, "myid", enc)
        # DB 직접 조회
        conn = sqlite3.connect(str(onboarding_db))
        row = conn.execute("SELECT bgf_password_enc FROM dashboard_users WHERE id = ?", (test_user,)).fetchone()
        conn.close()
        assert row[0] is not None
        assert "mypassword" not in row[0]
        assert row[0].startswith("v1:")

    def test_save_categories(self, repo, test_user):
        """카테고리 저장/조회"""
        repo.update_onboarding_step(test_user, 3)  # step 3 선행
        repo.save_categories(test_user, ["001", "002", "003"])
        status = repo.get_onboarding_status(test_user)
        assert status["step"] == 4
        assert status["active_categories"] == "001,002,003"

    def test_step_progression(self, repo, test_user):
        """단계 순서 보장"""
        repo.update_onboarding_step(test_user, 1)
        assert repo.get_onboarding_status(test_user)["step"] == 1
        repo.update_onboarding_step(test_user, 2)
        assert repo.get_onboarding_status(test_user)["step"] == 2
        repo.update_onboarding_step(test_user, 3)
        assert repo.get_onboarding_status(test_user)["step"] == 3

    def test_step_no_regression(self, repo, test_user):
        """step=3에서 step=1 불가"""
        repo.update_onboarding_step(test_user, 3)
        repo.update_onboarding_step(test_user, 1)
        status = repo.get_onboarding_status(test_user)
        assert status["step"] == 3  # 역행 안 됨

    def test_complete_onboarding(self, repo, test_user):
        """step=5 + completed"""
        repo.update_onboarding_step(test_user, 4)  # step 4 선행
        repo.complete_onboarding(test_user, kakao_connected=True)
        status = repo.get_onboarding_status(test_user)
        assert status["step"] == 5
        assert status["completed"] is True
        assert status["kakao_connected"] is True

    def test_log_event(self, repo, test_user, onboarding_db):
        """이벤트 기록"""
        repo.log_event(test_user, 1, "completed", duration_sec=3.5)
        conn = sqlite3.connect(str(onboarding_db))
        row = conn.execute(
            "SELECT * FROM onboarding_events WHERE user_id = ?", (test_user,)
        ).fetchone()
        conn.close()
        assert row is not None

    def test_get_onboarding_status_nonexistent(self, repo):
        """존재하지 않는 user_id → None"""
        status = repo.get_onboarding_status(99999)
        assert status is None


# ── store lookup 테스트 ──────────────────────────────────────

class TestStoreLookup:

    def test_store_exists(self, store_repo):
        """stores 테이블에서 매장 조회"""
        store = store_repo.get_store("46513")
        assert store is not None
        assert store["store_name"] == "CU 동양대점"

    def test_store_not_found(self, store_repo):
        """존재하지 않는 매장 → None"""
        store = store_repo.get_store("99999")
        assert store is None
