"""신상품 도입 현황 도메인 로직 [부분 활성]

활성:
  - 3일발주 후속 관리 (NEW_PRODUCT_MODULE_ENABLED = True)
    사용자 수동 발주 상품 → 3회 달성까지 자동 후속 발주

보류:
  - 미도입 자동 발주 (NEW_PRODUCT_AUTO_INTRO_ENABLED = False)
    유통기한(shelf_life_days) 소분류별 매핑 미구현
"""
