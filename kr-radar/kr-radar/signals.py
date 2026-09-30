"""
signals.py — ③ 차트 시그널 판정 + 국면(차트 모양) 판정 + 차트 점수(100점)

시그널은 3묶음
  반등 신호 : MACD 골든크로스, RSI 골든크로스, RSI 30 상향 돌파, 스토캐스틱 골든크로스
  과매도 위치: 구름대 하단 이탈, 피보나치 0.5~0.618 지지
  돌파 신호 : 구름대 상단 돌파, 20일선·60일선 돌파, 20/60 골든크로스 (+ 거래량 동반)
모두 '최근 SIGNAL_LOOKBACK(5)거래일 안에' 뜬 것만 인정.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config as C
import indicators as I

SIGNAL_META = {
    # key: (이름, 묶음, 쉬운 설명)
    "macd_gc": ("MACD 골든크로스", "bounce", "단기 흐름(MACD)이 평균선(시그널)을 뚫고 올라감 → 하락 힘이 약해지고 반등 시작"),
    "rsi_gc": ("RSI 골든크로스", "bounce", "RSI가 자기 평균선을 뚫고 올라감 (RSI 50 아래 바닥권에서만 인정)"),
    "rsi_30_up": ("RSI 30 탈출", "bounce", "과매도 기준선(30)을 아래에서 위로 돌파"),
    "stoch_gc": ("스토캐스틱 골든크로스", "bounce", "%K가 %D를 뚫고 올라감 (바닥권 25 아래에서만 인정)"),
    "cloud_below": ("구름대 하단 이탈", "oversold", "가격이 일목 구름대 아래로 3% 이상 떨어지고 RSI도 35 미만 → 과매도 구간"),
    "fib_support": ("피보나치 지지", "oversold", "직전 상승폭의 50~61.8% 되돌림 구간에서 버티는 중"),
    "cloud_breakout": ("구름대 상단 돌파", "breakout", "가격이 구름대 위로 올라섬 → 추세 전환 신호"),
    "ma20_break": ("20일선 돌파", "breakout", "종가가 20일 이동평균선을 뚫고 올라감"),
    "ma60_break": ("60일선 돌파", "breakout", "종가가 60일 이동평균선을 뚫고 올라감"),
    "ma_gc_20_60": ("20·60 골든크로스", "breakout", "20일선이 60일선을 뚫고 올라감 → 중기 추세 전환"),
    "volume_confirm": ("거래량 동반", "breakout", "돌파한 날 거래량이 평소의 1.5배 이상 → 돌파의 신뢰도↑"),
    "macd_gc_below0": ("0선 아래 골든크로스", "bounce", "MACD 골든크로스가 0선 아래(바닥권)에서 발생"),
}

REGIME_META = {
    "uptrend_pullback": ("상승추세 눌림목", "👑", "큰 흐름은 올라가는 중인데 잠깐 쉬어가는 자리. 가장 좋은 매수 후보 자리."),
    "reversal": ("추세전환 시도", "🔄", "내려가던 종목이 구름대나 60일선을 뚫고 올라오는 중. 안착하면 새 추세의 시작."),
    "uptrend": ("상승추세", "📈", "정배열로 잘 가고 있음. 눌림 없이 올라가는 중이라 추격 매수는 주의."),
    "sideways": ("횡보", "➖", "뚜렷한 방향이 없음. 박스권 위/아래 돌파를 기다리는 구간."),
    "downtrend": ("하락추세", "⚠️", "이평선 역배열 + 구름대 아래. 반등이 나와도 20일선·구름대 하단에서 막히기 쉬움."),
    "pending": ("판정 유보", "⏸", "고점에서 급락한 지 얼마 안 돼서 이평선이 아직 따라오지 못함. 추세 판단은 조금 더 지켜봐야 함."),
}


def _f(x):
    """NaN → None (웹으로 보낼 때)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _slope_pct(s: pd.Series, n: int) -> float | None:
    if len(s) <= n or pd.isna(s.iloc[-1]) or pd.isna(s.iloc[-1 - n]) or s.iloc[-1 - n] == 0:
        return None
    return (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100


def detect_signals(d: pd.DataFrame) -> dict:
    """지표가 붙은 일봉 표 → {signal_key: {"hit":bool, "ago":int|None, "note":str}}"""
    N = C.SIGNAL_LOOKBACK
    c = d["Close"]
    res: dict[str, dict] = {}

    def put(key, ago, note=""):
        res[key] = {"hit": ago is not None, "ago": ago, "note": note}

    # 반등 신호 ─────────────────────────
    gc = I.cross_up(d["macd"], d["macd_sig"])
    ago = I.last_true_ago(gc, N)
    put("macd_gc", ago)
    below0 = ago is not None and d["macd"].iloc[-1 - ago] < 0
    put("macd_gc_below0", ago if below0 else None)

    rgc = I.cross_up(d["rsi"], d["rsi_sig"]) & (d["rsi"] < C.RSI_GC_MAX)
    put("rsi_gc", I.last_true_ago(rgc, N))
    put("rsi_30_up", I.last_true_ago(I.cross_up(d["rsi"], C.RSI_OVERSOLD), N))

    sgc = I.cross_up(d["stoch_k"], d["stoch_d"]) & (d["stoch_k"].shift(1) < C.STOCH_OVERSOLD)
    put("stoch_gc", I.last_true_ago(sgc, N))

    # 과매도 위치 ─────────────────────────
    below = (c < d["cloud_bot"] * (1 - C.CLOUD_BREAK_BELOW_PCT / 100)) & (d["rsi"] < C.CLOUD_BELOW_RSI_MAX)
    ago = I.last_true_ago(below, N)
    gap = None
    if ago is not None:
        cb = d["cloud_bot"].iloc[-1 - ago]
        gap = (c.iloc[-1 - ago] / cb - 1) * 100
    put("cloud_below", ago, f"구름대 하단 대비 {gap:.1f}%" if gap is not None else "")

    fib = I.fib_swing(d)
    fib_ago = None
    if fib:
        H, L = fib["H"], fib["L"]
        lo_zone = C.FIB_ZONE[0] - C.FIB_TOLERANCE
        hi_zone = C.FIB_ZONE[1] + C.FIB_TOLERANCE
        floor_price = H - hi_zone * (H - L)          # 이 아래로 종가가 무너지면 지지 실패
        lows = d["Low"].iloc[-N:]
        retr = (H - lows) / (H - L)
        touched = (retr >= lo_zone) & (retr <= hi_zone + 0.02)
        held = (c.iloc[-C.FIB_HOLD_BARS:] >= floor_price).all()
        if touched.any() and held and fib["high_ago"] >= 5:
            fib_ago = int(len(touched) - 1 - np.flatnonzero(touched.to_numpy())[-1])
        fib["retr_now"] = float((H - c.iloc[-1]) / (H - L))
    put("fib_support", fib_ago,
        f"현재 되돌림 {fib['retr_now']*100:.0f}%" if fib else "의미 있는 상승 스윙 없음")

    # 돌파 신호 ─────────────────────────
    breakout_days: list[int] = []

    cbk = I.cross_up(c, d["cloud_top"])
    ago = I.last_true_ago(cbk, N)
    if ago is not None and not (c.iloc[-1] > d["cloud_top"].iloc[-1] * 0.99):
        ago = None                                     # 돌파 후 다시 구름 속으로 들어갔으면 무효
    put("cloud_breakout", ago)
    if ago is not None:
        breakout_days.append(ago)

    for key, col in (("ma20_break", "ma20"), ("ma60_break", "ma60")):
        ago = I.last_true_ago(I.cross_up(c, d[col]), N)
        if ago is not None and not (c.iloc[-1] > d[col].iloc[-1]):
            ago = None
        put(key, ago)
        if ago is not None:
            breakout_days.append(ago)

    put("ma_gc_20_60", I.last_true_ago(I.cross_up(d["ma20"], d["ma60"]), N))

    vol_ok = None
    for ago in breakout_days:
        i = len(d) - 1 - ago
        va = d["vol_avg"].iloc[i]
        if va and not pd.isna(va) and d["Volume"].iloc[i] >= va * C.VOLUME_SURGE:
            vol_ok = ago if vol_ok is None else min(vol_ok, ago)
    put("volume_confirm", vol_ok)

    return res, fib


def detect_regime(d: pd.DataFrame, signals: dict) -> dict:
    c = d["Close"]
    last = d.iloc[-1]
    close, ma20, ma60, ma120 = last["Close"], last["ma20"], last["ma60"], last["ma120"]
    s120 = _slope_pct(d["ma120"], C.SLOPE_WINDOW)
    s60 = _slope_pct(d["ma60"], C.SLOPE_WINDOW)
    s20 = _slope_pct(d["ma20"], 5)
    w = d.iloc[-250:]
    peak_i = int(np.argmax(w["High"].to_numpy()))
    peak = float(w["High"].iloc[peak_i])
    since_peak = len(w) - 1 - peak_i
    dd = (close / peak - 1) * 100
    hi20 = c.iloc[-20:].max()
    pull = (close / hi20 - 1) * 100
    cloud_top, cloud_bot = last["cloud_top"], last["cloud_bot"]
    above_cloud = not pd.isna(cloud_top) and close > cloud_top
    below_cloud = not pd.isna(cloud_bot) and close < cloud_bot
    aligned_up = all(not pd.isna(x) for x in (ma20, ma60, ma120)) and ma20 > ma60 > ma120
    aligned_down = all(not pd.isna(x) for x in (ma20, ma60, ma120)) and ma20 < ma60 < ma120

    # 20봉 전에 하락 상태였나? (추세전환 판정용)
    past = d.iloc[-21] if len(d) > 21 else d.iloc[0]
    was_down = (not pd.isna(past["cloud_bot"]) and past["Close"] < past["cloud_bot"]) or \
               (not pd.isna(past["ma60"]) and past["ma20"] < past["ma60"])

    reasons = []
    def fmt(x, suf="%"):
        return "–" if x is None or pd.isna(x) else f"{x:+.1f}{suf}"

    if since_peak < C.REGIME_MIN_BARS_SINCE_PEAK and dd <= -C.REGIME_CRASH_PCT:
        key = "pending"
        reasons.append(f"52주 고점 찍은 지 {since_peak}봉, 고점 대비 {dd:.1f}% 급락")
    elif was_down and (above_cloud or signals.get("ma60_break", {}).get("hit") or signals.get("cloud_breakout", {}).get("hit")) \
            and (s20 or 0) > 0:
        key = "reversal"
        reasons.append("20일 전엔 하락 구간(구름대 아래 또는 20일선<60일선)")
        reasons.append("지금은 구름대 위 또는 60일선 돌파" + (", 20일선 고개 듦" if (s20 or 0) > 0 else ""))
    elif (s120 or 0) > 0 and not pd.isna(ma60) and not pd.isna(ma120) and ma60 > ma120 \
            and close >= ma120 * 0.97 and (pull <= -C.PULLBACK_MIN_PCT or close < ma20):
        key = "uptrend_pullback"
        reasons.append(f"120일선 {C.SLOPE_WINDOW}일간 {fmt(s120)} 우상향, 60일선 > 120일선")
        reasons.append(f"20일 고점 대비 {pull:.1f}% 눌림" + (" · 20일선 아래" if close < ma20 else ""))
    elif aligned_up and above_cloud and (s120 or 0) > 0:
        key = "uptrend"
        reasons.append("20 > 60 > 120일선 정배열, 구름대 위")
    elif aligned_down or (below_cloud and (s120 or 0) < 0):
        key = "downtrend"
        reasons.append("20 < 60 < 120일선 역배열" if aligned_down else "구름대 아래 + 120일선 하락")
        reasons.append(f"120일선 {C.SLOPE_WINDOW}일간 {fmt(s120)}")
    else:
        key = "sideways"
        reasons.append("이평선이 엉켜 있음 (정배열도 역배열도 아님)")

    name, icon, desc = REGIME_META[key]
    bounce_hit = any(signals.get(k, {}).get("hit") for k in ("macd_gc", "rsi_gc", "rsi_30_up", "stoch_gc"))
    if key == "downtrend" and bounce_hit:
        name = "하락 중 반등"
        desc = "하락추세 안에서 나온 반등 신호. 추세 전환이 아니라 '기술적 반등'일 가능성이 높아서 목표는 짧게(20일선·구름대 하단)."
    return {
        "key": key, "name": name, "icon": icon, "desc": desc, "reasons": reasons,
        "ma120_slope": _f(s120), "ma60_slope": _f(s60), "drawdown": _f(dd), "since_peak": since_peak,
        "pullback": _f(pull), "above_cloud": bool(above_cloud), "below_cloud": bool(below_cloud),
    }


def chart_score(signals: dict, regime: dict) -> tuple[int, list[dict]]:
    pts = C.CHART_POINTS
    parts = []
    total = 0
    for key, p in pts.items():
        s = signals.get(key)
        if s and s["hit"]:
            total += p
            parts.append({"key": key, "points": p})
    adj = C.REGIME_ADJUST.get(regime["key"], 0)
    if adj:
        parts.append({"key": "regime", "points": adj})
    total += adj
    return int(max(0, min(100, total))), parts


def chart_payload(d: pd.DataFrame, fib: dict | None, signals: dict) -> dict:
    """웹페이지 캔들차트용 데이터 (최근 CHART_BARS봉 + 미래 구름 26칸)."""
    n = C.CHART_BARS
    t = d.iloc[-n:]
    r0 = lambda s: [None if pd.isna(x) else int(round(float(x))) for x in s]
    # 미래 구름: 마지막 26개의 raw 값이 앞으로 26칸에 그려진다
    fut_a = r0(d["span_a_raw"].iloc[-C.ICHI_SHIFT:])
    fut_b = r0(d["span_b_raw"].iloc[-C.ICHI_SHIFT:])
    marks = []
    for k, s in signals.items():
        if s["hit"] and k in SIGNAL_META and k not in ("macd_gc_below0", "volume_confirm"):
            marks.append({"i": len(t) - 1 - s["ago"], "k": k})
    return {
        "d": [x.strftime("%y%m%d") for x in t.index],
        "o": r0(t["Open"]), "h": r0(t["High"]), "l": r0(t["Low"]), "c": r0(t["Close"]),
        "v": [int(x) if not pd.isna(x) else 0 for x in t["Volume"]],
        "ma20": r0(t["ma20"]), "ma60": r0(t["ma60"]), "ma120": r0(t["ma120"]),
        "sa": r0(t["span_a"]) + fut_a, "sb": r0(t["span_b"]) + fut_b,
        "fib": ({k: int(round(v)) for k, v in fib["levels"].items()} if fib else None),
        "marks": marks,
    }


def analyze_chart(prices: pd.DataFrame) -> dict:
    d = I.compute_all(prices)
    sig, fib = detect_signals(d)
    regime = detect_regime(d, sig)
    score, parts = chart_score(sig, regime)
    last = d.iloc[-1]
    hits = [k for k, s in sig.items() if s["hit"]]
    return {
        "score": score,
        "parts": parts,
        "signals": sig,
        "hits": hits,
        "regime": regime,
        "is_signal": score >= C.SIGNAL_MIN_SCORE and bool([h for h in hits if h not in ("macd_gc_below0", "volume_confirm")]),
        "stats": {
            "close": _f(last["Close"]), "chg": _f(last["chg"]), "rsi": _f(last["rsi"]),
            "macd": _f(last["macd"]), "stoch_k": _f(last["stoch_k"]),
            "vs_ma20": _f((last["Close"] / last["ma20"] - 1) * 100) if last["ma20"] else None,
            "vs_ma60": _f((last["Close"] / last["ma60"] - 1) * 100) if last["ma60"] else None,
            "vs_ma120": _f((last["Close"] / last["ma120"] - 1) * 100) if last["ma120"] else None,
            "drawdown": regime["drawdown"],
            "date": d.index[-1].strftime("%Y-%m-%d"),
        },
        "chart": chart_payload(d, fib, sig),
        "fib": ({"H": fib["H"], "L": fib["L"], "retr_now": fib.get("retr_now")} if fib else None),
        "_df": d,
    }
