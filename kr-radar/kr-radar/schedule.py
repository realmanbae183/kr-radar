"""
schedule.py — 증시캘린더 (버거2세 담당)

네이버를 쓰지 않는다. 날짜가 미리 공표되는 일정은 공식 일정표에서 옮겨 적고(아래 목록),
만기일처럼 규칙이 있는 일정은 계산한다. 해가 바뀌면 아래 목록만 새 일정표로 채우면 된다.

  출처  미국 FOMC        federalreserve.gov/monetarypolicy/fomccalendars.htm
        미국 물가·고용   bls.gov/schedule/news_release/ (cpi.htm, empsit.htm)
        미국 GDP·PCE     bea.gov/news/schedule
        한국 금통위      한국은행 통화정책방향 결정회의 일정
        MSCI 정기변경    msci.com 의 Index Review 일정표
        기업 실적        회사가 날짜를 공시·예고한 뒤에만 넣는다 (예상 날짜는 넣지 않음)
        휴장일           한국거래소·뉴욕증권거래소 휴장 규정
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

KST, ET = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")

# 미국 FOMC: 회의 둘째 날(결과 발표일, 미국 날짜). True = 경제전망·점도표가 같이 나오는 회의
FOMC = [("2026-09-16", True), ("2026-10-28", False), ("2026-12-09", True),
        ("2027-01-27", False), ("2027-03-17", True), ("2027-04-28", False), ("2027-06-09", True),
        ("2027-07-28", False), ("2027-09-15", True), ("2027-10-27", False), ("2027-12-08", True)]
# 미국 소비자물가(CPI): (발표일, 몇 월 치) — 미국 동부 08:30
US_CPI = [("2026-10-14", 9), ("2026-11-10", 10), ("2026-12-10", 11)]
# 미국 고용보고서: (발표일, 몇 월 치) — 미국 동부 08:30
US_JOBS = [("2026-11-06", 10), ("2026-12-04", 11)]
# 미국 GDP + 개인소비지출 물가(PCE): (발표일, GDP 설명, PCE 몇 월 치) — 미국 동부 08:30
US_GDP_PCE = [("2026-10-29", "3분기 GDP 속보치", 9), ("2026-11-25", "3분기 GDP 잠정치", 10), ("2026-12-23", "3분기 GDP 확정치", 11)]
# 기업 실적: (한국 날짜, 시각, 제목, 메모, 출처) — 날짜가 확정·예고된 것만
EARNINGS = [("2026-10-08", "", "삼성전자 3분기 잠정실적", "매출과 영업이익 잠정치만 먼저 나와요. 사업부별 숫자는 월말 확정실적에서.", "머니투데이 10월 5일 보도")]
# MSCI 정기변경: (한국 날짜, 제목, 메모)
MSCI = [("2026-11-12", "MSCI 11월 정기변경 발표", "한국 시간 12일 아침에 편입·편출 종목이 나와요."),
        ("2026-11-30", "MSCI 정기변경 적용 (장 마감 때 반영)", "12월 1일부터 새 구성. 30일 장 막판에 지수 따라가는 자금이 한꺼번에 움직여요."),
        ("2027-02-10", "MSCI 2월 정기변경 발표", "한국 시간 10일 아침에 편입·편출 종목이 나와요."),
        ("2027-02-26", "MSCI 정기변경 적용 (장 마감 때 반영)", "3월 1일부터 새 구성.")]
# 한국은행 금융통화위원회 통화정책방향 결정회의 (기준금리)
BOK = ["2026-10-22", "2026-11-26"]
# 한국 증시 휴장일
KR_CLOSED = [("2026-10-09", "한글날"), ("2026-12-25", "성탄절"), ("2026-12-31", "연말 휴장일"), ("2027-01-01", "신정")]
# 미국 증시 휴장·조기 폐장
US_CLOSED = [("2026-11-26", "추수감사절 휴장"), ("2026-11-27", "조기 폐장 (추수감사절 다음 날, 평소보다 3시간 일찍)"),
             ("2026-12-24", "조기 폐장 (성탄 전야, 평소보다 3시간 일찍)"), ("2026-12-25", "성탄절 휴장"), ("2027-01-01", "신정 휴장")]

CATS = {"kr": "한국", "us": "미국", "earn": "실적", "expiry": "만기일", "closed": "휴장"}
PENDING = "2027년의 미국 물가·고용 발표일, 한국은행 금통위, 설 연휴 같은 휴장일은 공식 일정표가 나오면 추가해요. 기업 실적은 회사가 날짜를 확정·예고한 것만 넣어요. 공모주·배당락은 아직 없어요."


def _kst(us_day: str, hh: int, mm: int) -> tuple[str, str]:
    """미국 동부 시각 → 한국 날짜·시각 (서머타임 자동 반영)."""
    y, m, d = map(int, us_day.split("-"))
    t = datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(KST)
    return t.strftime("%Y-%m-%d"), t.strftime("%H:%M")


def nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _ev(d, title, cat, imp, t="", note="", src=""):
    return {"d": d, "t": t, "title": title, "cat": cat, "imp": imp, "note": note, "src": src}


def events(start: date, end: date) -> list[dict]:
    """start ~ end 사이의 일정 (한국 날짜 기준, 날짜·시각 순)."""
    out = []
    for day, sep in FOMC:
        d, t = _kst(day, 14, 0)
        out.append(_ev(d, "미국 기준금리 결정 (FOMC)", "us", 3, t,
                       "경제전망·점도표가 함께 나오는 회의예요. 30분 뒤 의장 기자회견." if sep else "30분 뒤 의장 기자회견.", "미국 연준"))
        md, mt = _kst((date.fromisoformat(day) + timedelta(days=21)).isoformat(), 14, 0)      # 의사록은 회의 3주 뒤
        out.append(_ev(md, "미국 FOMC 의사록 공개", "us", 2, mt, f"{int(day[5:7])}월 회의에서 위원들이 나눈 이야기가 공개돼요.", "미국 연준 (회의 3주 뒤 공개 규정)"))
    for day, what, mon in US_GDP_PCE:
        d, t = _kst(day, 8, 30)
        out.append(_ev(d, f"미국 {what} · {mon}월 PCE 물가", "us", 3, t, "PCE는 연준이 가장 중요하게 보는 물가 지표예요.", "미국 경제분석국"))
    for d, tm, title, note, src in EARNINGS:
        out.append(_ev(d, title, "earn", 3, tm, note, src))
    for d, title, note in MSCI:
        out.append(_ev(d, title, "kr", 2, "", note, "MSCI"))
    for day, mon in US_CPI:
        d, t = _kst(day, 8, 30)
        out.append(_ev(d, f"미국 {mon}월 소비자물가 (CPI)", "us", 3, t, "물가가 예상보다 높으면 금리 인하 기대가 줄어요.", "미국 노동통계국"))
    for day, mon in US_JOBS:
        d, t = _kst(day, 8, 30)
        out.append(_ev(d, f"미국 {mon}월 고용보고서", "us", 3, t, "일자리 증가와 실업률이 나와요.", "미국 노동통계국"))
    for day in BOK:
        out.append(_ev(day, "한국은행 기준금리 결정 (금통위)", "kr", 3, "10:00", "오전에 결과 발표, 이어서 총재 기자간담회.", "한국은행"))
    closed_kr = {d for d, _ in KR_CLOSED}
    for day, why in KR_CLOSED:
        out.append(_ev(day, f"한국 증시 휴장 · {why}", "closed", 2, "", "", "한국거래소"))
    for day, why in US_CLOSED:
        out.append(_ev(day, f"미국 증시 {why}", "closed", 1, "", "", "뉴욕증권거래소"))
    us_off = {d for d, w in US_CLOSED if "휴장" in w and "조기" not in w}
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        prev = 12 if m == 1 else m - 1
        out.append(_ev(date(y, m, 1).isoformat(), f"한국 {prev}월 수출입동향", "kr", 2, "09:00", "반도체 수출이 얼마나 늘었는지가 핵심이에요.", "산업통상부 (매달 1일 발표)"))
        for dd, label in ((11, "1~10일"), (21, "1~20일")):
            x = date(y, m, dd)
            while x.weekday() >= 5 or x.isoformat() in {d for d, _ in KR_CLOSED}:
                x += timedelta(days=1)
            out.append(_ev(x.isoformat(), f"한국 {m}월 {label} 수출", "kr", 1, "09:00", "이달 수출 흐름을 미리 보는 속보치.", "관세청 (휴일이면 다음 평일)"))
        x = date(y, m, 1)
        while x.weekday() >= 5 or x.isoformat() in us_off:
            x += timedelta(days=1)
        d, t = _kst(x.isoformat(), 10, 0)
        out.append(_ev(d, f"미국 {prev}월 ISM 제조업지수", "us", 2, t, "50보다 높으면 제조업 경기가 확장 중이라는 뜻이에요.", "ISM (매달 첫 영업일)"))
        d = nth_weekday(y, m, 3, 2)                       # 둘째 목요일
        while d.isoformat() in closed_kr or d.weekday() >= 5:
            d -= timedelta(days=1)                        # 휴장이면 앞 거래일로
        quad = m in (3, 6, 9, 12)
        out.append(_ev(d.isoformat(), "선물·옵션 동시만기일" if quad else "옵션 만기일", "expiry", 2 if quad else 1, "15:20",
                       "주가지수 선물·옵션과 개별주식 선물·옵션이 한꺼번에 만기라 장 막판에 출렁일 수 있어요." if quad
                       else "코스피200 옵션 만기. 장 막판 변동이 커질 수 있어요.", "한국거래소 규정 (매달 둘째 목요일)"))
        if quad:
            out.append(_ev(nth_weekday(y, m, 4, 3).isoformat(), "미국 선물·옵션 동시만기일", "expiry", 1, "",
                           "미국 주가지수·개별주식 선물옵션이 같이 만기 (셋째 금요일).", "규정으로 계산"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    s, e = start.isoformat(), end.isoformat()
    return sorted((x for x in out if s <= x["d"] <= e), key=lambda x: (x["d"], x["t"] or "99", -x["imp"]))


# ── 미국 지표 발표일 자동 받기: 세인트루이스 연준(FRED)의 공식 발표 달력 ──
# (발표 이름에 들어 있는 말, 화면 제목, 미국 동부 발표 시각, 중요도, 한 줄 설명)
FRED_RELEASES = [
    ("Consumer Price Index", "미국 소비자물가 (CPI)", (8, 30), 3, "물가가 예상보다 높으면 금리 인하 기대가 줄어요."),
    ("Employment Situation", "미국 고용보고서", (8, 30), 3, "일자리 증가와 실업률이 나와요."),
    ("Gross Domestic Product", "미국 GDP", (8, 30), 3, "미국 경제가 얼마나 컸는지."),
    ("Personal Income and Outlays", "미국 PCE 물가", (8, 30), 3, "연준이 가장 중요하게 보는 물가 지표예요."),
    ("Producer Price Index", "미국 생산자물가 (PPI)", (8, 30), 2, "기업이 받는 가격. 소비자물가보다 먼저 움직여요."),
    ("Advance Monthly Sales for Retail", "미국 소매판매", (8, 30), 2, "미국 소비가 얼마나 튼튼한지."),
    ("Job Openings and Labor Turnover", "미국 구인건수 (JOLTS)", (10, 0), 2, "기업들이 사람을 얼마나 구하는지."),
    ("Industrial Production and Capacity", "미국 산업생산", (9, 15), 1, "공장이 얼마나 돌아가는지."),
    ("New Residential Construction", "미국 주택착공", (8, 30), 1, "집을 얼마나 새로 짓는지."),
    ("Surveys of Consumers", "미국 미시간대 소비자심리", (10, 0), 1, "소비자가 느끼는 경기와 물가 전망."),
]


def fred_events(key: str, start: date, end: date, get=None) -> list[dict]:
    """FRED 발표 달력에서 앞으로의 발표일까지 받아 일정으로 바꾼다. 실패하면 예외."""
    if get is None:
        import requests
        get = lambda url, params: requests.get(url, params=params, timeout=20).json()
    out, offset = [], 0
    while offset < 5000:
        j = get("https://api.stlouisfed.org/fred/releases/dates",
                {"api_key": key, "file_type": "json", "realtime_start": start.isoformat(), "realtime_end": end.isoformat(),
                 "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 1000, "offset": offset})
        rows = j.get("release_dates")
        if rows is None:
            raise RuntimeError(str(j.get("error_message") or j)[:200])
        for r in rows:
            name = str(r.get("release_name", ""))
            for needle, title, (hh, mm), imp, note in FRED_RELEASES:
                if needle.lower() in name.lower():
                    d, t = _kst(r["date"], hh, mm)
                    out.append({**_ev(d, title, "us", imp, t, note, "세인트루이스 연준 발표 달력"), "kind": needle})
                    break
        if len(rows) < 1000:
            break
        offset += 1000
    return out


def _merge(base: list[dict], auto: list[dict]) -> list[dict]:
    """손으로 넣은 일정이 우선. 자동으로 받은 것은 같은 날 같은 지표가 없을 때만 더한다."""
    keys = {"Consumer Price Index": "소비자물가", "Employment Situation": "고용보고서", "Gross Domestic Product": "GDP", "Personal Income and Outlays": "PCE"}
    have = {(e["d"], k) for e in base for k in keys.values() if k in e["title"]}
    seen, out = set(), list(base)
    for e in auto:
        k = keys.get(e.get("kind"), e["title"])
        if (e["d"], k) in have or (e["d"], k) in seen:
            continue
        seen.add((e["d"], k))
        out.append({x: v for x, v in e.items() if x != "kind"})
    return sorted(out, key=lambda x: (x["d"], x["t"] or "99", -x["imp"]))


def build(today: date | None = None, fred_key: str | None = None, cache_path: str | None = None) -> dict:
    import json, os
    today = today or datetime.now(KST).date()
    start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)      # 지난달 1일부터
    end = today + timedelta(days=190)
    ev, auto, note = events(start, end), [], ""
    fred_key = fred_key if fred_key is not None else os.getenv("FRED_API_KEY")
    if fred_key:
        try:
            cached = None
            if cache_path and os.path.exists(cache_path):
                with open(cache_path, encoding="utf-8") as f:
                    cached = json.load(f)
            if cached and cached.get("day") == today.isoformat():       # 하루에 한 번만 새로 받는다
                auto = cached["events"]
            else:
                auto = fred_events(fred_key, start, end)
                if cache_path:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump({"day": today.isoformat(), "events": auto}, f, ensure_ascii=False)
        except Exception as e:
            note = f"미국 지표 자동 받기 실패: {str(e)[:120]}"
    return {"today": today.isoformat(), "events": _merge(ev, auto), "cats": CATS, "pending": PENDING, "auto": len(auto), "note": note,
            "sources": "미국 연준 · 미국 노동통계국 · 미국 경제분석국 · 한국은행 · 한국거래소 · MSCI 공식 일정" + (" · 세인트루이스 연준 발표 달력" if auto else "")}
