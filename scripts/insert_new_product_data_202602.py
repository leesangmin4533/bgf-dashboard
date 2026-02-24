#!/usr/bin/env python3
"""
BGF 사이트에서 수집한 2026-02 신상품 도입 현황 데이터를 DB에 삽입
(미도입 상품 상세 + 3일발주 미달성 상품 포함)
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

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database.repos import NewProductStatusRepository

STORE_ID = "46513"
MONTH_YM = "202602"

# ── 1. dsList (주차별 신상품 전체 도입률) ──
dsList = [
    {
        "N_WEEK_CD": "05", "DOIP_RATE": "76", "ITEM_CNT": 28, "ITEM_AD_CNT": 25,
        "DOIP_CNT": 19, "MIDOIP_CNT": 9,
        "PERIOD": "26.01.17~26.02.06", "WEEK_CONT": "02월1주차",
    },
    {
        "N_WEEK_CD": "06", "DOIP_RATE": "85.2", "ITEM_CNT": 38, "ITEM_AD_CNT": 34,
        "DOIP_CNT": 29, "MIDOIP_CNT": 9,
        "PERIOD": "26.01.26~26.02.13", "WEEK_CONT": "02월2주차",
    },
    {
        "N_WEEK_CD": "07", "DOIP_RATE": "44.4", "ITEM_CNT": 40, "ITEM_AD_CNT": 36,
        "DOIP_CNT": 16, "MIDOIP_CNT": 24,
        "PERIOD": "26.02.02~26.02.20", "WEEK_CONT": "02월3주차",
    },
    {
        "N_WEEK_CD": "08", "DOIP_RATE": "79.3", "ITEM_CNT": 33, "ITEM_AD_CNT": 29,
        "DOIP_CNT": 23, "MIDOIP_CNT": 10,
        "PERIOD": "26.02.09~26.02.27", "WEEK_CONT": "02월4주차",
    },
]

# ── 2. dsConvenienceList (간편식/디저트 3일 발주 달성률) ──
dsConvenienceList = [
    {
        "N_WEEK_CD": "05", "ITEM_CNT": 2, "DS_CNT": 1, "MIDS_CNT": 1,
        "DS_RATE": "50", "PERIOD": "26.01.17~26.02.06", "WEEK_CONT": "02월1주차",
    },
    {
        "N_WEEK_CD": "06", "ITEM_CNT": 3, "DS_CNT": 1, "MIDS_CNT": 2,
        "DS_RATE": "33.3", "PERIOD": "26.01.26~26.02.13", "WEEK_CONT": "02월2주차",
    },
    {
        "N_WEEK_CD": "07", "ITEM_CNT": 4, "DS_CNT": 3, "MIDS_CNT": 1,
        "DS_RATE": "75", "PERIOD": "26.02.02~26.02.20", "WEEK_CONT": "02월3주차",
    },
    {
        "N_WEEK_CD": "08", "ITEM_CNT": 6, "DS_CNT": 3, "MIDS_CNT": 3,
        "DS_RATE": "50", "PERIOD": "26.02.09~26.02.27", "WEEK_CONT": "02월4주차",
    },
]

# ── 3. dsDetailMonth (월별 합계) ──
dsDetailMonth = {
    "DOIP_RATE": "70.1", "DOIP_SCORE": "64",
    "DS_RATE": "53.3", "DS_SCORE": "12",
    "TOT_SCORE": "76", "SUPP_PAY_AMT": "110000",
}

# ── 4. 미도입 상품 상세 (midoip) ──
midoip_items = {
    5: [  # 1주차 (week_no=5)
        {"ORD_PSS_NM": "가능", "SMALL_NM": "소프트캔디,젤리", "ITEM_CD": "8691216098947", "ITEM_NM": "삼경)하리보스퀴시", "WEEK_CONT": "02월1주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장면", "ITEM_CD": "8801085883852", "ITEM_NM": "한일)계란탁들깨칼우동", "WEEK_CONT": "02월1주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "가공유", "ITEM_CD": "8801392039225", "ITEM_NM": "CJ)얼티브프로틴초코", "WEEK_CONT": "02월1주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "가공유", "ITEM_CD": "8801392081484", "ITEM_NM": "CJ)얼티브프로틴맛밤", "WEEK_CONT": "02월1주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "일반스낵", "ITEM_CD": "8802259028741", "ITEM_NM": "롯데)쌀로칩들기름김맛", "WEEK_CONT": "02월1주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "콘모나카", "ITEM_CD": "8802259029120", "ITEM_NM": "롯데)구구콘딸기크림치즈", "WEEK_CONT": "02월1주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "차음료", "ITEM_CD": "8809296885009", "ITEM_NM": "팔도)뽀로로보리차P220ml", "WEEK_CONT": "02월1주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "채소", "ITEM_CD": "8809444540361", "ITEM_NM": "해들넷)신선고구미700g", "WEEK_CONT": "02월1주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "비스켓", "ITEM_CD": "8809706062228", "ITEM_NM": "한올)마카디미아딸기쿠키", "WEEK_CONT": "02월1주차", "DS_YN": ""},
    ],
    6: [  # 2주차 (week_no=6)
        {"ORD_PSS_NM": "가능", "SMALL_NM": "과일", "ITEM_CD": "8800281968059", "ITEM_NM": "제스프리)썬골드키위4입", "WEEK_CONT": "02월2주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "감자스낵", "ITEM_CD": "8801019613517", "ITEM_NM": "해태)생생감자칩K김치맛", "WEEK_CONT": "02월2주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "두유", "ITEM_CD": "8801033812439", "ITEM_NM": "정식품)베지밀저당두유190", "WEEK_CONT": "02월2주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "핫바", "ITEM_CD": "8801068930771", "ITEM_NM": "삼립)핫스파이시후랑크", "WEEK_CONT": "02월2주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "가공유", "ITEM_CD": "8801121033227", "ITEM_NM": "매일)셀렉스프로핏코코넛", "WEEK_CONT": "02월2주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장커피", "ITEM_CD": "8801155745929", "ITEM_NM": "동원)소화잘되는카라멜", "WEEK_CONT": "02월2주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "콘모나카", "ITEM_CD": "8802259029205", "ITEM_NM": "롯데)찰옥수수카라멜팝콘", "WEEK_CONT": "02월2주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장밥", "ITEM_CD": "8809367693281", "ITEM_NM": "휴게소)한도부대찌개&밥", "WEEK_CONT": "02월2주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "건강지향식품", "ITEM_CD": "8809948527332", "ITEM_NM": "뉴드)올리브레몬샷26g", "WEEK_CONT": "02월2주차", "DS_YN": ""},
    ],
    7: [  # 3주차 (week_no=7) - 24개
        {"ORD_PSS_NM": "가능", "SMALL_NM": "프리미엄아이스크림", "ITEM_CD": "3415587454295", "ITEM_NM": "하겐)스트로베리앤유자바", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "코팅너겟초콜릿", "ITEM_CD": "8800280536723", "ITEM_NM": "SA)하츄핑초코크런치볼", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "소프트캔디,젤리", "ITEM_CD": "8800280536785", "ITEM_NM": "SA)프린세스트윈젤리", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "햄,소시지", "ITEM_CD": "8801068931143", "ITEM_NM": "삼립)제로닭가슴살청양", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "햄,소시지", "ITEM_CD": "8801068931150", "ITEM_NM": "삼립)제로닭가슴살페퍼", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "호상요구르트", "ITEM_CD": "8801104952699", "ITEM_NM": "빙그레)요플레그릭바나나", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "밑반찬", "ITEM_CD": "8801114181430", "ITEM_NM": "풀무원)저당쌈장고소180g", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "밑반찬", "ITEM_CD": "8801114181447", "ITEM_NM": "풀무원)저당쌈장매콤180g", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장커피", "ITEM_CD": "8801155746032", "ITEM_NM": "동원)쉐이커얼그레이밀크", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장커피", "ITEM_CD": "8801155746049", "ITEM_NM": "동원)쉐이커돌체라떼", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "탄산음료", "ITEM_CD": "8801188046002", "ITEM_NM": "웰치)탄산포도350ml", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "과일", "ITEM_CD": "8801599414497", "ITEM_NM": "허니듀)큐텐데이달콤토마토", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "두유", "ITEM_CD": "8801771041379", "ITEM_NM": "삼육)건강한약콩두유190", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "일반스낵", "ITEM_CD": "8801952012605", "ITEM_NM": "농심)인디안밥마라맛55g", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "일반스낵", "ITEM_CD": "8801952012612", "ITEM_NM": "농심)인디안밥체다치즈55g", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "에너지,비타민음료", "ITEM_CD": "8802094004187", "ITEM_NM": "동아)박카스맛젤리50g", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "코팅너겟초콜릿", "ITEM_CD": "8802041028538", "ITEM_NM": "롯데)마가렛트바닐라크림", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "콘모나카", "ITEM_CD": "8802259029212", "ITEM_NM": "롯데)구구콘체리블라썸", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "건면", "ITEM_CD": "8802259029267", "ITEM_NM": "풀무원)꽃게짬뽕", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "일반과자", "ITEM_CD": "8809022210167", "ITEM_NM": "CW)쫀득한찰떡파이", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장밥", "ITEM_CD": "8809367693298", "ITEM_NM": "휴게소)홍천한우국밥", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "건강지향식품", "ITEM_CD": "8809948527516", "ITEM_NM": "뉴드)올리브레몬샷26g", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉동간편식", "ITEM_CD": "8809958764389", "ITEM_NM": "풀무원)로스트치킨필라프", "WEEK_CONT": "02월3주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉동간편식", "ITEM_CD": "8809958764396", "ITEM_NM": "풀무원)직화짬뽕볶음밥", "WEEK_CONT": "02월3주차", "DS_YN": ""},
    ],
    8: [  # 4주차 (week_no=8) - 10개
        {"ORD_PSS_NM": "가능", "SMALL_NM": "덮밥,국밥류", "ITEM_CD": "8801045681849", "ITEM_NM": "오뚜기)컵밥사골곰탕밥", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉동만두", "ITEM_CD": "8801392164910", "ITEM_NM": "CJ)닭가슴살고기만두168g", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉동만두", "ITEM_CD": "8801392164934", "ITEM_NM": "CJ)닭가슴살김치만두168g", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "수산안주", "ITEM_CD": "8803622102594", "ITEM_NM": "한양)오징어숏다리130g", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장밥", "ITEM_CD": "8805684012866", "ITEM_NM": "휴게소)홍천옥수수불고기", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "프리미엄아이스크림", "ITEM_CD": "8809402813315", "ITEM_NM": "스위트)아이스파인애플바", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "과일", "ITEM_CD": "8809826442160", "ITEM_NM": "해성)천홍미니사과6입", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "건강지향식품", "ITEM_CD": "8809948527516", "ITEM_NM": "뉴드)올리브레몬샷26g", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉동간편식", "ITEM_CD": "8809958764389", "ITEM_NM": "풀무원)로스트치킨필라프", "WEEK_CONT": "02월4주차", "DS_YN": ""},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉동간편식", "ITEM_CD": "8809958764396", "ITEM_NM": "풀무원)직화짬뽕볶음밥", "WEEK_CONT": "02월4주차", "DS_YN": ""},
    ],
}

# ── 5. 3일발주 미달성 상품 (mids) ──
mids_items = {
    5: [  # 1주차
        {"ORD_PSS_NM": "가능", "SMALL_NM": "샐러드", "ITEM_CD": "8800247190302", "ITEM_NM": "샐)풀드포크나쵸브리또", "WEEK_CONT": "02월1주차", "DS_YN": "1/3(미달성)"},
    ],
    6: [  # 2주차
        {"ORD_PSS_NM": "가능", "SMALL_NM": "샐러드", "ITEM_CD": "8800247190364", "ITEM_NM": "샐)강낭콩훈제오리온샐", "WEEK_CONT": "02월2주차", "DS_YN": "1/3(미달성)"},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장디저트", "ITEM_CD": "8804455401373", "ITEM_NM": "노티드)저당초코크림도넛", "WEEK_CONT": "02월2주차", "DS_YN": "1/3(미달성)"},
    ],
    7: [  # 3주차
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장디저트", "ITEM_CD": "8801753116664", "ITEM_NM": "연세)학화호도컵케익", "WEEK_CONT": "02월3주차", "DS_YN": "2/3(미달성)"},
    ],
    8: [  # 4주차
        {"ORD_PSS_NM": "가능", "SMALL_NM": "샐러드", "ITEM_CD": "8800247190449", "ITEM_NM": "샐)귀리보리쉬림프온샐", "WEEK_CONT": "02월4주차", "DS_YN": "1/3(미달성)"},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장디저트", "ITEM_CD": "8801753116626", "ITEM_NM": "연세)명장단팥마리토쪼", "WEEK_CONT": "02월4주차", "DS_YN": "2/3(미달성)"},
        {"ORD_PSS_NM": "가능", "SMALL_NM": "냉장디저트", "ITEM_CD": "8809692959519", "ITEM_NM": "조이)요거트비스켓케이크", "WEEK_CONT": "02월4주차", "DS_YN": "1/3(미달성)"},
    ],
}


def parse_period(period_str: str):
    """'26.01.17~26.02.06' -> ('2026-01-17', '2026-02-06')"""
    if not period_str or "~" not in period_str:
        return "", ""
    parts = period_str.split("~")
    def to_date(s):
        s = s.strip()
        p = s.split(".")
        if len(p) == 3:
            yy, mm, dd = p
            year = f"20{yy}" if len(yy) == 2 else yy
            return f"{year}-{mm.zfill(2)}-{dd.zfill(2)}"
        return s
    return to_date(parts[0]), to_date(parts[1])


def safe_int(val, default=0):
    if val is None or val == "-" or val == "":
        return default
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return default


def safe_float(val, default=0.0):
    if val is None or val == "-" or val == "":
        return default
    try:
        return float(str(val))
    except (ValueError, TypeError):
        return default


def main():
    repo = NewProductStatusRepository(store_id=STORE_ID)

    # ── Step 1: 주차별 현황 저장 ──
    print(f"[1/4] 주차별 현황 저장 ({len(dsList)}주차)...")

    for i, ds_row in enumerate(dsList):
        conv_row = dsConvenienceList[i] if i < len(dsConvenienceList) else {}
        week_no = int(ds_row["N_WEEK_CD"])
        sta_dd, end_dd = parse_period(ds_row["PERIOD"])

        status_data = {
            "period": ds_row.get("PERIOD", ""),
            "week_cont": ds_row.get("WEEK_CONT", ""),
            "sta_dd": sta_dd,
            "end_dd": end_dd,
            "item_cnt": safe_int(ds_row.get("ITEM_CNT")),
            "item_ad_cnt": safe_int(ds_row.get("ITEM_AD_CNT")),
            "doip_cnt": safe_int(ds_row.get("DOIP_CNT")),
            "midoip_cnt": safe_int(ds_row.get("MIDOIP_CNT")),
            "doip_rate": safe_float(ds_row.get("DOIP_RATE")),
            "ds_item_cnt": safe_int(conv_row.get("ITEM_CNT")),
            "ds_cnt": safe_int(conv_row.get("DS_CNT")),
            "mids_cnt": safe_int(conv_row.get("MIDS_CNT")),
            "ds_rate": safe_float(conv_row.get("DS_RATE")),
            "doip_score": 0,
            "ds_score": 0,
            "tot_score": 0,
            "supp_pay_amt": 0,
        }

        repo.save_weekly_status(STORE_ID, MONTH_YM, week_no, status_data)
        print(f"  {status_data['week_cont']}: "
              f"doip {status_data['doip_rate']}% | "
              f"ds {status_data['ds_rate']}% | "
              f"midoip {status_data['midoip_cnt']} | mids {status_data['mids_cnt']}")

    # ── Step 2: 월별 합계 저장 ──
    print(f"\n[2/4] 월별 합계 저장...")

    monthly_data = {
        "doip_rate": safe_float(dsDetailMonth.get("DOIP_RATE")),
        "doip_score": safe_int(dsDetailMonth.get("DOIP_SCORE")),
        "ds_rate": safe_float(dsDetailMonth.get("DS_RATE")),
        "ds_score": safe_int(dsDetailMonth.get("DS_SCORE")),
        "tot_score": safe_int(dsDetailMonth.get("TOT_SCORE")),
        "supp_pay_amt": safe_int(dsDetailMonth.get("SUPP_PAY_AMT")),
        "doip_item_cnt": sum(safe_int(d.get("ITEM_AD_CNT")) for d in dsList),
        "doip_cnt": sum(safe_int(d.get("DOIP_CNT")) for d in dsList),
        "midoip_cnt": sum(safe_int(d.get("MIDOIP_CNT")) for d in dsList),
        "ds_item_cnt": sum(safe_int(d.get("ITEM_CNT")) for d in dsConvenienceList),
        "ds_cnt": sum(safe_int(d.get("DS_CNT")) for d in dsConvenienceList),
    }

    repo.save_monthly(STORE_ID, MONTH_YM, monthly_data)
    print(f"  tot={monthly_data['tot_score']}  -> {monthly_data['supp_pay_amt']:,}won")

    # ── Step 3: 미도입 상품 저장 ──
    print(f"\n[3/4] 미도입 상품 저장...")
    total_midoip = 0
    for week_no, items in sorted(midoip_items.items()):
        normalized = []
        for item in items:
            normalized.append({
                "item_cd": item["ITEM_CD"],
                "item_nm": item["ITEM_NM"],
                "small_nm": item["SMALL_NM"],
                "ord_pss_nm": item["ORD_PSS_NM"],
                "week_cont": item["WEEK_CONT"],
                "ds_yn": item.get("DS_YN", ""),
            })
        cnt = repo.save_items(STORE_ID, MONTH_YM, week_no, "midoip", normalized)
        total_midoip += cnt
        print(f"  {week_no}주차: {cnt}개 저장")

    # ── Step 4: 3일발주 미달성 상품 저장 ──
    print(f"\n[4/4] 3일발주 미달성 상품 저장...")
    total_mids = 0
    for week_no, items in sorted(mids_items.items()):
        normalized = []
        for item in items:
            normalized.append({
                "item_cd": item["ITEM_CD"],
                "item_nm": item["ITEM_NM"],
                "small_nm": item["SMALL_NM"],
                "ord_pss_nm": item["ORD_PSS_NM"],
                "week_cont": item["WEEK_CONT"],
                "ds_yn": item.get("DS_YN", ""),
            })
        cnt = repo.save_items(STORE_ID, MONTH_YM, week_no, "mids", normalized)
        total_mids += cnt
        print(f"  {week_no}주차: {cnt}개 저장")

    # ── 검증 ──
    print(f"\n=== 저장 확인 ===")
    weekly = repo.get_weekly_status(STORE_ID, MONTH_YM)
    monthly = repo.get_monthly_summary(STORE_ID, MONTH_YM)
    all_midoip = repo.get_missing_items(STORE_ID, MONTH_YM, item_type="midoip")
    all_mids = repo.get_missing_items(STORE_ID, MONTH_YM, item_type="mids")

    print(f"  weekly: {len(weekly)}rows")
    print(f"  monthly: {'OK' if monthly else 'MISSING'}")
    print(f"  midoip items: {len(all_midoip)} (expected {total_midoip})")
    print(f"  mids items: {len(all_mids)} (expected {total_mids})")

    if monthly:
        print(f"\n  doip {monthly.get('doip_rate')}% ({monthly.get('doip_score')}pt)")
        print(f"  ds   {monthly.get('ds_rate')}% ({monthly.get('ds_score')}pt)")
        print(f"  tot  {monthly.get('tot_score')}pt -> {monthly.get('supp_pay_amt'):,}won")

    print("\nDone!")


if __name__ == "__main__":
    main()
