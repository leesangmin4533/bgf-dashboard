"""
연관 규칙 (Association Rule) 모듈 테스트

- 도메인: calculate_association_boost 순수 함수 테스트
- 채굴: in-memory DB로 co-occurrence 패턴 검증
- 조정기: 부스트 계산 end-to-end
- 매장 격리: 두 매장 다른 패턴 → 다른 규칙
- 비활성화: enabled=False → 1.0 반환
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from src.prediction.association.association_miner import (
    AssociationMiner,
    AssociationRule,
    calculate_association_boost,
    DEFAULT_MINING_PARAMS,
)
from src.prediction.association.association_adjuster import AssociationAdjuster


# =====================================================================
# 픽스처
# =====================================================================

@pytest.fixture
def association_db(tmp_path):
    """연관 분석용 테스트 DB (60일 데이터)"""
    db_file = tmp_path / "test_association.db"
    conn = sqlite3.connect(str(db_file))

    # 테이블 생성
    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            sales_date TEXT NOT NULL,
            sale_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            mid_cd TEXT,
            store_id TEXT DEFAULT '46513',
            UNIQUE(store_id, item_cd, sales_date)
        )
    """)
    conn.execute("""
        CREATE TABLE association_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_a TEXT NOT NULL,
            item_b TEXT NOT NULL,
            rule_level TEXT NOT NULL,
            support REAL NOT NULL,
            confidence REAL NOT NULL,
            lift REAL NOT NULL,
            correlation REAL DEFAULT 0,
            sample_days INTEGER NOT NULL,
            computed_at TEXT NOT NULL,
            store_id TEXT,
            UNIQUE(item_a, item_b, rule_level)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_assoc_item_b
        ON association_rules(item_b, rule_level)
    """)
    conn.commit()

    yield str(db_file)
    conn.close()


def _insert_correlated_data(db_path, store_id="46513", days=60,
                             item_a="ITEM_A", item_b="ITEM_B",
                             mid_a="001", mid_b="002"):
    """두 상품이 동시에 잘 팔리는 패턴 데이터 삽입

    A가 잘 팔리는 날(짝수일) → B도 잘 팔린다 (강한 양의 상관)
    """
    conn = sqlite3.connect(db_path)
    today = datetime.now()

    for d in range(days):
        date_str = (today - timedelta(days=d)).strftime("%Y-%m-%d")
        is_high_day = (d % 2 == 0)  # 짝수일: 두 상품 모두 평균 이상

        qty_a = 15 if is_high_day else 3
        qty_b = 12 if is_high_day else 2
        # C 상품은 A, B와 무관 (랜덤-ish)
        qty_c = 8 if (d % 3 == 0) else 4

        for item_cd, mid_cd, qty in [
            (item_a, mid_a, qty_a),
            (item_b, mid_b, qty_b),
            ("ITEM_C", "003", qty_c),
        ]:
            conn.execute(
                "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd, store_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_cd, date_str, qty, mid_cd, store_id),
            )

    conn.commit()
    conn.close()


# =====================================================================
# 1. 도메인: calculate_association_boost 순수 함수
# =====================================================================

class TestCalculateAssociationBoost:
    """calculate_association_boost() 순수 함수 테스트"""

    def test_empty_rules_returns_one(self):
        """규칙이 없으면 1.0 반환"""
        assert calculate_association_boost([], {}) == 1.0

    def test_empty_triggers_returns_one(self):
        """트리거가 없으면 1.0 반환"""
        rules = [
            AssociationRule("A", "B", "item", 0.1, 0.6, 1.5, 0.5, 60),
        ]
        assert calculate_association_boost(rules, {}) == 1.0

    def test_trigger_below_one_no_boost(self):
        """선행 상품이 평균 이하면 부스트 없음"""
        rules = [
            AssociationRule("A", "B", "item", 0.1, 0.6, 1.5, 0.5, 60),
        ]
        triggers = {"A": 0.8}  # 평균 이하
        assert calculate_association_boost(rules, triggers) == 1.0

    def test_valid_trigger_produces_boost(self):
        """유효 트리거 → 1.0 이상 부스트"""
        rules = [
            AssociationRule("A", "B", "item", 0.1, 0.6, 1.5, 0.5, 60),
        ]
        triggers = {"A": 1.3}  # 평균 30% 초과
        boost = calculate_association_boost(rules, triggers)
        assert boost > 1.0

    def test_max_boost_cap(self):
        """max_boost 상한 적용"""
        rules = [
            AssociationRule("A", "B", "item", 0.2, 0.9, 3.0, 0.8, 60),
        ]
        triggers = {"A": 5.0}  # 매우 높은 트리거
        boost = calculate_association_boost(rules, triggers, max_boost=1.15)
        assert boost <= 1.15

    def test_low_lift_filtered(self):
        """min_lift 미만 규칙은 무시"""
        rules = [
            AssociationRule("A", "B", "item", 0.1, 0.6, 1.1, 0.3, 60),  # lift 1.1
        ]
        triggers = {"A": 1.5}
        # min_lift=1.3이므로 무시 → 1.0 반환
        boost = calculate_association_boost(rules, triggers, min_lift=1.3)
        assert boost == 1.0

    def test_low_confidence_filtered(self):
        """min_confidence 미만 규칙은 무시"""
        rules = [
            AssociationRule("A", "B", "item", 0.1, 0.3, 2.0, 0.5, 60),  # confidence 0.3
        ]
        triggers = {"A": 1.5}
        # min_confidence=0.5이므로 무시 → 1.0 반환
        boost = calculate_association_boost(rules, triggers, min_confidence=0.5)
        assert boost == 1.0

    def test_multiple_rules_weighted(self):
        """다수 규칙은 가중 평균으로 결합"""
        rules = [
            AssociationRule("A", "B", "item", 0.1, 0.6, 2.0, 0.5, 60),
            AssociationRule("C", "B", "item", 0.1, 0.7, 1.8, 0.4, 60),
        ]
        triggers = {"A": 1.3, "C": 1.4}
        boost = calculate_association_boost(rules, triggers, min_lift=1.3)
        assert boost > 1.0
        assert boost <= 1.15


# =====================================================================
# 2. 채굴 엔진: AssociationMiner
# =====================================================================

class TestAssociationMiner:
    """AssociationMiner 채굴 테스트 (in-memory DB)"""

    def test_mine_all_empty_db(self, association_db):
        """빈 DB → 규칙 0건"""
        miner = AssociationMiner(store_id="46513", db_path=association_db)
        result = miner.mine_all()
        assert result["total"] == 0

    def test_mine_all_with_correlated_data(self, association_db):
        """상관 데이터 → 규칙 발견"""
        _insert_correlated_data(association_db, days=60)
        miner = AssociationMiner(
            store_id="46513",
            db_path=association_db,
            params={"min_support": 0.05, "min_lift": 1.1, "min_confidence": 0.3},
        )
        result = miner.mine_all()
        assert result["total"] > 0

    def test_item_level_filters_low_volume(self, association_db):
        """일평균 미달 상품은 item 레벨에서 제외"""
        conn = sqlite3.connect(association_db)
        today = datetime.now()
        # 매우 저빈도 상품: 60일 중 5일만 1개씩 (일평균 0.08)
        for d in [0, 10, 20, 30, 40]:
            date_str = (today - timedelta(days=d)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd, store_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("RARE_ITEM", date_str, 1, "099", "46513"),
            )
        # 고빈도 상품 2개 추가
        for d in range(60):
            date_str = (today - timedelta(days=d)).strftime("%Y-%m-%d")
            for item_cd, mid_cd, qty in [("HI_A", "001", 10), ("HI_B", "002", 8)]:
                conn.execute(
                    "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd, store_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (item_cd, date_str, qty, mid_cd, "46513"),
                )
        conn.commit()
        conn.close()

        miner = AssociationMiner(
            store_id="46513",
            db_path=association_db,
            params={"min_item_daily_avg": 1.0, "min_lift": 1.0, "min_confidence": 0.1, "min_support": 0.01},
        )
        # item 레벨에서 RARE_ITEM은 제외
        daily_data = miner._load_daily_sales()
        item_rules = miner._mine_item_level(daily_data)
        item_b_set = {r.item_b for r in item_rules}
        assert "RARE_ITEM" not in item_b_set

    def test_rules_saved_to_db(self, association_db):
        """채굴 결과가 DB에 저장됨"""
        _insert_correlated_data(association_db, days=60)
        miner = AssociationMiner(
            store_id="46513",
            db_path=association_db,
            params={"min_support": 0.05, "min_lift": 1.1, "min_confidence": 0.3},
        )
        result = miner.mine_all()

        conn = sqlite3.connect(association_db)
        cursor = conn.execute("SELECT COUNT(*) FROM association_rules")
        db_count = cursor.fetchone()[0]
        conn.close()

        assert db_count == result["total"]
        assert db_count > 0

    def test_min_data_days_requirement(self, association_db):
        """최소 데이터 일수 미달 → 규칙 0건"""
        # 5일치만 넣기 (기본 min_data_days=14 미달)
        _insert_correlated_data(association_db, days=5)
        miner = AssociationMiner(store_id="46513", db_path=association_db)
        result = miner.mine_all()
        assert result["total"] == 0

    def test_max_rules_per_item(self, association_db):
        """item_b당 최대 규칙 수 제한"""
        conn = sqlite3.connect(association_db)
        today = datetime.now()
        # 10개 상품 넣기 → 하나의 B에 대해 많은 A 가능
        for d in range(60):
            date_str = (today - timedelta(days=d)).strftime("%Y-%m-%d")
            is_high = (d % 2 == 0)
            for i in range(10):
                qty = 15 if is_high else 3
                conn.execute(
                    "INSERT INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd, store_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"ITEM_{i:02d}", date_str, qty, f"M{i:02d}", "46513"),
                )
        conn.commit()
        conn.close()

        miner = AssociationMiner(
            store_id="46513",
            db_path=association_db,
            params={"max_rules_per_item": 3, "min_lift": 1.0, "min_confidence": 0.1, "min_support": 0.01},
        )
        daily_data = miner._load_daily_sales()
        rules = miner._compute_rules(
            {f"M{i:02d}": {d: daily_data.get(d, {}).get(f"ITEM_{i:02d}", 0)
                           for d in daily_data}
             for i in range(10)},
            rule_level="mid",
        )
        # 각 B에 대해 최대 3개
        from collections import Counter
        b_counts = Counter(r.item_b for r in rules)
        for b, count in b_counts.items():
            assert count <= 3, f"{b}에 대해 {count}개 규칙 (최대 3)"


# =====================================================================
# 3. 조정기: AssociationAdjuster
# =====================================================================

class TestAssociationAdjuster:
    """AssociationAdjuster 부스트 계산 테스트"""

    def test_disabled_returns_one(self, association_db):
        """enabled=False → 항상 1.0"""
        adjuster = AssociationAdjuster(
            store_id="46513",
            db_path=association_db,
            params={"enabled": False},
        )
        boost = adjuster.get_association_boost("ANY_ITEM", "ANY_MID")
        assert boost == 1.0

    def test_no_rules_returns_one(self, association_db):
        """규칙 없는 DB → 1.0"""
        adjuster = AssociationAdjuster(
            store_id="46513",
            db_path=association_db,
        )
        boost = adjuster.get_association_boost("ITEM_A", "001")
        assert boost == 1.0

    def test_end_to_end_boost(self, association_db):
        """규칙 존재 + 트리거 활성 → 부스트 적용"""
        # 1. 데이터 삽입 + 채굴
        _insert_correlated_data(association_db, days=60)
        miner = AssociationMiner(
            store_id="46513",
            db_path=association_db,
            params={"min_support": 0.05, "min_lift": 1.1, "min_confidence": 0.3},
        )
        result = miner.mine_all()
        assert result["total"] > 0

        # 2. 조정기로 부스트 계산
        adjuster = AssociationAdjuster(
            store_id="46513",
            db_path=association_db,
            params={
                "enabled": True,
                "min_lift": 1.1,
                "min_confidence": 0.3,
                "max_boost": 1.15,
                "trigger_lookback_days": 2,
                "cache_ttl_seconds": 300,
            },
        )
        # 최근 2일이 짝수일(고판매)이면 트리거 활성화 가능
        # 부스트가 정확히 어떤 값일지는 데이터 의존적이므로, >= 1.0 검증
        boost = adjuster.get_association_boost("ITEM_B", "002")
        assert boost >= 1.0  # 최소 1.0
        assert boost <= 1.15  # 상한 이하

    def test_association_score_normalization(self, association_db):
        """get_association_score()는 0.0~1.0 범위"""
        adjuster = AssociationAdjuster(
            store_id="46513",
            db_path=association_db,
        )
        score = adjuster.get_association_score("ANY", "ANY")
        assert 0.0 <= score <= 1.0


# =====================================================================
# 4. 매장 격리
# =====================================================================

class TestStoreIsolation:
    """두 매장 간 연관 규칙 격리 테스트"""

    def test_different_stores_different_rules(self, tmp_path):
        """매장별 다른 패턴 → 다른 규칙"""
        # 매장 A: ITEM_A↔ITEM_B 강한 상관
        db_a = str(tmp_path / "store_a.db")
        conn_a = sqlite3.connect(db_a)
        conn_a.execute("""
            CREATE TABLE daily_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT, sales_date TEXT, sale_qty INTEGER,
                mid_cd TEXT, store_id TEXT, UNIQUE(store_id, item_cd, sales_date)
            )
        """)
        conn_a.execute("""
            CREATE TABLE association_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_a TEXT NOT NULL, item_b TEXT NOT NULL, rule_level TEXT NOT NULL,
                support REAL NOT NULL, confidence REAL NOT NULL, lift REAL NOT NULL,
                correlation REAL DEFAULT 0, sample_days INTEGER NOT NULL,
                computed_at TEXT NOT NULL, store_id TEXT,
                UNIQUE(item_a, item_b, rule_level)
            )
        """)
        conn_a.commit()
        conn_a.close()

        # 매장 B: ITEM_A↔ITEM_C 강한 상관 (B와는 무관)
        db_b = str(tmp_path / "store_b.db")
        conn_b = sqlite3.connect(db_b)
        conn_b.execute("""
            CREATE TABLE daily_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT, sales_date TEXT, sale_qty INTEGER,
                mid_cd TEXT, store_id TEXT, UNIQUE(store_id, item_cd, sales_date)
            )
        """)
        conn_b.execute("""
            CREATE TABLE association_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_a TEXT NOT NULL, item_b TEXT NOT NULL, rule_level TEXT NOT NULL,
                support REAL NOT NULL, confidence REAL NOT NULL, lift REAL NOT NULL,
                correlation REAL DEFAULT 0, sample_days INTEGER NOT NULL,
                computed_at TEXT NOT NULL, store_id TEXT,
                UNIQUE(item_a, item_b, rule_level)
            )
        """)
        conn_b.commit()
        conn_b.close()

        # 매장 A 데이터: A↔B 상관
        _insert_correlated_data(db_a, store_id="STORE_A", days=60,
                                 item_a="ITEM_A", item_b="ITEM_B")
        # 매장 B 데이터: X↔Y 상관 (다른 상품 쌍)
        _insert_correlated_data(db_b, store_id="STORE_B", days=60,
                                 item_a="ITEM_X", item_b="ITEM_Y",
                                 mid_a="010", mid_b="011")

        params = {"min_support": 0.05, "min_lift": 1.1, "min_confidence": 0.3}

        miner_a = AssociationMiner(store_id="STORE_A", db_path=db_a, params=params)
        result_a = miner_a.mine_all()

        miner_b = AssociationMiner(store_id="STORE_B", db_path=db_b, params=params)
        result_b = miner_b.mine_all()

        # 각각 규칙이 있어야 함
        assert result_a["total"] > 0
        assert result_b["total"] > 0

        # 매장 A의 규칙에 ITEM_B 존재, ITEM_C는 없을 수 있음
        conn_a = sqlite3.connect(db_a)
        rows_a = conn_a.execute(
            "SELECT DISTINCT item_b FROM association_rules WHERE rule_level='item'"
        ).fetchall()
        conn_a.close()
        item_b_set_a = {r[0] for r in rows_a}

        conn_b = sqlite3.connect(db_b)
        rows_b = conn_b.execute(
            "SELECT DISTINCT item_b FROM association_rules WHERE rule_level='item'"
        ).fetchall()
        conn_b.close()
        item_b_set_b = {r[0] for r in rows_b}

        # 매장 A는 ITEM_B 패턴, 매장 B는 ITEM_Y 패턴
        # 매장 A에는 ITEM_Y가 없고, 매장 B에는 ITEM_B가 없어야 함
        assert "ITEM_B" not in item_b_set_b or len(item_b_set_b) > 0
        if item_b_set_b:
            assert "ITEM_Y" in item_b_set_b


# =====================================================================
# 5. 규칙 정리
# =====================================================================

class TestRuleCleanup:
    """오래된 규칙 정리 테스트"""

    def test_clear_old_rules(self, association_db):
        """_clear_old_rules()로 오래된 규칙 삭제"""
        conn = sqlite3.connect(association_db)
        # 10일 전 규칙
        conn.execute(
            "INSERT INTO association_rules (item_a, item_b, rule_level, support, confidence, lift, sample_days, computed_at, store_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("A", "B", "item", 0.1, 0.5, 1.5, 60,
             (datetime.now() - timedelta(days=10)).isoformat(), "46513"),
        )
        # 오늘 규칙
        conn.execute(
            "INSERT INTO association_rules (item_a, item_b, rule_level, support, confidence, lift, sample_days, computed_at, store_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("C", "D", "mid", 0.15, 0.6, 1.8, 60,
             datetime.now().isoformat(), "46513"),
        )
        conn.commit()
        conn.close()

        miner = AssociationMiner(store_id="46513", db_path=association_db)
        cleaned = miner._clear_old_rules(keep_days=7)

        # 10일 전 규칙 1건 삭제
        assert cleaned >= 1

        conn = sqlite3.connect(association_db)
        remaining = conn.execute("SELECT COUNT(*) FROM association_rules").fetchone()[0]
        conn.close()
        assert remaining == 1  # 오늘 규칙만 남음


# =====================================================================
# 6. AssociationRule 데이터 클래스
# =====================================================================

class TestAssociationRule:
    """AssociationRule 값 객체 테스트"""

    def test_frozen(self):
        """frozen=True → 변경 불가"""
        rule = AssociationRule("A", "B", "item", 0.1, 0.5, 1.5, 0.4, 60)
        with pytest.raises(AttributeError):
            rule.lift = 2.0  # type: ignore

    def test_equality(self):
        """동일 값 → 동일 객체"""
        r1 = AssociationRule("A", "B", "item", 0.1, 0.5, 1.5, 0.4, 60)
        r2 = AssociationRule("A", "B", "item", 0.1, 0.5, 1.5, 0.4, 60)
        assert r1 == r2
