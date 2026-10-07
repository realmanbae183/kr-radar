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

CATS = {"kr": "한국", "us": "미국", "earn": "실적", "ipo": "공모주", "expiry": "만기일", "closed": "휴장"}
PENDING = "2027년의 미국 물가·고용 발표일, 한국은행 금통위, 설 연휴 같은 휴장일은 공식 일정표가 나오면 추가해요. 미국 대형주 실적은 나스닥 실적 달력에서, 공모주는 한국거래소 공시에서 자동으로 받아요. 한국 기업 실적은 날짜가 확정·예고된 것만 넣어요. 배당락은 아직 없어요."


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


# ── 미국 대형주 실적 발표일: 나스닥의 날짜별 실적 발표 목록 ──
US_NAMES = {"NVDA": "엔비디아", "AAPL": "애플", "MSFT": "마이크로소프트", "AMZN": "아마존", "GOOGL": "알파벳(구글)", "GOOG": "알파벳(구글)", "META": "메타",
            "TSLA": "테슬라", "AVGO": "브로드컴", "TSM": "TSMC", "MU": "마이크론", "AMD": "AMD", "INTC": "인텔", "NFLX": "넷플릭스", "ORCL": "오라클",
            "ASML": "ASML", "QCOM": "퀄컴", "AMAT": "어플라이드 머티어리얼즈", "LRCX": "램리서치", "JPM": "JP모건", "BAC": "뱅크오브아메리카", "GS": "골드만삭스",
            "MS": "모건스탠리", "WFC": "웰스파고", "C": "씨티그룹", "BRK.B": "버크셔 해서웨이", "V": "비자", "MA": "마스터카드", "LLY": "일라이 릴리", "NVO": "노보 노디스크",
            "UNH": "유나이티드헬스", "JNJ": "존슨앤드존슨", "PFE": "화이자", "WMT": "월마트", "COST": "코스트코", "HD": "홈디포", "KO": "코카콜라", "PEP": "펩시코",
            "MCD": "맥도날드", "NKE": "나이키", "SBUX": "스타벅스", "DIS": "디즈니", "XOM": "엑슨모빌", "CVX": "셰브론", "BA": "보잉", "CAT": "캐터필러",
            "GE": "GE 에어로스페이스", "PLTR": "팔란티어", "CRM": "세일즈포스", "ADBE": "어도비", "CSCO": "시스코", "IBM": "IBM", "UBER": "우버", "COIN": "코인베이스",
            "DELL": "델", "SMCI": "슈퍼마이크로", "ARM": "ARM", "SNOW": "스노우플레이크", "PDD": "핀둬둬", "BABA": "알리바바", "TM": "도요타", "SONY": "소니"}
US_TOP = {"NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO", "TSM", "MU"}
US_CAP_MIN = 100e9            # 시가총액 1,000억 달러 이상이거나 위 목록에 있는 회사만


def parse_nasdaq_earnings(day: str, j: dict) -> list[dict]:
    """나스닥 실적 발표 목록(하루치) → 일정. 장 마감 뒤 발표는 한국 날짜로 다음 날 새벽."""
    out = []
    for r in ((j.get("data") or {}).get("rows") or []):
        sym = str(r.get("symbol", "")).upper()
        try:
            cap = float(str(r.get("marketCap", "")).replace("$", "").replace(",", "") or 0)
        except ValueError:
            cap = 0.0
        if sym not in US_NAMES and cap < US_CAP_MIN:
            continue
        name = US_NAMES.get(sym) or str(r.get("name", sym)).replace(" Inc.", "").replace(" Inc", "").replace(" Corporation", "").replace(", ", " ").strip()
        when = str(r.get("time", ""))
        if "after" in when:
            d, t = _kst(day, 16, 5)
            t, note = t, "미국 장 마감 뒤 발표 (한국 시간 새벽)"
        elif "pre" in when:
            d, t = _kst(day, 8, 0)
            note = "미국 개장 전 발표 (한국 시간 저녁)"
        else:
            d, t, note = day, "", "발표 시각 미정 (미국 날짜 기준)"
        q = str(r.get("fiscalQuarterEnding", ""))
        out.append({**_ev(d, f"{name} 실적 발표", "earn", 3 if sym in US_TOP else 2, t, note + (f" · {q} 분기" if q else ""), "나스닥 실적 달력"), "cap": cap, "sym": sym})
    out.sort(key=lambda e: -e["cap"])
    seen, keep = set(), []
    for e in out:
        base = e["sym"].replace("GOOG", "GOOGL") if e["sym"] in ("GOOG",) else e["sym"]
        if base in seen:
            continue
        seen.add(base)
        keep.append({k: v for k, v in e.items() if k not in ("cap", "sym")})
    return keep[:6]


def us_earnings(today: date, days: int = 35, get=None) -> list[dict]:
    if get is None:
        import requests
        ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
              "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
        get = lambda day: requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": day}, headers=ua, timeout=20).json()
    out, fails = [], 0
    for i in range(-1, days):
        d = today + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            out += parse_nasdaq_earnings(d.isoformat(), get(d.isoformat()))
        except Exception:
            fails += 1
            if fails >= 4:
                break
    if not out and fails:
        raise RuntimeError("나스닥 실적 달력을 받지 못함")
    return out


# ── 공모주 청약·상장 일정: 한국거래소 공시(KIND)의 공모기업 현황 표 ──
def parse_kind_ipo(html: str, start: date, end: date) -> list[dict]:
    import io
    import re
    import pandas as pd
    out = []
    for tb in pd.read_html(io.StringIO(html)):
        cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in tb.columns]
        name_c = next((c for c in cols if "회사" in c or "기업" in c or "종목" in c), None)
        sub_c = next((c for c in cols if "청약" in c), None)
        list_c = next((c for c in cols if "상장" in c and ("예정" in c or "일" in c) and "주선" not in c), None)
        if not name_c or not (sub_c or list_c):
            continue
        tb.columns = cols
        for _, r in tb.iterrows():
            name = re.sub(r"\s+", " ", str(r[name_c])).strip()
            if not name or name == "nan" or "스팩" in name or "기업인수목적" in name:
                continue
            if sub_c:
                ds = re.findall(r"(20\d\d)[-./](\d{1,2})[-./](\d{1,2})", str(r[sub_c]))
                if ds:
                    d0 = date(*map(int, ds[0]))
                    d1 = date(*map(int, ds[-1]))
                    if start <= d0 <= end:
                        out.append(_ev(d0.isoformat(), f"{name} 공모주 청약" + (" 시작" if d1 != d0 else ""), "ipo", 2, "",
                                       f"청약 기간 {d0.month}/{d0.day}" + (f" ~ {d1.month}/{d1.day}" if d1 != d0 else ""), "한국거래소 공시(KIND)"))
            if list_c:
                ds = re.findall(r"(20\d\d)[-./](\d{1,2})[-./](\d{1,2})", str(r[list_c]))
                if ds:
                    d0 = date(*map(int, ds[0]))
                    if start <= d0 <= end:
                        out.append(_ev(d0.isoformat(), f"{name} 신규 상장", "ipo", 2, "09:00", "상장 첫날은 가격 변동이 커요.", "한국거래소 공시(KIND)"))
    uniq = {(e["d"], e["title"]): e for e in out}
    return sorted(uniq.values(), key=lambda e: (e["d"], e["title"]))


def ipo_events(start: date, end: date) -> list[dict]:
    import requests
    form = {"method": "searchPubofrProgComSub", "currentPageSize": "100", "pageIndex": "1", "orderMode": "1", "orderStat": "D", "forward": "pubofrprogcom_sub",
            "searchCorpName": "", "fromDate": (start - timedelta(days=60)).isoformat(), "toDate": end.isoformat()}
    r = requests.post("https://kind.krx.co.kr/listinvstg/pubofrprogcom.do", data=form, timeout=25,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
                               "Referer": "https://kind.krx.co.kr/listinvstg/pubofrprogcom.do?method=searchPubofrProgComMain", "X-Requested-With": "XMLHttpRequest"})
    try:
        html = r.content.decode("utf-8")
    except UnicodeDecodeError:
        html = r.content.decode("cp949", errors="replace")
    return parse_kind_ipo(html, start, end)


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


def build(today: date | None = None, fred_key: str | None = None, cache_path: str | None = None, online: bool = False) -> dict:
    import json, os
    today = today or datetime.now(KST).date()
    start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)      # 지난달 1일부터
    end = today + timedelta(days=190)
    ev, auto, note = events(start, end), [], ""
    fred_key = fred_key if fred_key is not None else os.getenv("FRED_API_KEY")
    if fred_key and (online or fred_key == "test"):
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
    earn = []
    if online:                                                  # 실제 실행일 때만 (시험·견본에서는 인터넷을 안 쓴다)
        epath = cache_path.replace("fred_calendar", "us_earnings") if cache_path else None
        try:
            cached = None
            if epath and os.path.exists(epath):
                with open(epath, encoding="utf-8") as f:
                    cached = json.load(f)
            if cached and cached.get("day") == today.isoformat():
                earn = cached["events"]
            else:
                earn = us_earnings(today)
                if epath:
                    os.makedirs(os.path.dirname(epath), exist_ok=True)
                    with open(epath, "w", encoding="utf-8") as f:
                        json.dump({"day": today.isoformat(), "events": earn}, f, ensure_ascii=False)
        except Exception as e:
            note = (note + " / " if note else "") + f"미국 실적 일정 실패: {str(e)[:100]}"
        ipath = cache_path.replace("fred_calendar", "ipo") if cache_path else None
        try:
            cached = None
            if ipath and os.path.exists(ipath):
                with open(ipath, encoding="utf-8") as f:
                    cached = json.load(f)
            if cached and cached.get("day") == today.isoformat():
                ipo = cached["events"]
            else:
                ipo = ipo_events(today - timedelta(days=7), today + timedelta(days=60))
                if ipath:
                    with open(ipath, "w", encoding="utf-8") as f:
                        json.dump({"day": today.isoformat(), "events": ipo}, f, ensure_ascii=False)
            earn = earn + ipo
        except Exception as e:
            note = (note + " / " if note else "") + f"공모주 일정 실패: {str(e)[:100]}"
    ev = sorted(ev + earn, key=lambda x: (x["d"], x["t"] or "99", -x["imp"]))
    return {"today": today.isoformat(), "events": _merge(ev, auto), "cats": CATS, "pending": PENDING, "auto": len(auto), "earn_auto": len(earn), "note": note,
            "sources": "미국 연준 · 미국 노동통계국 · 미국 경제분석국 · 한국은행 · 한국거래소 · MSCI 공식 일정" + (" · 세인트루이스 연준 발표 달력" if auto else "")}
