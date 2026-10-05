"""
demo.py — 가상 종목 350개로 화면·계산을 점검하는 '샘플 데이터' 공급기.
종목 이름·가격·재무는 전부 지어낸 것이다. (실제 종목 아님)
실제 데이터 공급기(NaverProvider)와 똑같은 모양으로 돌려주므로,
계산·점수·웹페이지 코드는 실제 실행과 100% 같은 경로를 탄다.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import config as C
from sources import Fetch

A = ["버거", "치즈", "피클", "감자", "양상추", "케첩", "머스타드", "참깨", "패티", "베이컨", "양파", "토마토",
     "왕관", "망토", "금화", "황금", "로얄", "킹덤", "캐슬", "크라운"]
B = ["전자", "바이오", "화학", "반도체", "제약", "에너지", "소프트", "모빌리티", "건설", "금융지주", "증권",
     "중공업", "푸드", "엔터", "로보틱스", "배터리", "소재", "헬스케어", "통신", "게임"]
SECTORS = {"전자": "전자장비와기기", "바이오": "생물공학", "화학": "화학", "반도체": "반도체와반도체장비",
           "제약": "제약", "에너지": "에너지장비및서비스", "소프트": "소프트웨어", "모빌리티": "자동차부품",
           "건설": "건설", "금융지주": "은행", "증권": "증권", "중공업": "조선", "푸드": "식품",
           "엔터": "방송과엔터테인먼트", "로보틱스": "기계", "배터리": "전기장비", "소재": "철강",
           "헬스케어": "건강관리장비와용품", "통신": "다각화된통신서비스", "게임": "게임엔터테인먼트"}
SCENARIOS = ["uptrend_pullback", "downtrend_bounce", "reversal", "breakout", "sideways", "crash", "downtrend", "uptrend"]
DISC_OK = ["분기보고서", "임원ㆍ주요주주특정증권등소유상황보고서", "기업설명회(IR)개최(안내공시)", "현금ㆍ현물배당결정",
           "주식등의대량보유상황보고서", "자기주식취득결정", "무상증자결정"]
DISC_BAD = ["주요사항보고서(유상증자결정)", "주요사항보고서(전환사채권발행결정)", "횡령ㆍ배임혐의발생"]
DISC_WARN = ["최대주주변경", "소송등의제기ㆍ신청(일정금액이상의청구)"]


class DemoProvider:
    name = "demo"

    def __init__(self, n: int = 350, seed: int = 7):
        rnd = random.Random(seed)
        names = []
        for a in A:
            for b in B:
                names.append(a + b)
        rnd.shuffle(names)
        names = names[:n]
        self.stocks = []
        for i, nm in enumerate(names):
            code = f"B{i + 1:05d}"
            market = "KOSPI" if i < n * 200 // 350 else "KOSDAQ"
            suffix = next(b for b in B if nm.endswith(b))
            self.stocks.append({"code": code, "name": nm, "market": market, "sector": SECTORS[suffix],
                                "scenario": SCENARIOS[i % len(SCENARIOS)] if rnd.random() < 0.8 else rnd.choice(SCENARIOS),
                                "seed": seed * 1000 + i})
        self.by_code = {s["code"]: s for s in self.stocks}
        self._px: dict[str, pd.DataFrame] = {}
        # 시장 전체 흐름: 평소엔 잔잔하다가 가끔 며칠 급락 → 반등 (다 같이 빠지는 날을 만들기 위해)
        g = np.random.default_rng(seed)
        nb = C.HISTORY_BARS
        mkt = g.normal(0.0004, 0.007, nb)
        for st in list(g.integers(150, nb - 60, 9)) + [nb - 4]:
            st = int(st)
            mkt[st:st + 4] += g.uniform(-0.032, -0.018, len(mkt[st:st + 4]))
            mkt[st + 4:st + 12] += g.uniform(0.004, 0.012, len(mkt[st + 4:st + 12]))
        self.mkt = mkt
        self.days = pd.bdate_range(end=datetime(2026, 9, 29), periods=nb)

    def universe(self):
        return ([{"code": s["code"], "name": s["name"], "market": s["market"]} for s in self.stocks],
                ["샘플 데이터 — 가상 종목 350개 (실제 종목 아님)"])

    # ───── 시세 ─────
    def _gen_prices(self, s) -> pd.DataFrame:
        rng = np.random.default_rng(s["seed"])
        n = C.HISTORY_BARS
        sc = s["scenario"]
        vol = rng.uniform(0.013, 0.03) * (1.4 if "바이오" in s["sector"] or "생물" in s["sector"] else 1)
        drift = np.full(n, 0.0)
        cut = n - rng.integers(25, 60)
        if sc == "uptrend_pullback":
            drift[:n - 15] = 0.0022; drift[n - 15:] = -0.006
        elif sc == "uptrend":
            drift[:] = 0.0018
        elif sc == "downtrend":
            drift[:] = -0.0018
        elif sc == "downtrend_bounce":
            drift[:n - 6] = -0.0025; drift[n - 6:] = 0.012
        elif sc == "reversal":
            drift[:cut] = -0.002; drift[cut:] = 0.006
        elif sc == "breakout":
            drift[:n - 40] = 0.0; drift[n - 40:n - 8] = -0.001; drift[n - 8:] = 0.012
        elif sc == "crash":
            drift[:n - 30] = 0.0025; drift[n - 30:n - 22] = -0.04; drift[n - 22:] = 0.001
        else:
            drift[:] = 0.0
        ret = drift * 0.6 + rng.uniform(0.7, 1.5) * self.mkt + rng.normal(0, vol * 0.8, n)
        base = float(rng.choice([3000, 8000, 15000, 42000, 95000, 180000, 350000]))
        close = base * np.exp(np.cumsum(ret))
        tick = np.where(close > 50000, 100, np.where(close > 5000, 10, 1))
        close = np.round(close / tick) * tick
        op = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, vol / 3, n))
        hi = np.maximum(op, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
        lo = np.minimum(op, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
        v = rng.lognormal(12.5, 0.45, n) * (1 + 3 * (np.abs(ret) > vol * 1.8))
        days = self.days
        return pd.DataFrame({"Open": np.round(op), "High": np.round(hi), "Low": np.round(lo),
                             "Close": close, "Volume": np.round(v)}, index=days)

    def prices(self, code, bars=None):
        if code not in self._px:
            self._px[code] = self._gen_prices(self.by_code[code])
        df = self._px[code]
        return Fetch(True, df.tail(bars) if bars else df)

    def index(self, bars=None):
        c = 2600 * np.exp(np.cumsum(self.mkt))
        df = pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0}, index=self.days)
        return Fetch(True, df.tail(bars) if bars else df)

    # ───── 재무 ─────
    def _rng(self, code, salt):
        return np.random.default_rng(self.by_code[code]["seed"] + salt)

    def main(self, code):
        s = self.by_code[code]
        r = self._rng(code, 1)
        px = self.prices(code).data["Close"].iloc[-1]
        years = ["2022/12", "2023/12", "2024/12", "2025/12", "2026/12"]
        perf = {"annual": [{"period": y, "est": y == "2026/12", "v": {"quick_ratio": float(r.uniform(40, 260))}}
                           for y in years], "quarter": []}
        tp = float(px * r.uniform(0.85, 1.6)) if r.random() < 0.7 else None
        return Fetch(True, {"sector": s["sector"], "marcap": None, "target_price": tp,
                            "opinion": "매수" if tp and tp > px * 1.1 else ("중립" if tp else None), "perf": perf})

    def _fin_rows(self, code):
        s = self.by_code[code]
        r = self._rng(code, 2)
        px = self.prices(code).data["Close"].iloc[-1]
        shares = float(r.uniform(2e7, 6e8))
        marcap_eok = px * shares / 1e8
        sales0 = marcap_eok * r.uniform(0.3, 2.0)
        g = r.normal(0.08, 0.15)
        margin = r.normal(0.09, 0.08)
        if "생물" in s["sector"] and r.random() < 0.6:
            margin = -abs(margin) - 0.1
        annual = []
        sales = sales0
        for i, y in enumerate(["2022/12", "2023/12", "2024/12", "2025/12", "2026/12"]):
            sales *= (1 + g + r.normal(0, 0.06))
            m = margin + r.normal(0, 0.02)
            op = sales * m
            ni = op * r.uniform(0.6, 0.9)
            equity = marcap_eok * r.uniform(0.4, 1.2)
            liab = equity * r.uniform(0.2, 1.8)
            ocf = ni * r.uniform(0.4, 1.6) if ni > 0 else op * r.uniform(-0.5, 0.8)
            v = {"sales": sales, "op": op, "ni": ni, "ni_ctrl": ni, "equity": equity, "liab": liab,
                 "assets": equity + liab, "ocf": ocf, "op_margin": m * 100, "net_margin": ni / sales * 100,
                 "roe": ni / equity * 100, "debt_ratio": liab / equity * 100,
                 "eps": ni * 1e8 / shares, "bps": equity * 1e8 / shares, "shares": shares}
            annual.append({"period": y, "est": y == "2026/12", "v": v})
        if r.random() < 0.3:          # 증권사 추정치 없는 종목
            annual = annual[:-1]
        quarter = []
        qs = ["2025/03", "2025/06", "2025/09", "2025/12", "2026/03", "2026/06"]
        prev_op = {}
        for i, q in enumerate(qs):
            qsales = sales0 * (1 + g) ** (i / 4 + 3) / 4 * r.uniform(0.85, 1.15)
            m = margin + r.normal(0, 0.03) - (0.012 * i if r.random() < 0.15 else 0)
            quarter.append({"period": q, "est": False, "v": {"sales": qsales, "op": qsales * m, "op_margin": m * 100}})
        return {"annual": annual, "quarter": []}, {"annual": [], "quarter": quarter}

    def fin(self, code, freq):
        y, q = self._fin_rows(code)
        return Fetch(True, y if freq == "Y" else q)

    def flows(self, code, main=None):
        r = self._rng(code, 3)
        px = self.prices(code).data
        t = px.tail(20)
        bias = r.normal(0, 1)
        return Fetch(True, pd.DataFrame({
            "date": t.index, "close": t["Close"].to_numpy(),
            "inst": (r.normal(bias, 1.5, 20) * t["Volume"].to_numpy() * 0.05).round(),
            "foreign": (r.normal(bias, 1.5, 20) * t["Volume"].to_numpy() * 0.06).round(),
        }))

    def disclosures(self, code):
        r = self._rng(code, 4)
        items = []
        for k in range(int(r.integers(1, 6))):
            u = r.random()
            title = DISC_BAD[int(r.integers(0, len(DISC_BAD)))] if u < 0.07 else \
                DISC_WARN[int(r.integers(0, len(DISC_WARN)))] if u < 0.14 else DISC_OK[int(r.integers(0, len(DISC_OK)))]
            d = (datetime(2026, 9, 29) - timedelta(days=int(r.integers(1, 89)))).strftime("%Y%m%d")
            items.append({"date": d, "title": title, "url": "#demo"})
        return Fetch(True, items)
