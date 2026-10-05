"""
signals.py — 종목의 '지금 상태'를 읽고, 과거 성과표에서 같은 상태의 기록을 찾아 붙인다.

보는 순서 (2026-10-05 합의)
  1. 과하게 빠졌나 — RSI 30 아래 / 볼린저 하단 이탈 / 20일선 대비 -10% 이격. 몇 개가 겹쳤나
  2. 배경 — 종목 120일선이 오르는 중인가, 시장이 다 같이 빠진 날인가(시장 폭)
  3. 과거 같은 상태의 성과 (횟수·승률·평균 수익·물린 폭)
  별도: 거래량 실린 구름대 상단 돌파, 구름대 진입 뒤 상단 도달 확률(목표가 참고)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config as C
import history as H
import indicators as I

OS_NAMES = {"rsi": "RSI 30 아래", "bb": "볼린저 하단 이탈", "gap": "20일선 대비 -10% 이격"}
BUCKET_NAMES = {"lt30": "시장 폭 30% 미만", "30_50": "시장 폭 30~50%", "ge50": "시장 폭 50% 이상", "*": "시장 폭 무관"}


def _f(x, nd=2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def cell_label(key: str | None) -> str:
    """성과표 칸 이름을 사람이 읽는 말로."""
    if not key:
        return ""
    if key.count("|") == 2:
        k, t, b = key.split("|")
        parts = [f"과매도 {k}개" + (" 겹침" if k in ("2", "3") else "")]
        if t != "*":
            parts.append("종목 120일선 상승 중" if t == "U" else "종목 120일선 하락 중")
        if b != "*":
            parts.append(BUCKET_NAMES[b])
        return " · ".join(parts)
    t, m = key.split("|")
    if t == "*":
        return "거래량 실린 구름대 상단 돌파 전체"
    return ("종목 120일선 상승 중" if t == "U" else "종목 120일선 하락 중") + " · " + \
           ("코스피 20·120일선 모두 위" if m == "both_up" else "코스피가 20일선 또는 120일선 아래")


def _last_event(mask: pd.Series, lookback: int) -> int | None:
    tail = mask.iloc[-lookback:].to_numpy()
    idx = np.flatnonzero(tail)
    return None if len(idx) == 0 else int(len(tail) - 1 - idx[-1])


def depth_note(rsi, gap) -> str | None:
    """얼마나 깊이 빠졌나 — 깊을수록 과거 성과가 좋았다."""
    notes = []
    if rsi is not None and not pd.isna(rsi):
        if rsi < 20:
            notes.append("RSI 20 아래 (매우 깊음)")
        elif rsi < 25:
            notes.append("RSI 25 아래 (깊음)")
    if gap is not None and not pd.isna(gap):
        if gap <= -20:
            notes.append("이격 -20% 이상 (매우 깊음)")
        elif gap <= -15:
            notes.append("이격 -15% 이상 (깊음)")
    return " · ".join(notes) or None


def analyze(d: pd.DataFrame, breadth: pd.Series, market: pd.DataFrame, table: dict | None) -> dict:
    """d: indicators.compute_all 을 거친 일봉."""
    last = d.iloc[-1]
    n = len(d)
    close = float(last["Close"])
    br = breadth.reindex(d.index)
    mk = market.reindex(d.index)

    # ── 과매도 ──
    flags = {"rsi": bool(last["os_rsi"]), "bb": bool(last["os_bb"]), "gap": bool(last["os_gap"])}
    os_ = {
        "flags": flags, "n_now": int(last["n_os"]),
        "values": {"rsi": _f(last["rsi"], 1), "z": _f(last["z"]), "gap": _f(last["gap"], 1), "atrgap": _f(last["atrgap"], 1)},
        "event": None, "hist": None, "hist_key": None, "hist_label": None, "hist_level": None, "grade": None,
        "depth": depth_note(last["rsi"], last["gap"]),
    }
    ago = _last_event(d["os_event"] & d["valid"], C.SIGNAL_LOOKBACK)
    if ago is not None:
        t = n - 1 - ago
        e = d.iloc[t]
        bucket = I.breadth_bucket(br.iloc[t])
        look = H.lookup_os(table, int(e["n_os"]), bool(e["trend_up"]), bucket)
        os_["event"] = {
            "ago": ago, "date": d.index[t].strftime("%Y-%m-%d"), "n": int(e["n_os"]),
            "flags": {"rsi": bool(e["os_rsi"]), "bb": bool(e["os_bb"]), "gap": bool(e["os_gap"])},
            "trend_up": bool(e["trend_up"]), "breadth": _f(br.iloc[t], 1), "bucket": bucket,
            "close": _f(e["Close"], 0), "ret_since": _f((close / e["Close"] - 1) * 100, 1),
            "rsi": _f(e["rsi"], 1), "gap": _f(e["gap"], 1),
            "depth": depth_note(e["rsi"], e["gap"]),
        }
        os_.update({"hist": look["stat"], "hist_key": look["key"], "hist_label": cell_label(look["key"]),
                    "hist_level": look["level"], "grade": H.grade(look["stat"])})

    # ── 돌파 ──
    bo = None
    ago = _last_event(d["bo_event"] & d["valid"], C.SIGNAL_LOOKBACK)
    if ago is not None and close > float(last["cloud_top"]) * 0.99:       # 돌파 뒤 다시 구름 속으로 들어갔으면 무효
        t = n - 1 - ago
        e = d.iloc[t]
        both = bool(mk["up20"].iloc[t]) and bool(mk["up120"].iloc[t]) if not pd.isna(mk["close"].iloc[t]) else False
        look = H.lookup_bo(table, bool(e["trend_up"]), both)
        bo = {"ago": ago, "date": d.index[t].strftime("%Y-%m-%d"), "vol_mult": _f(e["vol_mult"], 1),
              "trend_up": bool(e["trend_up"]), "both_up": both, "close": _f(e["Close"], 0),
              "ret_since": _f((close / e["Close"] - 1) * 100, 1),
              "hist": look["stat"], "hist_key": look["key"], "hist_label": cell_label(look["key"]),
              "hist_level": look["level"], "grade": H.grade(look["stat"])}

    # ── 구름대 (목표가 참고) ──
    cloud = None
    top, bot = last["cloud_top"], last["cloud_bot"]
    if not pd.isna(top) and bot <= close < top:
        ago = _last_event(d["cloud_in"], 20)
        if ago is not None:
            t = n - 1 - ago
            seg = d.iloc[t + 1:]
            touched = bool((seg["High"] >= seg["cloud_top"]).any()) if len(seg) else False
            fell = bool((seg["Close"] < seg["cloud_bot"]).any()) if len(seg) else False
            if not touched and not fell:
                conds = I.cloud_conditions(d, t)
                cnt = sum(conds.values())
                key = "0-1" if cnt <= 1 else str(min(cnt, 6))
                cloud = {"entered_ago": ago, "conds": conds, "count": cnt,
                         "top": _f(top, 0), "bot": _f(bot, 0),
                         "up_room": _f((top / close - 1) * 100, 1), "down_room": _f((close / bot - 1) * 100, 1),
                         "hist": ((table or {}).get("cloud") or {}).get(key)}

    position = "구름 위" if (not pd.isna(top) and close >= top) else "구름 안" if (not pd.isna(bot) and close >= bot) else \
        "구름 아래" if not pd.isna(bot) else None
    return {
        "os": os_, "bo": bo, "cloud": cloud,
        "trend_up": bool(last["trend_up"]),
        "stats": {
            "close": _f(close, 0), "chg": _f(last["chg"]), "date": d.index[-1].strftime("%Y-%m-%d"),
            "rsi": _f(last["rsi"], 1), "gap": _f(last["gap"], 1), "z": _f(last["z"]),
            "vs_ma60": _f((close / last["ma60"] - 1) * 100, 1) if not pd.isna(last["ma60"]) else None,
            "vs_ma120": _f((close / last["ma120"] - 1) * 100, 1) if not pd.isna(last["ma120"]) else None,
            "ma120_slope": _f((last["ma120"] / d["ma120"].iloc[-1 - C.TREND_SLOPE_DAYS] - 1) * 100, 1)
            if n > C.TREND_SLOPE_DAYS + 1 and not pd.isna(d["ma120"].iloc[-1 - C.TREND_SLOPE_DAYS]) else None,
            "drawdown": _f((close / d["High"].iloc[-250:].max() - 1) * 100, 1),
            "vol_mult": _f(last["vol_mult"], 1), "cloud_pos": position,
        },
        "chart": chart_payload(d),
    }


def chart_payload(d: pd.DataFrame) -> dict:
    """웹페이지 캔들차트용 (최근 CHART_BARS봉 + 앞으로 26칸 구름)."""
    t = d.iloc[-C.CHART_BARS:]
    r0 = lambda s: [None if pd.isna(x) else int(round(float(x))) for x in s]
    base = len(d) - len(t)
    marks = []
    for i in np.flatnonzero((t["os_event"] & t["valid"]).to_numpy()):
        marks.append({"i": int(i), "k": "os", "n": int(t["n_os"].iloc[i])})
    for i in np.flatnonzero((t["bo_event"] & t["valid"]).to_numpy()):
        marks.append({"i": int(i), "k": "bo"})
    return {
        "d": [x.strftime("%y%m%d") for x in t.index],
        "o": r0(t["Open"]), "h": r0(t["High"]), "l": r0(t["Low"]), "c": r0(t["Close"]),
        "v": [int(x) if not pd.isna(x) else 0 for x in t["Volume"]],
        "ma20": r0(t["ma20"]), "ma60": r0(t["ma60"]), "ma120": r0(t["ma120"]), "bbl": r0(t["bb_low"]),
        "sa": r0(t["span_a"]) + r0(d["span_a_raw"].iloc[-C.ICHI_SHIFT:]),
        "sb": r0(t["span_b"]) + r0(d["span_b_raw"].iloc[-C.ICHI_SHIFT:]),
        "marks": marks,
    }


def market_summary(breadth: pd.Series, market: pd.DataFrame, table: dict | None, n_stocks: int) -> dict:
    """화면 맨 위 '지금 어떤 장인가'."""
    b = breadth.dropna()
    cur = float(b.iloc[-1]) if len(b) else None
    bucket = I.breadth_bucket(cur)
    m = market.dropna(subset=["close"])
    last = m.iloc[-1] if len(m) else None
    base = (table or {}).get("baseline") or {}
    os2 = (table or {}).get("os") or {}
    return {
        "breadth": _f(cur, 1), "bucket": bucket, "bucket_name": BUCKET_NAMES.get(bucket),
        "above": int(round(cur / 100 * n_stocks)) if cur is not None else None, "stocks": n_stocks,
        "breadth_hist": [None if pd.isna(x) else round(float(x), 1) for x in breadth.iloc[-60:]],
        "breadth_dates": [x.strftime("%m/%d") for x in breadth.iloc[-60:].index],
        "kospi": _f(last["close"], 2) if last is not None else None,
        "kospi_chg": _f(m["close"].pct_change().iloc[-1] * 100, 2) if len(m) > 1 else None,
        "up20": bool(last["up20"]) if last is not None else None,
        "up120": bool(last["up120"]) if last is not None else None,
        "vs20": _f((last["close"] / last["ma20"] - 1) * 100, 1) if last is not None and not pd.isna(last["ma20"]) else None,
        "vs120": _f((last["close"] / last["ma120"] - 1) * 100, 1) if last is not None and not pd.isna(last["ma120"]) else None,
        "vol": _f(last["vol"], 1) if last is not None else None,
        # 오늘 같은 시장 폭에서 과매도 2개 겹침의 과거 성과 (종목 추세 무관) 와 기준선
        "os2_here": os2.get(f"2|*|{bucket}"), "os3_here": os2.get(f"3|*|{bucket}"),
        "base_here": base.get(bucket), "base_all": base.get("all"),
    }
