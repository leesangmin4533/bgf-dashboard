"""
데이터 품질 검증기

수집된 판매 데이터의 품질을 검증하고 이상 데이터를 탐지합니다.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import re
import statistics

from src.validation.validation_result import ValidationResult, ValidationError, ValidationWarning
from src.validation.validation_rules import ValidationRules
from src.infrastructure.database.repos import SalesRepository
from src.infrastructure.database.connection import get_connection
from src.db.store_query import store_filter
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DataValidator:
    """데이터 품질 검증기

    수집된 판매 데이터의 품질을 검증하고 이상 데이터를 탐지합니다.
    """

    def __init__(self, store_id: str = "46704", rules: Optional[ValidationRules] = None):
        """초기화

        Args:
            store_id: 점포 ID
            rules: 검증 규칙 (기본값: ValidationRules())
        """
        self.store_id = store_id
        self.rules = rules or ValidationRules()
        self.repo = SalesRepository(store_id=self.store_id)

    def validate_sales_data(
        self,
        data: List[Dict[str, Any]],
        sales_date: str,
        store_id: Optional[str] = None
    ) -> ValidationResult:
        """판매 데이터 검증 (메인 엔트리포인트)

        Args:
            data: 검증할 데이터 리스트
            sales_date: 판매 일자 (YYYY-MM-DD)
            store_id: 점포 ID (기본값: self.store_id)

        Returns:
            ValidationResult: 검증 결과
        """
        if store_id is None:
            store_id = self.store_id

        result = ValidationResult(
            is_valid=True,
            total_count=len(data),
            passed_count=0,
            failed_count=0,
            sales_date=sales_date,
            store_id=store_id
        )

        for item in data:
            item_cd = item.get('item_cd') or item.get('ITEM_CD', '')

            # 1. 상품코드 형식 검증
            if not self.validate_item_code(item_cd):
                result.add_error(
                    error_code='INVALID_ITEM_CD',
                    error_message=f'상품코드 형식 오류: {item_cd}',
                    affected_item=item_cd,
                    metadata={'expected': '13자리 숫자', 'actual': len(item_cd)}
                )
                continue

            # 2. 수량 범위 검증
            qty_errors = self.validate_quantities(item)
            if qty_errors:
                for error in qty_errors:
                    result.add_error(
                        error_code=error['code'],
                        error_message=error['message'],
                        affected_item=item_cd,
                        metadata=error['metadata']
                    )
                continue

            # 3. 중복 수집 검증
            if self.rules.get_rule('duplicate_detection.enabled', True):
                if self.detect_duplicate(sales_date, item_cd):
                    result.add_error(
                        error_code='DUPLICATE_COLLECTION',
                        error_message=f'중복 수집 감지: {sales_date} / {item_cd}',
                        affected_item=item_cd
                    )
                    continue

            # 4. 이상치 탐지 (경고만, 치명적 아님)
            sale_qty = item.get('sale_qty') or item.get('SALE_QTY', 0)
            anomaly = self.detect_sales_anomaly(item_cd, sale_qty)
            if anomaly:
                result.add_warning(
                    warning_code=anomaly['code'],
                    warning_message=anomaly['message'],
                    affected_item=item_cd,
                    metadata=anomaly['metadata']
                )

            result.passed_count += 1

        return result

    def validate_item_code(self, item_cd: str) -> bool:
        """상품코드 형식 검증

        Args:
            item_cd: 상품코드

        Returns:
            bool: 유효하면 True
        """
        # 길이 체크
        expected_length = self.rules.get_rule('item_code.length', 13)
        if len(item_cd) != expected_length:
            return False

        # 패턴 체크 (숫자만)
        pattern = self.rules.get_rule('item_code.pattern', r'^\d{13}$')
        if not re.match(pattern, item_cd):
            return False

        # 제외 패턴 체크 (테스트 데이터)
        exclude_patterns = self.rules.get_rule('item_code.exclude_patterns', [])
        for exclude in exclude_patterns:
            if re.match(exclude, item_cd):
                logger.debug(f"테스트 패턴 감지: {item_cd}")
                return False

        return True

    def validate_quantities(self, item: Dict[str, Any]) -> List[Dict[str, Any]]:
        """수량 검증

        Args:
            item: 판매 데이터 아이템

        Returns:
            List[Dict]: 오류 리스트 (없으면 빈 리스트)
        """
        errors = []
        item_cd = item.get('item_cd') or item.get('ITEM_CD', '')

        qty_rules = self.rules.get_rule('quantity', {})

        for qty_field, limits in qty_rules.items():
            # 대소문자 모두 지원
            value = item.get(qty_field) or item.get(qty_field.upper(), 0)
            try:
                value = int(value) if value is not None else 0
            except (ValueError, TypeError):
                value = 0

            # 음수 체크
            if value < limits.get('min', 0):
                errors.append({
                    'code': 'NEGATIVE_QTY',
                    'message': f'{qty_field} 음수: {value}',
                    'metadata': {'field': qty_field, 'value': value, 'min': limits['min']}
                })

            # 최대값 체크
            if value > limits.get('max', 9999):
                errors.append({
                    'code': 'EXCESSIVE_QTY',
                    'message': f'{qty_field} 범위 초과: {value}',
                    'metadata': {'field': qty_field, 'value': value, 'max': limits['max']}
                })

        return errors

    def detect_duplicate(self, sales_date: str, item_cd: str) -> bool:
        """중복 수집 감지

        Args:
            sales_date: 판매 일자
            item_cd: 상품코드

        Returns:
            bool: 중복이면 True
        """
        sf, sp = store_filter("", self.store_id)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT COUNT(DISTINCT collected_at) as cnt
                FROM daily_sales
                WHERE sales_date = ? AND item_cd = ?
                {sf}
            """, (sales_date, item_cd) + sp)

            row = cursor.fetchone()
            return row['cnt'] > 1 if row else False
        finally:
            conn.close()

    def detect_sales_anomaly(
        self,
        item_cd: str,
        sale_qty: int
    ) -> Optional[Dict[str, Any]]:
        """판매량 이상치 탐지 (3σ 기준)

        Args:
            item_cd: 상품코드
            sale_qty: 판매량

        Returns:
            Optional[Dict]: 이상치면 경고 정보, 아니면 None
        """
        window_days = self.rules.get_rule('anomaly.window_days', 30)
        min_samples = self.rules.get_rule('anomaly.min_samples', 7)

        # 최근 N일 판매량 조회
        sf, sp = store_filter("", self.store_id)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT sale_qty
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', ?)
                AND sale_qty > 0
                {sf}
            """, (item_cd, f'-{window_days} days') + sp)

            rows = cursor.fetchall()

            if len(rows) < min_samples:
                return None  # 데이터 부족

            values = [row['sale_qty'] for row in rows]
            mean = statistics.mean(values)
            stddev = statistics.stdev(values) if len(values) > 1 else 0

            # 3σ 초과 체크
            if stddev > 0 and abs(sale_qty - mean) > 3 * stddev:
                return {
                    'code': 'ANOMALY_3SIGMA',
                    'message': f'판매량 이상치 감지 (3σ 초과)',
                    'metadata': {
                        'value': sale_qty,
                        'mean': round(mean, 2),
                        'stddev': round(stddev, 2),
                        'threshold': round(3 * stddev, 2)
                    }
                }

            return None
        finally:
            conn.close()

    def validate_batch(
        self,
        dates: List[str],
        store_id: Optional[str] = None
    ) -> Dict[str, ValidationResult]:
        """여러 날짜 일괄 검증

        Args:
            dates: 검증할 날짜 리스트 (YYYY-MM-DD)
            store_id: 점포 ID

        Returns:
            Dict[str, ValidationResult]: 날짜별 검증 결과
        """
        if store_id is None:
            store_id = self.store_id

        results = {}
        conn = get_connection()

        try:
            for sales_date in dates:
                # 해당 날짜의 데이터 조회
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT item_cd, mid_cd, sale_qty, ord_qty, buy_qty, disuse_qty, stock_qty
                    FROM daily_sales
                    WHERE sales_date = ? AND store_id = ?
                """, (sales_date, store_id))

                data = [dict(row) for row in cursor.fetchall()]

                if data:
                    results[sales_date] = self.validate_sales_data(data, sales_date, store_id)
                    logger.info(f"검증 완료: {sales_date} - {results[sales_date]}")
                else:
                    logger.warning(f"데이터 없음: {sales_date}")

        finally:
            conn.close()

        return results
