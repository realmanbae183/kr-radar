"""
measure.py — 종목을 늘려도 규칙이 통하는지 재보는 일회성 조사 (2026-10-06).

코스피 전 종목 + 코스닥 시총 상위 150을 받아서 네 묶음으로 나눈 뒤,
지금 쓰는 성과표(history.build)와, 지난번에 효과 없다고 뺀 신호까지 포함한 전체 신호 성과를
묶음마다 따로 만들어 state/measure/expand.json 에 저장한다.
  K200     코스피200
  KREST_A  코스피200 밖 · 시가총액 큰 절반
  KREST_B  코스피200 밖 · 시가총액 작은 절반
  KQ150    코스닥 시총 상위 150
웹페이지에는 아무 영향이 없다. GitHub Actions 의 measure 를 손으로 한 번 돌린다.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import config as C
import history as H
import indicators as I
import sources as S
from universe import is_excluded

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "state", "measure")


def extra_signals(ds: dict, breadth: pd.Series, market: pd.DataFrame) -> dict:
    """지난번에 효과 없다고 뺀 신호까지 전부 다시 잰다. 신호가 '새로 뜬 날' 종가에 샀다고 가정."""
    bag = H.Bag()
    bb = breadth.map(I.breadth_bucket)
    cu = I.cross_up
    for code, d in ds.items():
        if len(d) < 200:
            continue
        c, o, h, l, v = d["Close"], d["Open"], d["High"], d["Low"], d["Volume"]
        ca, lo = c.to_numpy(float), l.to_numpy(float)
        cols = {f"r{k}": H._fwd(ca, k) for k in C.HORIZONS}
        cols["mae10"] = H._mae(ca, lo, 10)
        cols["date"] = d.index.values.astype("datetime64[D]").astype(np.int64).astype(float)
        valid = d["valid"].to_numpy()
        up = d["trend_up"].to_numpy()
        bk = bb.reindex(d.index).fillna("*").to_numpy()
        mk = market.reindex(d.index)
        mkt_up = (mk["up20"].fillna(False) & mk["up120"].fillna(False)).to_numpy()
        ll, hh = l.rolling(14).min(), h.rolling(14).max()
        k = ((c - ll) / (hh - ll).replace(0, np.nan) * 100).rolling(3).mean()
        dd = k.rolling(3).mean()
        sd = c.rolling(20).std(ddof=0)
        first = lambda m: (m & ~m.shift(1, fill_value=False))
        align = (d["ma20"] > d["ma60"]) & (d["ma60"] > d["ma120"])
        hi52 = h.rolling(250, min_periods=200).max().shift(1)
        vm = d["vol_mult"]
        sig = {
            "MACD 골든크로스": cu(d["macd"], d["macd_sig"]),
            "MACD 골든크로스 (0 아래에서)": cu(d["macd"], d["macd_sig"]) & (d["macd"] < 0),
            "MACD 0선 상향 돌파": cu(d["macd"], 0.0),
            "RSI 30 상향 돌파 (과매도 탈출)": cu(d["rsi"], 30.0),
            "RSI 50 상향 돌파": cu(d["rsi"], 50.0),
            "RSI 70 상향 돌파": cu(d["rsi"], 70.0),
            "스토캐스틱 골든크로스 (20 아래)": cu(k, dd) & (k < 20),
            "스토캐스틱 골든크로스 (전체)": cu(k, dd),
            "종가 20일선 상향 돌파": cu(c, d["ma20"]),
            "종가 60일선 상향 돌파": cu(c, d["ma60"]),
            "종가 120일선 상향 돌파": cu(c, d["ma120"]),
            "20·60 골든크로스": cu(d["ma20"], d["ma60"]),
            "60·120 골든크로스": cu(d["ma60"], d["ma120"]),
            "정배열 진입 (20>60>120)": first(align.fillna(False)),
            "볼린저 상단 돌파": cu(c, d["ma20"] + 2 * sd),
            "볼린저 하단 복귀 (아래에서 위로)": cu(c, d["bb_low"]),
            "52주 신고가 돌파": first((c > hi52).fillna(False)),
            "거래량 2배 양봉": ((vm >= 2) & (c > o) & (c > c.shift(1))).fillna(False),
            "거래량 3배 양봉": ((vm >= 3) & (c > o) & (c > c.shift(1))).fillna(False),
            "구름대 상단 돌파 (거래량 무관)": cu(c, d["cloud_top"]),
            "구름대 상단 돌파 + 거래량 1.5배": d["bo_event"],
            "구름대 하단 이탈": cu(-c, -d["cloud_bot"]),
            "20일선 하향 이탈": cu(-c, -d["ma20"]),
            "20·60 데드크로스": cu(-d["ma20"], -d["ma60"]),
            "과매도 1개 이상 새로 켜짐": d["os_event"],
            "과매도 2개 이상 겹침": d["os_event"] & (d["n_os"] >= 2),
            "과매도 3개 겹침": d["os_event"] & (d["n_os"] >= 3),
        }
        bag.add("기준선|all", valid, cols)
        for b in ("lt30", "30_50", "ge50"):
            bag.add(f"기준선|{b}", valid & (bk == b), cols)
        bag.add("기준선|U", valid & up, cols)
        bag.add("기준선|D", valid & ~up, cols)
        bag.add("기준선|MU", valid & mkt_up, cols)
        for name, m in sig.items():
            m = m.fillna(False).to_numpy(bool) & valid
            bag.add(f"{name}|all", m, cols)
            for b in ("lt30", "30_50", "ge50"):
                bag.add(f"{name}|{b}", m & (bk == b), cols)
            bag.add(f"{name}|U", m & up, cols)
            bag.add(f"{name}|D", m & ~up, cols)
            bag.add(f"{name}|MU", m & mkt_up, cols)          # 코스피가 20·120일선 모두 위
            bag.add(f"{name}|U+MU", m & up & mkt_up, cols)
    return {k: bag.stat(k) for k in bag.d}


def main():
    t0 = time.time()
    notes = []
    k200 = S.fetch_kospi200()
    k200_codes = {c for c, _ in k200.data} if k200.ok else set()
    kospi = S.fetch_marcap_json("KOSPI", 25)
    kosdaq = S.fetch_marcap_json("KOSDAQ", 5)
    if not kospi.data or not kosdaq.data:
        raise SystemExit(f"명단 실패: {kospi.error} / {kosdaq.error}")
    if not kospi.ok:
        notes.append("코스피 명단 일부만 받음: " + str(kospi.error))
    raw_kospi = len(kospi.data)
    kp = [r for r in kospi.data if not is_excluded(r["code"], r["name"])]
    kq = [r for r in kosdaq.data if not is_excluded(r["code"], r["name"])][:150]
    if not k200_codes:
        notes.append("코스피200 명단 실패 → 시총 상위 200으로 대체: " + str(k200.error))
        k200_codes = {r["code"] for r in kp[:200]}
    rest = [r for r in kp if r["code"] not in k200_codes]
    half = len(rest) // 2
    group = {}
    for r in kp:
        if r["code"] in k200_codes:
            group[r["code"]] = "K200"
    for i, r in enumerate(rest):
        group[r["code"]] = "KREST_A" if i < half else "KREST_B"
    for r in kq:
        group.setdefault(r["code"], "KQ150")
    info = {r["code"]: {"code": r["code"], "name": r["name"], "marcap": r.get("marcap"),
                        "market": "KOSDAQ" if group[r["code"]] == "KQ150" else "KOSPI"} for r in kp + kq if r["code"] in group}
    codes = list(info)
    print(f"명단: 코스피 원본 {raw_kospi} → 제외 후 {len(kp)} (200 안 {sum(v == 'K200' for v in group.values())}, 밖 {len(rest)}) · 코스닥 {len(kq)} · 합 {len(codes)}")

    def one(code):
        try:
            r = S.fetch_prices(code, C.HISTORY_BARS)
            return code, (r.data if r.ok else None), ("" if r.ok else r.error)
        except Exception as e:
            return code, None, str(e)

    prepared, fail = {}, {}
    with ThreadPoolExecutor(max_workers=C.MAX_WORKERS) as ex:
        for i, (code, df, err) in enumerate(ex.map(one, codes), 1):
            if df is None:
                fail[code] = err
            else:
                try:
                    prepared[code] = I.compute_all(df)
                except Exception as e:
                    fail[code] = f"지표 오류: {e}"
            if i % 100 == 0:
                print(f"  {i}/{len(codes)} · {time.time() - t0:.0f}초")
    t_fetch = time.time() - t0
    print(f"시세 완료: 성공 {len(prepared)} · 실패 {len(fail)} · {t_fetch:.0f}초")

    ix = S.fetch_prices("KOSPI", C.HISTORY_BARS)
    if not ix.ok:
        raise SystemExit("코스피 지수 실패: " + str(ix.error))
    market = I.market_frame(ix.data["Close"])

    by = {g: {c: d for c, d in prepared.items() if group[c] == g} for g in ("K200", "KREST_A", "KREST_B", "KQ150")}
    now350 = {**by["K200"], **by["KQ150"]}
    br350 = I.breadth_series(now350)
    br_all = I.breadth_series(prepared)
    both = pd.concat([br350, br_all], axis=1, keys=["b350", "ball"]).dropna()
    bucket = lambda s: s.map(I.breadth_bucket).value_counts().to_dict()

    out = {"built": time.strftime("%Y-%m-%d %H:%M"), "notes": notes,
           "counts": {"kospi_raw": raw_kospi, "kospi_after_exclude": len(kp), "total": len(codes),
                      "ok": len(prepared), "fail": len(fail)},
           "seconds": {"prices": round(t_fetch)},
           "fail_samples": dict(list(fail.items())[:8]),
           "breadth": {"corr": round(float(both["b350"].corr(both["ball"])), 3),
                       "mean_diff": round(float((both["ball"] - both["b350"]).mean()), 2),
                       "days_350": bucket(both["b350"]), "days_all": bucket(both["ball"]),
                       "last_350": round(float(both["b350"].iloc[-1]), 1), "last_all": round(float(both["ball"].iloc[-1]), 1)},
           "groups": {}}

    def profile(ds):
        bars, val, halt, mc = [], [], [], []
        for c, d in ds.items():
            bars.append(len(d))
            t = d.tail(60)
            val.append(float((t["Close"] * t["Volume"]).mean() / 1e8))
            halt.append(float((d["Volume"].tail(250) == 0).mean() * 100))
            if info[c].get("marcap"):
                mc.append(info[c]["marcap"])
        q = lambda a, p: round(float(np.percentile(a, p)), 1) if a else None
        return {"stocks": len(ds), "short_history": int(sum(b < 200 for b in bars)), "bars_median": q(bars, 50),
                "trade_value_eok": {"p10": q(val, 10), "p50": q(val, 50), "p90": q(val, 90)},
                "under_5eok_pct": round(float(np.mean([v < 5 for v in val]) * 100), 1) if val else None,
                "halt_day_pct_mean": round(float(np.mean(halt)), 2) if halt else None,
                "marcap_eok": {"p10": q(mc, 10), "p50": q(mc, 50), "p90": q(mc, 90)}}

    sets = dict(by)
    sets["NOW350"] = now350
    sets["KREST"] = {**by["KREST_A"], **by["KREST_B"]}
    sets["ALL"] = prepared
    for g, ds in sets.items():
        if len(ds) < 20:
            out["groups"][g] = {"profile": profile(ds), "error": "종목이 20개 미만"}
            continue
        try:
            tb = H.build(ds, br350, market, info)                 # 시장 폭은 지금 쓰는 350종목 기준 그대로
            tb2 = H.build(ds, br_all, market, info) if g in ("KREST", "ALL") else None
            out["groups"][g] = {"profile": profile(ds), "table": tb, "table_breadth_all": tb2,
                                "signals": extra_signals(ds, br350, market)}
        except Exception as e:
            out["groups"][g] = {"profile": profile(ds), "error": str(e)}
        o = (out["groups"][g].get("table") or {}).get("os", {})
        b = (out["groups"][g].get("table") or {}).get("baseline", {}).get("all")
        print(g, len(ds), "기준선", b and (b.get("w5"), b.get("m5")), "2겹", o.get("2|*|*") and (o["2|*|*"]["n"], o["2|*|*"]["w5"], o["2|*|*"]["m5"]))
    out["seconds"]["total"] = round(time.time() - t0)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "expand.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), default=str)
    print("저장:", os.path.join(OUT, "expand.json"), f"· 전체 {out['seconds']['total']}초")


if __name__ == "__main__":
    main()
