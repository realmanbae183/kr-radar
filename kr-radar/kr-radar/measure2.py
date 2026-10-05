"""
measure2.py — 2차 측정 (measure.py 가 시세를 받은 뒤 이어서 부른다)

  A. '많이 빠졌다'의 기준: RSI·볼린저·이격을 단계별로 + 평소 변동폭(ATR) 대비 이격
  B. 과매도 세 신호가 1개 / 2개 / 3개 겹친 날
  C. 추세(120일선) × 시장(코스피 20일선) 네 칸, 그리고 지금 레이더의 국면 라벨별
  D. 과매도에 사서 20·40·60일 들고 있었을 때 — 코스피/코스닥, 시총 큰 쪽/작은 쪽
  E. 구름대 진입에 조건을 붙였을 때 상단 먼저 닿는 비율 (앞·뒤 구간 따로)
결과: state/measure/result2.json, result2.md
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

import config as C
import signals as SG

COST = 0.25
RECENT = 375
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, C.STATE_DIR, "measure")


def _first(mask):
    m = pd.Series(mask).fillna(False)
    return (m & ~m.shift(1, fill_value=False)).to_numpy()


def _fwd(c, h):
    r = np.full(len(c), np.nan)
    r[:len(c) - h] = (c[h:] / c[:len(c) - h] - 1) * 100 - COST
    return r


def _mae(c, lo, h):
    out = np.full(len(c), np.nan)
    for t in range(len(c) - h):
        out[t] = (lo[t + 1:t + 1 + h].min() / c[t] - 1) * 100
    return out


def _stat(a, base=None):
    a = np.asarray(a, float)
    a = a[~np.isnan(a)]
    if len(a) == 0:
        return {"n": 0}
    o = {"n": int(len(a)), "win": round(float((a > 0).mean() * 100), 1), "mean": round(float(a.mean()), 2),
         "p5": round(float(np.percentile(a, 5)), 1)}
    if base is not None:
        o["edge"] = round(o["mean"] - base, 2)
    return o


def _n(x, d=1):
    """숫자가 없으면 '–'."""
    try:
        return "–" if x is None or np.isnan(x) else f"{x:.{d}f}"
    except TypeError:
        return "–"


class Acc:
    def __init__(self):
        self.d = {}

    def add(self, key, field, vals):
        self.d.setdefault(key, {}).setdefault(field, []).append(np.asarray(vals, float))

    def get(self, key, field):
        xs = self.d.get(key, {}).get(field)
        return np.concatenate(xs) if xs else np.array([])


def main(prices: dict, market: pd.Series, uni: list[dict], prepare) -> None:
    info = {u["code"]: u for u in uni}
    caps = sorted([u.get("marcap") or 0 for u in uni], reverse=True)
    big_cut = caps[min(99, len(caps) - 1)] if caps else 0          # 시총 상위 100위 = '큰 쪽'
    mkt_up = market > market.rolling(20).mean()
    mkt_long = market > market.rolling(120).mean()
    # 시장 폭: 종목 중 몇 %가 20일선 위인가 (지수보다 '다 같이 빠졌나'를 잘 보여줌)
    above = pd.DataFrame({k: (v["Close"] > v["Close"].rolling(20).mean()) for k, v in prices.items()})
    has = pd.DataFrame({k: v["Close"].rolling(20).mean().notna() for k, v in prices.items()})
    breadth = (above.sum(axis=1) / has.sum(axis=1).replace(0, np.nan) * 100)
    # 시장 변동성: 코스피 최근 20일 하루 등락의 크기 (연율 %)
    mvol = market.pct_change().rolling(20).std() * np.sqrt(252) * 100
    F, G = Acc(), Acc()

    A, B, Cx, D, E = Acc(), Acc(), Acc(), Acc(), Acc()
    base = Acc()
    cloud_rows = []

    for code, df in prices.items():
        d = prepare(df)
        c, lo, hi = d["Close"].to_numpy(float), d["Low"].to_numpy(float), d["High"].to_numpy(float)
        n = len(d)
        valid = ~np.isnan(d["ma120"].to_numpy())
        recent = np.zeros(n, bool)
        recent[-RECENT:] = True
        up = d["trend_up"].fillna(False).to_numpy()
        mu = mkt_up.reindex(d.index).fillna(False).to_numpy()
        ml = mkt_long.reindex(d.index).fillna(False).to_numpy()
        br = breadth.reindex(d.index).to_numpy()
        mv = mvol.reindex(d.index).to_numpy()
        R = {h: _fwd(c, h) for h in (5, 10, 20, 40, 60)}
        mae10, mae60 = _mae(c, lo, 10), _mae(c, lo, 60)
        for h in R:
            base.add("all", f"r{h}", R[h][valid])
            base.add("recent", f"r{h}", R[h][valid & recent])
            base.add("earlier", f"r{h}", R[h][valid & ~recent])
            base.add("up", f"r{h}", R[h][valid & up]); base.add("down", f"r{h}", R[h][valid & ~up])
            base.add("mstrong", f"r{h}", R[h][valid & mu]); base.add("mweak", f"r{h}", R[h][valid & ~mu])

        rsi = d["rsi"].to_numpy()
        sd = d["Close"].rolling(20).std(ddof=0).to_numpy()
        z = (c - d["ma20"].to_numpy()) / np.where(sd == 0, np.nan, sd)
        gap = (c / d["ma20"].to_numpy() - 1) * 100
        atrgap = (d["ma20"].to_numpy() - c) / d["atr"].to_numpy()

        # A. 단계별 기준
        levels = {}
        for th in (35, 30, 25, 20):
            levels[f"RSI {th} 아래 진입"] = _first(rsi < th)
        for th in (2.0, 2.5, 3.0):
            levels[f"볼린저 {th}배 아래 진입"] = _first(z < -th)
        for th in (5, 10, 15, 20):
            levels[f"20일선 대비 -{th}% 이격 진입"] = _first(gap <= -th)
        for th in (2, 3, 4, 5):
            levels[f"20일선에서 평소 변동폭 {th}배 아래 진입"] = _first(atrgap >= th)
        for name, m in levels.items():
            m = m & valid
            for sub, cond in (("", np.ones(n, bool)), ("|recent", recent), ("|earlier", ~recent)):
                mm = m & cond
                for h in (5, 10, 20):
                    A.add(name + sub, f"r{h}", R[h][mm])
                A.add(name + sub, "mae10", mae10[mm])

        # B. 겹침 (기본 세 신호 중 하나라도 '처음' 뜬 날, 그날 몇 개가 켜져 있나)
        s1, s2, s3 = rsi < 30, z < -2, gap <= -10
        ev = (_first(s1) | _first(s2) | _first(s3)) & valid
        cnt = s1.astype(int) + s2.astype(int) + s3.astype(int)
        for k in (1, 2, 3):
            mm = ev & (cnt == k)
            for h in (5, 10, 20):
                B.add(f"{k}개 겹침", f"r{h}", R[h][mm])
            B.add(f"{k}개 겹침", "mae10", mae10[mm])

        # C. 추세 × 시장 네 칸 + 국면 라벨
        for tn, tc in (("종목 상승추세", up), ("종목 하락추세", ~up)):
            for mn, mc in (("시장 약세", ~mu), ("시장 강세", mu)):
                mm = ev & tc & mc
                for h in (5, 10, 20):
                    Cx.add(f"{tn} · {mn}", f"r{h}", R[h][mm])
                Cx.add(f"{tn} · {mn}", "mae10", mae10[mm])
        for t in np.flatnonzero(ev):
            if t < 260:
                continue
            try:
                rg = SG.detect_regime(d.iloc[:t + 1], {})["key"]
            except Exception:
                continue
            for h in (5, 10, 20):
                Cx.add(f"국면: {SG.REGIME_META[rg][0]}", f"r{h}", [R[h][t]])

        # F. 시장 배경별 + 승률표(레이더에 띄울 '과거 이 상태의 성과')
        bo = ((d["Close"] > d["cloud_top"]) & (d["Close"].shift(1) <= d["cloud_top"].shift(1))
              & (d["Volume"] >= d["vol_avg"] * 1.5)).fillna(False).to_numpy() & valid
        events = {"과매도": ev, "거래량 실린 구름대 상단 돌파": bo}
        ctx = {
            "코스피 장기↑·단기↑ (상승장 속 강세)": ml & mu, "코스피 장기↑·단기↓ (상승장 속 조정)": ml & ~mu,
            "코스피 장기↓·단기↑ (하락장 속 반등)": ~ml & mu, "코스피 장기↓·단기↓ (하락장 속 약세)": ~ml & ~mu,
            "시장 폭 30% 미만 (대부분 20일선 아래)": br < 30, "시장 폭 30~50%": (br >= 30) & (br < 50),
            "시장 폭 50~70%": (br >= 50) & (br < 70), "시장 폭 70% 이상 (대부분 20일선 위)": br >= 70,
            "시장 변동성 낮음(12% 미만)": mv < 12, "시장 변동성 보통(12~20%)": (mv >= 12) & (mv < 20),
            "시장 변동성 높음(20% 이상)": mv >= 20,
        }
        for en, em in events.items():
            for cn, cm in ctx.items():
                mm = em & cm
                for h in (5, 10, 20):
                    F.add(f"{en} | {cn}", f"r{h}", R[h][mm])
                    F.add(f"(기준) {cn}", f"r{h}", R[h][valid & cm])
                F.add(f"{en} | {cn}", "mae10", mae10[mm])
        # G. 승률표: 신호 × 종목 추세 × 시장 단기 (× 시장 장기)
        states = {"과매도 1개": ev & (cnt == 1), "과매도 2개 겹침": ev & (cnt == 2), "과매도 3개 겹침": ev & (cnt == 3),
                  "거래량 실린 구름대 상단 돌파": bo}
        for sn, sm in states.items():
            for tn, tc in (("종목↑", up), ("종목↓", ~up)):
                for mn, mc in (("시장 단기↓", ~mu), ("시장 단기↑", mu)):
                    for h in (5, 10, 20):
                        G.add(f"{sn} · {tn} · {mn}", f"r{h}", R[h][sm & tc & mc])
                    G.add(f"{sn} · {tn} · {mn}", "mae10", mae10[sm & tc & mc])
                    for ln, lc in (("장기↑", ml), ("장기↓", ~ml)):
                        for h in (5, 10, 20):
                            G.add(f"{sn} · {tn} · {mn} · {ln}", f"r{h}", R[h][sm & tc & mc & lc])
                        G.add(f"{sn} · {tn} · {mn} · {ln}", "mae10", mae10[sm & tc & mc & lc])

        # D. 오래 들고 있기
        u = info.get(code, {})
        grp = [("코스피" if u.get("market") == "KOSPI" else "코스닥"),
               ("시총 큰 쪽(상위 100)" if (u.get("marcap") or 0) >= big_cut and big_cut else "시총 작은 쪽")]
        for g in grp + ["전체"]:
            for h in (20, 40, 60):
                D.add(g, f"r{h}", R[h][ev])
            D.add(g, "mae60", mae60[ev])
            for h in (20, 40, 60):
                D.add(g + "|기준(아무 날)", f"r{h}", R[h][valid])
            D.add(g + "|기준(아무 날)", "mae60", mae60[valid])

        # E. 구름대 진입 + 조건
        top, bot = d["cloud_top"].to_numpy(float), d["cloud_bot"].to_numpy(float)
        cs = d["Close"]
        ent = ((cs.shift(1) < d["cloud_bot"].shift(1)) & (cs >= d["cloud_bot"]) & (cs < d["cloud_top"])).fillna(False).to_numpy() & valid
        vol_ok = (d["Volume"] >= d["vol_avg"] * 1.5).fillna(False).to_numpy()
        fut_bull = (d["span_a_raw"] > d["span_b_raw"]).fillna(False).to_numpy()
        macd_ok = (d["macd"] > d["macd_sig"]).fillna(False).to_numpy()
        was_os = (d["rsi"].rolling(10).min() < 30).fillna(False).to_numpy()
        for t in np.flatnonzero(ent):
            if t + 20 >= n:
                continue
            res = "neither"
            for k in range(t + 1, t + 21):
                if hi[k] >= top[k]:
                    res = "top"
                    break
                if c[k] < bot[k]:
                    res = "bottom"
                    break
            conds = {
                "구름 얇음(5% 미만)": (top[t] / bot[t] - 1) * 100 < 5,
                "진입일 거래량 1.5배": bool(vol_ok[t]),
                "120일선 상승 중": bool(up[t]),
                "앞쪽 구름이 양운": bool(fut_bull[t]),
                "MACD가 시그널 위": bool(macd_ok[t]),
                "최근 10일 내 RSI 30 아래 찍음": bool(was_os[t]),
                "RSI 50 이상": bool(rsi[t] >= 50),
                "시장 강세(코스피 20일선 위)": bool(mu[t]),
            }
            cloud_rows.append({"res": res, "recent": bool(recent[t]), "r10": R[10][t], "r20": R[20][t],
                               "up_room": (top[t] / c[t] - 1) * 100, "down_room": (c[t] / bot[t] - 1) * 100, **conds})

    # ── 정리 ──
    bm = {k: {f: round(float(np.nanmean(base.get(k, f))), 2) for f in base.d[k]} for k in base.d}
    out = {"baseline": bm, "A": {}, "B": {}, "C": {}, "D": {}, "E": {}, "F": {}, "G": {}}
    for key in F.d:
        if key.startswith("(기준)"):
            continue
        cn = key.split(" | ")[1]
        out["F"][key] = {f"r{h}": _stat(F.get(key, f"r{h}"), float(np.nanmean(F.get(f"(기준) {cn}", f"r{h}")))) for h in (5, 10, 20)}
        out["F"][key]["mae10"] = _stat(F.get(key, "mae10")).get("mean")
    for key in G.d:
        out["G"][key] = {f"r{h}": _stat(G.get(key, f"r{h}"), bm["all"][f"r{h}"]) for h in (5, 10, 20)}
        out["G"][key]["mae10"] = _stat(G.get(key, "mae10")).get("mean")
    for key in A.d:
        bk = "recent" if key.endswith("|recent") else "earlier" if key.endswith("|earlier") else "all"
        out["A"][key] = {f"r{h}": _stat(A.get(key, f"r{h}"), bm[bk][f"r{h}"]) for h in (5, 10, 20)}
        out["A"][key]["mae10"] = _stat(A.get(key, "mae10")).get("mean")
    for key in B.d:
        out["B"][key] = {f"r{h}": _stat(B.get(key, f"r{h}"), bm["all"][f"r{h}"]) for h in (5, 10, 20)}
        out["B"][key]["mae10"] = _stat(B.get(key, "mae10")).get("mean")
    for key in Cx.d:
        out["C"][key] = {f"r{h}": _stat(Cx.get(key, f"r{h}"), bm["all"][f"r{h}"]) for h in (5, 10, 20)}
        if "mae10" in Cx.d[key]:
            out["C"][key]["mae10"] = _stat(Cx.get(key, "mae10")).get("mean")
    for key in D.d:
        out["D"][key] = {f"r{h}": _stat(D.get(key, f"r{h}")) for h in (20, 40, 60)}
        m60 = D.get(key, "mae60")
        m60 = m60[~np.isnan(m60)]
        if len(m60):
            out["D"][key]["mae60_mean"] = round(float(m60.mean()), 1)
            out["D"][key]["mae60_p5"] = round(float(np.percentile(m60, 5)), 1)
            out["D"][key]["down20_pct"] = round(float((m60 <= -20).mean() * 100), 1)   # 60일 안에 -20% 넘게 물린 비율

    cr = pd.DataFrame(cloud_rows)
    if len(cr):
        def summ(x):
            if len(x) == 0:
                return {"n": 0}
            return {"n": int(len(x)), "top": round(float((x["res"] == "top").mean() * 100), 1),
                    "bottom": round(float((x["res"] == "bottom").mean() * 100), 1),
                    "r20": round(float(x["r20"].mean()), 2),
                    "top_recent": round(float((x[x["recent"]]["res"] == "top").mean() * 100), 1) if x["recent"].any() else None,
                    "top_earlier": round(float((x[~x["recent"]]["res"] == "top").mean() * 100), 1) if (~x["recent"]).any() else None,
                    "up_room": round(float(x["up_room"].median()), 1), "down_room": round(float(x["down_room"].median()), 1)}
        cond_cols = [k for k in cr.columns if k not in ("res", "recent", "r10", "r20", "up_room", "down_room")]
        out["E"]["전체"] = summ(cr)
        for k in cond_cols:
            out["E"][f"{k} — 예"] = summ(cr[cr[k]])
            out["E"][f"{k} — 아니오"] = summ(cr[~cr[k]])
        score = cr[cond_cols].sum(axis=1)
        for k in sorted(score.unique()):
            out["E"][f"조건 {int(k)}개 충족"] = summ(cr[score == k])

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "result2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    L = ["# 2차 측정", "", f"기준선 평균: 5일 {bm['all']['r5']:+.2f}% · 10일 {bm['all']['r10']:+.2f}% · 20일 {bm['all']['r20']:+.2f}% · 40일 {bm['all']['r40']:+.2f}% · 60일 {bm['all']['r60']:+.2f}%", "",
         "## A. 기준 단계별 (횟수 · 5일 승률 · 5일 기준대비 · 10일 기준대비 · 20일 기준대비 · 10일내 평균낙폭 | 최근 5일 기준대비 / 이전 5일 기준대비)", ""]
    for key, v in out["A"].items():
        if "|" in key or not v["r5"].get("n"):
            continue
        rc, er = out["A"].get(key + "|recent", {}).get("r5", {}), out["A"].get(key + "|earlier", {}).get("r5", {})
        L.append(f"- {key}: {v['r5']['n']} · {v['r5']['win']}% · {v['r5']['edge']:+.2f} · {v['r10']['edge']:+.2f} · {v['r20']['edge']:+.2f} · {_n(v.get('mae10'))}% | {rc.get('edge', 0):+.2f} / {er.get('edge', 0):+.2f}")
    L += ["", "## B. 겹침", ""]
    for key, v in sorted(out["B"].items()):
        if v["r5"].get("n"):
            L.append(f"- {key}: {v['r5']['n']} · 5일 {v['r5']['win']}% {v['r5']['edge']:+.2f} · 10일 {v['r10']['edge']:+.2f} · 20일 {v['r20']['edge']:+.2f} · 낙폭 {_n(v.get('mae10'))}%")
    L += ["", "## C. 배경별 (과매도 뜬 날)", ""]
    for key, v in out["C"].items():
        if v["r5"].get("n"):
            L.append(f"- {key}: {v['r5']['n']} · 5일 {v['r5']['win']}% {v['r5']['edge']:+.2f} · 10일 {v['r10']['edge']:+.2f} · 20일 {v['r20']['edge']:+.2f}")
    L += ["", "## F. 시장 배경별 (횟수 · 5일 승률 · 5일 / 10일 / 20일 '같은 배경의 아무 종목' 대비)", ""]
    for key, v in out["F"].items():
        if v["r5"].get("n"):
            L.append(f"- {key}: {v['r5']['n']} · {v['r5']['win']}% · {v['r5']['edge']:+.2f} / {v['r10'].get('edge', 0):+.2f} / {v['r20'].get('edge', 0):+.2f}")
    L += ["", "## G. 승률표 (횟수 · 5일 승률·평균 · 10일 승률·평균 · 20일 승률·평균 · 10일내 낙폭)", ""]
    for key, v in out["G"].items():
        if v["r5"].get("n"):
            L.append(f"- {key}: {v['r5']['n']} · {v['r5']['win']}% {v['r5']['mean']:+.2f}% · {v['r10'].get('win')}% {v['r10'].get('mean', 0):+.2f}% · {v['r20'].get('win')}% {v['r20'].get('mean', 0):+.2f}% · {_n(v.get('mae10'))}%")
    L += ["", "## D. 오래 들고 있기 (평균 수익 20/40/60일 · 60일내 평균 최대낙폭 · 나쁜 5% · -20% 넘게 물린 비율)", ""]
    for key, v in out["D"].items():
        if v["r20"].get("n"):
            L.append(f"- {key}: {v['r20']['n']} · {v['r20']['mean']:+.2f}% / {v['r40'].get('mean', 0):+.2f}% / {v['r60'].get('mean', 0):+.2f}% · 승률 {v['r60'].get('win', 0)}% · {v.get('mae60_mean')}% · {v.get('mae60_p5')}% · {v.get('down20_pct')}%")
    L += ["", "## E. 구름대 진입 + 조건 (횟수 · 상단먼저 · 하단먼저 · 20일수익 | 상단먼저 최근/이전 · 위 여유/아래 여유)", ""]
    for key, v in out["E"].items():
        if v.get("n"):
            L.append(f"- {key}: {v['n']} · {v['top']}% · {v['bottom']}% · {v['r20']:+.2f}% | {v['top_recent']}/{v['top_earlier']} · +{v['up_room']}%/-{v['down_room']}%")
    with open(os.path.join(OUT, "result2.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
