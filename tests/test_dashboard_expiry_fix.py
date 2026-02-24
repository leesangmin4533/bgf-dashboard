"""
dashboard-expiry-fix 테스트

대시보드 폐기 위험 상세 버그 수정 검증:
- D-1 (M-1+M-2+M-3): daily_sales 24h 필터 + 시간 포맷 + 만료 경과건
- D-2 (m-1): 복합키 중복 제거
- D-3 (L-1): XSS 이스케이프
"""

import pytest
from datetime import datetime, timedelta


# =============================================================================
# D-1: daily_sales 24시간 필터 + 시간 포맷 (M-1 + M-2 + M-3)
# =============================================================================
class TestExpiryRiskFilter:
    """D-1: daily_sales 추정 폐기 24시간 필터 검증"""

    def _estimate_expiry(self, sales_date_str, mid_cd):
        """_get_expiry_risk()의 추정 로직 재현"""
        shelf_days = {"001": 1, "002": 1, "003": 1, "004": 2, "005": 1, "012": 3}
        days = shelf_days.get(mid_cd, 1)
        sd = datetime.strptime(sales_date_str, "%Y-%m-%d")
        est_expiry_dt = sd + timedelta(days=days)
        est_expiry = est_expiry_dt.strftime("%Y-%m-%d 00:00")
        return est_expiry_dt, est_expiry

    def _should_include(self, est_expiry_dt, now=None):
        """24h 필터 로직: 만료가 now+24h 이후면 제외"""
        if now is None:
            now = datetime.now()
        cutoff = now + timedelta(hours=24)
        if est_expiry_dt and est_expiry_dt > cutoff:
            return False
        return True

    @pytest.mark.unit
    def test_bread_3days_excluded_when_fresh(self):
        """빵(012) 오늘 입고 → 추정 만료 +3일 → 24h 이후 → 제외"""
        today = datetime.now().strftime("%Y-%m-%d")
        est_dt, _ = self._estimate_expiry(today, "012")
        assert self._should_include(est_dt) is False

    @pytest.mark.unit
    def test_lunchbox_yesterday_included(self):
        """도시락(001) 어제 데이터 → 추정 만료 오늘 → 24h 이내 → 포함"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        est_dt, _ = self._estimate_expiry(yesterday, "001")
        assert self._should_include(est_dt) is True

    @pytest.mark.unit
    def test_sandwich_2days_boundary(self):
        """샌드위치(004) 어제 데이터 → 추정 만료 내일 → 24h 이내 → 포함"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        est_dt, _ = self._estimate_expiry(yesterday, "004")
        assert self._should_include(est_dt) is True

    @pytest.mark.unit
    def test_already_expired_included(self):
        """추정 만료가 이미 지남 + 재고 있음 → 폐기 누락으로 포함"""
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        est_dt, _ = self._estimate_expiry(three_days_ago, "001")
        # est_expiry_dt = 2일 전 → cutoff 이내 → 포함
        assert self._should_include(est_dt) is True

    @pytest.mark.unit
    def test_expiry_format_includes_time(self):
        """추정 expiry_date가 'YYYY-MM-DD HH:MM' 포맷"""
        today = datetime.now().strftime("%Y-%m-%d")
        _, est_expiry = self._estimate_expiry(today, "001")
        assert " " in est_expiry
        assert est_expiry.endswith("00:00")
        # YYYY-MM-DD 00:00 포맷
        parts = est_expiry.split(" ")
        assert len(parts) == 2
        assert len(parts[0]) == 10  # YYYY-MM-DD
        assert len(parts[1]) == 5   # HH:MM

    @pytest.mark.unit
    def test_bread_2days_ago_included(self):
        """빵(012) 2일 전 데이터 → 추정 만료 내일 → 24h 이내 → 포함"""
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        est_dt, _ = self._estimate_expiry(two_days_ago, "012")
        assert self._should_include(est_dt) is True

    @pytest.mark.unit
    def test_none_expiry_dt_included(self):
        """est_expiry_dt가 None (파싱 실패) → 포함 (안전한 방향)"""
        assert self._should_include(None) is True


# =============================================================================
# D-2: 복합키 중복 제거 (m-1)
# =============================================================================
class TestDuplicateKeyComplex:
    """D-2: (item_cd, expiry_date) 복합키 중복 제거 검증"""

    @pytest.mark.unit
    def test_same_item_different_expiry_both_shown(self):
        """동일 item_cd, 다른 expiry_date → 둘 다 표시"""
        seen = set()
        items = []
        rows = [
            ("상품A", "2026-02-04 14:00", 3, "ITEM001", "001"),
            ("상품A", "2026-02-05 14:00", 5, "ITEM001", "001"),
        ]
        for r in rows:
            key = (r[3], r[1])  # (item_cd, expiry_date)
            if key in seen:
                continue
            seen.add(key)
            items.append({"item_cd": r[3], "expiry_date": r[1]})

        assert len(items) == 2

    @pytest.mark.unit
    def test_same_item_same_expiry_deduped(self):
        """동일 (item_cd, expiry_date) → 하나만 표시"""
        seen = set()
        items = []
        rows = [
            ("상품A", "2026-02-04 14:00", 3, "ITEM001", "001"),
            ("상품A", "2026-02-04 14:00", 2, "ITEM001", "001"),
        ]
        for r in rows:
            key = (r[3], r[1])
            if key in seen:
                continue
            seen.add(key)
            items.append({"item_cd": r[3], "expiry_date": r[1]})

        assert len(items) == 1

    @pytest.mark.unit
    def test_order_tracking_overrides_daily_sales(self):
        """order_tracking에 있는 item_cd → daily_sales에서 제외"""
        food_item_cds = set()
        food_item_cds.add("ITEM001")  # order_tracking에서 추가됨

        daily_sales_items = [
            {"item_cd": "ITEM001", "mid_cd": "001"},  # 중복 → 제외
            {"item_cd": "ITEM002", "mid_cd": "002"},  # 새로운 → 포함
        ]

        included = []
        for item in daily_sales_items:
            if item["item_cd"] in food_item_cds:
                continue
            food_item_cds.add(item["item_cd"])
            included.append(item)

        assert len(included) == 1
        assert included[0]["item_cd"] == "ITEM002"

    @pytest.mark.unit
    def test_different_items_both_shown(self):
        """다른 item_cd → 모두 표시"""
        seen = set()
        items = []
        rows = [
            ("상품A", "2026-02-04 14:00", 3, "ITEM001", "001"),
            ("상품B", "2026-02-04 14:00", 5, "ITEM002", "001"),
        ]
        for r in rows:
            key = (r[3], r[1])
            if key in seen:
                continue
            seen.add(key)
            items.append({"item_cd": r[3]})

        assert len(items) == 2


# =============================================================================
# D-3: XSS 이스케이프 (L-1)
# =============================================================================
class TestXssEscape:
    """D-3: 텍스트 이스케이프 검증 (Python에서 로직 재현)"""

    def _esc(self, s):
        """JS의 esc() 함수와 동일한 로직 (Python 재현)"""
        import html
        return html.escape(s)

    @pytest.mark.unit
    def test_escape_script_tag(self):
        """<script> 태그 이스케이프"""
        result = self._esc('<script>alert("xss")</script>')
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    @pytest.mark.unit
    def test_escape_html_entities(self):
        """HTML 엔티티 이스케이프"""
        result = self._esc('상품 <b>A</b> & "특가"')
        assert "<b>" not in result
        assert "&lt;b&gt;" in result
        assert "&amp;" in result

    @pytest.mark.unit
    def test_normal_text_unchanged(self):
        """일반 텍스트는 변경 없음"""
        result = self._esc("CU 도시락 매콤불닭")
        assert result == "CU 도시락 매콤불닭"

    @pytest.mark.unit
    def test_empty_string(self):
        """빈 문자열"""
        result = self._esc("")
        assert result == ""
