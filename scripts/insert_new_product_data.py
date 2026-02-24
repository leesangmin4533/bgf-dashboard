#!/usr/bin/env python3
"""
BGF 사이트에서 수집한 신상품 도입 현황 데이터를 DB에 삽입하는 스크립트
(수집기 테스트 전 시뮬레이션용)
"""

import sys
import io
from pathlib import Path

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database.repos import NewProductStatusRepository

STORE_ID = "46513"
MONTH_YM = "202601"

# ── BGF 사이트에서 수집한 실제 데이터 ──

# 1. dsList (주차별 신상품 전체 도입률)
dsList = [
    {
        "STORE_CD": "46513", "YYYY_CD": "2026", "N_MNTH_CD": "01", "N_WEEK_CD": "02",
        "DOIP_RATE": "76.4", "ITEM_CNT": 19, "ITEM_AD_CNT": 17,
        "DOIP_CNT": 13, "MIDOIP_CNT": 6,
        "STA_DD": None, "END_DD": None,
        "PERIOD": "25.12.29~26.01.16", "WEEK_CONT": "01월2주차",
    },
    {
        "STORE_CD": "46513", "YYYY_CD": "2026", "N_MNTH_CD": "01", "N_WEEK_CD": "03",
        "DOIP_RATE": "86.6", "ITEM_CNT": 34, "ITEM_AD_CNT": 30,
        "DOIP_CNT": 26, "MIDOIP_CNT": 8,
        "STA_DD": None, "END_DD": None,
        "PERIOD": "26.01.05~26.01.23", "WEEK_CONT": "01월3주차",
    },
    {
        "STORE_CD": "46513", "YYYY_CD": "2026", "N_MNTH_CD": "01", "N_WEEK_CD": "04",
        "DOIP_RATE": "88", "ITEM_CNT": 28, "ITEM_AD_CNT": 25,
        "DOIP_CNT": 22, "MIDOIP_CNT": 6,
        "STA_DD": None, "END_DD": None,
        "PERIOD": "26.01.12~26.01.30", "WEEK_CONT": "01월4주차",
    },
]

# 2. dsConvenienceList (간편식/디저트 3일 발주 달성률)
dsConvenienceList = [
    {
        "STORE_CD": "46513", "YYYY_CD": "2026", "N_MNTH_CD": "01", "N_WEEK_CD": "02",
        "ITEM_CNT": 2, "DS_CNT": 2, "MIDS_CNT": 0, "DS_RATE": "100",
        "PERIOD": "25.12.29~26.01.16", "WEEK_CONT": "01월2주차",
    },
    {
        "STORE_CD": "46513", "YYYY_CD": "2026", "N_MNTH_CD": "01", "N_WEEK_CD": "03",
        "ITEM_CNT": 6, "DS_CNT": 2, "MIDS_CNT": 4, "DS_RATE": "33.3",
        "PERIOD": "26.01.05~26.01.23", "WEEK_CONT": "01월3주차",
    },
    {
        "STORE_CD": "46513", "YYYY_CD": "2026", "N_MNTH_CD": "01", "N_WEEK_CD": "04",
        "ITEM_CNT": 4, "DS_CNT": 4, "MIDS_CNT": 0, "DS_RATE": "100",
        "PERIOD": "26.01.12~26.01.30", "WEEK_CONT": "01월4주차",
    },
]

# 3. dsDetailTotal (종합 주차별)
dsDetailTotal = [
    {
        "N_WEEK_CD": "02", "DOIP_RATE": "76.4", "DOIP_SCORE": "-",
        "DS_RATE": "100", "DS_SCORE": "-",
        "TOT_SCORE": "-", "SUPP_PAY_AMT": "-",
    },
    {
        "N_WEEK_CD": "03", "DOIP_RATE": "86.6", "DOIP_SCORE": "-",
        "DS_RATE": "33.3", "DS_SCORE": "-",
        "TOT_SCORE": "-", "SUPP_PAY_AMT": "-",
    },
    {
        "N_WEEK_CD": "04", "DOIP_RATE": "88", "DOIP_SCORE": "-",
        "DS_RATE": "100", "DS_SCORE": "-",
        "TOT_SCORE": "-", "SUPP_PAY_AMT": "-",
    },
]

# 4. dsDetailMonth (월별 합계) - 화면 하단 "합 계" 행
dsDetailMonth = [
    {
        "DOIP_RATE": "84.7", "DOIP_SCORE": "80",
        "DS_RATE": "66.6", "DS_SCORE": "16",
        "TOT_SCORE": "96", "SUPP_PAY_AMT": "160000",
    },
]


def parse_period(period_str: str):
    """'25.12.29~26.01.16' → ('2025-12-29', '2026-01-16')"""
    if not period_str or "~" not in period_str:
        return "", ""
    parts = period_str.split("~")
    def to_date(s):
        s = s.strip()
        parts = s.split(".")
        if len(parts) == 3:
            yy, mm, dd = parts
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

    print(f"[1/3] 주차별 현황 저장 ({len(dsList)}주차)...")

    for i, ds_row in enumerate(dsList):
        conv_row = dsConvenienceList[i] if i < len(dsConvenienceList) else {}
        detail_row = dsDetailTotal[i] if i < len(dsDetailTotal) else {}

        week_no = int(ds_row["N_WEEK_CD"])
        sta_dd, end_dd = parse_period(ds_row["PERIOD"])

        status_data = {
            "period": ds_row.get("PERIOD", ""),
            "week_cont": ds_row.get("WEEK_CONT", ""),
            "sta_dd": sta_dd,
            "end_dd": end_dd,
            # 도입률
            "item_cnt": safe_int(ds_row.get("ITEM_CNT")),
            "item_ad_cnt": safe_int(ds_row.get("ITEM_AD_CNT")),
            "doip_cnt": safe_int(ds_row.get("DOIP_CNT")),
            "midoip_cnt": safe_int(ds_row.get("MIDOIP_CNT")),
            "doip_rate": safe_float(ds_row.get("DOIP_RATE")),
            # 간편식/디저트 3일 발주
            "ds_item_cnt": safe_int(conv_row.get("ITEM_CNT")),
            "ds_cnt": safe_int(conv_row.get("DS_CNT")),
            "mids_cnt": safe_int(conv_row.get("MIDS_CNT")),
            "ds_rate": safe_float(conv_row.get("DS_RATE")),
            # 종합
            "doip_score": safe_int(detail_row.get("DOIP_SCORE")),
            "ds_score": safe_int(detail_row.get("DS_SCORE")),
            "tot_score": safe_int(detail_row.get("TOT_SCORE")),
            "supp_pay_amt": safe_int(detail_row.get("SUPP_PAY_AMT")),
        }

        repo.save_weekly_status(STORE_ID, MONTH_YM, week_no, status_data)
        print(f"  {status_data['week_cont']}: 도입률 {status_data['doip_rate']}%, "
              f"달성률 {status_data['ds_rate']}%")

    print(f"\n[2/3] 월별 합계 저장...")

    month_row = dsDetailMonth[0]
    monthly_data = {
        "doip_rate": safe_float(month_row.get("DOIP_RATE")),
        "doip_score": safe_int(month_row.get("DOIP_SCORE")),
        "ds_rate": safe_float(month_row.get("DS_RATE")),
        "ds_score": safe_int(month_row.get("DS_SCORE")),
        "tot_score": safe_int(month_row.get("TOT_SCORE")),
        "supp_pay_amt": safe_int(month_row.get("SUPP_PAY_AMT")),
        # 전체 합산
        "doip_item_cnt": sum(safe_int(d.get("ITEM_CNT")) for d in dsList),
        "doip_cnt": sum(safe_int(d.get("DOIP_CNT")) for d in dsList),
        "midoip_cnt": sum(safe_int(d.get("MIDOIP_CNT")) for d in dsList),
        "ds_item_cnt": sum(safe_int(d.get("ITEM_CNT")) for d in dsConvenienceList),
        "ds_cnt": sum(safe_int(d.get("DS_CNT")) for d in dsConvenienceList),
    }

    repo.save_monthly(STORE_ID, MONTH_YM, monthly_data)
    print(f"  종합 {monthly_data['tot_score']}점 → 예상지원금 {monthly_data['supp_pay_amt']:,}원")

    print(f"\n[3/3] 저장 확인...")
    weekly = repo.get_weekly_status(STORE_ID, MONTH_YM)
    monthly = repo.get_monthly_summary(STORE_ID, MONTH_YM)
    print(f"  주차별: {len(weekly)}건")
    print(f"  월별: {'있음' if monthly else '없음'}")

    if monthly:
        print(f"\n  ✓ 도입률 {monthly.get('doip_rate')}% ({monthly.get('doip_score')}점)")
        print(f"  ✓ 달성률 {monthly.get('ds_rate')}% ({monthly.get('ds_score')}점)")
        print(f"  ✓ 종합 {monthly.get('tot_score')}점 → {monthly.get('supp_pay_amt'):,}원")

    print("\n완료!")


if __name__ == "__main__":
    main()
