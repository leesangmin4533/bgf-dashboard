"""인증 API Blueprint"""

import re
import time
import threading
from collections import defaultdict
from functools import wraps

from flask import Blueprint, jsonify, request, session

from src.infrastructure.database.repos.user_repo import DashboardUserRepository
from src.infrastructure.database.repos.signup_repo import SignupRequestRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

auth_bp = Blueprint("auth", __name__)

# ── 브루트포스 방어 ──────────────────────────────────────────
_login_attempts: dict[str, list[float]] = defaultdict(list)
_attempts_lock = threading.Lock()
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 300  # 5분


def _check_login_rate(ip: str) -> bool:
    """IP당 로그인 시도 횟수 확인. 초과 시 False."""
    now = time.time()
    cutoff = now - _WINDOW_SECONDS
    with _attempts_lock:
        _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]
        if len(_login_attempts[ip]) >= _MAX_ATTEMPTS:
            return False
        _login_attempts[ip].append(now)
    return True


# ── 데코레이터 ──────────────────────────────────────────────

def login_required(f):
    """로그인 필수 데코레이터."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "로그인이 필요합니다", "code": "UNAUTHORIZED"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """관리자 권한 필수 데코레이터."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "로그인이 필요합니다", "code": "UNAUTHORIZED"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "관리자 권한이 필요합니다", "code": "FORBIDDEN"}), 403
        return f(*args, **kwargs)
    return decorated


# ── 입력 검증 ──────────────────────────────────────────────

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
_STORE_ID_RE = re.compile(r"^\d{5}$")
_PHONE_RE = re.compile(r"^[\d\-]{10,13}$")
_MIN_PASSWORD_LEN = 4


# ── 로그인/로그아웃 ──────────────────────────────────────────

@auth_bp.route("/login", methods=["POST"])
def login():
    """로그인 — 세션 생성."""
    ip = request.remote_addr
    if not _check_login_rate(ip):
        logger.warning(f"[AUTH] 로그인 시도 초과: {ip}")
        return jsonify({"error": "로그인 시도가 너무 많습니다. 5분 후 다시 시도하세요.", "code": "RATE_LIMITED"}), 429

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "아이디와 비밀번호를 입력하세요.", "code": "MISSING_FIELDS"}), 400

    if not _USERNAME_RE.match(username):
        return jsonify({"error": "아이디 형식이 올바르지 않습니다.", "code": "INVALID_USERNAME"}), 400

    if len(password) < _MIN_PASSWORD_LEN:
        return jsonify({"error": "비밀번호가 너무 짧습니다.", "code": "INVALID_PASSWORD"}), 400

    repo = DashboardUserRepository()
    user = repo.verify_password(username, password)
    if not user:
        logger.warning(f"[AUTH] 로그인 실패: {username} from {ip}")
        return jsonify({"error": "아이디 또는 비밀번호가 올바르지 않습니다.", "code": "INVALID_CREDENTIALS"}), 401

    # 세션 생성
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["store_id"] = user["store_id"]
    session["role"] = user["role"]
    session["full_name"] = user.get("full_name") or user["username"]

    repo.update_last_login(user["id"])
    logger.info(f"[AUTH] 로그인 성공: {username} (role={user['role']}, store={user['store_id']})")

    return jsonify({
        "message": "로그인 성공",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "store_id": user["store_id"],
            "role": user["role"],
            "full_name": user.get("full_name") or user["username"],
        },
    })


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """로그아웃 — 세션 삭제."""
    username = session.get("username", "unknown")
    session.clear()
    logger.info(f"[AUTH] 로그아웃: {username}")
    return jsonify({"message": "로그아웃 완료"})


@auth_bp.route("/session", methods=["GET"])
@login_required
def get_session():
    """현재 세션 정보."""
    return jsonify({
        "user": {
            "id": session["user_id"],
            "username": session["username"],
            "store_id": session["store_id"],
            "role": session["role"],
            "full_name": session.get("full_name", session["username"]),
        }
    })


# ── 사용자 관리 (admin 전용) ──────────────────────────────────

@auth_bp.route("/users", methods=["GET"])
@admin_required
def list_users():
    """사용자 목록."""
    repo = DashboardUserRepository()
    users = repo.list_users()
    return jsonify({"users": users})


@auth_bp.route("/users", methods=["POST"])
@admin_required
def create_user():
    """사용자 생성."""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    store_id = (data.get("store_id") or "").strip()
    role = data.get("role", "viewer")
    full_name = data.get("full_name")

    if not username or not password or not store_id:
        return jsonify({"error": "username, password, store_id는 필수입니다."}), 400

    if not _USERNAME_RE.match(username):
        return jsonify({"error": "아이디: 영문/숫자/언더스코어 3~30자"}), 400

    if len(password) < _MIN_PASSWORD_LEN:
        return jsonify({"error": f"비밀번호는 {_MIN_PASSWORD_LEN}자 이상이어야 합니다."}), 400

    if role not in ("admin", "viewer"):
        return jsonify({"error": "role은 admin 또는 viewer여야 합니다."}), 400

    repo = DashboardUserRepository()
    if repo.get_by_username(username):
        return jsonify({"error": "이미 존재하는 아이디입니다."}), 409

    user_id = repo.create_user(username, password, store_id, role, full_name)
    logger.info(f"[AUTH] 사용자 생성: {username} (role={role}, store={store_id}) by {session['username']}")
    return jsonify({"message": "사용자 생성 완료", "id": user_id}), 201


@auth_bp.route("/users/<int:user_id>", methods=["PUT"])
@admin_required
def update_user(user_id):
    """사용자 수정."""
    data = request.get_json(silent=True) or {}
    kwargs = {}

    if "password" in data and data["password"]:
        if len(data["password"]) < _MIN_PASSWORD_LEN:
            return jsonify({"error": f"비밀번호는 {_MIN_PASSWORD_LEN}자 이상이어야 합니다."}), 400
        kwargs["password"] = data["password"]
    if "role" in data:
        if data["role"] not in ("admin", "viewer"):
            return jsonify({"error": "role은 admin 또는 viewer여야 합니다."}), 400
        kwargs["role"] = data["role"]
    if "is_active" in data:
        kwargs["is_active"] = bool(data["is_active"])
    if "full_name" in data:
        kwargs["full_name"] = data["full_name"]

    if not kwargs:
        return jsonify({"error": "수정할 항목이 없습니다."}), 400

    repo = DashboardUserRepository()
    if not repo.update_user(user_id, **kwargs):
        return jsonify({"error": "사용자를 찾을 수 없습니다."}), 404

    logger.info(f"[AUTH] 사용자 수정: id={user_id} {list(kwargs.keys())} by {session['username']}")
    return jsonify({"message": "수정 완료"})


@auth_bp.route("/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    """사용자 삭제."""
    if user_id == session.get("user_id"):
        return jsonify({"error": "자기 자신은 삭제할 수 없습니다."}), 400

    repo = DashboardUserRepository()
    if not repo.delete_user(user_id):
        return jsonify({"error": "사용자를 찾을 수 없습니다."}), 404

    logger.info(f"[AUTH] 사용자 삭제: id={user_id} by {session['username']}")
    return jsonify({"message": "삭제 완료"})


# ── 회원가입 ──────────────────────────────────────────────

@auth_bp.route("/signup", methods=["POST"])
def signup():
    """회원가입 요청 (Public)."""
    data = request.get_json(silent=True) or {}
    store_id = (data.get("store_id") or "").strip()
    password = data.get("password") or ""
    phone = (data.get("phone") or "").strip()

    if not store_id or not password or not phone:
        return jsonify({"error": "매장 코드, 비밀번호, 전화번호를 모두 입력하세요."}), 400

    if not _STORE_ID_RE.match(store_id):
        return jsonify({"error": "매장 코드는 숫자 5자리입니다."}), 400

    if len(password) < _MIN_PASSWORD_LEN:
        return jsonify({"error": f"비밀번호는 {_MIN_PASSWORD_LEN}자 이상이어야 합니다."}), 400

    if not _PHONE_RE.match(phone):
        return jsonify({"error": "전화번호 형식이 올바르지 않습니다. (예: 010-1234-5678)"}), 400

    signup_repo = SignupRequestRepository()

    if signup_repo.has_pending_for_store(store_id):
        return jsonify({"error": "이미 가입 요청이 접수되어 있습니다. 관리자 승인을 기다려 주세요."}), 409

    user_repo = DashboardUserRepository()
    if user_repo.get_by_username(store_id):
        return jsonify({"error": "이미 등록된 매장입니다."}), 409

    request_id = signup_repo.create_request(store_id, password, phone)
    logger.info(f"[AUTH] 회원가입 요청: store_id={store_id}, phone={phone}, id={request_id}")

    return jsonify({"message": "가입 요청이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다.", "id": request_id}), 201


@auth_bp.route("/signup-requests", methods=["GET"])
@admin_required
def list_signup_requests():
    """대기중 가입 요청 목록."""
    repo = SignupRequestRepository()
    pending = repo.get_pending()
    return jsonify({"requests": pending, "count": len(pending)})


@auth_bp.route("/signup-requests/count", methods=["GET"])
@admin_required
def signup_request_count():
    """대기중 가입 요청 건수."""
    repo = SignupRequestRepository()
    return jsonify({"count": repo.get_pending_count()})


@auth_bp.route("/signup-requests/<int:request_id>/approve", methods=["POST"])
@admin_required
def approve_signup(request_id):
    """가입 요청 승인 → viewer 계정 생성."""
    signup_repo = SignupRequestRepository()
    req = signup_repo.get_by_id(request_id)

    if not req:
        return jsonify({"error": "요청을 찾을 수 없습니다."}), 404
    if req["status"] != "pending":
        return jsonify({"error": "이미 처리된 요청입니다."}), 409

    user_repo = DashboardUserRepository()
    if user_repo.get_by_username(req["store_id"]):
        return jsonify({"error": "이미 등록된 매장입니다."}), 409

    user_id = user_repo.create_user_with_hash(
        username=req["store_id"],
        password_hash=req["password_hash"],
        store_id=req["store_id"],
        role="viewer",
    )
    # phone 정보 복사
    if req.get("phone"):
        user_repo.update_user(user_id, phone=req["phone"])
    signup_repo.approve(request_id, session["user_id"])

    logger.info(f"[AUTH] 가입 승인: store_id={req['store_id']}, user_id={user_id} by {session['username']}")
    return jsonify({"message": "승인 완료", "user_id": user_id})


@auth_bp.route("/signup-requests/<int:request_id>/reject", methods=["POST"])
@admin_required
def reject_signup(request_id):
    """가입 요청 거절."""
    data = request.get_json(silent=True) or {}
    reason = data.get("reason")

    signup_repo = SignupRequestRepository()
    req = signup_repo.get_by_id(request_id)

    if not req:
        return jsonify({"error": "요청을 찾을 수 없습니다."}), 404
    if req["status"] != "pending":
        return jsonify({"error": "이미 처리된 요청입니다."}), 409

    signup_repo.reject(request_id, session["user_id"], reason)

    logger.info(f"[AUTH] 가입 거절: store_id={req['store_id']} by {session['username']}")
    return jsonify({"message": "거절 완료"})
