"""상권 분석 서비스.

카카오 로컬 API로 매장 주변 시설을 분석하여 상권 유형과 점수를 산출한다.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 카카오 카테고리 검색 코드 → DB 컬럼 매핑
CATEGORY_MAP = {
    "CS2": "competitor_count",   # 편의점
    "SC4": "school_count",       # 학교
    "HP8": "hospital_count",     # 병원/약국
    "FD6": "restaurant_count",   # 음식점
    "CE7": "cafe_count",         # 카페
    "SW8": "subway_count",       # 지하철역
    "AT4": "office_count",       # 공공기관 (유동인구 유발)
    "PS3": "daycare_count",      # 어린이집/유치원 (주거 지표)
}

# traffic_score 가중치
TRAFFIC_WEIGHTS = {
    "subway_count": 3,
    "school_count": 2,
    "daycare_count": 2,
    "restaurant_count": 1,
    "cafe_count": 1,
    "hospital_count": 1,
    "office_count": 1,
}

DEFAULT_RADIUS = 500  # 미터


def _search_category(api_key: str, lat: float, lng: float,
                     category_code: str, radius: int = DEFAULT_RADIUS) -> int:
    """카카오 카테고리 검색 API로 반경 내 시설 수 조회."""
    url = "https://dapi.kakao.com/v2/local/search/category.json"
    headers = {"Authorization": f"KakaoAK {api_key}"}
    params = {
        "category_group_code": category_code,
        "x": str(lng),
        "y": str(lat),
        "radius": radius,
        "size": 1,  # total_count만 필요, 실제 데이터는 불필요
    }
    resp = requests.get(url, headers=headers, params=params, timeout=5)
    data = resp.json()
    return data.get("meta", {}).get("total_count", 0)


def _calc_traffic_score(counts: dict) -> float:
    """유동인구 점수 계산 (0~100)."""
    raw = 0
    for key, weight in TRAFFIC_WEIGHTS.items():
        raw += counts.get(key, 0) * weight
    # raw 150 기준 정규화 (150 이상이면 100점)
    return min(100.0, round((raw / 150) * 100, 1))


def _calc_competition_score(competitor_count: int) -> float:
    """경쟁 점수 (0~100, 낮을수록 유리). 구간별 세분화."""
    if competitor_count == 0:
        return 0.0    # 독점
    elif competitor_count <= 2:
        return 30.0   # 여유
    elif competitor_count <= 4:
        return 60.0   # 보통
    elif competitor_count <= 7:
        return 80.0   # 경쟁
    else:
        return 100.0  # 과열


def _classify_area_type(counts: dict) -> str:
    """상권 유형 복합 분류 (최대 2개 태그)."""
    subway = counts.get("subway_count", 0)
    school = counts.get("school_count", 0)
    food = counts.get("restaurant_count", 0)
    office = counts.get("office_count", 0)
    daycare = counts.get("daycare_count", 0)
    hospital = counts.get("hospital_count", 0)

    # 주거 지표: 어린이집+병원+학교 합산 (아파트 단지 특성)
    is_residential = (daycare >= 3) or (daycare >= 2 and hospital >= 3) or (daycare >= 2 and school >= 2)

    # 1. 주상권 분류 (우선순위 순)
    if subway >= 2:
        main = "역세권"
    elif is_residential:
        main = "아파트상권"
    elif school >= 3:
        main = "학원가"
    elif food >= 10:
        main = "상업지구"
    elif office >= 5:
        main = "직장형"
    else:
        main = "주거형"

    # 2. 부특성 태그 (경쟁/유동인구)
    competitor = counts.get("competitor_count", 0)
    cafe = counts.get("cafe_count", 0)

    sub = None
    if competitor >= 8:
        sub = "경쟁과열"
    elif competitor >= 5:
        sub = "경쟁"
    elif subway >= 1 and main != "역세권":
        sub = "역근처"
    elif cafe >= 10:
        sub = "카페거리"
    elif school >= 1 and main != "학원가":
        sub = "학교근처"

    return f"{main}·{sub}" if sub else main


def run_store_analysis(store_id: str, radius: int = DEFAULT_RADIUS) -> dict:
    """매장 상권 분석 실행.

    1. stores 테이블에서 lat, lng 조회
    2. 카카오 카테고리 검색 API 병렬 호출 (7개 카테고리)
    3. traffic_score, competition_score, area_type 계산
    4. store_analysis 테이블 UPSERT

    Args:
        store_id: 매장 코드
        radius: 검색 반경 (미터, 기본 500)

    Returns:
        분석 결과 dict (실패 시 빈 dict)
    """
    api_key = os.environ.get("KAKAO_REST_API_KEY", "")
    if not api_key:
        logger.warning("[상권분석] KAKAO_REST_API_KEY 미설정, 분석 건너뜀")
        return {}

    # 1. lat, lng 조회
    conn = DBRouter.get_common_connection()
    try:
        row = conn.execute(
            "SELECT lat, lng FROM stores WHERE store_id = ?", (store_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row or not row[0] or not row[1]:
        logger.warning("[상권분석] store_id=%s 좌표 없음, 분석 스킵", store_id)
        return {}

    lat, lng = float(row[0]), float(row[1])

    # 2. 카카오 API 병렬 호출
    counts = {}
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_search_category, api_key, lat, lng, code, radius): col
                for code, col in CATEGORY_MAP.items()
            }
            for future in as_completed(futures):
                col_name = futures[future]
                try:
                    counts[col_name] = future.result()
                except Exception as e:
                    logger.warning("[상권분석] %s 조회 실패: %s", col_name, e)
                    counts[col_name] = 0
    except Exception as e:
        logger.warning("[상권분석] API 병렬 호출 실패: %s", e)
        return {}

    # 3. 점수 계산
    traffic_score = _calc_traffic_score(counts)
    competition_score = _calc_competition_score(counts.get("competitor_count", 0))
    area_type = _classify_area_type(counts)
    analyzed_at = datetime.now().isoformat()

    result = {
        "store_id": store_id,
        **counts,
        "traffic_score": traffic_score,
        "competition_score": competition_score,
        "area_type": area_type,
        "analyzed_at": analyzed_at,
        "radius_m": radius,
    }

    # 4. DB UPSERT
    conn = DBRouter.get_common_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO store_analysis
            (store_id, competitor_count, school_count, hospital_count,
             restaurant_count, cafe_count, subway_count, office_count,
             daycare_count,
             traffic_score, competition_score, area_type, analyzed_at, radius_m)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                store_id,
                counts.get("competitor_count", 0),
                counts.get("school_count", 0),
                counts.get("hospital_count", 0),
                counts.get("restaurant_count", 0),
                counts.get("cafe_count", 0),
                counts.get("subway_count", 0),
                counts.get("office_count", 0),
                counts.get("daycare_count", 0),
                traffic_score,
                competition_score,
                area_type,
                analyzed_at,
                radius,
            ),
        )
        conn.commit()
        logger.info(
            "[상권분석] 완료: store=%s, 유형=%s, 유동=%s, 경쟁=%s",
            store_id, area_type, traffic_score, competition_score,
        )
    finally:
        conn.close()

    return result


def get_store_analysis(store_id: str) -> dict:
    """저장된 상권 분석 결과 조회.

    Returns:
        분석 결과 dict (없으면 빈 dict)
    """
    conn = DBRouter.get_common_connection()
    try:
        conn.row_factory = _dict_factory
        row = conn.execute(
            "SELECT * FROM store_analysis WHERE store_id = ?", (store_id,)
        ).fetchone()
        return row or {}
    finally:
        conn.close()


def _dict_factory(cursor, row):
    """sqlite3 Row → dict 변환."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
