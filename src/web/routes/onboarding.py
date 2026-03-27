"""온보딩 API Blueprint

4단계 온보딩 플로우 — **Step 4까지 DB 저장 없음**:
  1. 회원가입 (초대 코드)     → 세션 임시 저장
  2. BGF 계정 연결 (비동기)   → 세션 임시 저장 (store_code = bgf_id 자동 설정)
  3. 카테고리 선택            → 세션 임시 저장
  4. 완료                    → 계정 생성 + 모든 데이터 DB 일괄 저장
"""

import re
import time
import uuid
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request, session, render_template
from werkzeug.security import generate_password_hash

from src.infrastructure.database.repos.onboarding_repo import OnboardingRepository
from src.infrastructure.database.repos.user_repo import DashboardUserRepository
from src.infrastructure.database.repos.store_repo import StoreRepository
from src.application.services.store_service import StoreService
from src.utils.crypto import decrypt_password
from src.utils.logger import get_logger

logger = get_logger(__name__)

onboarding_bp = Blueprint("onboarding", __name__)

# ── 상수 ──────────────────────────────────────────────────

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
_STORE_CODE_RE = re.compile(r"^\d{5}$")
_BGF_ID_RE = re.compile(r"^[a-zA-Z0-9]{4,20}$")
_MIN_PASSWORD_LEN = 4
BGF_TEST_TIMEOUT = 90  # 초
VALID_FOOD_CATEGORIES = {"001", "002", "003", "004", "005", "012"}

# ── BGF 테스트 태스크 저장소 ───────────────────────────────

_bgf_tasks = {}  # {task_id: {"status", "error", "created_at", "bgf_id", "bgf_password_enc"}}
_bgf_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=2)

# BGF 테스트 Rate Limit (IP별 3회/300초)
_bgf_test_attempts = defaultdict(list)
_bgf_test_lock = threading.Lock()
_BGF_TEST_MAX = 3
_BGF_TEST_WINDOW = 300


def _cleanup_old_tasks():
    """5분 이상 된 태스크 정리."""
    cutoff = time.time() - 300
    with _bgf_lock:
        expired = [k for k, v in _bgf_tasks.items() if v["created_at"] < cutoff]
        for k in expired:
            del _bgf_tasks[k]


def _check_bgf_rate(ip):
    """BGF 테스트 Rate Limit. 초과 시 남은 시간 반환, 정상이면 None."""
    now = time.time()
    cutoff = now - _BGF_TEST_WINDOW
    with _bgf_test_lock:
        _bgf_test_attempts[ip] = [t for t in _bgf_test_attempts[ip] if t > cutoff]
        if len(_bgf_test_attempts[ip]) >= _BGF_TEST_MAX:
            oldest = min(_bgf_test_attempts[ip])
            return int(oldest + _BGF_TEST_WINDOW - now)
        _bgf_test_attempts[ip].append(now)
    return None


def _require_onboarding():
    """온보딩 진행 중(세션) 또는 완료(DB) 사용자 인증 체크.
    반환: (True, None) 또는 (False, error_response).
    """
    # 이미 DB에 계정이 있는 사용자 (온보딩 완료 또는 기존 사용자)
    if session.get("user_id"):
        return True, None
    # 온보딩 진행 중 (Step 1~4, 아직 DB 계정 없음)
    if session.get("onboarding_pending"):
        return True, None
    return False, (jsonify({"error": "로그인이 필요합니다"}), 401)


# ── 온보딩 메인 페이지 ──────────────────────────────────────

@onboarding_bp.route("/onboarding")
def onboarding_page():
    """온보딩 SPA 렌더링."""
    return render_template("onboarding.html")


# ── API: 현재 단계 조회 ──────────────────────────────────────

@onboarding_bp.route("/api/onboarding/status")
def onboarding_status():
    """현재 온보딩 단계 조회 (세션 데이터 기반)."""
    ok, err = _require_onboarding()
    if not ok:
        return err

    step = session.get("onboarding_step", 0)

    result = {
        "step": step,
        "store_code": session.get("onb_store_code", ""),
        "store_name": session.get("onb_store_name", ""),
        "active_categories": session.get("onb_categories_str", "001,002,003,004,005,012"),
        "kakao_connected": False,
        "bgf_connected": bool(session.get("onb_bgf_id")),
        "completed": step >= 5,
    }

    # DB에 계정이 있으면 DB 데이터도 병합 (온보딩 완료 사용자)
    user_id = session.get("user_id")
    if user_id:
        repo = OnboardingRepository()
        db_status = repo.get_onboarding_status(user_id)
        if db_status:
            db_step = db_status.get("step", 0)
            if db_step > step:
                step = db_step
                result["step"] = step
            result["store_code"] = result["store_code"] or db_status.get("store_code", "")
            result["store_name"] = result["store_name"] or db_status.get("store_name", "")
            result["active_categories"] = result["active_categories"] or db_status.get("active_categories", "001,002,003,004,005,012")
            result["kakao_connected"] = bool(db_status.get("kakao_connected"))
            result["bgf_connected"] = result["bgf_connected"] or bool(db_status.get("bgf_connected"))
            result["completed"] = step >= 4

    return jsonify(result)


# ── API: STEP 1 — 회원가입 (세션만, DB 저장 없음) ────────────

@onboarding_bp.route("/api/onboarding/signup", methods=["POST"])
def signup():
    """초대 코드 기반 회원가입 — 세션에만 저장, DB 저장은 Step 4에서."""
    data = request.get_json(silent=True) or {}

    invite_code = (data.get("invite_code") or "").strip()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    full_name = (data.get("full_name") or "").strip()
    store_location = (data.get("store_location") or "").strip()

    # 입력 검증
    if not username or not _USERNAME_RE.match(username):
        return jsonify({"success": False, "error": "invalid_username"}), 400
    if len(password) < _MIN_PASSWORD_LEN:
        return jsonify({"success": False, "error": "password_too_short"}), 400

    onb_repo = OnboardingRepository()
    user_repo = DashboardUserRepository()

    # 초대 코드 검증 (선택사항 — 없으면 스킵)
    code_info = None
    if invite_code:
        code_info = onb_repo.validate_invite_code(invite_code)
        if not code_info:
            return jsonify({"success": False, "error": "invalid_invite_code"}), 400

    # username 중복 검사
    existing = user_repo.get_by_username(username)
    if existing:
        return jsonify({"success": False, "error": "username_taken"}), 409

    # 세션에 임시 저장 (DB 저장 안 함)
    session.permanent = True
    session["onboarding_pending"] = True
    session["onb_username"] = username
    session["onb_password_hash"] = generate_password_hash(password)
    session["onb_full_name"] = full_name
    session["onb_store_location"] = store_location
    session["onb_invite_code"] = invite_code
    session["onb_invite_store_id"] = (code_info.get("store_id") or "") if code_info else ""
    session["onboarding_step"] = 1

    logger.info("[ONBOARDING] 가입 정보 세션 저장: %s", username)
    return jsonify({"success": True, "step": 1})


# ── API: 온보딩 초기화 ──────────────────────────────────────

@onboarding_bp.route("/api/onboarding/reset", methods=["POST"])
def reset_onboarding():
    """온보딩 초기화 — 세션 임시 데이터 전체 삭제."""
    # 세션 온보딩 임시 데이터 전체 삭제
    for key in ["onboarding_pending", "onb_username", "onb_password_hash",
                 "onb_full_name", "onb_invite_code", "onb_invite_store_id",
                 "onb_store_code", "onb_store_name", "onb_store_location",
                 "onb_bgf_id", "onb_bgf_password_enc", "onb_categories_str"]:
        session.pop(key, None)

    session["onboarding_step"] = 1  # Step 1(가입)로 리셋

    # DB에 계정이 있으면 DB도 초기화
    user_id = session.get("user_id")
    if user_id:
        onb_repo = OnboardingRepository()
        onb_repo.reset_onboarding(user_id)
        onb_repo.log_event(user_id, 0, "reset")
        session.pop("store_id", None)

    logger.info("[ONBOARDING] 초기화")
    return jsonify({"success": True})


# ── API: STEP 2 — BGF 계정 연결 (비동기, 세션 저장) ──────────
# BGF ID = 매장코드이므로 매장 등록 단계 불필요

@onboarding_bp.route("/api/onboarding/bgf/test", methods=["POST"])
def bgf_test():
    """BGF 로그인 테스트 시작 (비동기). 성공 시 세션에 자격증명 임시 저장."""
    ok, err = _require_onboarding()
    if not ok:
        return err

    # Rate Limit
    ip = request.remote_addr
    retry_after = _check_bgf_rate(ip)
    if retry_after is not None:
        return jsonify({"success": False, "error": "rate_limited", "retry_after": retry_after}), 429

    data = request.get_json(silent=True) or {}
    bgf_id = (data.get("bgf_id") or "").strip()
    bgf_password = data.get("bgf_password") or ""

    if not _BGF_ID_RE.match(bgf_id):
        return jsonify({"success": False, "error": "invalid_bgf_id"}), 400
    if not bgf_password:
        return jsonify({"success": False, "error": "bgf_password_required"}), 400

    # 태스크 생성
    _cleanup_old_tasks()
    task_id = uuid.uuid4().hex[:12]
    with _bgf_lock:
        _bgf_tasks[task_id] = {
            "status": "running", "error": None, "created_at": time.time(),
            "bgf_id": None, "bgf_password_enc": None,
        }

    # 백그라운드 실행
    _executor.submit(_bgf_login_task, task_id, bgf_id, bgf_password)

    logger.info("[ONBOARDING] BGF 테스트 시작: task=%s", task_id)
    return jsonify({"task_id": task_id})


def _bgf_login_task(task_id, bgf_id, bgf_password):
    """ThreadPoolExecutor에서 실행되는 BGF 로그인 테스트.
    성공 시 암호화된 자격증명을 _bgf_tasks에 저장 (DB 저장 안 함).
    """
    analyzer = None
    try:
        from src.sales_analyzer import SalesAnalyzer
        from src.utils.crypto import encrypt_password

        # 온보딩용: credential 로딩 우회 후 사용자 입력값 직접 설정
        try:
            analyzer = SalesAnalyzer(store_id="temp_onboarding")
        except ValueError:
            # temp_onboarding 매장 설정 없음 → 최소 초기화
            analyzer = SalesAnalyzer.__new__(SalesAnalyzer)
            analyzer.store_id = "temp_onboarding"
            analyzer.driver = None
            analyzer.sales_data = []
            analyzer.mid_categories = []
            analyzer.weather_data = None
            analyzer._direct_sales_fetcher = None

        # 사용자가 입력한 BGF 자격증명으로 덮어쓰기
        analyzer.USER_ID = bgf_id
        analyzer.PASSWORD = bgf_password

        analyzer.setup_driver()
        analyzer.connect()
        login_ok = analyzer.do_login()  # self.USER_ID/PASSWORD 사용

        if login_ok:
            # 암호화하여 태스크에 저장 (DB 저장은 Step 5에서)
            enc = encrypt_password(bgf_password)
            with _bgf_lock:
                _bgf_tasks[task_id]["status"] = "success"
                _bgf_tasks[task_id]["bgf_id"] = bgf_id
                _bgf_tasks[task_id]["bgf_password_enc"] = enc
            logger.info("[ONBOARDING] BGF 연결 성공")
        else:
            with _bgf_lock:
                _bgf_tasks[task_id]["status"] = "failed"
                _bgf_tasks[task_id]["error"] = "invalid_credentials"
            logger.warning("[ONBOARDING] BGF 연결 실패 (invalid_credentials)")

    except Exception as e:
        with _bgf_lock:
            _bgf_tasks[task_id]["status"] = "failed"
            _bgf_tasks[task_id]["error"] = "server_error"
        logger.warning("[ONBOARDING] BGF 테스트 에러: %s", e)

    finally:
        try:
            if analyzer and analyzer.driver:
                analyzer.driver.quit()
        except Exception:
            pass


@onboarding_bp.route("/api/onboarding/bgf/status/<task_id>")
def bgf_status(task_id):
    """BGF 테스트 폴링. 성공 시 자격증명을 세션에 저장."""
    ok, err = _require_onboarding()
    if not ok:
        return err

    with _bgf_lock:
        task = _bgf_tasks.get(task_id)

    if not task:
        return jsonify({"status": "not_found"}), 404

    # 타임아웃 체크
    elapsed = time.time() - task["created_at"]
    if task["status"] == "running" and elapsed > BGF_TEST_TIMEOUT:
        with _bgf_lock:
            _bgf_tasks[task_id]["status"] = "failed"
            _bgf_tasks[task_id]["error"] = "timeout"
        return jsonify({"status": "failed", "error": "timeout"})

    if task["status"] == "success":
        # 태스크에서 자격증명 읽어 세션에 저장
        bgf_id = task.get("bgf_id", "")
        session["onb_bgf_id"] = bgf_id
        session["onb_bgf_password_enc"] = task.get("bgf_password_enc", "")

        # BGF ID = 매장코드 → 자동 설정
        session["onb_store_code"] = bgf_id
        store_repo = StoreRepository()
        store = store_repo.get_store(bgf_id)
        session["onb_store_name"] = store["store_name"] if store else bgf_id

        session["onboarding_step"] = 2
        return jsonify({"status": "success", "step": 2})
    elif task["status"] == "failed":
        return jsonify({"status": "failed", "error": task.get("error", "unknown")})
    else:
        return jsonify({"status": "running"})


# ── API: STEP 3 — 카테고리 선택 (세션 저장) ──────────────────

@onboarding_bp.route("/api/onboarding/categories", methods=["POST"])
def save_categories():
    """카테고리를 세션에 임시 저장 (DB 저장은 Step 4에서)."""
    ok, err = _require_onboarding()
    if not ok:
        return err

    data = request.get_json(silent=True) or {}
    categories = data.get("categories", [])

    if not isinstance(categories, list) or len(categories) == 0:
        return jsonify({"success": False, "error": "categories_empty"}), 400

    # 유효한 카테고리만 필터링
    valid = [c for c in categories if c in VALID_FOOD_CATEGORIES]
    if not valid:
        return jsonify({"success": False, "error": "no_valid_categories"}), 400

    # 세션에 임시 저장
    session["onb_categories_str"] = ",".join(valid)
    session["onboarding_step"] = 3

    logger.info("[ONBOARDING] 카테고리 선택 (세션): %s", ",".join(valid))
    return jsonify({"success": True, "step": 3})


# ── API: STEP 4 — 완료 (계정 생성 + 모든 데이터 DB 일괄 저장) ──

@onboarding_bp.route("/api/onboarding/complete", methods=["POST"])
def complete():
    """온보딩 완료 — 계정 생성 + 세션의 모든 데이터를 DB에 일괄 저장."""
    ok, err = _require_onboarding()
    if not ok:
        return err

    data = request.get_json(silent=True) or {}
    kakao_connected = bool(data.get("kakao_connected", False))

    # 세션에서 모든 온보딩 데이터 수집
    username = session.get("onb_username", "")
    password_hash = session.get("onb_password_hash", "")
    full_name = session.get("onb_full_name", "")
    invite_code = session.get("onb_invite_code", "")
    store_code = session.get("onb_store_code", "")
    store_name = session.get("onb_store_name", "")
    bgf_id = session.get("onb_bgf_id", "")
    bgf_password_enc = session.get("onb_bgf_password_enc", "")
    store_location = session.get("onb_store_location", "")
    categories_str = session.get("onb_categories_str", "001,002,003,004,005,012")

    # BGF ID = 매장코드 자동 설정 (Step 2에서 설정되지만 안전장치)
    if not store_code and bgf_id:
        store_code = bgf_id

    # 필수 데이터 검증
    if not username or not password_hash:
        return jsonify({"success": False, "error": "signup_not_completed"}), 400
    if not bgf_id:
        return jsonify({"success": False, "error": "bgf_not_connected"}), 400
    if not store_code:
        return jsonify({"success": False, "error": "store_not_set"}), 400

    user_repo = DashboardUserRepository()
    onb_repo = OnboardingRepository()

    # username 중복 재검사 (Step 1 이후 다른 사용자가 같은 이름으로 가입했을 수 있음)
    existing = user_repo.get_by_username(username)
    if existing:
        return jsonify({"success": False, "error": "username_taken"}), 409

    # 초대 코드 재검증
    if invite_code:
        code_info = onb_repo.validate_invite_code(invite_code)
        if not code_info:
            return jsonify({"success": False, "error": "invite_code_expired"}), 400

    # ── DB 일괄 저장 ──

    # 1. 사용자 계정 생성
    user_id = user_repo.create_user_with_hash(
        username=username,
        password_hash=password_hash,
        store_id=store_code,
        role="viewer",
        full_name=full_name,
    )

    # 2. 초대 코드 사용 처리
    if invite_code:
        onb_repo.use_invite_code(invite_code, user_id)

    # 3. 온보딩 데이터 일괄 저장 (onboarding_step=5 + 모든 필드)
    onb_repo.complete_onboarding_all(
        user_id=user_id,
        store_code=store_code,
        store_name=store_name,
        bgf_id=bgf_id,
        bgf_password_enc=bgf_password_enc,
        active_categories=categories_str,
        kakao_connected=kakao_connected,
    )
    onb_repo.log_event(user_id, 4, "completed")

    # 4. 매장 자동 등록 (stores.json + common.db stores + 매장 DB 생성)
    try:
        bgf_pw = decrypt_password(bgf_password_enc) if bgf_password_enc else ""

        # 주소 → 좌표 변환 (실패해도 가입 차단 안 함)
        lat, lng = None, None
        if store_location:
            from src.application.services.store_service import get_coordinates
            lat, lng = get_coordinates(store_location)

        store_service = StoreService()
        store_service.add_store(
            store_id=store_code,
            store_name=store_name,
            location=store_location,
            lat=lat,
            lng=lng,
            bgf_user_id=bgf_id,
            bgf_password=bgf_pw,
        )
        logger.info("[ONBOARDING] 매장 자동 등록 완료: %s", store_code)

        # 과거 데이터 수집 백그라운드 시작 (6개월, 즉시 응답 반환)
        from src.application.services.historical_collection_service import collect_historical_sales
        thread = threading.Thread(
            target=collect_historical_sales,
            args=(store_code, 6),
            daemon=True,
            name=f"historical-collect-{store_code}",
        )
        thread.start()
        logger.info("[ONBOARDING] 과거 데이터 수집 백그라운드 시작: %s (6개월)", store_code)

        # 상권 분석 백그라운드 실행 (좌표가 있을 때만)
        if lat and lng:
            from src.application.services.analysis_service import run_store_analysis
            analysis_thread = threading.Thread(
                target=run_store_analysis,
                args=(store_code,),
                daemon=True,
                name=f"store-analysis-{store_code}",
            )
            analysis_thread.start()
            logger.info("[ONBOARDING] 상권 분석 백그라운드 시작: %s", store_code)

    except ValueError as e:
        # 이미 존재하는 매장 (기존 매장에 새 사용자 추가) → 정상 케이스
        logger.info("[ONBOARDING] 매장 이미 등록됨: %s (%s)", store_code, e)
    except Exception as e:
        # 매장 등록 실패해도 온보딩 자체는 성공 처리
        logger.warning("[ONBOARDING] 매장 자동 등록 실패: %s — %s", store_code, e)

    # ── 세션 전환: pending → 정식 로그인 ──
    # 임시 데이터 정리
    for key in ["onboarding_pending", "onb_username", "onb_password_hash",
                 "onb_full_name", "onb_invite_code", "onb_invite_store_id",
                 "onb_store_code", "onb_store_name", "onb_store_location",
                 "onb_bgf_id", "onb_bgf_password_enc", "onb_categories_str"]:
        session.pop(key, None)

    # 정식 로그인 세션 설정
    session["user_id"] = user_id
    session["username"] = username
    session["store_id"] = store_code
    session["role"] = "viewer"
    session["onboarding_step"] = 5  # 미들웨어 체크: step < 5 → redirect, 5이상이면 통과

    logger.info("[ONBOARDING] 완료: user_id=%d, store=%s, bgf=%s, categories=%s",
                user_id, store_code, bgf_id, categories_str)
    return jsonify({"success": True, "step": 5, "completed": True})
