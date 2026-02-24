"""
공통 HTML 리포트 생성기

Jinja2 기반으로 HTML 리포트를 렌더링하고 파일로 저장하는 기반 클래스.
모든 리포트 모듈은 BaseReport를 상속하여 generate()를 구현한다.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

from jinja2 import Environment, FileSystemLoader

from src.utils.logger import get_logger

logger = get_logger(__name__)


class BaseReport:
    """Jinja2 기반 HTML 리포트 공통 클래스

    서브클래스에서 REPORT_SUB_DIR, TEMPLATE_NAME을 정의하고
    generate() 메서드를 구현한다.
    """

    REPORT_SUB_DIR: str = ""     # data/reports/ 하위 폴더명 (서브클래스 정의)
    TEMPLATE_NAME: str = ""      # 사용할 Jinja2 템플릿 파일명 (서브클래스 정의)

    def __init__(self, db_path: str = None):
        self.db_path = db_path or self._default_db_path()
        self.template_dir = Path(__file__).parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=True,
        )
        # 공통 Jinja2 필터
        self.env.filters["number_format"] = self._number_format
        self.env.filters["percent"] = self._percent_format
        self.env.filters["to_json"] = self._to_json

    def render(self, context: dict) -> str:
        """템플릿 렌더링 → HTML 문자열 반환"""
        template = self.env.get_template(self.TEMPLATE_NAME)
        context["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return template.render(**context)

    def save(self, html: str, filename: str) -> Path:
        """HTML 파일 저장 → 경로 반환"""
        output_dir = (
            Path(__file__).parent.parent.parent / "data" / "reports" / self.REPORT_SUB_DIR
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        output_path.write_text(html, encoding="utf-8")
        logger.info(f"[리포트] 저장 완료: {output_path}")
        return output_path

    def generate(self, **kwargs) -> Path:
        """서브클래스에서 구현: 데이터 조회 → 렌더링 → 저장"""
        raise NotImplementedError

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        """SQLite 커넥션 생성"""
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    # === 유틸리티 ===

    @staticmethod
    def _default_db_path() -> str:
        return str(Path(__file__).parent.parent.parent / "data" / "bgf_sales.db")

    @staticmethod
    def _number_format(value) -> str:
        """숫자 천단위 콤마"""
        if value is None:
            return "0"
        if isinstance(value, (int, float)):
            return f"{value:,.0f}"
        return str(value)

    @staticmethod
    def _percent_format(value, decimals=1) -> str:
        """퍼센트 포맷"""
        if value is None:
            return "0%"
        return f"{value:.{decimals}f}%"

    @staticmethod
    def _to_json(value) -> str:
        """Chart.js용 JSON 직렬화"""
        return json.dumps(value, ensure_ascii=False)
