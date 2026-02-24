"""
알림 설정
"""

# 알림 대상 카테고리 (mid_cd)
# 푸드류 (001~005)
ALERT_CATEGORIES = {
    "001": {"name": "도시락", "shelf_life_default": 1, "shelf_life_hours": {"1차": 30, "2차": 31}},
    "002": {"name": "주먹밥", "shelf_life_default": 1, "shelf_life_hours": {"1차": 30, "2차": 31}},   # 삼각김밥 포함
    "003": {"name": "김밥", "shelf_life_default": 1, "shelf_life_hours": {"1차": 30, "2차": 31}},
    "004": {"name": "샌드위치", "shelf_life_default": 3, "shelf_life_hours": {"1차": 74, "2차": 75}},
    "005": {"name": "햄버거", "shelf_life_default": 3, "shelf_life_hours": {"1차": 74, "2차": 75}},
    "012": {"name": "빵", "shelf_life_default": 3, "use_product_expiry": True},
}

# 배송 차수 설정
# 상품명 끝자리: 1 = 1차 배송, 2 = 2차 배송
# 폐기 시간은 차수별로 결정 (도착일 + 유통기한 후 expiry_hour)
DELIVERY_CONFIG = {
    "1차": {
        "suffix": "1",            # 상품명 끝자리
        "arrival_next_day": False,  # 당일 도착
        "arrival_hour": 20,       # 도착 시간 (20:00) - 저녁 배송
        "expiry_hour": 22,        # 폐기 시간 (22:00)
    },
    "2차": {
        "suffix": "2",            # 상품명 끝자리
        "arrival_next_day": True,  # 익일 도착
        "arrival_hour": 7,        # 도착 시간 (07:00) - 아침 배송
        "expiry_hour": 2,         # 폐기 시간 (02:00)
    },
}

# 알림 단계 설정
ALERT_LEVELS = {
    "warning": {
        "threshold": 0.7,  # 유통기한의 70% 소진 시
        "emoji": "🟡",
        "label": "주의"
    },
    "urgent": {
        "threshold": 0.9,  # 유통기한의 90% 소진 시
        "emoji": "🔴",
        "label": "긴급"
    },
    "expired": {
        "threshold": 1.0,  # 유통기한 초과
        "emoji": "⚫",
        "label": "폐기"
    }
}

# 카카오 API 설정
KAKAO_CONFIG = {
    "token_file": "data/kakao_token.json",  # 토큰 저장 파일
    "api_url": "https://kapi.kakao.com/v2/api/talk/memo/default/send",
    "auth_url": "https://kauth.kakao.com/oauth/authorize",
    "token_url": "https://kauth.kakao.com/oauth/token",
}

# 폐기 확정 3단계 스케줄 (10분전 수집 → 판정 → 10분후 수집+확정)
# expiry_hour → (pre_collect_time, judge_time, post_collect_time)
EXPIRY_CONFIRM_SCHEDULE = {
    22: {"pre_collect": "21:50", "judge": "22:00", "post_collect": "22:10"},
    14: {"pre_collect": "13:50", "judge": "14:00", "post_collect": "14:10"},
    10: {"pre_collect": "09:50", "judge": "10:00", "post_collect": "10:10"},
    2:  {"pre_collect": "01:50", "judge": "02:00", "post_collect": "02:10"},
    0:  {"pre_collect": "23:50", "judge": "00:00", "post_collect": "00:10"},
}

# 알림 시간 설정 (폐기 30분 전)
# 차수별 폐기 시간 → 알림 시간
EXPIRY_ALERT_SCHEDULE = {
    # 1차 샌드위치/햄버거 (004/005) → 22:00 폐기
    22: "21:30",  # 22:00 폐기 → 21:30 알림
    # 2차 도시락/주먹밥/김밥 (001/002/003) → 14:00 폐기
    14: "13:30",  # 14:00 폐기 → 13:30 알림
    # 2차 샌드위치/햄버거 (004/005) → 10:00 폐기
    10: "09:30",  # 10:00 폐기 → 09:30 알림
    # 1차 도시락/주먹밥/김밥 (001/002/003) → 02:00 폐기
    2: "01:30",   # 02:00 폐기 → 01:30 알림
    # 빵(012) → 자정(00:00) 유통기한 만료
    0: "23:00",   # 00:00 만료 → 23:00 알림 (1시간 전)
}

# 기존 일일 알림 (07:00 데이터 수집 후)
ALERT_SCHEDULE = {
    "morning": "07:00",   # 데이터 수집 후 일일 리포트
}
