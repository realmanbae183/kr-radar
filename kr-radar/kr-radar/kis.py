"""
kis.py — 한국투자증권 오픈API (공식). 오늘의 국장 한 장에 들어가는 숫자를 여기서 받는다.

  필요한 것  GitHub Secrets 의 KIS_APP_KEY, KIS_APP_SECRET (한국투자증권에서 발급)
  받는 것    코스피·코스닥 지수와 장중 흐름, 투자자별 매매동향, 업종 등락률, 증시자금(예탁금·신용잔고), 환율·해외 지표
  주의       키와 접근토큰은 어떤 파일에도 쓰지 않는다 (저장소가 공개라서). 토큰은 실행할 때마다 한 번 받아 메모리에서만 쓴다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://openapi.koreainvestment.com:9443"
KST = ZoneInfo("Asia/Seoul")
DOW_KR = "월화수목금토일"
SRC = "한국투자증권 오픈API"

# (경로, 거래ID) — 한국투자증권 공식 예제(github.com/koreainvestment/open-trading-api) 기준
INDEX_PRICE = ("/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000")
INDEX_MINUTE = ("/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice", "FHKUP03500200")
INVESTOR_DAILY = ("/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market", "FHPTJ04040000")
INDEX_CATEGORY = ("/uapi/domestic-stock/v1/quotations/inquire-index-category-price", "FHPUP02140000")
MKTFUNDS = ("/uapi/domestic-stock/v1/quotations/mktfunds", "FHKST649100C0")
OVERSEAS_DAILY = ("/uapi/overseas-price/v1/quotations/inquire-daily-chartprice", "FHKST03030100")

# 해외 지표: (화면 이름, 그림, 시장구분, 종목코드 후보들) — 첫 번째로 값이 나오는 코드를 쓴다
OVERSEAS = [("미국 USD", "usa", "X", ["FX@KRW"]),
            ("달러인덱스", "earth", "N", [".DXY", "DXY", "DX-Y.NYB", "USDX"]),
            ("국제 금", "gold", "S", ["GCcv1", "GC", "NYGOLD", "XAUUSD", "GOLD"]),
            ("WTI", "oil", "S", ["CLcv1", "CL", "WTIF", "WTI", "NYMEX@CL"])]


def keys() -> tuple[str | None, str | None]:
    return os.getenv("KIS_APP_KEY"), os.getenv("KIS_APP_SECRET")


def num(x):
    try:
        s = str(x).replace(",", "").strip()
        return float(s) if s not in ("", "-", "None") else None
    except (TypeError, ValueError):
        return None


def pick(d: dict, *names):
    """여러 이름 후보 가운데 처음으로 값이 있는 것."""
    for n in names:
        if isinstance(d, dict) and d.get(n) not in (None, ""):
            return d[n]
    return None


class Client:
    def __init__(self, app_key: str, app_secret: str, cache_dir: str | None = None, post=None, get=None):
        self.key, self.secret = app_key, app_secret
        self.cache = os.path.join(cache_dir, "kis_token.json") if cache_dir else None      # 시험할 때만 쓴다
        self._post = post or (lambda url, body: requests.post(url, json=body, timeout=20).json())
        self._get = get or (lambda url, headers, params: requests.get(url, headers=headers, params=params, timeout=20).json())
        self._token = None

    def token(self) -> str:
        if self._token:
            return self._token
        try:
            if not self.cache:
                raise FileNotFoundError
            with open(self.cache, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("until", 0) > time.time() + 600:
                self._token = c["token"]
                return self._token
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        j = self._post(BASE + "/oauth2/tokenP", {"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
        if not j.get("access_token"):
            raise RuntimeError("한국투자증권 토큰 발급 실패: " + str(j.get("error_description") or j.get("msg1") or j)[:160])
        self._token = j["access_token"]
        if self.cache:
            os.makedirs(os.path.dirname(self.cache), exist_ok=True)
            with open(self.cache, "w", encoding="utf-8") as f:
                json.dump({"token": self._token, "until": time.time() + int(j.get("expires_in", 86400)) - 1800}, f)
        return self._token

    def call(self, api: tuple[str, str], params: dict) -> dict:
        path, tr = api
        h = {"content-type": "application/json; charset=utf-8", "authorization": "Bearer " + self.token(),
             "appkey": self.key, "appsecret": self.secret, "tr_id": tr, "custtype": "P"}
        j = self._get(BASE + path, h, params)
        time.sleep(0.08)                          # 초당 호출 제한 여유
        if str(j.get("rt_cd", "0")) != "0":
            raise RuntimeError(f"{tr}: {str(j.get('msg1') or j)[:120]}")
        return j


# ───────── 응답 → 화면용 숫자 ─────────
def parse_index(name: str, j: dict) -> dict:
    o = j.get("output") or {}
    v, p = num(pick(o, "bstp_nmix_prpr")), num(pick(o, "bstp_nmix_prdy_ctrt"))
    c = num(pick(o, "bstp_nmix_prdy_vrss"))
    if v is None or p is None or c is None:
        raise RuntimeError(f"{name} 지수 값을 못 읽음")
    c = abs(c) if p >= 0 else -abs(c)
    return {"n": name, "v": v, "c": c, "p": p, "hi": num(pick(o, "bstp_nmix_hgpr")) or v, "lo": num(pick(o, "bstp_nmix_lwpr")) or v, "line": []}


def parse_minute(j: dict, day: str | None = None) -> list[float]:
    rows = j.get("output2") or []
    pts = []
    for r in rows:
        d, t, v = str(pick(r, "stck_bsop_date") or ""), str(pick(r, "stck_cntg_hour", "bsop_hour") or ""), num(pick(r, "bstp_nmix_prpr"))
        if v is not None and (not day or not d or d == day):
            pts.append((d + t, v))
    pts.sort()
    return [round(v, 2) for _, v in pts]


def parse_investor(j: dict) -> list:
    rows = j.get("output") or []
    r = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
    out = []
    for label, key, pre in (("개인", "ind", "prsn"), ("외국인", "for", "frgn"), ("기관", "ins", "orgn")):
        v = num(pick(r, pre + "_ntby_tr_pbmn"))
        if v is None:
            raise RuntimeError("투자자 동향 값을 못 읽음")
        out.append([label, key, round(v / 100)])          # 백만원 → 억원
    return out


def parse_sectors(j: dict) -> list:
    rows = [r for r in (j.get("output2") or []) if num(pick(r, "bstp_nmix_prdy_ctrt")) is not None and pick(r, "hts_kor_isnm")]
    skip = ("종합", "대형주", "중형주", "소형주", "KOSPI", "코스피", "제조업")
    rows = [r for r in rows if not any(s in str(r["hts_kor_isnm"]) for s in skip)]
    rows.sort(key=lambda r: -(num(pick(r, "acml_tr_pbmn")) or 0))
    out = [[str(r["hts_kor_isnm"]).strip(), num(r["bstp_nmix_prdy_ctrt"]), round(num(pick(r, "acml_tr_pbmn")) or 0)] for r in rows[:10]]
    if len(out) < 6:
        raise RuntimeError(f"업종을 {len(out)}개밖에 못 읽음")
    return out


def parse_mktfunds(j: dict) -> list:
    rows = j.get("output") or []
    rows = rows if isinstance(rows, list) else [rows]
    if not rows:
        raise RuntimeError("증시자금 응답이 비어 있음")
    r, prev = rows[0], (rows[1] if len(rows) > 1 else {})
    out = []
    for label, icon, names, diff_names in (("고객예탁금", "cash", ("cust_dpmn_amt", "cust_dpmn"), ("cust_dpmn_amt_prdy_vrss", "cust_dpmn_prdy_vrss")),
                                           ("신용잔고", "graph", ("crdt_loan_rmnd", "crdt_rmnd", "crdt_loan_rmnd_amt"), ("crdt_loan_rmnd_prdy_vrss", "crdt_rmnd_prdy_vrss"))):
        v = num(pick(r, *names))
        if v is None:
            continue
        c = num(pick(r, *diff_names))
        if c is None and num(pick(prev, *names)) is not None:
            c = v - num(pick(prev, *names))
        out.append({"n": label, "i": icon, "v": f"{v:,.0f}억", "c": c or 0.0})
    if not out:
        raise RuntimeError("예탁금·신용잔고 항목 이름을 못 찾음")
    return out


def parse_overseas(name: str, icon: str, j: dict) -> dict:
    o = j.get("output1") or {}
    v, c, p = num(pick(o, "ovrs_nmix_prpr")), num(pick(o, "ovrs_nmix_prdy_vrss")), num(pick(o, "prdy_ctrt"))
    if v is None or v == 0 or c is None:
        raise RuntimeError(f"{name} 값을 못 읽음")
    sign = str(pick(o, "prdy_vrss_sign") or "")
    if p is None:
        p = c / (v - c) * 100 if v != c else 0.0
    if sign in ("4", "5") or p < 0:
        c, p = -abs(c), -abs(p)
    return {"n": name, "i": icon, "v": f"{v:,.2f}", "c": c, "p": round(p, 2)}


def fetch_today(cl: Client, now: datetime | None = None) -> dict:
    """오늘의 국장. 지수는 꼭 있어야 하고, 나머지는 빠지면 그 칸만 비운다."""
    now = now or datetime.now(KST)
    errs, idx = [], []
    day = now.strftime("%Y%m%d")
    for code, name in (("0001", "코스피"), ("1001", "코스닥")):
        ix = parse_index(name, cl.call(INDEX_PRICE, {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code}))
        try:
            ix["line"] = parse_minute(cl.call(INDEX_MINUTE, {"FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0", "FID_INPUT_ISCD": code,
                                                             "FID_INPUT_HOUR_1": "300", "FID_PW_DATA_INCU_YN": "N"}))
            if ix["line"]:
                ix["hi"], ix["lo"] = max(ix["hi"], max(ix["line"])), min(ix["lo"], min(ix["line"]))
        except Exception as e:
            errs.append(f"{name} 장중 흐름: {e}")
        idx.append(ix)
    out = {"src": SRC, "date": f"{now.month}월 {now.day}일", "dow": DOW_KR[now.weekday()], "day": now.strftime("%Y-%m-%d"),
           "closed": not (now.weekday() < 5 and "0900" <= now.strftime("%H%M") < "1535"),
           "idx": idx, "glob": [], "mood": [], "sec": [], "flow": [], "sec_label": "거래대금 큰 코스피 업종", "errors": errs}
    try:
        out["flow"] = parse_investor(cl.call(INVESTOR_DAILY, {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001", "FID_INPUT_DATE_1": day,
                                                              "FID_INPUT_ISCD_1": "KSP", "FID_INPUT_DATE_2": day, "FID_INPUT_ISCD_2": "0001"}))
    except Exception as e:
        errs.append(f"투자자 동향: {e}")
    try:
        out["sec"] = parse_sectors(cl.call(INDEX_CATEGORY, {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001", "FID_COND_SCR_DIV_CODE": "20214",
                                                            "FID_MRKT_CLS_CODE": "K", "FID_BLNG_CLS_CODE": "0"}))
    except Exception as e:
        errs.append(f"업종: {e}")
    try:
        out["mood"] = parse_mktfunds(cl.call(MKTFUNDS, {"FID_INPUT_DATE_1": day}))
    except Exception as e:
        errs.append(f"증시자금: {e}")
    d1 = (now - timedelta(days=10)).strftime("%Y%m%d")
    for name, icon, mk, codes in OVERSEAS:
        last = None
        for code in codes:
            try:
                out["glob"].append(parse_overseas(name, icon, cl.call(OVERSEAS_DAILY, {"FID_COND_MRKT_DIV_CODE": mk, "FID_INPUT_ISCD": code,
                                   "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": day, "FID_PERIOD_DIV_CODE": "D"})))
                last = None
                break
            except Exception as e:
                last = e
        if last:
            errs.append(f"{name}: {last}")
    return out


def probe(out_dir: str) -> dict:
    """각 호출의 응답 원본을 state/probe/kis_*.txt 로 저장 (키·토큰은 응답에 들어 있지 않다)."""
    k, s = keys()
    res = {}
    if not (k and s):
        return {"kis": "키 없음 (KIS_APP_KEY, KIS_APP_SECRET)"}
    cl, now = Client(k, s), datetime.now(KST)
    day, d1 = now.strftime("%Y%m%d"), (now - timedelta(days=10)).strftime("%Y%m%d")
    calls = {"kis_index_kospi": (INDEX_PRICE, {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"}),
             "kis_minute_kospi": (INDEX_MINUTE, {"FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0", "FID_INPUT_ISCD": "0001", "FID_INPUT_HOUR_1": "300", "FID_PW_DATA_INCU_YN": "N"}),
             "kis_investor": (INVESTOR_DAILY, {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001", "FID_INPUT_DATE_1": day, "FID_INPUT_ISCD_1": "KSP", "FID_INPUT_DATE_2": day, "FID_INPUT_ISCD_2": "0001"}),
             "kis_category": (INDEX_CATEGORY, {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001", "FID_COND_SCR_DIV_CODE": "20214", "FID_MRKT_CLS_CODE": "K", "FID_BLNG_CLS_CODE": "0"}),
             "kis_mktfunds": (MKTFUNDS, {"FID_INPUT_DATE_1": day})}
    for _, _, mk, codes in OVERSEAS:
        for code in codes:
            calls[f"kis_ovs_{mk}_{code.replace('@', '_').replace('.', '_')}"] = (OVERSEAS_DAILY, {"FID_COND_MRKT_DIV_CODE": mk, "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": day, "FID_PERIOD_DIV_CODE": "D"})
    for name, (api, params) in calls.items():
        try:
            h = {"content-type": "application/json; charset=utf-8", "authorization": "Bearer " + cl.token(), "appkey": k, "appsecret": s, "tr_id": api[1], "custtype": "P"}
            r = requests.get(BASE + api[0], headers=h, params=params, timeout=20)
            time.sleep(0.1)
            with open(os.path.join(out_dir, name + ".txt"), "w", encoding="utf-8") as f:
                f.write(f"API: {api[0]} ({api[1]})\nPARAMS: {json.dumps(params, ensure_ascii=False)}\nSTATUS: {r.status_code}\n\n{r.text[:40_000]}")
            res[name] = {"status": r.status_code, "bytes": len(r.content)}
        except Exception as e:
            res[name] = {"error": str(e)[:160].replace(k, "***").replace(s, "***")}
    return res
