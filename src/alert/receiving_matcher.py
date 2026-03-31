"""
발주-입고 불일치 감지 모듈

order_tracking(발주) vs receiving_history(센터매입) 대조:
- 미입고: 발주O + 센터X + 리드타임 초과
- 수량 불일치: 발주수량 ≠ 입고수량
- 미발주 입고: 발주X + 센터O (본사 강제배송 등)
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.infrastructure.database.connection import DBRouter
from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
from src.utils.logger import get_logger

logger = get_logger(__name__)


def calc_max_lead_days(orderable_day: str) -> int:
    """발주가능요일 기반 최대 입고 리드타임 계산

    발주일 → 다음 발주가능일까지 최대 간격 + 입고 1일(배송)

    Args:
        orderable_day: "일월화수목금토" 형태 문자열

    Returns:
        최대 리드타임 (일)
    """
    if not orderable_day:
        return 3  # 기본값

    days_kr = ['일', '월', '화', '수', '목', '금', '토']
    available = [i for i, d in enumerate(days_kr) if d in orderable_day]

    if not available:
        return 3

    if len(available) >= 7:
        return 2  # 매일

    if len(available) >= 6:
        return 2  # 주6일 (1일 간격 + 입고 1일)

    # 최대 간격 = 발주 불가 연속일수
    # 예: 화목토 → [2,4,6] → 간격: 2,2,3 → 최대 3일 → 발주불가 2일 + 입고 1일 = 3
    max_gap = 0
    for i in range(len(available)):
        next_i = (i + 1) % len(available)
        gap = (available[next_i] - available[i]) % 7
        max_gap = max(max_gap, gap)

    # gap = 발주 간격(일), 여기에 입고까지 +1일은 이미 간격에 포함
    # 예: 화목(2→4) gap=2, 실제 수→목 발주 → 금 입고 = 2일
    return max_gap + 1  # 간격일 + 배송 1일


class ReceivingMatcher:
    """발주(OT) vs 센터매입(receiving_history) 대조기"""

    def __init__(self, store_id: str, store_name: Optional[str] = None):
        self.store_id = store_id
        self.store_name = store_name or store_id

    def check_mismatches(self, lookback_days: int = 7) -> Dict[str, List]:
        """발주-입고 불일치 검사

        Args:
            lookback_days: 과거 N일 발주 대상

        Returns:
            {undelivered, qty_mismatch, unexpected, pending}
        """
        conn = DBRouter.get_store_connection_with_common(self.store_id)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        today = datetime.now().date()

        try:
            # 1) 최근 N일 발주 목록 (OT)
            cursor.execute("""
                SELECT ot.item_cd, p.item_nm, ot.mid_cd, ot.order_date,
                       ot.order_qty, ot.delivery_type, ot.status,
                       pd.orderable_day
                FROM order_tracking ot
                JOIN common.products p ON ot.item_cd = p.item_cd
                LEFT JOIN common.product_details pd ON ot.item_cd = pd.item_cd
                WHERE ot.order_date >= date('now', '-' || ? || ' days')
                  AND ot.store_id = ?
                ORDER BY ot.order_date
            """, (lookback_days, self.store_id))
            orders = cursor.fetchall()

            # 2) 같은 기간 센터매입 목록
            cursor.execute("""
                SELECT item_cd, receiving_date, SUM(receiving_qty) as total_qty
                FROM receiving_history
                WHERE receiving_date >= date('now', '-' || ? || ' days')
                  AND store_id = ?
                GROUP BY item_cd, receiving_date
            """, (lookback_days, self.store_id))
            receivings = cursor.fetchall()

            # 센터매입 맵: {(item_cd, date): qty}
            recv_map = {}
            recv_all_items = {}  # {item_cd: [(date, qty), ...]}
            for r in receivings:
                key = (r['item_cd'], r['receiving_date'])
                recv_map[key] = r['total_qty']
                recv_all_items.setdefault(r['item_cd'], []).append(
                    (r['receiving_date'], r['total_qty'])
                )

            # 3) 대조
            undelivered = []
            qty_mismatch = []
            pending = []
            matched_recv_keys = set()

            for order in orders:
                item_cd = order['item_cd']
                order_date = datetime.strptime(order['order_date'], '%Y-%m-%d').date()
                lead_days = calc_max_lead_days(order['orderable_day'])
                deadline = order_date + timedelta(days=lead_days)

                # 리드타임 범위 내 입고 찾기
                found_recv = None
                for delta in range(lead_days + 1):
                    check_date = (order_date + timedelta(days=delta)).strftime('%Y-%m-%d')
                    key = (item_cd, check_date)
                    if key in recv_map:
                        found_recv = (check_date, recv_map[key])
                        matched_recv_keys.add(key)
                        break

                if found_recv:
                    recv_date, recv_qty = found_recv
                    if recv_qty != order['order_qty']:
                        qty_mismatch.append({
                            'item_cd': item_cd,
                            'item_nm': order['item_nm'],
                            'order_date': order['order_date'],
                            'order_qty': order['order_qty'],
                            'recv_qty': recv_qty,
                            'diff': recv_qty - order['order_qty'],
                        })
                elif today > deadline:
                    # 리드타임 초과 + 미입고
                    # daily_sales에서 실제 입고 확인 (센터 경유 안 한 직납일 수 있음)
                    cursor.execute("""
                        SELECT buy_qty FROM daily_sales
                        WHERE item_cd = ? AND sales_date BETWEEN ? AND ?
                          AND buy_qty > 0
                        LIMIT 1
                    """, (item_cd, order['order_date'],
                          deadline.strftime('%Y-%m-%d')))
                    ds_row = cursor.fetchone()
                    if not ds_row:
                        undelivered.append({
                            'item_cd': item_cd,
                            'item_nm': order['item_nm'],
                            'order_date': order['order_date'],
                            'order_qty': order['order_qty'],
                            'lead_days': lead_days,
                            'days_overdue': (today - deadline).days,
                        })
                else:
                    pending.append({
                        'item_cd': item_cd,
                        'item_nm': order['item_nm'],
                        'order_date': order['order_date'],
                        'deadline': deadline.strftime('%Y-%m-%d'),
                    })

            # 4) 미발주 입고 (센터매입에 있는데 OT에 매칭 안 된 것)
            order_items = {o['item_cd'] for o in orders}
            unexpected = []
            for r in receivings:
                key = (r['item_cd'], r['receiving_date'])
                if key not in matched_recv_keys and r['item_cd'] not in order_items:
                    # 상품명 조회
                    cursor.execute(
                        "SELECT item_nm FROM common.products WHERE item_cd = ?",
                        (r['item_cd'],)
                    )
                    nm_row = cursor.fetchone()
                    unexpected.append({
                        'item_cd': r['item_cd'],
                        'item_nm': nm_row['item_nm'] if nm_row else r['item_cd'],
                        'recv_date': r['receiving_date'],
                        'recv_qty': r['total_qty'],
                    })

            return {
                'undelivered': undelivered,
                'qty_mismatch': qty_mismatch,
                'unexpected': unexpected,
                'pending': pending,
            }

        finally:
            conn.close()

    def generate_mismatch_message(self, result: Dict[str, List]) -> Optional[str]:
        """불일치 알림 메시지 생성"""
        undelivered = result['undelivered']
        qty_mismatch = result['qty_mismatch']
        unexpected = result['unexpected']

        if not undelivered and not qty_mismatch and not unexpected:
            return None

        store_prefix = f"[{self.store_name}] " if self.store_name else ""
        today_str = datetime.now().strftime('%m/%d')
        lines = [f"{store_prefix}입고 확인 알림 ({today_str})", ""]

        if undelivered:
            lines.append(f"미입고 {len(undelivered)}건 (리드타임 초과):")
            for item in undelivered[:5]:
                od = item['order_date'][5:]  # MM-DD
                lines.append(f"  {item['item_nm'][:18]}  발주 {od}  {item['days_overdue']}일 초과")
            if len(undelivered) > 5:
                lines.append(f"  ...외 {len(undelivered) - 5}건")
            lines.append("")

        if qty_mismatch:
            lines.append(f"수량 불일치 {len(qty_mismatch)}건:")
            for item in qty_mismatch[:5]:
                sign = '+' if item['diff'] > 0 else ''
                lines.append(f"  {item['item_nm'][:18]}  발주 {item['order_qty']}→입고 {item['recv_qty']}  "
                             f"({sign}{item['diff']})")
            if len(qty_mismatch) > 5:
                lines.append(f"  ...외 {len(qty_mismatch) - 5}건")
            lines.append("")

        if unexpected:
            lines.append(f"미발주 입고 {len(unexpected)}건:")
            for item in unexpected[:5]:
                lines.append(f"  {item['item_nm'][:18]}  {item['recv_qty']}개")
            if len(unexpected) > 5:
                lines.append(f"  ...외 {len(unexpected) - 5}건")

        return "\n".join(lines)

    def send_mismatch_alert(self, lookback_days: int = 7) -> bool:
        """불일치 알림 발송 (나에게 보내기)"""
        result = self.check_mismatches(lookback_days)

        total = len(result['undelivered']) + len(result['qty_mismatch']) + len(result['unexpected'])
        if total == 0:
            logger.info(f"[{self.store_id}] 발주-입고 불일치 없음")
            return True

        msg = self.generate_mismatch_message(result)
        if not msg:
            return True

        try:
            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
            if not notifier.access_token:
                return False

            sent = notifier.send_message(msg, category="receiving_mismatch")
            if sent:
                logger.info(f"[{self.store_id}] 입고 확인 알림 발송: "
                            f"미입고={len(result['undelivered'])}, "
                            f"수량불일치={len(result['qty_mismatch'])}, "
                            f"미발주입고={len(result['unexpected'])}")
            return sent
        except Exception as e:
            logger.error(f"[{self.store_id}] 입고 확인 알림 실패: {e}")
            return False
