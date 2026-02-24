"""
Phase 3: Repository Layer Modification for Multi-Store Architecture

Repository 메서드에 store_id 파라미터 추가 및 쿼리 수정
이 스크립트는 수정 가이드 및 예시를 제공합니다.
"""

import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logger import get_logger

logger = get_logger(__name__)


class Phase3Guide:
    """Phase 3 Repository 수정 가이드"""

    @staticmethod
    def print_guide():
        """수정 가이드 출력"""
        print("=" * 80)
        print("Phase 3: Repository Layer Modification Guide")
        print("=" * 80)
        print()
        print("17개 Repository 클래스에 store_id 파라미터를 추가합니다.")
        print()
        print("수정 패턴:")
        print()
        print("1. 메서드 시그니처에 store_id 파라미터 추가")
        print("   Before: def get_sales(self, start_date: str, end_date: str)")
        print("   After:  def get_sales(self, start_date: str, end_date: str, store_id: str = '46513')")
        print()
        print("2. WHERE 절에 store_id 조건 추가")
        print("   Before: WHERE sales_date BETWEEN ? AND ?")
        print("   After:  WHERE store_id = ? AND sales_date BETWEEN ? AND ?")
        print()
        print("3. INSERT 문에 store_id 컬럼 추가")
        print("   Before: INSERT INTO daily_sales (sales_date, item_cd, ...)")
        print("   After:  INSERT INTO daily_sales (store_id, sales_date, item_cd, ...)")
        print()
        print("4. 파라미터 순서에 store_id 추가")
        print("   Before: (start_date, end_date)")
        print("   After:  (store_id, start_date, end_date)")
        print()
        print("=" * 80)
        print()

    @staticmethod
    def show_examples():
        """수정 예시 출력"""
        print("=" * 80)
        print("Repository 수정 예시")
        print("=" * 80)
        print()

        # 예시 1: SalesRepository
        print("예시 1: SalesRepository - save_daily_sales()")
        print("-" * 80)
        print()
        print("BEFORE:")
        print("""
def save_daily_sales(
    self,
    sales_data: List[Dict[str, Any]],
    sales_date: str,
    collected_at: Optional[str] = None
) -> Dict[str, int]:
        """)
        print()
        print("AFTER:")
        print("""
def save_daily_sales(
    self,
    sales_data: List[Dict[str, Any]],
    sales_date: str,
    store_id: str = "46513",
    collected_at: Optional[str] = None
) -> Dict[str, int]:
        """)
        print()

        # 예시 2: _upsert_daily_sale
        print("예시 2: SalesRepository - _upsert_daily_sale() WHERE 절")
        print("-" * 80)
        print()
        print("BEFORE:")
        print("""
cursor.execute(
    "SELECT id FROM daily_sales WHERE sales_date = ? AND item_cd = ?",
    (sales_date, item_cd)
)
        """)
        print()
        print("AFTER:")
        print("""
cursor.execute(
    "SELECT id FROM daily_sales WHERE store_id = ? AND sales_date = ? AND item_cd = ?",
    (store_id, sales_date, item_cd)
)
        """)
        print()

        # 예시 3: INSERT
        print("예시 3: SalesRepository - INSERT 문")
        print("-" * 80)
        print()
        print("BEFORE:")
        print("""
cursor.execute(
    '''
    INSERT INTO daily_sales
    (collected_at, sales_date, item_cd, mid_cd, sale_qty, ...)
    VALUES (?, ?, ?, ?, ?, ...)
    ''',
    (collected_at, sales_date, item_cd, mid_cd, sale_qty, ...)
)
        """)
        print()
        print("AFTER:")
        print("""
cursor.execute(
    '''
    INSERT INTO daily_sales
    (store_id, collected_at, sales_date, item_cd, mid_cd, sale_qty, ...)
    VALUES (?, ?, ?, ?, ?, ?, ...)
    ''',
    (store_id, collected_at, sales_date, item_cd, mid_cd, sale_qty, ...)
)
        """)
        print()

        # 예시 4: UPDATE
        print("예시 4: SalesRepository - UPDATE 문")
        print("-" * 80)
        print()
        print("BEFORE:")
        print("""
cursor.execute(
    '''
    UPDATE daily_sales SET
        sale_qty = ?,
        stock_qty = ?
    WHERE sales_date = ? AND item_cd = ?
    ''',
    (sale_qty, stock_qty, sales_date, item_cd)
)
        """)
        print()
        print("AFTER:")
        print("""
cursor.execute(
    '''
    UPDATE daily_sales SET
        sale_qty = ?,
        stock_qty = ?
    WHERE store_id = ? AND sales_date = ? AND item_cd = ?
    ''',
    (sale_qty, stock_qty, store_id, sales_date, item_cd)
)
        """)
        print()

        print("=" * 80)
        print()

    @staticmethod
    def list_repositories():
        """수정 대상 Repository 목록 출력"""
        print("=" * 80)
        print("수정 대상 Repository 목록 (17개)")
        print("=" * 80)
        print()

        repos = [
            ("1",  "SalesRepository", "daily_sales", "HIGH"),
            ("2",  "OrderRepository", "orders", "HIGH"),
            ("3",  "PredictionLogRepository", "prediction_logs", "HIGH"),
            ("4",  "EvalOutcomeRepository", "eval_outcomes", "HIGH"),
            ("5",  "CalibrationHistoryRepository", "calibration_history", "MEDIUM"),
            ("6",  "OrderHistoryRepository", "order_history", "MEDIUM"),
            ("7",  "ProductDetailsRepository", "product_details", "HIGH"),
            ("8",  "PromotionRepository", "promotions", "MEDIUM"),
            ("9",  "RealtimeInventoryRepository", "realtime_inventory", "HIGH"),
            ("10", "InventoryBatchRepository", "inventory_batches", "MEDIUM"),
            ("11", "FailReasonRepository", "fail_reasons", "LOW"),
            ("12", "HolidayRepository", "holidays", "LOW"),
            ("13", "OrderStatusRepository", "N/A (조회만)", "LOW"),
            ("14", "WeatherRepository", "N/A (조회만)", "LOW"),
            ("15", "CalendarRepository", "N/A (조회만)", "LOW"),
            ("16", "ReceivingRepository", "N/A (조회만)", "LOW"),
            ("17", "ProductInfoRepository", "N/A (조회만)", "LOW"),
        ]

        print(f"{'#':<4} {'Repository':<32} {'Table':<24} {'Priority':<10}")
        print("-" * 80)
        for num, repo, table, priority in repos:
            print(f"{num:<4} {repo:<32} {table:<24} {priority:<10}")

        print()
        print("Priority:")
        print("  HIGH   - 매장별 트랜잭션 데이터, 반드시 수정 필요")
        print("  MEDIUM - 매장별 설정/이력, 수정 권장")
        print("  LOW    - 공유 데이터 또는 조회만, 선택적 수정")
        print()
        print("=" * 80)
        print()

    @staticmethod
    def show_next_steps():
        """다음 단계 안내"""
        print("=" * 80)
        print("다음 단계")
        print("=" * 80)
        print()
        print("Phase 3는 수동 작업이 필요합니다:")
        print()
        print("1. HIGH Priority Repository 수정 (필수):")
        print("   - SalesRepository")
        print("   - PredictionLogRepository")
        print("   - EvalOutcomeRepository")
        print("   - ProductDetailsRepository")
        print("   - RealtimeInventoryRepository")
        print()
        print("2. MEDIUM Priority Repository 수정 (권장):")
        print("   - CalibrationHistoryRepository")
        print("   - OrderHistoryRepository")
        print("   - PromotionRepository")
        print("   - InventoryBatchRepository")
        print()
        print("3. Business Logic 레이어 수정:")
        print("   - src/collectors/*.py - store_id 전파")
        print("   - src/prediction/improved_predictor.py - store_id 전파")
        print("   - src/order/auto_order.py - store_id 전파")
        print("   - src/scheduler/daily_job.py - DailyCollectionJob(store_id) 지원")
        print()
        print("4. 자동 생성된 수정 패치 파일 적용:")
        print("   - docs/05-implementation/phase3-repository-patches.md 참조")
        print()
        print("=" * 80)
        print()


def main():
    """메인 실행 함수"""
    guide = Phase3Guide()

    print()
    guide.print_guide()
    guide.show_examples()
    guide.list_repositories()
    guide.show_next_steps()

    print("[안내] Phase 3는 코드 수정이 광범위하므로, 가이드와 예시를 참조하여 진행하세요.")
    print()
    print("다음 파일을 참조하세요:")
    print("  - docs/05-implementation/multi-store-architecture.implementation.md")
    print("  - docs/05-implementation/phase3-repository-patches.md (생성 예정)")
    print()


if __name__ == "__main__":
    main()
