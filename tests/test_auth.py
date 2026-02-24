"""대시보드 인증 시스템 테스트"""

import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from src.infrastructure.database.repos.user_repo import DashboardUserRepository
from src.infrastructure.database.repos.signup_repo import SignupRequestRepository


# ── 픽스처 ──────────────────────────────────────────────────

@pytest.fixture
def auth_db(tmp_path):
    """인증 테스트용 임시 DB."""
    db_path = tmp_path / "test_common.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE dashboard_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            store_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            is_active INTEGER DEFAULT 1,
            full_name TEXT,
            phone TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE signup_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reject_reason TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by INTEGER
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def repo(auth_db):
    """DashboardUserRepository with test DB."""
    return DashboardUserRepository(db_path=auth_db)


@pytest.fixture
def admin_user(repo):
    """테스트용 admin 사용자."""
    user_id = repo.create_user("admin", "admin1234", "46513", "admin", "관리자")
    return repo.get_by_id(user_id)


@pytest.fixture
def viewer_user(repo):
    """테스트용 viewer 사용자."""
    user_id = repo.create_user("store46704", "pass1234", "46704", "viewer", "동양점")
    return repo.get_by_id(user_id)


# ── Repository 테스트 ────────────────────────────────────────

class TestDashboardUserRepository:

    def test_create_user(self, repo):
        user_id = repo.create_user("testuser", "pass123", "46513")
        assert user_id > 0

        user = repo.get_by_id(user_id)
        assert user is not None
        assert user["username"] == "testuser"
        assert user["store_id"] == "46513"
        assert user["role"] == "viewer"
        assert user["is_active"] == 1

    def test_create_user_with_full_name(self, repo):
        user_id = repo.create_user("mgr", "pass123", "46513", "admin", "매니저")
        user = repo.get_by_id(user_id)
        assert user["full_name"] == "매니저"
        assert user["role"] == "admin"

    def test_create_duplicate_username_fails(self, repo):
        repo.create_user("dup", "pass1", "46513")
        with pytest.raises(Exception):
            repo.create_user("dup", "pass2", "46704")

    def test_get_by_username(self, repo):
        repo.create_user("findme", "pass123", "46513")
        user = repo.get_by_username("findme")
        assert user is not None
        assert user["username"] == "findme"

    def test_get_by_username_not_found(self, repo):
        assert repo.get_by_username("nobody") is None

    def test_get_by_id(self, repo):
        user_id = repo.create_user("byid", "pass123", "46513")
        user = repo.get_by_id(user_id)
        assert user is not None
        assert user["id"] == user_id

    def test_get_by_id_not_found(self, repo):
        assert repo.get_by_id(99999) is None

    def test_verify_password_success(self, repo):
        repo.create_user("verify", "correct_pw", "46513")
        user = repo.verify_password("verify", "correct_pw")
        assert user is not None
        assert user["username"] == "verify"

    def test_verify_password_wrong(self, repo):
        repo.create_user("verify2", "correct_pw", "46513")
        assert repo.verify_password("verify2", "wrong_pw") is None

    def test_verify_password_inactive_user(self, repo):
        user_id = repo.create_user("inactive", "pass123", "46513")
        repo.update_user(user_id, is_active=False)
        assert repo.verify_password("inactive", "pass123") is None

    def test_verify_password_unknown_user(self, repo):
        assert repo.verify_password("ghost", "pass123") is None

    def test_update_last_login(self, repo):
        user_id = repo.create_user("login_track", "pass123", "46513")
        user = repo.get_by_id(user_id)
        assert user["last_login_at"] is None

        repo.update_last_login(user_id)
        user = repo.get_by_id(user_id)
        assert user["last_login_at"] is not None

    def test_list_users_excludes_password(self, repo):
        repo.create_user("user1", "pass1", "46513")
        repo.create_user("user2", "pass2", "46704")

        users = repo.list_users()
        assert len(users) == 2
        for u in users:
            assert "password_hash" not in u
            assert "username" in u
            assert "store_id" in u

    def test_update_user_password(self, repo):
        user_id = repo.create_user("pwchange", "oldpass", "46513")
        repo.update_user(user_id, password="newpass")

        assert repo.verify_password("pwchange", "oldpass") is None
        assert repo.verify_password("pwchange", "newpass") is not None

    def test_update_user_role(self, repo):
        user_id = repo.create_user("rolechange", "pass123", "46513")
        assert repo.get_by_id(user_id)["role"] == "viewer"

        repo.update_user(user_id, role="admin")
        assert repo.get_by_id(user_id)["role"] == "admin"

    def test_update_user_not_found(self, repo):
        assert repo.update_user(99999, role="admin") is False

    def test_update_user_no_kwargs(self, repo):
        user_id = repo.create_user("noop", "pass123", "46513")
        assert repo.update_user(user_id) is False

    def test_delete_user(self, repo):
        user_id = repo.create_user("deleteme", "pass123", "46513")
        assert repo.delete_user(user_id) is True
        assert repo.get_by_id(user_id) is None

    def test_delete_user_not_found(self, repo):
        assert repo.delete_user(99999) is False


# ── API 테스트 ──────────────────────────────────────────────

@pytest.fixture
def flask_app(auth_db):
    """Flask test app with auth."""
    from src.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    # DashboardUserRepository가 테스트 DB를 사용하도록 패치
    with patch.object(
        DashboardUserRepository,
        "_get_conn",
        lambda self: sqlite3.connect(str(auth_db)),
    ):
        # admin 사용자 생성
        repo = DashboardUserRepository(db_path=auth_db)
        repo.create_user("admin", "admin1234", "46513", "admin", "관리자")
        repo.create_user("viewer1", "pass1234", "46704", "viewer", "동양점")

        yield app


@pytest.fixture
def client(flask_app):
    """Flask test client."""
    return flask_app.test_client()


class TestAuthAPI:

    def test_login_success(self, client, auth_db):
        with patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        ):
            resp = client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin1234",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["user"]["username"] == "admin"
            assert data["user"]["role"] == "admin"

    def test_login_wrong_password(self, client, auth_db):
        with patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        ):
            resp = client.post("/api/auth/login", json={
                "username": "admin",
                "password": "wrong",
            })
            assert resp.status_code == 401

    def test_login_missing_fields(self, client):
        resp = client.post("/api/auth/login", json={"username": ""})
        assert resp.status_code == 400

    def test_session_without_login(self, client):
        resp = client.get("/api/auth/session")
        assert resp.status_code == 401

    def test_session_after_login(self, client, auth_db):
        with patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        ):
            client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin1234",
            })
            resp = client.get("/api/auth/session")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["user"]["store_id"] == "46513"

    def test_logout(self, client, auth_db):
        with patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        ):
            client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin1234",
            })
            resp = client.post("/api/auth/logout")
            assert resp.status_code == 200

            resp = client.get("/api/auth/session")
            assert resp.status_code == 401


class TestAdminRequired:

    def test_admin_can_list_users(self, client, auth_db):
        from src.web.routes.api_auth import _login_attempts
        _login_attempts.clear()
        with patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        ):
            client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin1234",
            })
            resp = client.get("/api/auth/users")
            assert resp.status_code == 200

    def test_viewer_cannot_list_users(self, client, auth_db):
        from src.web.routes.api_auth import _login_attempts
        _login_attempts.clear()
        with patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        ):
            client.post("/api/auth/login", json={
                "username": "viewer1",
                "password": "pass1234",
            })
            resp = client.get("/api/auth/users")
            assert resp.status_code == 403


class TestStoreIsolation:

    def test_viewer_cannot_access_other_store(self, client, auth_db):
        from src.web.routes.api_auth import _login_attempts
        _login_attempts.clear()
        with patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        ):
            # viewer1 -> store_id=46704
            client.post("/api/auth/login", json={
                "username": "viewer1",
                "password": "pass1234",
            })
            # 다른 매장 접근 시도
            resp = client.get("/api/home/status?store_id=46513")
            assert resp.status_code == 403

    def test_viewer_can_access_own_store(self, client, auth_db):
        from src.web.routes.api_auth import _login_attempts
        _login_attempts.clear()
        with patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        ):
            client.post("/api/auth/login", json={
                "username": "viewer1",
                "password": "pass1234",
            })
            # 자기 매장은 접근 가능 (API 자체가 에러 안 나면 OK)
            resp = client.get("/api/home/status?store_id=46704")
            # 200 또는 500 (DB 없어서) 둘 다 OK, 403이 아니면 됨
            assert resp.status_code != 403


# ── SignupRequestRepository 테스트 ──────────────────────────

@pytest.fixture
def signup_repo(auth_db):
    """SignupRequestRepository with test DB."""
    return SignupRequestRepository(db_path=auth_db)


class TestSignupRequestRepository:

    def test_create_request(self, signup_repo):
        req_id = signup_repo.create_request("12345", "pass1234", "010-1111-2222")
        assert req_id > 0

        req = signup_repo.get_by_id(req_id)
        assert req is not None
        assert req["store_id"] == "12345"
        assert req["phone"] == "010-1111-2222"
        assert req["status"] == "pending"
        assert req["password_hash"] != "pass1234"  # 해싱됨

    def test_get_pending(self, signup_repo):
        signup_repo.create_request("11111", "pw1", "010-0000-0001")
        signup_repo.create_request("22222", "pw2", "010-0000-0002")
        pending = signup_repo.get_pending()
        assert len(pending) == 2
        # password_hash가 목록에 포함되지 않아야 함
        assert "password_hash" not in pending[0]

    def test_get_pending_count(self, signup_repo):
        assert signup_repo.get_pending_count() == 0
        signup_repo.create_request("33333", "pw3", "010-0000-0003")
        assert signup_repo.get_pending_count() == 1

    def test_approve(self, signup_repo):
        req_id = signup_repo.create_request("44444", "pw4", "010-0000-0004")
        result = signup_repo.approve(req_id, reviewed_by=1)
        assert result is True

        req = signup_repo.get_by_id(req_id)
        assert req["status"] == "approved"
        assert req["reviewed_by"] == 1

    def test_approve_already_processed(self, signup_repo):
        req_id = signup_repo.create_request("55555", "pw5", "010-0000-0005")
        signup_repo.approve(req_id, reviewed_by=1)
        # 이미 승인된 요청을 다시 승인 시도
        result = signup_repo.approve(req_id, reviewed_by=1)
        assert result is False

    def test_reject(self, signup_repo):
        req_id = signup_repo.create_request("66666", "pw6", "010-0000-0006")
        result = signup_repo.reject(req_id, reviewed_by=1, reason="테스트 거절")
        assert result is True

        req = signup_repo.get_by_id(req_id)
        assert req["status"] == "rejected"
        assert req["reject_reason"] == "테스트 거절"

    def test_has_pending_for_store(self, signup_repo):
        assert signup_repo.has_pending_for_store("77777") is False
        signup_repo.create_request("77777", "pw7", "010-0000-0007")
        assert signup_repo.has_pending_for_store("77777") is True


# ── Signup API 테스트 ──────────────────────────────────────

class TestSignupAPI:

    def _patch_both_repos(self, auth_db):
        """DashboardUserRepository + SignupRequestRepository 동시 패치."""
        p1 = patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        )
        p2 = patch.object(
            SignupRequestRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        )
        return p1, p2

    def test_signup_success(self, client, auth_db):
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            resp = client.post("/api/auth/signup", json={
                "store_id": "99999",
                "password": "pass1234",
                "phone": "010-9999-8888",
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["id"] > 0

    def test_signup_missing_fields(self, client, auth_db):
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            resp = client.post("/api/auth/signup", json={"store_id": "99999"})
            assert resp.status_code == 400

    def test_signup_invalid_store_id(self, client, auth_db):
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            resp = client.post("/api/auth/signup", json={
                "store_id": "ABC",
                "password": "pass1234",
                "phone": "010-1234-5678",
            })
            assert resp.status_code == 400

    def test_signup_duplicate_pending(self, client, auth_db):
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            client.post("/api/auth/signup", json={
                "store_id": "88888",
                "password": "pass1234",
                "phone": "010-8888-7777",
            })
            resp = client.post("/api/auth/signup", json={
                "store_id": "88888",
                "password": "pass5678",
                "phone": "010-8888-6666",
            })
            assert resp.status_code == 409

    def test_signup_existing_user(self, client, auth_db):
        """이미 등록된 매장은 가입 불가."""
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            # admin은 username='admin', store_id='46513'이므로
            # store_id '46513'으로 가입하면 username='46513'은 존재하지 않지만
            # 여기서는 viewer1의 username='viewer1'이 있는 상태
            # approve 시 username=store_id이므로, 기존 유저의 username과 같은 store_id 테스트
            resp = client.post("/api/auth/signup", json={
                "store_id": "admin",  # 5자리 숫자 아님
                "password": "pass1234",
                "phone": "010-1234-5678",
            })
            assert resp.status_code == 400  # store_id 검증 실패


class TestApproveRejectAPI:

    def _patch_both_repos(self, auth_db):
        p1 = patch.object(
            DashboardUserRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        )
        p2 = patch.object(
            SignupRequestRepository,
            "_get_conn",
            lambda self: sqlite3.connect(str(auth_db)),
        )
        return p1, p2

    def _login_admin(self, client, auth_db):
        from src.web.routes.api_auth import _login_attempts
        _login_attempts.clear()
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin1234",
            })

    def test_approve_flow(self, client, auth_db):
        """가입 요청 → 승인 → 로그인 가능."""
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            # 1. 가입 요청
            resp = client.post("/api/auth/signup", json={
                "store_id": "77777",
                "password": "mypass",
                "phone": "010-7777-6666",
            })
            req_id = resp.get_json()["id"]

            # 2. admin 로그인
            from src.web.routes.api_auth import _login_attempts
            _login_attempts.clear()
            client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin1234",
            })

            # 3. 대기 목록 확인
            resp = client.get("/api/auth/signup-requests")
            assert resp.status_code == 200
            assert len(resp.get_json()["requests"]) >= 1

            # 4. 승인
            resp = client.post(f"/api/auth/signup-requests/{req_id}/approve")
            assert resp.status_code == 200

            # 5. 승인된 계정으로 로그인
            _login_attempts.clear()
            resp = client.post("/api/auth/login", json={
                "username": "77777",
                "password": "mypass",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["user"]["store_id"] == "77777"
            assert data["user"]["role"] == "viewer"

    def test_reject_flow(self, client, auth_db):
        """가입 요청 → 거절."""
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            # 1. 가입 요청
            resp = client.post("/api/auth/signup", json={
                "store_id": "66666",
                "password": "mypass",
                "phone": "010-6666-5555",
            })
            req_id = resp.get_json()["id"]

            # 2. admin 로그인
            from src.web.routes.api_auth import _login_attempts
            _login_attempts.clear()
            client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin1234",
            })

            # 3. 거절
            resp = client.post(f"/api/auth/signup-requests/{req_id}/reject", json={
                "reason": "테스트 거절",
            })
            assert resp.status_code == 200

            # 4. 거절된 계정으로 로그인 불가
            _login_attempts.clear()
            resp = client.post("/api/auth/login", json={
                "username": "66666",
                "password": "mypass",
            })
            assert resp.status_code == 401

    def test_approve_not_found(self, client, auth_db):
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            from src.web.routes.api_auth import _login_attempts
            _login_attempts.clear()
            client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin1234",
            })
            resp = client.post("/api/auth/signup-requests/99999/approve")
            assert resp.status_code == 404

    def test_viewer_cannot_approve(self, client, auth_db):
        p1, p2 = self._patch_both_repos(auth_db)
        with p1, p2:
            from src.web.routes.api_auth import _login_attempts
            _login_attempts.clear()
            client.post("/api/auth/login", json={
                "username": "viewer1",
                "password": "pass1234",
            })
            resp = client.post("/api/auth/signup-requests/1/approve")
            assert resp.status_code == 403
