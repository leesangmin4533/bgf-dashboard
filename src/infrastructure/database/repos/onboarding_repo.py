"""온보딩 Repository — 초대 코드 + 온보딩 상태 관리 (common.db)"""

import uuid
import sqlite3
from datetime import datetime

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OnboardingRepository(BaseRepository):
    """온보딩 CRUD (common.db)"""

    db_type = "common"

    # ── 초대 코드 ──────────────────────────────

    def create_invite_code(self, store_id=None, created_by=None, expires_at=None):
        """초대 코드 생성. UUID4 앞 16자리 반환."""
        code = uuid.uuid4().hex[:16]
        now = self._now()
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO invite_codes
                   (code, store_id, created_by, is_used, expires_at, created_at)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (code, store_id, created_by, expires_at, now),
            )
            conn.commit()
            return code
        finally:
            conn.close()

    def validate_invite_code(self, code):
        """코드 유효성 검증. 유효하면 코드 정보 dict, 아니면 None."""
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM invite_codes WHERE code = ? AND is_used = 0",
                (code,),
            ).fetchone()
            if not row:
                return None
            info = dict(row)
            # 만료 체크
            if info.get("expires_at"):
                if datetime.fromisoformat(info["expires_at"]) < datetime.now():
                    return None
            return info
        finally:
            conn.close()

    def use_invite_code(self, code, user_id):
        """코드 사용 처리. is_used=1, used_by, used_at 설정."""
        now = self._now()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """UPDATE invite_codes
                   SET is_used = 1, used_by = ?, used_at = ?
                   WHERE code = ? AND is_used = 0""",
                (user_id, now, code),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ── 온보딩 상태 ──────────────────────────────

    def get_onboarding_status(self, user_id):
        """현재 온보딩 상태 조회."""
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT onboarding_step, store_code, store_name,
                          active_categories, kakao_connected, bgf_id
                   FROM dashboard_users WHERE id = ?""",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            step = d.get("onboarding_step") or 0
            return {
                "step": step,
                "store_code": d.get("store_code"),
                "store_name": d.get("store_name"),
                "active_categories": d.get("active_categories") or "001,002,003,004,005,012",
                "kakao_connected": bool(d.get("kakao_connected")),
                "bgf_connected": bool(d.get("bgf_id")),
                "completed": step >= 5,
            }
        finally:
            conn.close()

    def update_onboarding_step(self, user_id, step, **kwargs):
        """onboarding_step 업데이트 + 추가 필드 동시 갱신. 단계 역행 방지."""
        conn = self._get_conn()
        try:
            # 현재 단계 조회
            row = conn.execute(
                "SELECT onboarding_step FROM dashboard_users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                return
            current = row[0] or 0
            if step < current:
                return  # 역행 방지

            sets = ["onboarding_step = ?", "updated_at = ?"]
            params = [step, self._now()]

            for key, val in kwargs.items():
                sets.append("{} = ?".format(key))
                params.append(val)

            params.append(user_id)
            conn.execute(
                "UPDATE dashboard_users SET {} WHERE id = ?".format(", ".join(sets)),
                params,
            )
            conn.commit()
        finally:
            conn.close()

    # ── STEP 2: 매장 ──────────────────────────────

    def save_store_info(self, user_id, store_code, store_name):
        """매장 정보 저장 + onboarding_step=2."""
        self.update_onboarding_step(
            user_id, 2,
            store_code=store_code,
            store_name=store_name,
            store_id=store_code,
        )

    # ── STEP 3: BGF 계정 ──────────────────────────────

    def save_bgf_credentials(self, user_id, bgf_id, bgf_password_enc):
        """암호화된 BGF 계정 저장 + onboarding_step=3."""
        self.update_onboarding_step(
            user_id, 3,
            bgf_id=bgf_id,
            bgf_password_enc=bgf_password_enc,
        )

    # ── STEP 4: 카테고리 ──────────────────────────────

    def save_categories(self, user_id, category_codes):
        """쉼표 구분 문자열로 저장 + onboarding_step=4."""
        cats = ",".join(category_codes) if isinstance(category_codes, list) else category_codes
        self.update_onboarding_step(
            user_id, 4,
            active_categories=cats,
        )

    # ── STEP 5: 완료 ──────────────────────────────

    def complete_onboarding(self, user_id, kakao_connected=False):
        """onboarding_step=5, kakao_connected 업데이트."""
        self.update_onboarding_step(
            user_id, 5,
            kakao_connected=1 if kakao_connected else 0,
        )

    def complete_onboarding_all(self, user_id, store_code, store_name,
                                bgf_id, bgf_password_enc, active_categories,
                                kakao_connected=False):
        """온보딩 완료 — 모든 데이터를 한번에 DB 저장. 역행 방지 우회."""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE dashboard_users
                   SET onboarding_step = 5,
                       store_code = ?,
                       store_name = ?,
                       store_id = ?,
                       bgf_id = ?,
                       bgf_password_enc = ?,
                       active_categories = ?,
                       kakao_connected = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (store_code, store_name, store_code,
                 bgf_id, bgf_password_enc, active_categories,
                 1 if kakao_connected else 0, self._now(), user_id),
            )
            conn.commit()
            logger.info("[ONBOARDING] DB 일괄 저장 완료: user_id=%d", user_id)
        finally:
            conn.close()

    # ── 초기화 ──────────────────────────────

    def reset_onboarding(self, user_id):
        """온보딩 초기화 — onboarding_step=0, 입력값 리셋. 역행 방지 우회."""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE dashboard_users
                   SET onboarding_step = 0,
                       store_code = '',
                       store_name = '',
                       store_id = '',
                       bgf_id = NULL,
                       bgf_password_enc = NULL,
                       active_categories = '001,002,003,004,005,012',
                       kakao_connected = 0,
                       updated_at = ?
                   WHERE id = ?""",
                (self._now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ── 분석 이벤트 ──────────────────────────────

    def log_event(self, user_id, step, action, error_code=None, duration_sec=None):
        """onboarding_events에 이벤트 기록."""
        now = self._now()
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO onboarding_events
                   (user_id, step, action, error_code, duration_sec, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, step, action, error_code, duration_sec, now),
            )
            conn.commit()
        finally:
            conn.close()
