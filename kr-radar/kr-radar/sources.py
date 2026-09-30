"""
sources.py — 바깥에서 데이터를 받아오는 곳 (네이버 금융 + DART 전자공시)

원칙: 받아오기에 '실패'한 것과 '값이 없는' 것을 절대 섞지 않는다.
  - 성공하면 Fetch(ok=True, data=...)
  - 실패하면 Fetch(ok=False, error="이유")   ← 화면에 '확인 불가'로 표시됨
미장 13F에서 "20곳 성공, 종목 0개" 모순이 났던 원인이 이걸 섞은 것이었다.
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

import config as C

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@dataclass
class Fetch:
    ok: bool
    data: Any = None
    error: str = ""


# ─────────────────────────────────────────────
# 공통 도우미
# ─────────────────────────────────────────────
_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
        _local.s = s
    return s


def get_text(url: str, referer: str | None = None, cookies: dict | None = None, tries: int = 3) -> str:
    """페이지를 받아서 글자로 돌려준다. 네이버 금융은 옛날 한글 인코딩(EUC-KR)을 쓰는 페이지가 있어서 둘 다 시도."""
    last = None
    for i in range(tries):
        try:
            h = {"Referer": referer} if referer else {}
            r = _session().get(url, headers=h, cookies=cookies, timeout=15)
            r.raise_for_status()
            time.sleep(C.REQUEST_SLEEP)
            raw = r.content
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("cp949", errors="replace")
        except Exception as e:  # 네트워크 오류는 잠깐 쉬고 다시
            last = e
            time.sleep(1.0 + i)
    raise RuntimeError(f"요청 실패: {url} ({last})")


def to_num(x) -> float | None:
    """'1,234', '+12,345', '-3.2%', '12.5배' 같은 글자를 숫자로. 못 바꾸면 None."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        if pd.isna(x):
            return None
        return float(x)
    s = str(x).strip()
    if s in ("", "-", "N/A", "nan", "NaN", "None"):
        return None
    s = re.sub(r"[,%배원\s]", "", s)
    s = s.replace("+", "")
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(\.\d+)?", s)
        return float(m.group()) if m else None


def parse_korean_money_eok(text: str) -> float | None:
    """'475조 3,285억원' → 4,753,285 (억 단위)."""
    t = re.sub(r"[\s,]", "", str(text))
    jo = re.search(r"(\d+)조", t)
    eok = re.search(r"(\d+)억", t)
    if not jo and not eok:
        return to_num(t)
    return (int(jo.group(1)) * 10000 if jo else 0) + (int(eok.group(1)) if eok else 0)


# ─────────────────────────────────────────────
# ① 유니버스
# ─────────────────────────────────────────────
ETF_BRANDS = ("KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL", "HANARO", "KOSEF", "ARIRANG", "PLUS",
              "KIWOOM", "TIMEFOLIO", "WON", "BNK", "마이다스", "히어로즈", "파워", "TREX", "FOCUS", "1Q",
              "UNICORN", "VITA", "에셋플러스", "KCGI", "DAISHIN343", "ITF", "마이티", "HK", "TRUSTON")


def parse_code_name_links(html: str) -> list[tuple[str, str]]:
    """페이지 안의 종목 링크(<a href="...code=005930">삼성전자</a>)를 순서대로 뽑는다."""
    out, seen = [], set()
    for code, name in re.findall(r'href="[^"]*?/item/main\.n(?:aver|hn)\?code=([0-9A-Z]{6})"[^>]*>\s*([^<]+?)\s*</a>', html):
        if code not in seen and name.strip():
            seen.add(code)
            out.append((code, name.strip()))
    return out


def fetch_kospi200() -> Fetch:
    """코스피200 구성종목 (네이버 금융 코스피200 편입종목 페이지, 한 페이지 10개 × 20페이지)."""
    items: list[tuple[str, str]] = []
    try:
        for p in range(1, 25):
            html = get_text(f"https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page={p}")
            got = parse_code_name_links(html)
            new = [g for g in got if g[0] not in {c for c, _ in items}]
            if not new:
                break
            items += new
    except Exception as e:
        return Fetch(False, items, f"코스피200 명단 조회 실패: {e}")
    if len(items) < 150:
        return Fetch(False, items, f"코스피200 명단이 {len(items)}개밖에 안 나옴 (페이지 구조 변경 의심)")
    return Fetch(True, items)


def parse_marcap_page(html: str) -> list[dict]:
    """시가총액 순위 페이지 한 장 → [{code, name, marcap(억)}].
    표 안의 종목 링크(class="tltle")만 쓴다 — 옆 '인기검색종목' 링크가 섞이지 않게."""
    links, seen = [], set()
    for tag, name in re.findall(r'(<a\s[^>]*class="tltle"[^>]*>)\s*([^<]+?)\s*</a>', html):
        m = re.search(r"code=([0-9A-Z]{6})", tag)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            links.append((m.group(1), name.strip()))
    try:
        tables = pd.read_html(io.StringIO(html), match="시가총액")
    except ValueError:
        tables = []
    caps: dict[str, float] = {}
    for t in tables:
        cols = [str(c[-1] if isinstance(c, tuple) else c) for c in t.columns]
        t.columns = cols
        if "종목명" in cols and "시가총액" in cols:
            for _, row in t.dropna(subset=["종목명"]).iterrows():
                v = to_num(row["시가총액"])
                if v is not None:
                    caps[str(row["종목명"]).strip()] = v
    if not links:                       # 클래스 이름이 바뀐 경우: 표 영역 안의 모든 종목 링크를 사용
        start = html.find("type_2")
        links = parse_code_name_links(html[start:] if start >= 0 else html)
    return [{"code": c, "name": n, "marcap": caps.get(n)} for c, n in links]


def _snippet(text: str, n: int = 160) -> str:
    return re.sub(r"\s+", " ", text or "")[:n]


def fetch_marcap_json(market: str, pages: int) -> Fetch:
    """네이버 모바일 증권 API (JSON). market='KOSPI' 또는 'KOSDAQ'. 한 페이지 60종목."""
    rows: list[dict] = []
    try:
        for p in range(1, pages + 1):
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={p}&pageSize=60"
            r = _session().get(url, headers={"Referer": "https://m.stock.naver.com/"}, timeout=15)
            time.sleep(C.REQUEST_SLEEP)
            if r.status_code != 200:
                return Fetch(False, rows, f"JSON API {r.status_code}: {_snippet(r.text)}")
            try:
                j = r.json()
            except ValueError:
                return Fetch(False, rows, f"JSON이 아닌 응답: {_snippet(r.text)}")
            stocks = j.get("stocks") or []
            if not stocks:
                break
            for it in stocks:
                code = it.get("itemCode") or it.get("symbolCode")
                name = it.get("stockName") or it.get("stockNameEng")
                if code and name:
                    rows.append({"code": str(code), "name": str(name).strip(), "marcap": parse_korean_money_eok(it.get("marketValue"))
                                 if it.get("marketValue") not in (None, "") else None})
    except Exception as e:
        return Fetch(False, rows, f"JSON API 조회 오류: {e}")
    if not rows:
        return Fetch(False, rows, "JSON API에서 종목을 하나도 못 읽음")
    return Fetch(True, rows)


def fetch_marcap_rank(sosok: int, pages: int) -> Fetch:
    """sosok=0 코스피, 1 코스닥. JSON API를 먼저 쓰고, 안 되면 HTML 페이지를 읽는다."""
    market = "KOSPI" if sosok == 0 else "KOSDAQ"
    per_page = 60
    need_pages = max(1, -(-pages * 50 // per_page))         # HTML 50개짜리 페이지 수 → JSON 60개짜리 페이지 수
    j = fetch_marcap_json(market, need_pages)
    if j.ok:
        return j
    rows: list[dict] = []
    err_html = ""
    try:
        for p in range(1, pages + 1):
            html = get_text(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={p}")
            got = parse_marcap_page(html)
            if not got and p == 1:
                err_html = f"HTML 페이지에서 종목을 못 읽음 (받은 글자 {len(html)}자, 앞부분: {_snippet(html, 200)})"
                break
            rows += got
    except Exception as e:
        err_html = f"HTML 조회 실패: {e}"
    if rows:
        return Fetch(True, rows)
    return Fetch(False, rows, f"[JSON] {j.error} / [HTML] {err_html}")


# ─────────────────────────────────────────────
# 시세 (일봉)
# ─────────────────────────────────────────────
def parse_fchart(xml: str) -> pd.DataFrame:
    rows = re.findall(r'<item data="(.*?)"\s*/>', xml)
    recs = []
    for r in rows:
        p = r.split("|")
        if len(p) < 6:
            continue
        recs.append([p[0]] + [to_num(v) for v in p[1:6]])
    df = pd.DataFrame(recs, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    if df.empty:
        return df
    df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d")
    df = df.set_index("Date").sort_index()
    # 거래정지 등으로 시가가 0인 날은 종가로 채움
    for c in ("Open", "High", "Low"):
        df.loc[df[c].fillna(0) <= 0, c] = df["Close"]
    return df.dropna(subset=["Close"])


def fetch_prices(code: str, count: int = C.PRICE_BARS) -> Fetch:
    try:
        xml = get_text(f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0")
        df = parse_fchart(xml)
        if len(df) < 60:
            return Fetch(False, df, f"일봉이 {len(df)}개뿐 (상장 직후이거나 조회 실패)")
        return Fetch(True, df)
    except Exception as e:
        return Fetch(False, None, str(e))


# ─────────────────────────────────────────────
# 종목 메인 페이지 (당좌비율, 목표주가, 투자의견, 업종, 시가총액)
# ─────────────────────────────────────────────
def _flatten_period_table(df: pd.DataFrame) -> tuple[list[str], list[dict]]:
    """
    네이버/와이즈리포트 재무 표는 머리글이 2~3줄이다.
    첫 칸 = 항목 이름, 나머지 칸 = 기간('2024/12', '2024.12', '(E)'면 추정치).
    → labels, columns=[{period, est, annual}] 로 정리.
    """
    labels = [str(v).strip() for v in df.iloc[:, 0].tolist()]
    cols = []
    for c in df.columns[1:]:
        parts = [str(x) for x in (c if isinstance(c, tuple) else (c,))]
        joined = " ".join(parts)
        m = re.search(r"(\d{4})[./](\d{2})", joined)
        period = f"{m.group(1)}/{m.group(2)}" if m else ""
        cols.append({
            "period": period,
            "est": "(E)" in joined,
            "annual": ("분기" not in joined),
        })
    return labels, cols


LABEL_KEYS = [
    # (정규화 키, 표에 나오는 이름 패턴) — 위에서부터 먼저 맞는 것 사용
    ("op_margin", r"^영업이익률"),
    ("net_margin", r"^순이익률"),
    ("op", r"^영업이익(\(발표기준\))?$"),
    ("sales", r"^매출액"),
    ("ni_ctrl", r"^(지배주주순이익|당기순이익\(지배\))"),
    ("ni", r"^당기순이익$"),
    ("assets", r"^자산총계"),
    ("liab", r"^부채총계"),
    ("equity", r"^자본총계$"),
    ("ocf", r"^영업활동(으로인한)?현금흐름"),
    ("fcf", r"^FCF"),
    ("roe", r"^ROE"),
    ("roa", r"^ROA"),
    ("debt_ratio", r"^부채비율"),
    ("quick_ratio", r"^당좌비율"),
    ("reserve_ratio", r"^(자본)?유보율"),
    ("eps", r"^EPS"),
    ("per", r"^PER"),
    ("bps", r"^BPS"),
    ("pbr", r"^PBR"),
    ("dps", r"^(현금)?DPS|^주당배당금"),
    ("shares", r"^발행주식수"),
]


def _label_key(label: str) -> str | None:
    lab = re.sub(r"\s+", "", label)
    for key, pat in LABEL_KEYS:
        if re.search(pat, lab):
            return key
    return None


def parse_period_table(df: pd.DataFrame) -> dict:
    """재무 표 하나 → {"annual":[{period, est, v:{key:val}}], "quarter":[...]}"""
    labels, cols = _flatten_period_table(df)
    out = {"annual": [], "quarter": []}
    for j, col in enumerate(cols):
        if not col["period"]:
            continue
        vals = {}
        for i, lab in enumerate(labels):
            key = _label_key(lab)
            if key and key not in vals:
                v = to_num(df.iat[i, j + 1])
                if v is not None:
                    vals[key] = v
        out["annual" if col["annual"] else "quarter"].append({"period": col["period"], "est": col["est"], "v": vals})
    return out


def parse_main_page(html: str) -> dict:
    res: dict[str, Any] = {}
    # 업종명
    m = re.search(r"업종명\s*:\s*<a[^>]*>([^<]+)</a>", html)
    res["sector"] = m.group(1).strip() if m else None
    # 시가총액 (예: 475조 3,285억원)
    m = re.search(r'id="_market_sum"[^>]*>(.*?)</em>', html, re.S)
    if m:
        res["marcap"] = parse_korean_money_eok(re.sub(r"<[^>]+>", "", m.group(1)) + "억")
    # 투자의견 / 목표주가
    try:
        t = pd.read_html(io.StringIO(html), match="목표주가")[0]
        cell = str(t.iloc[0, 1])
        nums = [to_num(x) for x in re.findall(r"[\d,]+\.?\d*", cell)]
        nums = [n for n in nums if n is not None]
        res["target_price"] = nums[-1] if nums and nums[-1] > 100 else None
        op = re.search(r"(적극매수|매수|중립|매도|Buy|Hold|Sell)", cell)
        res["opinion"] = op.group(1) if op else None
    except (ValueError, IndexError):
        res["target_price"], res["opinion"] = None, None
    # 기업실적분석 표 (당좌비율 등)
    try:
        t = pd.read_html(io.StringIO(html), match="당좌비율")[0]
        res["perf"] = parse_period_table(t)
    except (ValueError, IndexError):
        res["perf"] = None
    return res


def fetch_main_pc(code: str) -> Fetch:
    try:
        html = get_text(f"https://finance.naver.com/item/main.naver?code={code}")
        d = parse_main_page(html)
        if d.get("perf") is None and d.get("sector") is None:
            return Fetch(False, d, "종목 메인 페이지에서 아무것도 못 읽음 (구조 변경 의심)")
        return Fetch(True, d)
    except Exception as e:
        return Fetch(False, None, str(e))


# ─────────────────────────────────────────────
# 요약 재무제표 (와이즈리포트 = 네이버 '종목분석' 탭의 원본)
# ─────────────────────────────────────────────
_enc = {"v": None, "lock": threading.Lock()}


def _encparam() -> str:
    with _enc["lock"]:
        if _enc["v"] is None:
            html = get_text("https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd=005930")
            m = re.search(r"encparam\s*:\s*'(.*?)'", html)
            if not m:
                raise RuntimeError("encparam(접속 열쇠)을 못 찾음 — 와이즈리포트 구조 변경 의심")
            _enc["v"] = m.group(1)
        return _enc["v"]


def parse_fin_summary(html: str) -> dict:
    tables = pd.read_html(io.StringIO(html))
    for t in tables:
        first = " ".join(str(x) for x in (t.columns[0] if isinstance(t.columns[0], tuple) else (t.columns[0],)))
        if "주요재무정보" in first or t.iloc[:, 0].astype(str).str.contains("매출액|영업이익").any():
            parsed = parse_period_table(t)
            if parsed["annual"] or parsed["quarter"]:
                return parsed
    raise RuntimeError("요약 재무표를 못 찾음")


def fetch_fin_summary(code: str, freq: str) -> Fetch:
    """freq='Y' 연간, 'Q' 분기."""
    try:
        enc = _encparam()
        url = (f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?cmp_cd={code}"
               f"&fin_typ=0&freq_typ={freq}&encparam={enc}")
        html = get_text(url, referer=f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}")
        d = parse_fin_summary(html)
        rows = d["annual"] + d["quarter"]
        if not rows:
            return Fetch(False, d, "재무표가 비어 있음")
        # 분기 요청이면 전부 분기로 표시 (표 머리글에 '분기'가 없을 수도 있어서)
        if freq == "Q":
            d = {"annual": [], "quarter": rows}
        else:
            d = {"annual": rows, "quarter": []}
        return Fetch(True, d)
    except Exception as e:
        if "encparam" in str(e):
            _enc["v"] = None
        return Fetch(False, None, str(e))


# ─────────────────────────────────────────────
# 투자자별 매매 (기관·외국인 순매매량, 최근 20일)
# ─────────────────────────────────────────────
def parse_frgn(html: str) -> pd.DataFrame:
    tables = pd.read_html(io.StringIO(html))
    for t in tables:
        flat = [" ".join(str(x) for x in (c if isinstance(c, tuple) else (c,))) for c in t.columns]
        if any("기관" in f for f in flat) and any("외국인" in f for f in flat) and any("날짜" in f for f in flat) \
                and any("종가" in f for f in flat) and len(t.columns) >= 7:
            t = t.copy()
            t.columns = flat
            date_col = next(f for f in flat if "날짜" in f)
            close_col = next(f for f in flat if "종가" in f)
            inst_col = next(f for f in flat if "기관" in f)
            fr_col = next(f for f in flat if "외국인" in f and "순매매" in f) if any(
                "외국인" in f and "순매매" in f for f in flat) else [f for f in flat if "외국인" in f][0]
            out = pd.DataFrame({
                "date": pd.to_datetime(t[date_col].astype(str).str.strip(), format="%Y.%m.%d", errors="coerce"),
                "close": t[close_col].map(to_num),
                "inst": t[inst_col].map(to_num),
                "foreign": t[fr_col].map(to_num),
            }).dropna(subset=["date"])
            if len(out):
                return out.sort_values("date").reset_index(drop=True)
    raise RuntimeError("기관/외국인 표를 못 찾음")


def fetch_flows_pc(code: str) -> Fetch:
    try:
        html = get_text(f"https://finance.naver.com/item/frgn.naver?code={code}",
                        referer=f"https://finance.naver.com/item/main.naver?code={code}")
        df = parse_frgn(html)
        return Fetch(True, df)
    except Exception as e:
        return Fetch(False, None, str(e))


# ─────────────────────────────────────────────
# 네이버 모바일 증권 API (JSON) + 와이즈리포트 기업개요
#   2026-09-30 첫 실행에서 finance.naver.com PC 페이지(종목 메인·투자자별 매매)가
#   GitHub 서버에서 전부 실패(표 없음 / 410)해서, 실제로 성공한 곳(모바일 API, 와이즈리포트)으로 옮김.
#   응답 모양은 확인 전이라 여러 모양을 모두 받아들이도록 넓게 해석한다.
# ─────────────────────────────────────────────
MOBILE = "https://m.stock.naver.com"
WISE = "https://navercomp.wisereport.co.kr/v2/company"


def get_json(url: str, referer: str = MOBILE + "/", tries: int = 2):
    last = None
    for i in range(tries):
        try:
            r = _session().get(url, headers={"Referer": referer, "Accept": "application/json, text/plain, */*"}, timeout=15)
            time.sleep(C.REQUEST_SLEEP)
            if r.status_code == 404:
                raise RuntimeError(f"404: {url}")
            r.raise_for_status()
            return r.json()
        except ValueError as e:
            raise RuntimeError(f"JSON 아님: {url} ({_snippet(r.text, 120)})") from e
        except Exception as e:
            last = e
            if "404" in str(e):
                break
            time.sleep(1.0 + i)
    raise RuntimeError(f"요청 실패: {url} ({last})")


def _walk(obj):
    """JSON 안의 모든 dict/list를 차례로 돌려준다."""
    stack = [obj]
    while stack:
        x = stack.pop()
        yield x
        if isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)


def _pick(d: dict, *patterns):
    """dict 키 중 정규식에 맞는 첫 값."""
    for pat in patterns:
        for k, v in d.items():
            if re.search(pat, str(k), re.I):
                return v
    return None


def _to_date(v):
    s = re.sub(r"\D", "", str(v or ""))[:8]
    return pd.to_datetime(s, format="%Y%m%d", errors="coerce") if len(s) == 8 else pd.NaT


def parse_flow_json(obj) -> pd.DataFrame:
    """{..., [{bizdate, foreignerPureBuyQuant, organPureBuyQuant, closePrice}, ...]} 모양 어디든 찾아서 표로."""
    best = None
    for x in _walk(obj):
        if isinstance(x, list) and x and isinstance(x[0], dict):
            keys = " ".join(x[0].keys())
            if re.search(r"foreign", keys, re.I) and re.search(r"organ|institution", keys, re.I):
                if best is None or len(x) > len(best):
                    best = x
    if not best:
        raise RuntimeError("기관/외국인 순매수 목록을 JSON에서 못 찾음")
    rows = []
    for it in best:
        rows.append({
            "date": _to_date(_pick(it, r"^bizdate$", r"date", r"^dt$")),
            "close": to_num(_pick(it, r"^closePrice$", r"close")),
            "inst": to_num(_pick(it, r"organ.*pure.*(buy|quant)", r"organ.*net", r"institution.*net", r"^organ")),
            "foreign": to_num(_pick(it, r"foreign(er)?.*pure.*(buy|quant)", r"foreign.*net", r"^foreign(er)?PureBuy")),
        })
    df = pd.DataFrame(rows).dropna(subset=["date"])
    if df.empty:
        raise RuntimeError("순매수 목록에 날짜가 없음")
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def parse_mobile_finance(obj) -> dict:
    """모바일 '재무' JSON (trTitleList + rowList) → {"annual":[...]} 같은 모양 (기간 표)."""
    titles, rows = None, None
    for x in _walk(obj):
        if isinstance(x, dict) and "rowList" in x and ("trTitleList" in x or "titleList" in x):
            titles, rows = x.get("trTitleList") or x.get("titleList"), x["rowList"]
            break
    if not titles or not rows:
        raise RuntimeError("재무 JSON에서 표(rowList)를 못 찾음")
    cols = []
    for t in titles:
        key = str(t.get("key") or t.get("title") or "")
        m = re.search(r"(\d{4})\.?(\d{2})", str(t.get("title") or key))
        cols.append({"key": key, "period": f"{m.group(1)}/{m.group(2)}" if m else "",
                     "est": str(t.get("isConsensus", "N")).upper() == "Y" or "(E)" in str(t.get("title"))})
    out = []
    for c in cols:
        if not c["period"]:
            continue
        vals = {}
        for r in rows:
            key = _label_key(str(r.get("title") or ""))
            if not key or key in vals:
                continue
            cell = (r.get("columns") or {}).get(c["key"])
            v = to_num(cell.get("value") if isinstance(cell, dict) else cell)
            if v is not None:
                vals[key] = v
        out.append({"period": c["period"], "est": c["est"], "v": vals})
    return out


def parse_wise_overview(html: str) -> dict:
    """와이즈리포트 기업개요(c1010001): 업종(WICS), 투자의견·목표주가."""
    res: dict[str, Any] = {}
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|\s+", " ", text)
    m = re.search(r"WICS\s*:\s*([^<]+?)\s*<", html) or re.search(r"WICS\s*:\s*(\S+)", text)
    if m:
        res["sector"] = m.group(1).strip()
    try:
        for t in pd.read_html(io.StringIO(html)):
            flat = " ".join(" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns)
            if "목표주가" in flat and "투자의견" in flat and len(t):
                cols = [(" ".join(map(str, c)) if isinstance(c, tuple) else str(c)) for c in t.columns]
                row = t.iloc[0]
                for c, v in zip(cols, row):
                    if "목표주가" in c:
                        res["target_price"] = to_num(v)
                    elif "투자의견" in c:
                        n = to_num(v)
                        res["opinion"] = _opinion_label(n) if n is not None else (str(v) if str(v) != "nan" else None)
                break
    except (ValueError, IndexError):
        pass
    return res


def _opinion_label(mean: float | None) -> str | None:
    """증권사 투자의견 평균(1 매도 ~ 5 적극매수) → 글자."""
    if mean is None:
        return None
    return "적극매수" if mean >= 4.5 else "매수" if mean >= 3.5 else "중립" if mean >= 2.5 else "매도"


def parse_integration(obj) -> dict:
    """모바일 종합정보: 시가총액, 컨센서스(목표주가·투자의견), 최근 매매동향(있으면)."""
    res: dict[str, Any] = {}
    for x in _walk(obj):
        if isinstance(x, dict) and ("code" in x or "key" in x) and "value" in x:
            k = f"{x.get('code', '')} {x.get('key', '')}"
            if re.search(r"marketValue|시총|시가총액", k):
                res["marcap"] = parse_korean_money_eok(x["value"])
    cons = obj.get("consensusInfo") if isinstance(obj, dict) else None
    if isinstance(cons, dict):
        res["target_price"] = to_num(_pick(cons, r"priceTarget", r"target"))
        res["opinion"] = _opinion_label(to_num(_pick(cons, r"recomm", r"opinion")))
    ind = _pick(obj, r"^industryName$", r"industryGroupKor") if isinstance(obj, dict) else None
    if ind:
        res["sector"] = str(ind)
    try:
        res["flows"] = parse_flow_json(obj)
    except RuntimeError:
        res["flows"] = None
    return res


def fetch_main(code: str) -> Fetch:
    """종목 기본정보 = 모바일 종합정보 + 와이즈리포트 기업개요 + 모바일 연간 재무(당좌비율)."""
    d: dict[str, Any] = {"sector": None, "marcap": None, "target_price": None, "opinion": None, "perf": None}
    errs = []
    try:
        integ = parse_integration(get_json(f"{MOBILE}/api/stock/{code}/integration"))
        d.update({k: v for k, v in integ.items() if v is not None and k != "flows"})
        d["_flows"] = integ.get("flows")
    except Exception as e:
        errs.append(f"종합정보: {e}")
    try:
        w = parse_wise_overview(get_text(f"{WISE}/c1010001.aspx?cmp_cd={code}", referer=f"{WISE}/c1010001.aspx?cmp_cd={code}"))
        for k, v in w.items():
            if v is not None and not d.get(k):
                d[k] = v
    except Exception as e:
        errs.append(f"기업개요: {e}")
    try:
        ann = parse_mobile_finance(get_json(f"{MOBILE}/api/stock/{code}/finance/annual"))
        d["perf"] = {"annual": ann, "quarter": []}
    except Exception as e:
        errs.append(f"연간재무(당좌비율): {e}")
    if d["perf"] is None and d["sector"] is None and d["target_price"] is None and d["marcap"] is None:
        return Fetch(False, d, " / ".join(errs) or "아무것도 못 읽음")
    d["_partial_errors"] = errs
    return Fetch(True, d)


FLOW_URLS = [
    MOBILE + "/api/stock/{code}/trend?pageSize=20",
    MOBILE + "/api/stock/{code}/trend",
]


def fetch_flows(code: str, integ_flows: pd.DataFrame | None = None) -> Fetch:
    """기관·외국인 순매수: 모바일 매매동향(20일) → 안 되면 종합정보 안의 최근 며칠치."""
    errs = []
    for tpl in FLOW_URLS:
        try:
            df = parse_flow_json(get_json(tpl.format(code=code)))
            if len(df) >= 5:
                return Fetch(True, df)
            errs.append(f"{len(df)}일치뿐")
        except Exception as e:
            errs.append(str(e)[:120])
    if integ_flows is not None and len(integ_flows) >= 3:
        return Fetch(True, integ_flows)
    return Fetch(False, None, " / ".join(errs))


def fetch_disclosures_mobile(code: str) -> Fetch:
    """DART 인증키가 없을 때 쓰는 예비: 모바일 공시 목록 (최근 20건)."""
    errs = []
    for url in (f"{MOBILE}/api/stock/{code}/disclosure?pageSize=20&page=1",
                f"{MOBILE}/api/stock/{code}/disclosure"):
        try:
            obj = get_json(url)
            items = []
            for x in _walk(obj):
                if isinstance(x, list) and x and isinstance(x[0], dict) and _pick(x[0], r"title"):
                    for it in x:
                        dt = _to_date(_pick(it, r"datetime", r"date", r"^dt$"))
                        items.append({"date": dt.strftime("%Y%m%d") if not pd.isna(dt) else "",
                                      "title": str(_pick(it, r"^title$", r"title") or "").strip(),
                                      "url": f"https://m.stock.naver.com/domestic/stock/{code}/notice"})
                    break
            cutoff = (datetime.now() - timedelta(days=C.DISCLOSURE_DAYS)).strftime("%Y%m%d")
            items = [i for i in items if i["title"] and (not i["date"] or i["date"] >= cutoff)]
            if items or obj:
                return Fetch(True, items)
        except Exception as e:
            errs.append(str(e)[:120])
    return Fetch(False, None, "네이버 공시 목록 조회 실패: " + " / ".join(errs))


PROBE_URLS = {
    "integration": MOBILE + "/api/stock/{code}/integration",
    "basic": MOBILE + "/api/stock/{code}/basic",
    "trend20": MOBILE + "/api/stock/{code}/trend?pageSize=20",
    "trend": MOBILE + "/api/stock/{code}/trend",
    "finance_annual": MOBILE + "/api/stock/{code}/finance/annual",
    "finance_quarter": MOBILE + "/api/stock/{code}/finance/quarter",
    "disclosure": MOBILE + "/api/stock/{code}/disclosure?pageSize=20&page=1",
    "wise_overview": WISE + "/c1010001.aspx?cmp_cd={code}",
    "pc_main": "https://finance.naver.com/item/main.naver?code={code}",
    "pc_frgn": "https://finance.naver.com/item/frgn.naver?code={code}",
    "marcap_kospi": MOBILE + "/api/stocks/marketValue/KOSPI?page=1&pageSize=5",
}


def probe(out_dir: str, codes=("005930", "247540")) -> dict:
    """각 주소의 실제 응답 원본을 파일로 저장 (Claude가 GitHub에서 직접 읽어 고칠 수 있게)."""
    os.makedirs(out_dir, exist_ok=True)
    summary = {"at": datetime.now().isoformat(timespec="seconds"), "results": {}}
    for code in codes:
        for name, tpl in PROBE_URLS.items():
            if name.startswith("marcap") and code != codes[0]:
                continue
            url = tpl.format(code=code)
            try:
                r = _session().get(url, headers={"Referer": MOBILE + "/"}, timeout=15, allow_redirects=True)
                time.sleep(C.REQUEST_SLEEP)
                raw = r.content
                try:
                    txt = raw.decode("utf-8")
                except UnicodeDecodeError:
                    txt = raw.decode("cp949", errors="replace")
                fn = f"{code}_{name}.txt"
                with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
                    f.write(f"URL: {url}\nFINAL: {r.url}\nSTATUS: {r.status_code}\nTYPE: {r.headers.get('content-type')}\n\n")
                    f.write(txt[:150_000])
                summary["results"][fn] = {"status": r.status_code, "final": r.url, "bytes": len(raw)}
            except Exception as e:
                summary["results"][f"{code}_{name}"] = {"error": str(e)[:200]}
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return summary


# ─────────────────────────────────────────────
# DART 전자공시 (인증키가 있을 때만)
# ─────────────────────────────────────────────
class Dart:
    def __init__(self, key: str | None):
        self.key = key or os.getenv("DART_API_KEY")
        self.corp: dict[str, str] = {}
        self.error = ""
        if not self.key:
            self.error = "DART 인증키 없음 (GitHub Secrets에 DART_API_KEY 등록 필요)"
            return
        try:
            r = _session().get("https://opendart.fss.or.kr/api/corpCode.xml",
                               params={"crtfc_key": self.key}, timeout=60)
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            xml = z.read(z.namelist()[0]).decode("utf-8")
            for corp, stock in re.findall(
                    r"<corp_code>(\d+)</corp_code>.*?<stock_code>\s*([0-9A-Z]{6})?\s*</stock_code>", xml, re.S):
                if stock:
                    self.corp[stock] = corp
            if not self.corp:
                self.error = "DART 회사 코드 목록이 비어 있음 (인증키 확인)"
        except Exception as e:
            self.error = f"DART 회사 코드 목록 조회 실패: {e}"

    @property
    def ok(self) -> bool:
        return bool(self.key) and bool(self.corp) and not self.error

    def disclosures(self, code: str) -> Fetch:
        if not self.ok:
            return Fetch(False, None, self.error)
        corp = self.corp.get(code)
        if not corp:
            return Fetch(False, None, "DART에서 이 종목의 회사 코드를 못 찾음")
        end = datetime.now()
        bgn = end - timedelta(days=C.DISCLOSURE_DAYS)
        try:
            r = _session().get("https://opendart.fss.or.kr/api/list.json", params={
                "crtfc_key": self.key, "corp_code": corp,
                "bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"), "page_count": 100,
            }, timeout=20)
            time.sleep(C.REQUEST_SLEEP)
            j = r.json()
            st = j.get("status")
            if st == "013":           # 조회된 데이터 없음 = 공시가 하나도 없음 (정상)
                return Fetch(True, [])
            if st != "000":
                return Fetch(False, None, f"DART 오류 {st}: {j.get('message')}")
            items = [{"date": it.get("rcept_dt"), "title": (it.get("report_nm") or "").strip(),
                      "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no')}"}
                     for it in j.get("list", [])]
            return Fetch(True, items)
        except Exception as e:
            return Fetch(False, None, f"DART 공시 조회 실패: {e}")
