"""이슈 체인 .md 파일 자동 갱신

파일 I/O만, 판정 없음.
[PLANNED] 블록을 해당 이슈 체인 파일에 안전 삽입.
"""

import re
from pathlib import Path
from datetime import date, timedelta
from typing import List

from src.settings.constants import OPS_COOLDOWN_DAYS, OPS_DUPLICATE_KEYWORD_THRESHOLD
from src.utils.logger import get_logger

logger = get_logger(__name__)

ISSUES_DIR = Path(__file__).parent.parent.parent / "docs" / "05-issues"


class IssueChainWriter:
    """이슈 체인 파일에 [PLANNED] 블록 자동 삽입"""

    def __init__(self, issues_dir: Path = ISSUES_DIR):
        self.issues_dir = issues_dir

    def write_anomalies(self, anomalies: list) -> int:
        """이상 항목 리스트를 해당 이슈 체인 파일에 등록. 등록 건수 반환."""
        registered = 0
        for anomaly in anomalies:
            filepath = self.issues_dir / anomaly.issue_chain_file
            if not filepath.exists():
                logger.warning(f"[IssueChainWriter] 파일 없음: {filepath}")
                continue

            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"[IssueChainWriter] 파일 읽기 실패 {filepath}: {e}")
                continue

            # 중복 확인
            title_keywords = _extract_keywords(anomaly.title)
            if self._is_duplicate(content, title_keywords):
                logger.info(
                    f"[IssueChainWriter] 중복 스킵: {anomaly.title} in {anomaly.issue_chain_file}"
                )
                continue

            # 쿨다운 확인 (14일 내 RESOLVED 동일 패턴)
            if self._is_recently_resolved(content, title_keywords, OPS_COOLDOWN_DAYS):
                logger.info(
                    f"[IssueChainWriter] 쿨다운 스킵: {anomaly.title} in {anomaly.issue_chain_file}"
                )
                continue

            # [PLANNED] 블록 생성 & 삽입
            block = self._build_block(anomaly)
            if self._insert_planned_block(filepath, content, block):
                registered += 1
                logger.info(f"[IssueChainWriter] 등록: {anomaly.title} -> {anomaly.issue_chain_file}")

        return registered

    def _is_duplicate(self, content: str, title_keywords: List[str]) -> bool:
        """기존 [PLANNED]/[OPEN]/[WATCHING] 제목에서 핵심 키워드 매칭"""
        # 활성 이슈 제목 추출
        active_pattern = re.compile(
            r"^## \[(PLANNED|OPEN|WATCHING)\]\s+(.+?)(?:\s+\(P[123]\))?$",
            re.MULTILINE,
        )
        active_titles = [m.group(2) for m in active_pattern.finditer(content)]

        for existing_title in active_titles:
            existing_kw = _extract_keywords(existing_title)
            matched = sum(1 for kw in title_keywords if kw in existing_kw)
            if matched >= OPS_DUPLICATE_KEYWORD_THRESHOLD:
                return True

        return False

    def _is_recently_resolved(self, content: str, title_keywords: List[str], days: int) -> bool:
        """최근 N일 내 [RESOLVED]로 전환된 동일 패턴 확인"""
        # [RESOLVED] 블록에서 날짜 추출
        resolved_pattern = re.compile(
            r"^## \[RESOLVED\]\s+(.+?)(?:\s+\(P[123]\))?$",
            re.MULTILINE,
        )

        cutoff = (date.today() - timedelta(days=days)).isoformat()

        for m in resolved_pattern.finditer(content):
            resolved_title = m.group(1)
            resolved_kw = _extract_keywords(resolved_title)
            matched = sum(1 for kw in title_keywords if kw in resolved_kw)

            if matched >= OPS_DUPLICATE_KEYWORD_THRESHOLD:
                # 해당 블록 근처에서 날짜 찾기
                block_start = m.start()
                block_text = content[block_start:block_start + 500]
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", block_text)
                if date_match and date_match.group(1) >= cutoff:
                    return True

        return False

    def _insert_planned_block(self, filepath: Path, content: str, block: str) -> bool:
        """마지막 --- 구분자 위에 [PLANNED] 블록 안전 삽입"""
        try:
            # 마지막 --- 위치 찾기
            separator_positions = [m.start() for m in re.finditer(r"^---\s*$", content, re.MULTILINE)]

            if separator_positions:
                # 마지막 --- 바로 위에 삽입
                last_sep = separator_positions[-1]
                new_content = content[:last_sep] + block + "\n" + content[last_sep:]
            else:
                # --- 없으면 파일 끝에 추가
                new_content = content.rstrip() + "\n\n---\n\n" + block + "\n\n---\n"

            # 최종 갱신일 업데이트
            today_str = date.today().isoformat()
            new_content = re.sub(
                r"^(> 최종 갱신: )\d{4}-\d{2}-\d{2}",
                rf"\g<1>{today_str}",
                new_content,
                count=1,
                flags=re.MULTILINE,
            )

            filepath.write_text(new_content, encoding="utf-8")
            return True
        except Exception as e:
            logger.warning(f"[IssueChainWriter] 삽입 실패 {filepath}: {e}")
            return False

    def _build_block(self, anomaly) -> str:
        """[PLANNED] 블록 마크다운 텍스트 생성"""
        today_str = date.today().isoformat()
        return (
            f"## [PLANNED] {anomaly.title} ({anomaly.priority})\n"
            f"\n"
            f"**목표**: {anomaly.description}\n"
            f"**동기**: 자동 감지 ({today_str}) -- {anomaly.metric_name}\n"
            f"**선행조건**: 없음\n"
            f"**예상 영향**: {anomaly.metric_name} 관련 파일\n"
            f"\n"
        )


def _extract_keywords(title: str) -> set:
    """제목에서 핵심 키워드 추출 (2자 이상 한글/영숫자 단어)"""
    words = re.findall(r"[\w가-힣]{2,}", title)
    # 불용어 제거
    stopwords = {"조사", "검토", "확인", "분석", "개선", "수정", "테스트"}
    return {w for w in words if w not in stopwords}
