"""
카카오톡 알림 모듈
- 나에게 보내기 API 사용
- OAuth 토큰 관리
"""

import json
import os
import sys
import time
import threading
import requests
import webbrowser
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 설정
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
TOKEN_FILE = CONFIG_DIR / "kakao_token.json"

# API 엔드포인트
KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MESSAGE_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


class KakaoNotifier:
    """카카오톡 알림 전송기"""

    def __init__(self, rest_api_key: str, client_secret: str = "",
                 redirect_uri: str = "http://localhost:9999/callback") -> None:
        """카카오톡 알림 전송기 초기화

        Args:
            rest_api_key: 카카오 REST API 키
            client_secret: 클라이언트 시크릿 (활성화된 경우 필수)
            redirect_uri: OAuth 콜백 URI
        """
        self.rest_api_key = rest_api_key
        self.client_secret = client_secret or os.environ.get("KAKAO_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

        # 설정 디렉토리 생성
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        # 저장된 토큰 로드
        self._load_token()

    def _load_token(self) -> None:
        """저장된 토큰 로드"""
        if TOKEN_FILE.exists():
            try:
                with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.access_token = data.get("access_token")
                    self.refresh_token = data.get("refresh_token")
                    logger.info("Token loaded from file")
            except Exception as e:
                logger.error(f"Failed to load token: {e}")

        if not self.access_token:
            logger.warning("유효한 access_token이 없습니다. --auth로 인증하세요.")

    def _save_token(self) -> None:
        """토큰 저장 (파일 권한 제한)"""
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "updated_at": datetime.now().isoformat()
                }, f, indent=2)
            # 토큰 파일 접근 권한 제한 (소유자만 읽기/쓰기)
            try:
                import stat
                TOKEN_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass  # Windows에서는 chmod 제한적
            logger.info("Token saved")
        except Exception as e:
            logger.error(f"Failed to save token: {e}")

    def get_auth_url(self) -> str:
        """인증 URL 생성

        Returns:
            카카오 OAuth 인증 페이지 URL
        """
        return (
            f"{KAKAO_AUTH_URL}"
            f"?client_id={self.rest_api_key}"
            f"&redirect_uri={self.redirect_uri}"
            f"&response_type=code"
            f"&scope=talk_message"
        )

    def authorize(self) -> bool:
        """
        OAuth 인증 수행 (브라우저 열림)

        Returns:
            인증 성공 여부
        """
        auth_code = None

        # 콜백 서버 핸들러
        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                nonlocal auth_code
                parsed = urlparse(self.path)
                if parsed.path == "/callback":
                    params = parse_qs(parsed.query)
                    if "code" in params:
                        auth_code = params["code"][0]
                        self.send_response(200)
                        self.send_header("Content-type", "text/html; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(
                            "<html><body><h1>인증 성공!</h1>"
                            "<p>이 창을 닫아도 됩니다.</p></body></html>".encode("utf-8")
                        )
                    else:
                        self.send_response(400)
                        self.end_headers()
                        self.wfile.write(b"Authorization failed")

            def log_message(self, format: str, *args: Any) -> None:
                pass  # 로그 출력 억제

        # 브라우저로 인증 페이지 열기
        auth_url = self.get_auth_url()
        logger.info("Opening browser for authorization...")
        logger.info(f"If browser doesn't open, visit: {auth_url}")
        webbrowser.open(auth_url)

        # 콜백 서버 시작
        server = HTTPServer(("localhost", 9999), CallbackHandler)
        server.timeout = 120  # 2분 타임아웃

        logger.info("Waiting for authorization callback...")
        server.handle_request()
        server.server_close()

        if auth_code:
            logger.info("Authorization code received")
            return self._get_token(auth_code)
        else:
            logger.error("Authorization failed - no code received")
            return False

    def _get_token(self, auth_code: str) -> bool:
        """인증 코드로 토큰 발급"""
        try:
            data = {
                "grant_type": "authorization_code",
                "client_id": self.rest_api_key,
                "redirect_uri": self.redirect_uri,
                "code": auth_code,
            }
            if self.client_secret:
                data["client_secret"] = self.client_secret

            response = requests.post(
                KAKAO_TOKEN_URL,
                data=data,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self._save_token()
                logger.info("Token obtained successfully")
                return True
            else:
                logger.error(f"Token request failed: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Token request error: {e}")
            return False

    def refresh_access_token(self) -> bool:
        """토큰 갱신

        Returns:
            갱신 성공 여부
        """
        if not self.refresh_token:
            logger.warning("No refresh token available")
            return False

        try:
            data = {
                "grant_type": "refresh_token",
                "client_id": self.rest_api_key,
                "refresh_token": self.refresh_token,
            }
            if self.client_secret:
                data["client_secret"] = self.client_secret

            response = requests.post(
                KAKAO_TOKEN_URL,
                data=data,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                # refresh_token은 만료 임박시에만 새로 발급됨
                if "refresh_token" in data:
                    self.refresh_token = data["refresh_token"]
                self._save_token()
                logger.info("Token refreshed successfully")
                return True
            else:
                logger.error(f"Token refresh failed: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return False

    def selenium_auto_authorize(self) -> bool:
        """Selenium 헤드리스 브라우저를 사용한 자동 OAuth 재인증

        refresh_token이 완전 만료(~60일 미사용)된 경우의 최후 수단.
        환경변수 KAKAO_ID, KAKAO_PW 필요.

        Returns:
            인증 성공 여부
        """
        kakao_id = os.environ.get("KAKAO_ID", "")
        kakao_pw = os.environ.get("KAKAO_PW", "")

        if not kakao_id or not kakao_pw:
            logger.warning("KAKAO_ID/KAKAO_PW 환경변수 미설정. Selenium 자동 인증 불가.")
            return False

        if not self.rest_api_key:
            logger.warning("REST API 키 미설정. Selenium 자동 인증 불가.")
            return False

        auth_code = None
        server = None
        driver = None

        # 콜백 서버 핸들러 (인증 코드 수신용)
        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(handler_self) -> None:
                nonlocal auth_code
                parsed = urlparse(handler_self.path)
                if parsed.path == "/callback":
                    params = parse_qs(parsed.query)
                    if "code" in params:
                        auth_code = params["code"][0]
                        handler_self.send_response(200)
                        handler_self.send_header("Content-type", "text/html")
                        handler_self.end_headers()
                        handler_self.wfile.write(b"OK")
                    else:
                        handler_self.send_response(400)
                        handler_self.end_headers()

            def log_message(handler_self, format: str, *args: Any) -> None:
                logger.debug(format % args if args else format)

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from webdriver_manager.chrome import ChromeDriverManager

            # 1. 콜백 서버 시작 (별도 스레드)
            server = HTTPServer(("localhost", 9999), CallbackHandler)
            server.timeout = 60
            server_thread = threading.Thread(target=server.handle_request)
            server_thread.daemon = True
            server_thread.start()

            # 2. 헤드리스 크롬 설정
            chrome_options = Options()
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option("useAutomationExtension", False)

            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.implicitly_wait(10)
            driver.set_page_load_timeout(30)

            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            })

            logger.info("Selenium 카카오 자동 인증 시작...")

            # 3. 카카오 인증 페이지 이동
            auth_url = self.get_auth_url()
            driver.get(auth_url)

            wait = WebDriverWait(driver, 15)

            # 4. 로그인 폼 입력
            login_input = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='loginId']"))
            )
            login_input.clear()
            login_input.send_keys(kakao_id)

            pw_input = driver.find_element(By.CSS_SELECTOR, "input[name='password']")
            pw_input.clear()
            pw_input.send_keys(kakao_pw)

            # 로그인 버튼 클릭
            login_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            login_btn.click()
            logger.info("카카오 로그인 폼 제출")

            # 5. 로그인 처리 대기
            time.sleep(3)

            # 로그인 실패 감지 (에러 메시지 확인)
            try:
                error_elem = driver.find_element(By.CSS_SELECTOR, ".txt_error, .error_message, #error-message")
                error_text = error_elem.text.strip()
                if error_text:
                    logger.error(f"카카오 로그인 실패: {error_text}")
                    return False
            except Exception:
                pass  # 에러 요소 없으면 로그인 성공

            # 6. 동의 페이지 처리 (있을 경우)
            try:
                agree_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR,
                        "button.btn_agree, button[type='submit'].agree, "
                        "#acceptButton, button.submit"))
                )
                agree_btn.click()
                logger.info("카카오 동의 페이지 승인")
            except Exception:
                pass  # 동의 페이지 없으면 무시 (이미 동의 완료)

            # 7. 콜백 서버 스레드 완료 대기 (인증 코드 수신)
            server_thread.join(timeout=30)

            if auth_code:
                logger.info("Selenium 자동 인증 코드 획득 성공")
                return self._get_token(auth_code)
            else:
                logger.error(f"인증 코드 수신 실패. 현재 URL: {driver.current_url}")
                return False

        except ImportError:
            logger.error("Selenium 미설치. pip install selenium webdriver-manager")
            return False
        except Exception as e:
            logger.error(f"Selenium 자동 인증 실패: {e}")
            return False
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception as e:
                    logger.debug(f"Selenium 드라이버 종료 중: {e}")
            if server:
                try:
                    server.server_close()
                except Exception as e:
                    logger.debug(f"콜백 서버 종료 중: {e}")

    def ensure_valid_token(self) -> bool:
        """만료된 토큰 복구 (refresh → Selenium 재인증 순서)

        send_message()에서 401 수신 시 호출.
        1단계: refresh_token으로 access_token 갱신
        2단계: Selenium 자동 재인증 (refresh_token도 만료 시)

        Returns:
            유효한 토큰 확보 여부
        """
        # 1단계: refresh_token으로 갱신
        if self.refresh_token:
            if self.refresh_access_token():
                return True
            logger.warning("Refresh token 갱신 실패")

        # 2단계: Selenium 자동 재인증 (최후 수단)
        logger.warning("Selenium 자동 재인증 시도...")
        return self.selenium_auto_authorize()

    def send_message(self, text: str, link_title: Optional[str] = None, link_url: Optional[str] = None, _retry: bool = True) -> bool:
        """
        카카오톡 메시지 전송 (나에게 보내기)

        Args:
            text: 메시지 내용
            link_title: 링크 버튼 제목 (선택)
            link_url: 링크 URL (선택)
            _retry: 401 시 토큰 갱신 후 재시도 여부 (내부용, 무한 재귀 방지)

        Returns:
            전송 성공 여부
        """
        if not self.access_token:
            logger.warning("No access token. Run authorize() first.")
            return False

        # 메시지 템플릿 구성
        # link는 필수 필드이며, 도메인이 카카오 앱의 플랫폼 설정에 등록되어야 함
        default_url = "http://localhost"
        template = {
            "object_type": "text",
            "text": text,
            "link": {
                "web_url": link_url or default_url,
                "mobile_web_url": link_url or default_url,
            },
            "button_title": link_title or "확인",
        }

        try:
            response = requests.post(
                KAKAO_MESSAGE_URL,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "template_object": json.dumps(template)
                },
                timeout=10
            )

            if response.status_code == 200:
                logger.info("Message sent successfully")
                return True
            elif response.status_code == 401:
                # 토큰 만료 - 갱신 → Selenium 재인증 순서 시도
                logger.warning("Token expired, recovering...")
                if _retry and self.ensure_valid_token():
                    return self.send_message(text, link_title, link_url, _retry=False)
                else:
                    logger.error("All token recovery methods failed.")
                    return False
            else:
                logger.error(f"Message send failed: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Message send error: {e}")
            return False

    def send_report(self, report: Dict[str, Any]) -> bool:
        """
        리포트를 카카오톡으로 전송

        Args:
            report: DailyReport.generate() 결과

        Returns:
            전송 성공 여부
        """
        s = report.get("summary", {})
        w = report.get("weather", {})
        c = report.get("calendar", {})
        comp = report.get("comparison", {})

        # 메시지 구성
        lines = [
            f"[BGF 일일 리포트] {report['date']}",
            "",
            f"[요약]",
            f"- 상품수: {s.get('total_items', 0):,}개",
            f"- 판매량: {s.get('total_sales', 0):,}",
            f"- 발주량: {s.get('total_orders', 0):,}",
            f"- 재고량: {s.get('total_stock', 0):,}",
        ]

        # 날씨/캘린더 정보
        if w or c:
            lines.append("")
            lines.append("[외부요인]")
            if w.get("temperature"):
                lines.append(f"- 온도: {w['temperature']}도")
            if c.get("day_of_week"):
                day_type = ""
                if c.get("is_holiday"):
                    day_type = f" (공휴일: {c.get('holiday_name', '')})"
                elif c.get("is_weekend"):
                    day_type = " (주말)"
                lines.append(f"- 요일: {c['day_of_week']}요일{day_type}")

        # 전일 대비
        vs_prev = comp.get("vs_prev_day", {})
        if vs_prev.get("sales_change") is not None:
            lines.append("")
            sign = "+" if vs_prev["sales_change"] >= 0 else ""
            lines.append(f"[전일대비] {sign}{vs_prev['sales_change']}%")

        # 상위 카테고리
        cats = report.get("categories", [])[:5]
        if cats:
            lines.append("")
            lines.append("[TOP 카테고리]")
            for i, cat in enumerate(cats, 1):
                lines.append(f"{i}. {cat['mid_nm']} ({cat['total_sales']})")

        text = "\n".join(lines)
        return self.send_message(text)


def setup_kakao(rest_api_key: str) -> KakaoNotifier:
    """카카오 알림 설정 및 인증

    Args:
        rest_api_key: 카카오 REST API 키

    Returns:
        인증 완료된 KakaoNotifier 인스턴스
    """
    notifier = KakaoNotifier(rest_api_key)

    if not notifier.access_token:
        logger.info("No token found. Starting authorization...")
        if not notifier.authorize():
            raise Exception("Kakao authorization failed")

    return notifier


# 기본 REST API 키 / 클라이언트 시크릿 (환경변수에서 로드)
DEFAULT_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "")
DEFAULT_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Kakao Notifier")
    parser.add_argument("--auth", action="store_true", help="Run authorization (browser)")
    parser.add_argument("--selenium-auth", action="store_true", help="Run auto authorization (headless Selenium)")
    parser.add_argument("--refresh", action="store_true", help="Refresh access token")
    parser.add_argument("--test", action="store_true", help="Send test message")
    parser.add_argument("--key", type=str, default=DEFAULT_REST_API_KEY, help="REST API Key")

    args = parser.parse_args()

    if not args.key:
        print("[WARN] KAKAO_REST_API_KEY 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    notifier = KakaoNotifier(args.key)

    if args.auth:
        notifier.authorize()
    elif args.selenium_auth:
        success = notifier.selenium_auto_authorize()
        print(f"Selenium auto-auth: {'성공' if success else '실패'}")
    elif args.refresh:
        success = notifier.refresh_access_token()
        print(f"Token refresh: {'성공' if success else '실패'}")
    elif args.test:
        if not notifier.access_token:
            print("No token. Run with --auth first.")
        else:
            notifier.send_message(
                f"[BGF 테스트] 카카오톡 알림 테스트\n\n"
                f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"상태: 정상 작동"
            )
    else:
        parser.print_help()
