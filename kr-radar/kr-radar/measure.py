"""
measure.py — 신호별 '얼마나 늦나 / 얼마나 맞나' 측정 (기준을 정하기 위한 1회성 조사)

  python measure.py           실제 데이터 (GitHub에서 실행)
  python measure.py --demo    가상 데이터로 코드 점검

규칙 (2026-10-05 합의)
  대상   지금 레이더의 350종목
  진입   신호가 뜬 날 종가
  성과   1·3·5·10·20거래일 뒤 종가, 왕복 비용 0.25% 차감
  비교   '아무 날 아무 종목' (같은 기간 전체 평균)
  손절   없음 / -3% / -5% / -8% / 평소 변동폭(ATR) 2배 / 직전 10일 저점 이탈  (10일 보유 기준)
결과는 state/measure/result.json 과 result.md 로 저장.

주의: 지금 350종목만 보므로 '살아남은 종목' 편향이 있다(상장폐지·탈락 종목 없음). 절대 수익보다 신호끼리의 비교로 읽을 것.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import config as C
import indicators as I
import sources as S

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, C.STATE_DIR, "measure")
BARS = 1300                 # 약 5년 (받을 수 있으면)
HORIZONS = (1, 3, 5, 10, 20)
COST = 0.25                 # 왕복 거래세+수수료 (%)
STOP_HOLD = 10              # 손절 비교는 10일 보유 기준
RECENT = 375                # '최근 1년 반' 구간
KST = timezone(timedelta(hours=9))


# ───────────────────────── 지표 ─────────────────────────
def prepare(df: pd.DataFrame) -> pd.DataFrame:
    d = I.compute_all(df)
    c = d["Close"]
    sd = c.rolling(20).std(ddof=0)
    d["bb_low"] = d["ma20"] - 2 * sd
    pc = c.shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - pc).abs(), (d["Low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["low20"] = d["Low"].rolling(20).min()
    d["low10"] = d["Low"].rolling(10).min()
    d["trend_up"] = d["ma120"] > d["ma120"].shift(20)
    return d


def first_day(mask: pd.Series) -> pd.Series:
    """조건이 '처음' 참이 된 날만 (연속으로 참인 날을 매번 세지 않게)."""
    m = mask.fillna(False)
    return m & ~m.shift(1, fill_value=False)


def divergence(d: pd.DataFrame) -> pd.Series:
    """상승 다이버전스: 오늘 20일 신저가인데, 5~30일 전 저점 때보다 RSI는 높다."""
    low, rsi = d["Low"].to_numpy(), d["rsi"].to_numpy()
    out = np.zeros(len(d), bool)
    new_low = (d["Low"] <= d["low20"]).to_numpy()
    for t in np.flatnonzero(new_low):
        if t < 40:
            continue
        seg = low[t - 30:t - 4]
        j = t - 30 + int(np.argmin(seg))
        if low[t] < low[j] and rsi[t] > rsi[j] and rsi[j] < 40 and not np.isnan(rsi[t]):
            out[t] = True
    return pd.Series(out, index=d.index)


def signals(d: pd.DataFrame) -> dict[str, pd.Series]:
    c = d["Close"]
    vol_ok = d["Volume"] >= d["vol_avg"] * 1.5
    s: dict[str, pd.Series] = {}
    # 과하게 빠짐
    s["과매도: RSI 30 아래 진입"] = first_day(d["rsi"] < 30)
    s["과매도: 볼린저 하단 이탈"] = first_day(c < d["bb_low"])
    s["과매도: 20일선 대비 -10% 이격"] = first_day(c / d["ma20"] - 1 <= -0.10)
    # 반등 신호
    s["반등: RSI 골든크로스(50 아래)"] = I.cross_up(d["rsi"], d["rsi_sig"]) & (d["rsi"] < 50)
    s["반등: RSI 30 상향 돌파"] = I.cross_up(d["rsi"], 30)
    s["반등: 스토캐스틱 골든크로스(25 아래)"] = I.cross_up(d["stoch_k"], d["stoch_d"]) & (d["stoch_k"].shift(1) < 25)
    mg = I.cross_up(d["macd"], d["macd_sig"])
    s["반등: MACD 골든크로스"] = mg
    s["반등: MACD 골든크로스(0선 아래)"] = mg & (d["macd"] < 0)
    s["반등: RSI 상승 다이버전스"] = divergence(d)
    # 돌파
    for name, col in (("20일선 돌파", "ma20"), ("60일선 돌파", "ma60"), ("구름대 상단 돌파", "cloud_top")):
        b = I.cross_up(c, d[col])
        s[f"돌파: {name}"] = b
        s[f"돌파: {name} + 거래량 1.5배"] = b & vol_ok
        s[f"돌파: {name} + 거래량 평범"] = b & ~vol_ok
    s["돌파: 20·60 골든크로스"] = I.cross_up(d["ma20"], d["ma60"])
    # 구름대 진입 (아래에서 구름 안으로)
    s["구름: 아래에서 구름 안으로 진입"] = (c.shift(1) < d["cloud_bot"].shift(1)) & (c >= d["cloud_bot"]) & (c < d["cloud_top"])
    return {k: v.fillna(False) for k, v in s.items()}


# ───────────────────────── 성과 계산 ─────────────────────────
def forward(d: pd.DataFrame) -> dict[str, np.ndarray]:
    c, lo = d["Close"].to_numpy(float), d["Low"].to_numpy(float)
    n = len(c)
    out = {}
    for h in HORIZONS:
        r = np.full(n, np.nan)
        r[:n - h] = (c[h:] / c[:n - h] - 1) * 100 - COST
        out[f"r{h}"] = r
    # 10일 안 최대 낙폭 (장중 저가 기준)
    mae = np.full(n, np.nan)
    for t in range(n - STOP_HOLD):
        mae[t] = (lo[t + 1:t + 1 + STOP_HOLD].min() / c[t] - 1) * 100
    out["mae10"] = mae
    out["late"] = ((d["Close"] / d["low20"] - 1) * 100).to_numpy()
    return out


def stop_sim(d: pd.DataFrame, idx: np.ndarray) -> dict[str, list[float]]:
    """신호일(idx)에 종가로 사서 10일 보유. 손절 규칙별 수익률(%)."""
    c, lo, op = d["Close"].to_numpy(float), d["Low"].to_numpy(float), d["Open"].to_numpy(float)
    atr, low10 = d["atr"].to_numpy(float), d["low10"].to_numpy(float)
    n = len(c)
    res = {k: [] for k in ("손절 없음", "-3%", "-5%", "-8%", "ATR 2배", "직전 10일 저점")}
    for t in idx:
        if t + STOP_HOLD >= n or np.isnan(atr[t]):
            continue
        entry = c[t]
        stops = {"손절 없음": 0.0, "-3%": entry * 0.97, "-5%": entry * 0.95, "-8%": entry * 0.92,
                 "ATR 2배": entry - 2 * atr[t], "직전 10일 저점": low10[t] * 0.995}
        for k, sp in stops.items():
            exit_p = c[t + STOP_HOLD]
            if sp > 0:
                for u in range(t + 1, t + 1 + STOP_HOLD):
                    if lo[u] <= sp:
                        exit_p = min(sp, op[u])      # 갭 하락이면 시가에 팔림
                        break
            res[k].append((exit_p / entry - 1) * 100 - COST)
    return res


def cloud_traverse(d: pd.DataFrame, idx: np.ndarray) -> list[str]:
    """구름 안으로 들어온 뒤 20일 안에: 상단 먼저 닿음 / 하단 아래로 먼저 빠짐 / 둘 다 아님."""
    c, hi = d["Close"].to_numpy(float), d["High"].to_numpy(float)
    top, bot = d["cloud_top"].to_numpy(float), d["cloud_bot"].to_numpy(float)
    out = []
    for t in idx:
        if t + 20 >= len(c):
            continue
        r = "neither"
        for u in range(t + 1, t + 21):
            if hi[u] >= top[u]:
                r = "top"
                break
            if c[u] < bot[u]:
                r = "bottom"
                break
        out.append(r)
    return out


def stat(a, base_mean=None) -> dict:
    a = np.asarray(a, float)
    a = a[~np.isnan(a)]
    if len(a) == 0:
        return {"n": 0}
    o = {"n": int(len(a)), "win": round(float((a > 0).mean() * 100), 1), "mean": round(float(a.mean()), 2),
         "median": round(float(np.median(a)), 2), "p5": round(float(np.percentile(a, 5)), 1)}
    if base_mean is not None:
        o["edge"] = round(o["mean"] - base_mean, 2)
    return o


class Bag:
    """신호 이름 → (조건별) 값 모음."""
    def __init__(self):
        self.d: dict[str, dict[str, list]] = {}

    def add(self, key: str, field: str, vals):
        self.d.setdefault(key, {}).setdefault(field, []).append(np.asarray(vals, float))

    def get(self, key, field):
        xs = self.d.get(key, {}).get(field)
        return np.concatenate(xs) if xs else np.array([])


# ───────────────────────── 데이터 받기 ─────────────────────────
def fetch_flows_long(code: str, pages: int = 10) -> pd.DataFrame | None:
    """기관·외국인 순매수를 가능한 한 길게 (페이지를 넘겨가며)."""
    frames, seen = [], set()
    for mode in ("page", "bizdate"):
        last = None
        for p in range(1, pages + 1):
            if mode == "page":
                url = f"{S.MOBILE}/api/stock/{code}/trend?pageSize=20&page={p}"
            else:
                if last is None:
                    break
                url = f"{S.MOBILE}/api/stock/{code}/trend?pageSize=20&bizdate={last}"
            try:
                df = S.parse_flow_json(S.get_json(url))
            except Exception:
                break
            new = df[~df["date"].isin(seen)]
            if new.empty:
                break
            frames.append(new)
            seen.update(new["date"])
            last = df["date"].min().strftime("%Y%m%d")
        if len(seen) > 25:
            break
        if frames and mode == "page":
            last = min(seen).strftime("%Y%m%d")
            # page 방식이 20일에서 멈췄으면 bizdate 방식으로 이어서 시도
            for p in range(pages):
                try:
                    df = S.parse_flow_json(S.get_json(f"{S.MOBILE}/api/stock/{code}/trend?pageSize=20&bizdate={last}"))
                except Exception:
                    break
                new = df[~df["date"].isin(seen)]
                if new.empty:
                    break
                frames.append(new)
                seen.update(new["date"])
                last = df["date"].min().strftime("%Y%m%d")
            break
    if not frames:
        return None
    return pd.concat(frames).sort_values("date").drop_duplicates("date").reset_index(drop=True)


def load_live():
    import run as RUN
    cache = RUN.load_cache()
    if cache.get("universe"):
        uni = cache["universe"]
    else:
        import universe
        uni, _ = universe.build_universe()
    print(f"[측정] {len(uni)}종목 시세·수급 받는 중")

    def one(s):
        pr = S.fetch_prices(s["code"], BARS)
        fl = fetch_flows_long(s["code"])
        return s["code"], (pr.data if pr.ok else None), fl

    prices, flows = {}, {}
    with ThreadPoolExecutor(max_workers=C.MAX_WORKERS) as ex:
        for i, (code, p, f) in enumerate(ex.map(one, uni), 1):
            if p is not None and len(p) >= 200:
                prices[code] = p
            if f is not None:
                flows[code] = f
            if i % 50 == 0:
                print(f"  {i}/{len(uni)}")
    mk = S.fetch_prices("KOSPI", BARS)
    market = mk.data["Close"] if mk.ok else None
    return prices, flows, market, uni


def load_demo():
    import demo
    p = demo.DemoProvider(n=40)
    prices = {s["code"]: p.prices(s["code"]).data for s in p.stocks}
    flows = {}
    for s in p.stocks:
        f = p.flows(s["code"]).data.copy()
        flows[s["code"]] = f
    uni = [{"code": s["code"], "market": s["market"], "marcap": 1000 + i * 50} for i, s in enumerate(p.stocks)]
    return prices, flows, None, uni


# ───────────────────────── 본체 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    prices, flows, market, uni = load_demo() if a.demo else load_live()
    if not prices:
        print("시세를 하나도 못 받음")
        sys.exit(1)

    # 시장 분위기: 코스피가 20일선 위인가 (코스피를 못 받으면 350종목 평균으로 대신)
    if market is None:
        allc = pd.DataFrame({k: v["Close"] / v["Close"].iloc[0] for k, v in prices.items()})
        market = allc.mean(axis=1)
        market_src = "350종목 평균 (코스피 지수 조회 실패)"
    else:
        market_src = "코스피 지수"
    mkt_up = (market > market.rolling(20).mean())

    bag, base = Bag(), Bag()
    stops: dict[str, dict[str, list]] = {}
    traverse: dict[str, list] = {"all": [], "thick": [], "thin": []}
    flow_bag = Bag()
    flow_days = []

    for code, df in prices.items():
        d = prepare(df)
        fw = forward(d)
        sig = signals(d)
        n = len(d)
        up = d["trend_up"].fillna(False).to_numpy()
        mu = mkt_up.reindex(d.index).fillna(False).to_numpy()
        recent = np.zeros(n, bool)
        recent[-RECENT:] = True
        valid = ~np.isnan(d["ma120"].to_numpy())           # 지표가 다 계산된 날만
        for f in [f"r{h}" for h in HORIZONS] + ["mae10"]:
            base.add("전체", f, fw[f][valid])
            base.add("전체·상승추세", f, fw[f][valid & up])
            base.add("전체·하락추세", f, fw[f][valid & ~up])
            base.add("전체·시장 강세", f, fw[f][valid & mu])
            base.add("전체·시장 약세", f, fw[f][valid & ~mu])
            base.add("전체·최근 1년반", f, fw[f][valid & recent])
            base.add("전체·그 이전", f, fw[f][valid & ~recent])
        for name, m in sig.items():
            m = m.to_numpy() & valid
            for sub, cond in (("", np.ones(n, bool)), ("|상승추세", up), ("|하락추세", ~up),
                              ("|시장 강세", mu), ("|시장 약세", ~mu), ("|최근 1년반", recent), ("|그 이전", ~recent)):
                mm = m & cond
                if not mm.any():
                    continue
                for f in [f"r{h}" for h in HORIZONS] + ["mae10", "late"]:
                    bag.add(name + sub, f, fw[f][mm])
            idx = np.flatnonzero(m)
            if len(idx):
                for k, v in stop_sim(d, idx).items():
                    stops.setdefault(name, {}).setdefault(k, []).extend(v)
            if name.startswith("구름:") and len(idx):
                thick = ((d["cloud_top"] / d["cloud_bot"] - 1) * 100).to_numpy()
                res = cloud_traverse(d, idx)
                kept = [t for t in idx if t + 20 < n]
                for t, r in zip(kept, res):
                    traverse["all"].append(r)
                    traverse["thick" if thick[t] >= 5 else "thin"].append(r)

        # 수급 (기록이 있는 기간만)
        fl = flows.get(code)
        if fl is not None and len(fl) >= 8:
            f = fl.set_index("date").reindex(d.index)
            inst, frg = f["inst"], f["foreign"]
            have = inst.notna() & frg.notna()
            flow_days.append(int(have.sum()))
            both = (inst > 0) & (frg > 0)
            net = inst.fillna(0) + frg.fillna(0)
            fs = {
                "수급: 외국인·기관 같은 날 동시 순매수": both & have,
                "수급: 둘 다 3일 연속 순매수": both & both.shift(1, fill_value=False) & both.shift(2, fill_value=False) & have,
                "수급: 5일 연속 순매도 뒤 순매수 전환": (net > 0) & have & (net.shift(1).rolling(5).max() < 0),
                "수급: 외국인·기관 같은 날 동시 순매도": (inst < 0) & (frg < 0) & have,
                "수급: (비교) 수급 기록 있는 모든 날": have,
            }
            for name, m in fs.items():
                m = m.fillna(False).to_numpy()
                for ff in [f"r{h}" for h in HORIZONS]:
                    flow_bag.add(name, ff, fw[ff][m])

    # ── 정리 ──
    result = {"meta": {
        "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "stocks": len(prices),
        "bars_median": int(np.median([len(v) for v in prices.values()])),
        "from": min(v.index[0] for v in prices.values()).strftime("%Y-%m-%d"),
        "to": max(v.index[-1] for v in prices.values()).strftime("%Y-%m-%d"),
        "cost_pct": COST, "market": market_src,
        "flow_days_median": int(np.median(flow_days)) if flow_days else 0, "flow_stocks": len(flow_days),
        "elapsed": round(time.time() - t0),
    }, "baseline": {}, "signals": {}, "stops": {}, "cloud": {}, "flows": {}}

    for key in base.d:
        result["baseline"][key] = {f: stat(base.get(key, f)) for f in base.d[key]}
    bm = {f"r{h}": result["baseline"]["전체"][f"r{h}"]["mean"] for h in HORIZONS}
    sub_base = {"상승추세": "전체·상승추세", "하락추세": "전체·하락추세", "시장 강세": "전체·시장 강세",
                "시장 약세": "전체·시장 약세", "최근 1년반": "전체·최근 1년반", "그 이전": "전체·그 이전"}
    for key in sorted(bag.d):
        sub = key.split("|")[1] if "|" in key else None
        bkey = sub_base.get(sub, "전체")
        row = {}
        for h in HORIZONS:
            row[f"r{h}"] = stat(bag.get(key, f"r{h}"), result["baseline"][bkey][f"r{h}"]["mean"])
        row["mae10"] = stat(bag.get(key, "mae10"))
        late = bag.get(key, "late")
        late = late[~np.isnan(late)]
        row["late_median"] = round(float(np.median(late)), 1) if len(late) else None
        result["signals"][key] = row
    for name, dd in stops.items():
        result["stops"][name] = {k: stat(v) for k, v in dd.items()}
    for k, v in traverse.items():
        if v:
            result["cloud"][k] = {"n": len(v), "top_first": round(v.count("top") / len(v) * 100, 1),
                                  "bottom_first": round(v.count("bottom") / len(v) * 100, 1),
                                  "neither": round(v.count("neither") / len(v) * 100, 1)}
    fb = {f"r{h}": stat(flow_bag.get("수급: (비교) 수급 기록 있는 모든 날", f"r{h}")).get("mean") for h in HORIZONS}
    for key in flow_bag.d:
        result["flows"][key] = {f"r{h}": stat(flow_bag.get(key, f"r{h}"), fb[f"r{h}"]) for h in HORIZONS}

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    # 사람이 읽는 요약
    L = [f"# 신호 측정 결과 ({result['meta']['at']})", "",
         f"{result['meta']['stocks']}종목 · {result['meta']['from']} ~ {result['meta']['to']} · 왕복 비용 {COST}% 차감 · 시장 기준: {market_src}",
         "", "## 기준선 (아무 날 아무 종목)", ""]
    b = result["baseline"]["전체"]
    L.append(" · ".join(f"{h}일 승률 {b[f'r{h}']['win']}% 평균 {b[f'r{h}']['mean']:+.2f}%" for h in HORIZONS))
    L += ["", "## 신호별 (전체 구간)", "", "| 신호 | 횟수 | 늦음(저점 대비) | 5일 승률 | 5일 평균 | 기준 대비 | 10일 평균 | 기준 대비 | 20일 평균 | 10일 내 평균 최대낙폭 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for key, r in result["signals"].items():
        if "|" in key or not r["r5"].get("n"):
            continue
        L.append(f"| {key} | {r['r5']['n']} | +{r['late_median']}% | {r['r5']['win']}% | {r['r5']['mean']:+.2f}% | {r['r5']['edge']:+.2f} | "
                 f"{r['r10']['mean']:+.2f}% | {r['r10']['edge']:+.2f} | {r['r20']['mean']:+.2f}% | {r['mae10'].get('mean', 0):.1f}% |")
    L += ["", "## 구름대 진입 뒤 20일", ""]
    for k, v in result["cloud"].items():
        L.append(f"- {k}: {v['n']}번 · 상단 먼저 {v['top_first']}% · 하단 아래로 먼저 {v['bottom_first']}% · 둘 다 아님 {v['neither']}%")
    L += ["", f"## 수급 (종목당 기록 {result['meta']['flow_days_median']}일)", ""]
    for k, v in result["flows"].items():
        if v["r5"].get("n"):
            L.append(f"- {k}: {v['r5']['n']}번 · 5일 평균 {v['r5']['mean']:+.2f}% (비교 대비 {v['r5']['edge']:+.2f}) · 10일 {v['r10'].get('mean', 0):+.2f}%")
    with open(os.path.join(OUT, "result.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n[저장] {OUT}  ({result['meta']['elapsed']}초)")

    # 2차 측정 (같은 시세로 이어서)
    try:
        import measure2
        measure2.main(prices, market, uni, prepare)
    except Exception:
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
