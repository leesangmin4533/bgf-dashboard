"""PythonAnywhere DB 동기화 스크립트.

로컬 DB 파일을 PythonAnywhere에 업로드하고 웹앱을 리로드합니다.

사용법:
    # 직접 실행
    python scripts/sync_to_cloud.py

    # 스케줄러에서 자동 호출
    python run_scheduler.py --sync-cloud

    # 모듈로 import
    from scripts.sync_to_cloud import CloudSyncer
    syncer = CloudSyncer()
    result = syncer.sync_all()
"""

import hashlib
import json
import os
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

# 프로젝트 루트 설정
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("bgf_auto.cloud_sync")

# PythonAnywhere API base URL
PA_API_BASE = "https://www.pythonanywhere.com/api/v0/user"

# 설정 파일 경로
CONFIG_PATH = PROJECT_ROOT / "config" / "pythonanywhere.json"

# 재시도 설정
MAX_RETRIES = 3
RETRY_DELAYS = [10, 30, 60]  # 초 단위
UPLOAD_TIMEOUT = 300  # 5분 (대용량 DB 대응)


class CloudSyncError(Exception):
    """클라우드 동기화 오류."""
    pass


class CloudSyncer:
    """PythonAnywhere DB 동기화 클래스.

    로컬 DB 파일을 PythonAnywhere Files API로 업로드하고
    웹앱을 리로드합니다.
    """

    def __init__(self, config_path: Optional[Path] = None):
        """초기화.

        Args:
            config_path: 설정 파일 경로 (기본: config/pythonanywhere.json)
        """
        self._config_path = config_path or CONFIG_PATH
        self._config: Optional[Dict] = None
        self._session = None

    @property
    def config(self) -> Dict:
        """설정 로드 (lazy)."""
        if self._config is None:
            self._config = self._load_config()
        return self._config

    @property
    def is_configured(self) -> bool:
        """설정 파일이 존재하는지 확인."""
        return self._config_path.exists()

    def _load_config(self) -> Dict:
        """설정 파일 로드."""
        if not self._config_path.exists():
            raise CloudSyncError(
                f"설정 파일 없음: {self._config_path}\n"
                f"config/pythonanywhere.json 파일을 생성하세요."
            )

        with open(self._config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        # 필수 필드 검증
        required = ["username", "api_token", "domain", "remote_base"]
        missing = [k for k in required if not config.get(k)]
        if missing:
            raise CloudSyncError(f"설정 필드 누락: {missing}")

        return config

    def _get_session(self):
        """requests 세션 (lazy import + 생성)."""
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Token {self.config['api_token']}"
            })
        return self._session

    def _api_url(self, endpoint: str) -> str:
        """PythonAnywhere API URL 생성."""
        username = self.config["username"]
        return f"{PA_API_BASE}/{username}/{endpoint}"

    def delete_remote_file(self, remote_path: str) -> bool:
        """원격 파일 삭제 (공간 확보용).

        Args:
            remote_path: 원격 파일 경로 (remote_base 상대 경로)

        Returns:
            삭제 성공 여부
        """
        remote_full = f"{self.config['remote_base']}/{remote_path}"
        api_url = self._api_url(f"files/path{remote_full}")

        try:
            response = self._get_session().delete(api_url, timeout=30)
            if response.status_code in (200, 204, 404):
                # 404도 성공으로 간주 (이미 없는 파일)
                return True
            logger.warning(
                f"[CloudSync] 원격 파일 삭제 실패: {remote_path} "
                f"(HTTP {response.status_code})"
            )
            return False
        except Exception as e:
            logger.warning(f"[CloudSync] 원격 파일 삭제 오류: {remote_path} - {e}")
            return False

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        """파일 SHA256 해시 계산.

        Args:
            file_path: 파일 경로

        Returns:
            SHA256 해시 문자열 (hex)
        """
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def upload_file(self, local_path: str, remote_path: str) -> Dict[str, Any]:
        """단일 파일 업로드.

        기존 파일이 있으면 먼저 삭제하여 디스크 할당량 초과를 방지합니다.
        업로드 전 SHA256 해시를 계산하여 결과에 포함합니다.

        Args:
            local_path: 로컬 파일 경로 (프로젝트 루트 상대 경로)
            remote_path: 원격 파일 경로 (remote_base 상대 경로)

        Returns:
            {"success": True/False, "file": remote_path, "size_kb": int, "elapsed": float, "sha256": str}
        """
        local_full = PROJECT_ROOT / local_path
        if not local_full.exists():
            return {
                "success": False,
                "file": remote_path,
                "error": f"파일 없음: {local_full}",
            }

        remote_full = f"{self.config['remote_base']}/{remote_path}"
        api_url = self._api_url(f"files/path{remote_full}")
        file_size_kb = local_full.stat().st_size // 1024

        # SHA256 무결성 해시 계산
        file_hash = self.compute_sha256(local_full)

        # 기존 파일 삭제 (디스크 할당량 확보)
        self.delete_remote_file(remote_path)

        logger.info(
            f"[CloudSync] 업로드: {local_path} ({file_size_kb:,}KB) -> {remote_path} "
            f"[SHA256: {file_hash[:16]}...]"
        )

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                start_time = time.time()
                with open(local_full, "rb") as f:
                    response = self._get_session().post(
                        api_url,
                        files={"content": f},
                        timeout=UPLOAD_TIMEOUT,
                    )

                elapsed = time.time() - start_time

                if response.status_code in (200, 201):
                    logger.info(
                        f"[CloudSync] 완료: {remote_path} "
                        f"({file_size_kb:,}KB, {elapsed:.1f}초, "
                        f"SHA256: {file_hash[:16]}...)"
                    )
                    return {
                        "success": True,
                        "file": remote_path,
                        "size_kb": file_size_kb,
                        "elapsed": round(elapsed, 1),
                        "sha256": file_hash,
                    }
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(
                        f"[CloudSync] 실패 (시도 {attempt + 1}/{MAX_RETRIES}): "
                        f"{remote_path} - {last_error}"
                    )

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"[CloudSync] 에러 (시도 {attempt + 1}/{MAX_RETRIES}): "
                    f"{remote_path} - {last_error}"
                )

            # 재시도 대기
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.info(f"[CloudSync] {delay}초 후 재시도...")
                time.sleep(delay)

        return {
            "success": False,
            "file": remote_path,
            "error": last_error,
        }

    def reload_webapp(self) -> Dict[str, Any]:
        """웹앱 리로드.

        Returns:
            {"success": True/False, "domain": str}
        """
        domain = self.config["domain"]
        api_url = self._api_url(f"webapps/{domain}/reload/")

        logger.info(f"[CloudSync] 웹앱 리로드: {domain}")

        try:
            response = self._get_session().post(api_url, timeout=30)
            if response.status_code == 200:
                logger.info(f"[CloudSync] 리로드 완료: {domain}")
                return {"success": True, "domain": domain}
            else:
                error = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.error(f"[CloudSync] 리로드 실패: {error}")
                return {"success": False, "domain": domain, "error": error}
        except Exception as e:
            logger.error(f"[CloudSync] 리로드 에러: {e}")
            return {"success": False, "domain": domain, "error": str(e)}

    def sync_all(self) -> Dict[str, Any]:
        """전체 동기화: DB 파일 업로드 + 웹앱 리로드.

        Returns:
            {
                "success": True/False,
                "uploaded": [{"file": ..., "size_kb": ..., "elapsed": ...}],
                "failed": [{"file": ..., "error": ...}],
                "reload": {"success": True/False},
                "total_elapsed": float
            }
        """
        if not self.is_configured:
            logger.warning(
                "[CloudSync] 설정 파일 없음 - 동기화 건너뜀. "
                "config/pythonanywhere.json을 생성하세요."
            )
            return {"success": False, "skipped": True, "reason": "설정 파일 없음"}

        start_time = time.time()
        uploaded = []
        failed = []

        # 동기화 대상 파일 목록
        sync_files = self.config.get("sync_files", [
            {"local": "data/common.db", "remote": "data/common.db"},
            {"local": "data/stores/46513.db", "remote": "data/stores/46513.db"},
        ])

        logger.info(f"[CloudSync] 동기화 시작: {len(sync_files)}개 파일")

        # 파일 업로드
        for file_info in sync_files:
            result = self.upload_file(file_info["local"], file_info["remote"])
            if result["success"]:
                uploaded.append(result)
            else:
                failed.append(result)

        # 업로드된 파일이 있으면 웹앱 리로드
        reload_result = {"success": False, "skipped": True}
        if uploaded:
            reload_result = self.reload_webapp()

        total_elapsed = round(time.time() - start_time, 1)
        overall_success = len(failed) == 0 and (not uploaded or reload_result.get("success"))

        # 결과 요약 로그
        total_kb = sum(r.get("size_kb", 0) for r in uploaded)
        logger.info(
            f"[CloudSync] 완료: "
            f"{len(uploaded)}개 성공 ({total_kb:,}KB), "
            f"{len(failed)}개 실패, "
            f"리로드={'OK' if reload_result.get('success') else 'FAIL'}, "
            f"총 {total_elapsed}초"
        )

        return {
            "success": overall_success,
            "uploaded": uploaded,
            "failed": failed,
            "reload": reload_result,
            "total_elapsed": total_elapsed,
        }


def run_cloud_sync() -> Dict[str, Any]:
    """클라우드 동기화 실행 (외부 호출용).

    설정 파일이 없으면 경고만 남기고 skip.
    발주 플로우에 영향을 주지 않도록 모든 예외를 캐치.

    Returns:
        동기화 결과 dict
    """
    try:
        syncer = CloudSyncer()
        return syncer.sync_all()
    except CloudSyncError as e:
        logger.warning(f"[CloudSync] {e}")
        return {"success": False, "skipped": True, "reason": str(e)}
    except Exception as e:
        logger.error(f"[CloudSync] 예기치 않은 오류: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # 직접 실행 시 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    print("")
    print("  PythonAnywhere DB Sync")
    print("  ======================")
    print("")

    result = run_cloud_sync()

    if result.get("skipped"):
        print(f"  [SKIP] {result.get('reason', '설정 없음')}")
    elif result["success"]:
        print(f"  [OK] 동기화 완료 ({result['total_elapsed']}초)")
        for f in result.get("uploaded", []):
            print(f"    - {f['file']} ({f['size_kb']:,}KB, {f['elapsed']}초)")
        print(f"  [OK] 웹앱 리로드 완료")
        print(f"\n  https://{CloudSyncer().config['domain']} 에 최신 데이터 반영됨")
    else:
        print(f"  [FAIL] 동기화 실패")
        for f in result.get("failed", []):
            print(f"    - {f['file']}: {f.get('error', 'Unknown')}")
        if result.get("error"):
            print(f"    Error: {result['error']}")

    print("")
