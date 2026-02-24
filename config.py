"""
BGF 리테일 자동화 설정 파일
"""

# BGF 리테일 스토어 URL
BASE_URL = "https://store.bgfretail.com/websrc/deploy/index.html"

# 브라우저 설정
BROWSER_OPTIONS = {
    "headless": False,  # True로 설정하면 브라우저 창이 보이지 않음
    "window_size": (1920, 1080),
    "implicit_wait": 10,  # 요소 대기 시간(초)
    "page_load_timeout": 30,  # 페이지 로드 타임아웃(초)
}

# 넥사크로 관련 설정
NEXACRO_CONFIG = {
    "load_wait_time": 5,  # 넥사크로 초기 로딩 대기 시간
    "component_wait_time": 3,  # 컴포넌트 로딩 대기 시간
}
