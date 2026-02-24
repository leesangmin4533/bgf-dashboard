"""
CUT(발주중지) 상태 신선도 관련 테스트

버그: CUT 전환된 상품이 DB에 is_cut=0으로 남아있어 계속 발주되는 문제
수정: 재고0 + 조회 오래됨 → CUT 미확인 의심 상품으로 취급, FORCE_ORDER 방지

테스트:
  1. get_stale_suspicious_items() — DB 조건 검증
  2. get_stale_item_codes() — stale 상품 set 반환
  3. FORCE_ORDER 가드 — CUT 미확인 상품 FORCE→NORMAL 다운그레이드
  4. CUT 해제 — set에서 제거 확인
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STORE_ID = "46513"
NOW = datetime.now().isoformat()
STALE_DATE = (datetime.now() - timedelta(days=5)).isoformat()  # 5일 전 (stale)
FRESH_DATE = (datetime.now() - timedelta(days=1)).isoformat()  # 1일 전 (fresh)


@pytest.fixture
def cut_test_db(tmp_path):
    """CUT 테스트용 DB (프로덕션 realtime_inventory 스키마)"""
    db_file = tmp_path / "cut_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            order_unit_qty INTEGER DEFAULT 1,
            is_available INTEGER DEFAULT 1,
            is_cut_item INTEGER DEFAULT 0,
            queried_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(store_id, item_cd)
        )
    """)

    # 테스트 데이터 삽입
    items = [
        # CUT 확정: is_cut=1
        (STORE_ID, "CUT001", "CUT확정상품", 0, 0, 1, 1, 1, STALE_DATE, NOW),
        # CUT 의심: is_cut=0, stock=0, stale (5일 전 조회)
        (STORE_ID, "SUSPECT001", "의심상품1", 0, 0, 1, 1, 0, STALE_DATE, NOW),
        (STORE_ID, "SUSPECT002", "의심상품2", 0, 0, 1, 1, 0, STALE_DATE, NOW),
        # 정상: is_cut=0, stock=0, fresh (1일 전 조회)
        (STORE_ID, "FRESH001", "최근조회품절", 0, 0, 1, 1, 0, FRESH_DATE, NOW),
        # 정상: is_cut=0, stock>0, stale
        (STORE_ID, "STOCK001", "재고있음상품", 5, 0, 1, 1, 0, STALE_DATE, NOW),
        # 미취급: is_available=0
        (STORE_ID, "UNAVAIL001", "미취급상품", 0, 0, 1, 0, 0, STALE_DATE, NOW),
    ]

    conn.executemany("""
        INSERT INTO realtime_inventory
        (store_id, item_cd, item_nm, stock_qty, pending_qty, order_unit_qty, is_available, is_cut_item, queried_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, items)

    conn.commit()
    conn.close()
    return str(db_file)


class TestStaleItemsQuery:
    """Repository의 stale 상품 조회 메서드 테스트"""

    def test_stale_suspicious_items(self, cut_test_db):
        """재고0 + 조회 오래됨 + is_cut=0인 상품만 반환"""
        from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository

        with patch.object(RealtimeInventoryRepository, '_get_conn') as mock_conn:
            conn = sqlite3.connect(cut_test_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            repo = RealtimeInventoryRepository(store_id=STORE_ID)
            result = repo.get_stale_suspicious_items(
                stale_days=3, max_items=20, store_id=STORE_ID
            )

        # SUSPECT001, SUSPECT002만 반환 (stale + stock=0 + is_cut=0 + is_available=1)
        assert set(result) == {"SUSPECT001", "SUSPECT002"}
        # CUT001: is_cut=1이라 제외
        assert "CUT001" not in result
        # FRESH001: 1일 전 조회라 stale 아님
        assert "FRESH001" not in result
        # STOCK001: stock>0이라 제외
        assert "STOCK001" not in result
        # UNAVAIL001: is_available=0이라 제외
        assert "UNAVAIL001" not in result

    def test_stale_suspicious_items_respects_max(self, cut_test_db):
        """max_items 제한 작동 확인"""
        from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository

        with patch.object(RealtimeInventoryRepository, '_get_conn') as mock_conn:
            conn = sqlite3.connect(cut_test_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            repo = RealtimeInventoryRepository(store_id=STORE_ID)
            result = repo.get_stale_suspicious_items(
                stale_days=3, max_items=1, store_id=STORE_ID
            )

        assert len(result) == 1

    def test_stale_item_codes(self, cut_test_db):
        """queried_at 기준 stale 상품 코드 set 반환"""
        from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository

        with patch.object(RealtimeInventoryRepository, '_get_conn') as mock_conn:
            conn = sqlite3.connect(cut_test_db)
            conn.row_factory = sqlite3.Row
            mock_conn.return_value = conn

            repo = RealtimeInventoryRepository(store_id=STORE_ID)
            result = repo.get_stale_item_codes(stale_days=3, store_id=STORE_ID)

        # is_cut=0, is_available=1, queried_at > 3일 전
        assert isinstance(result, set)
        assert "SUSPECT001" in result
        assert "SUSPECT002" in result
        assert "STOCK001" in result  # stock>0이어도 stale이면 포함
        assert "FRESH001" not in result
        assert "CUT001" not in result  # is_cut=1이라 제외
        assert "UNAVAIL001" not in result  # is_available=0이라 제외


class TestForceOrderGuard:
    """pre_order_evaluator의 FORCE_ORDER 가드 테스트"""

    def test_stale_item_gets_normal_not_force(self):
        """CUT 미확인 의심 상품: 품절이어도 FORCE 대신 NORMAL"""
        from src.prediction.pre_order_evaluator import PreOrderEvaluator, EvalDecision

        evaluator = PreOrderEvaluator.__new__(PreOrderEvaluator)
        evaluator.store_id = STORE_ID
        evaluator._stale_cut_suspects = {"SUSPECT001", "SUSPECT002"}
        evaluator._cost_optimizer = None

        # config mock
        config = MagicMock()
        config.exposure_urgent.value = 1.0
        config.exposure_normal.value = 2.0
        config.exposure_sufficient.value = 3.0
        config.stockout_freq_threshold.value = 0.5
        evaluator.config = config

        # SUSPECT001: stock=0 → 원래 FORCE인데 stale이므로 NORMAL
        with patch('src.prediction.pre_order_evaluator.ENABLE_FORCE_DOWNGRADE', False), \
             patch('src.prediction.pre_order_evaluator.ENABLE_FORCE_INTERMITTENT_SUPPRESSION', False):
            decision, reason = evaluator._make_decision(
                current_stock=0, exposure_days=0,
                popularity_level="high", stockout_frequency=0.5,
                item_cd="SUSPECT001", daily_avg=1.0, mid_cd="049"
            )

        assert decision == EvalDecision.NORMAL_ORDER
        assert "CUT미확인" in reason

    def test_fresh_item_gets_force(self):
        """최근 조회된 품절 상품은 정상적으로 FORCE"""
        from src.prediction.pre_order_evaluator import PreOrderEvaluator, EvalDecision

        evaluator = PreOrderEvaluator.__new__(PreOrderEvaluator)
        evaluator.store_id = STORE_ID
        evaluator._stale_cut_suspects = {"SUSPECT001"}  # FRESH001은 여기 없음
        evaluator._cost_optimizer = None

        config = MagicMock()
        config.exposure_urgent.value = 1.0
        config.exposure_normal.value = 2.0
        config.exposure_sufficient.value = 3.0
        config.stockout_freq_threshold.value = 0.5
        evaluator.config = config

        with patch('src.prediction.pre_order_evaluator.ENABLE_FORCE_DOWNGRADE', False), \
             patch('src.prediction.pre_order_evaluator.ENABLE_FORCE_INTERMITTENT_SUPPRESSION', False):
            decision, reason = evaluator._make_decision(
                current_stock=0, exposure_days=0,
                popularity_level="high", stockout_frequency=0.5,
                item_cd="FRESH001", daily_avg=1.0, mid_cd="049"
            )

        assert decision == EvalDecision.FORCE_ORDER
        assert "품절" in reason

    def test_no_stale_suspects_attribute(self):
        """_stale_cut_suspects 미설정 시 기존 동작 유지 (FORCE)"""
        from src.prediction.pre_order_evaluator import PreOrderEvaluator, EvalDecision

        evaluator = PreOrderEvaluator.__new__(PreOrderEvaluator)
        evaluator.store_id = STORE_ID
        evaluator._cost_optimizer = None
        # _stale_cut_suspects를 설정하지 않음

        config = MagicMock()
        config.exposure_urgent.value = 1.0
        config.exposure_normal.value = 2.0
        config.exposure_sufficient.value = 3.0
        config.stockout_freq_threshold.value = 0.5
        evaluator.config = config

        with patch('src.prediction.pre_order_evaluator.ENABLE_FORCE_DOWNGRADE', False), \
             patch('src.prediction.pre_order_evaluator.ENABLE_FORCE_INTERMITTENT_SUPPRESSION', False):
            decision, reason = evaluator._make_decision(
                current_stock=0, exposure_days=0,
                popularity_level="high", stockout_frequency=0.5,
                item_cd="ANYITEM", daily_avg=1.0, mid_cd="049"
            )

        assert decision == EvalDecision.FORCE_ORDER


class TestCutUnmark:
    """CUT 해제 로직 테스트"""

    def test_cut_unmark_removes_from_set(self):
        """재조회 시 CUT이 아닌 상품은 _cut_items에서 제거"""
        # AutoOrderSystem의 prefetch_pending_quantities 내부 로직 시뮬레이션
        cut_items = {"ITEM_A", "ITEM_B", "ITEM_C"}

        # ITEM_B가 재조회 결과 CUT이 아님 (is_cut_item=False)
        result = {'is_cut_item': False, 'current_stock': 3, 'pending_qty': 0}
        item_cd = "ITEM_B"

        # 기존 로직: CUT이면 추가
        if result.get('is_cut_item'):
            cut_items.add(item_cd)
        elif item_cd in cut_items:
            # 새 로직: CUT 해제
            cut_items.discard(item_cd)

        assert "ITEM_B" not in cut_items
        assert "ITEM_A" in cut_items
        assert "ITEM_C" in cut_items

    def test_cut_stays_if_still_cut(self):
        """재조회 시 여전히 CUT이면 set에 유지"""
        cut_items = {"ITEM_A"}

        result = {'is_cut_item': True}
        item_cd = "ITEM_A"

        if result.get('is_cut_item'):
            cut_items.add(item_cd)
        elif item_cd in cut_items:
            cut_items.discard(item_cd)

        assert "ITEM_A" in cut_items
