# 발주 실행 이슈 체인

> 최종 갱신: 2026-04-05
> 현재 상태: 행사 종료 임박 감량 자동화 계획

---

## [PLANNED] 행사 종료 임박 상품 발주 감량 자동화 (P2)

**목표**: promo_end_date - today <= 5일인 상품의 발주량을 자동 감소 또는 0 처리
**동기**: 행사(1+1 등) 종료 후 재고가 남으면 폐기 직결. 냉장고 사진 토론(03-30) 교훈: "1+1 종료 5일 전 감소" 규칙이 수동 판단에 의존 중
**선행조건**: promotions 테이블의 promo_end_date 정확성 확인 (promo-sync-fix 03-28 완료)
**예상 영향**: order/order_adjuster.py 또는 order_filter.py, CLAUDE.md 발주 체크리스트 #13

---
