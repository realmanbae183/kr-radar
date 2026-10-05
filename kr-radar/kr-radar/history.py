"""
history.py — 과거 성과표 만들기

"지금 이 상태였던 과거 날들에 사서 며칠 뒤 팔았다면?" 을 5년치 일봉으로 세어 표로 만든다.
레이더 화면의 '과거 N번 · 5일 승률 · 평균 수익'이 전부 이 표에서 나온다.
정식 실행 때마다 다시 계산하므로 숫자는 스스로 갱신된다.

세는 규칙
  사는 값  신호가 뜬 날 종가
  파는 값  5·10·20거래일 뒤 종가, 왕복 비용(COST_PCT) 차감
  승률     수익이 0보다 큰 비율
읽을 때 주의
  · 지금 살아남은 종목만 본 결과라 절대 수익은 실제보다 좋게 나온다. 기준선(아무 날 아무 종목)과의 차이로 읽을 것.
  · 시장이 급락한 날엔 수백 종목이 한꺼번에 신호를 낸다. 그래서 '횟수'와 함께 '서로 다른 날짜 수'도 같이 적는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import config as C
import indicators as I

KST = timezone(timedelta(hours=9))


def _fwd(c: np.ndarray, h: int) -> np.ndarray:
    r = np.full(len(c), np.nan)
    if len(c) > h:
        r[:len(c) - h] = (c[h:] / c[:len(c) - h] - 1) * 100 - C.COST_PCT
    return r


def _mae(c: np.ndarray, lo: np.ndarray, h: int) -> np.ndarray:
    """앞으로 h일 동안 장중 저가 기준으로 가장 많이 물린 폭(%)."""
    n = len(c)
    out = np.full(n, np.nan)
    if n > h:
        w = np.lib.stride_tricks.sliding_window_view(lo, h)        # w[i] = lo[i:i+h]
        out[:n - h] = (w[1:n - h + 1].min(axis=1) / c[:n - h] - 1) * 100
    return out


class Bag:
    """칸 이름 → 값들 (수익률, 낙폭, 날짜) 모으기."""

    def __init__(self):
        self.d: dict[str, dict[str, list]] = {}

    def add(self, key: str, mask: np.ndarray, cols: dict[str, np.ndarray]):
        if not mask.any():
            return
        slot = self.d.setdefault(key, {})
        for name, arr in cols.items():
            slot.setdefault(name, []).append(arr[mask])

    def stat(self, key: str) -> dict | None:
        slot = self.d.get(key)
        if not slot:
            return None
        get = lambda n: np.concatenate(slot[n]) if n in slot else np.array([])
        out: dict = {}
        r5 = get("r5")
        ok5 = ~np.isnan(r5)
        out["n"] = int(ok5.sum())
        if out["n"] == 0:
            return None
        dates = get("date")
        out["dates"] = int(len(np.unique(dates[ok5]))) if len(dates) == len(r5) else None
        for h in C.HORIZONS:
            r = get(f"r{h}")
            r = r[~np.isnan(r)]
            if len(r):
                out[f"w{h}"] = round(float((r > 0).mean() * 100), 1)
                out[f"m{h}"] = round(float(r.mean()), 2)
        m = get("mae10")
        m = m[~np.isnan(m)]
        if len(m):
            out["mae10"] = round(float(m.mean()), 1)
        r10 = get("r10")
        r10 = r10[~np.isnan(r10)]
        if len(r10):
            out["p5_10"] = round(float(np.percentile(r10, 5)), 1)       # 나쁜 5%의 10일 수익
        return out


def build(prepared: dict[str, pd.DataFrame], breadth: pd.Series, market: pd.DataFrame,
          info: dict[str, dict]) -> dict:
    """prepared: 종목코드 → compute_all 을 거친 일봉(가능한 길게). 반환: 성과표(dict)."""
    base, osb, single, bob, hold = Bag(), Bag(), Bag(), Bag(), Bag()
    cloud_rows: list[tuple[int, str]] = []
    bcount = {"lt30": 0, "30_50": 0, "ge50": 0}
    bb = breadth.map(I.breadth_bucket)
    for k in bcount:
        bcount[k] = int((bb == k).sum())

    first = lambda s: (s & ~s.shift(1, fill_value=False)).to_numpy()

    for code, d in prepared.items():
        n = len(d)
        if n < 200:
            continue
        c, lo, hi = d["Close"].to_numpy(float), d["Low"].to_numpy(float), d["High"].to_numpy(float)
        valid = d["valid"].to_numpy()
        dates = d.index.values.astype("datetime64[D]").astype(np.int64).astype(float)
        cols = {f"r{h}": _fwd(c, h) for h in C.HORIZONS}
        cols["mae10"] = _mae(c, lo, 10)
        cols["date"] = dates
        up = d["trend_up"].to_numpy()
        bk = bb.reindex(d.index).fillna("*").to_numpy()
        mk = market.reindex(d.index)
        both_up = (mk["up20"].fillna(False) & mk["up120"].fillna(False)).to_numpy()

        # 기준선: 아무 날
        base.add("all", valid, cols)
        for b in ("lt30", "30_50", "ge50"):
            base.add(b, valid & (bk == b), cols)

        # 과매도: 겹침 수 × 종목 추세 × 시장 폭
        ev = d["os_event"].to_numpy() & valid
        nos = d["n_os"].to_numpy()
        for k in (1, 2, 3):
            mk_ = ev & (nos == k)
            osb.add(f"{k}|*|*", mk_, cols)
            for t, tm in (("U", up), ("D", ~up)):
                osb.add(f"{k}|{t}|*", mk_ & tm, cols)
                for b in ("lt30", "30_50", "ge50"):
                    osb.add(f"{k}|{t}|{b}", mk_ & tm & (bk == b), cols)
            for b in ("lt30", "30_50", "ge50"):
                osb.add(f"{k}|*|{b}", mk_ & (bk == b), cols)
        osb.add("2+|*|*", ev & (nos >= 2), cols)

        # 신호 하나하나 (그 조건이 처음 켜진 날) + 깊이 단계
        rsi, gap = d["rsi"], d["gap"]
        singles = {
            "rsi": first(d["os_rsi"]), "bb": first(d["os_bb"]), "gap": first(d["os_gap"]),
            "rsi25": first((rsi < 25).fillna(False)), "rsi20": first((rsi < 20).fillna(False)),
            "gap15": first((gap <= -15).fillna(False)), "gap20": first((gap <= -20).fillna(False)),
        }
        for k, m in singles.items():
            single.add(k, m & valid, cols)

        # 돌파: 종목 추세 × 시장(코스피 20·120일선 모두 위)
        bo = d["bo_event"].to_numpy() & valid
        bob.add("*|*", bo, cols)
        for t, tm in (("U", up), ("D", ~up)):
            for mname, mm in (("both_up", both_up), ("other", ~both_up)):
                bob.add(f"{t}|{mname}", bo & tm & mm, cols)

        # 구름대 진입 뒤 20일: 상단에 먼저 닿나, 하단 아래로 먼저 빠지나
        top, bot = d["cloud_top"].to_numpy(float), d["cloud_bot"].to_numpy(float)
        for t in np.flatnonzero(d["cloud_in"].to_numpy() & valid):
            if t + 20 >= n:
                continue
            res = "neither"
            for u in range(t + 1, t + 21):
                if hi[u] >= top[u]:
                    res = "top"
                    break
                if c[u] < bot[u]:
                    res = "bottom"
                    break
            cloud_rows.append((sum(I.cloud_conditions(d, t).values()), res))

        # 오래 들고 있을 때: 60일 안에 -20% 넘게 물린 적이 있나 (코스피/코스닥)
        mae60 = _mae(c, lo, 60)
        mkt_name = (info.get(code) or {}).get("market", "KOSPI")
        m2 = ev & (nos >= 2) & ~np.isnan(mae60)
        if m2.any():
            hold.add(mkt_name, m2, {"mae60": mae60, "r5": cols["r5"], "date": dates})

    table: dict = {"baseline": {}, "os": {}, "single": {}, "bo": {}, "cloud": {}, "hold": {}}
    for k in base.d:
        table["baseline"][k] = base.stat(k)
    for k in osb.d:
        table["os"][k] = osb.stat(k)
    for k in single.d:
        table["single"][k] = single.stat(k)
    for k in bob.d:
        table["bo"][k] = bob.stat(k)
    if cloud_rows:
        cr = pd.DataFrame(cloud_rows, columns=["cnt", "res"])
        for name, sel in (("0-1", cr["cnt"] <= 1), ("2", cr["cnt"] == 2), ("3", cr["cnt"] == 3),
                          ("4", cr["cnt"] == 4), ("5", cr["cnt"] == 5), ("6", cr["cnt"] >= 6), ("all", cr["cnt"] >= 0)):
            x = cr[sel]
            if len(x):
                table["cloud"][name] = {"n": int(len(x)), "top": round(float((x["res"] == "top").mean() * 100), 1),
                                        "bottom": round(float((x["res"] == "bottom").mean() * 100), 1)}
    for k, slot in hold.d.items():
        m = np.concatenate(slot["mae60"])
        table["hold"][k] = {"n": int(len(m)), "down20": round(float((m <= -20).mean() * 100), 1),
                            "p5": round(float(np.percentile(m, 5)), 1), "mean": round(float(m.mean()), 1)}

    idx = [d.index for d in prepared.values() if len(d)]
    table["meta"] = {
        "built": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "stocks": len(prepared),
        "from": min(i[0] for i in idx).strftime("%Y-%m-%d") if idx else None,
        "to": max(i[-1] for i in idx).strftime("%Y-%m-%d") if idx else None,
        "cost": C.COST_PCT,
        "breadth_days": bcount,
    }
    return table


# ─────────────────────────────────────────────
# 표에서 찾아 쓰기
# ─────────────────────────────────────────────
def lookup_os(table: dict | None, n: int, trend_up: bool, bucket: str) -> dict:
    """가장 자세한 칸부터 찾고, 표본이 MIN_SAMPLE보다 적으면 한 단계씩 넓힌다."""
    if not table:
        return {"stat": None, "key": None, "level": "none"}
    k = min(max(n, 1), 3)
    t = "U" if trend_up else "D"
    for key, level in ((f"{k}|{t}|{bucket}", "full"), (f"{k}|*|{bucket}", "no_trend"),
                       (f"{k}|{t}|*", "no_breadth"), (f"{k}|*|*", "count_only")):
        if "*" in bucket and level in ("full", "no_trend"):
            continue
        st = (table.get("os") or {}).get(key)
        if st and st["n"] >= C.MIN_SAMPLE:
            return {"stat": st, "key": key, "level": level}
    return {"stat": (table.get("os") or {}).get(f"{k}|*|*"), "key": f"{k}|*|*", "level": "thin"}


def lookup_bo(table: dict | None, trend_up: bool, both_up: bool) -> dict:
    if not table:
        return {"stat": None, "key": None, "level": "none"}
    key = f"{'U' if trend_up else 'D'}|{'both_up' if both_up else 'other'}"
    st = (table.get("bo") or {}).get(key)
    if st and st["n"] >= C.MIN_SAMPLE:
        return {"stat": st, "key": key, "level": "full"}
    return {"stat": (table.get("bo") or {}).get("*|*"), "key": "*|*", "level": "thin"}


def grade(stat: dict | None) -> str | None:
    """과거 같은 상태의 5일 승률로 등급. 표본이 모자라면 등급 없음."""
    if not stat or stat.get("n", 0) < C.MIN_SAMPLE or stat.get("w5") is None:
        return None
    w = stat["w5"]
    for g in ("A", "B", "C"):
        if w >= C.GRADE_RULE[g]:
            return g
    return "D"
