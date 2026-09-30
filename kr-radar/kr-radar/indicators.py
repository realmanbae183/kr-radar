"""
indicators.py — 차트 지표 계산 (순수 계산만. 점수는 signals.py)

용어 풀이
  이동평균(MA)  : 최근 N일 종가의 평균. 20일선 = 한 달, 60일선 = 석 달, 120일선 = 반 년
  MACD         : 빠른 평균(12일) - 느린 평균(26일). 이게 자기 평균(9일, 시그널선)을 뚫고 올라가면 골든크로스
  RSI          : 최근 14일 동안 오른 힘 / 전체 움직임 (0~100). 30 아래 = 과매도
  스토캐스틱    : 최근 14일 가격 범위 중 지금 가격이 어디쯤인지 (0~100)
  일목균형표     : 구름대(선행스팬A·B 사이 영역)를 지지/저항으로 보는 일본식 지표
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series):
    line = ema(close, C.MACD_FAST) - ema(close, C.MACD_SLOW)
    signal = ema(line, C.MACD_SIGNAL)
    return line, signal, line - signal


def rsi(close: pd.Series, n: int = C.RSI_PERIOD) -> pd.Series:
    """와일더 방식 RSI (HTS와 같은 계산)."""
    d = close.diff()
    up = d.clip(lower=0)
    dn = (-d).clip(lower=0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(ad == 0) & (au > 0)] = 100
    return out


def stoch_slow(h: pd.Series, l: pd.Series, c: pd.Series):
    ll = l.rolling(C.STOCH_K).min()
    hh = h.rolling(C.STOCH_K).max()
    fast_k = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    k = fast_k.rolling(C.STOCH_SMOOTH).mean()
    d = k.rolling(C.STOCH_D).mean()
    return k, d


def ichimoku(h: pd.Series, l: pd.Series):
    """
    반환: tenkan(전환선), kijun(기준선), span_a_raw, span_b_raw
    raw = '오늘 계산된 값'. 차트에서는 26칸 앞(미래)에 그린다.
    그래서 '오늘 가격 옆에 있는 구름' = 26일 전에 계산된 raw 값.
    """
    tenkan = (h.rolling(C.ICHI_TENKAN).max() + l.rolling(C.ICHI_TENKAN).min()) / 2
    kijun = (h.rolling(C.ICHI_KIJUN).max() + l.rolling(C.ICHI_KIJUN).min()) / 2
    span_a_raw = (tenkan + kijun) / 2
    span_b_raw = (h.rolling(C.ICHI_SENKOU_B).max() + l.rolling(C.ICHI_SENKOU_B).min()) / 2
    return tenkan, kijun, span_a_raw, span_b_raw


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 표에 모든 지표 열을 붙여서 돌려준다."""
    out = df.copy()
    c, h, l, v = out["Close"], out["High"], out["Low"], out["Volume"]
    out["ma20"] = sma(c, C.MA_SHORT)
    out["ma60"] = sma(c, C.MA_MID)
    out["ma120"] = sma(c, C.MA_LONG)
    out["macd"], out["macd_sig"], out["macd_hist"] = macd(c)
    out["rsi"] = rsi(c)
    out["rsi_sig"] = out["rsi"].rolling(C.RSI_SIGNAL).mean()
    out["stoch_k"], out["stoch_d"] = stoch_slow(h, l, c)
    tenkan, kijun, a_raw, b_raw = ichimoku(h, l)
    out["tenkan"], out["kijun"] = tenkan, kijun
    out["span_a_raw"], out["span_b_raw"] = a_raw, b_raw
    # 오늘 가격 옆 구름 = 26일 전 계산값
    out["span_a"] = a_raw.shift(C.ICHI_SHIFT)
    out["span_b"] = b_raw.shift(C.ICHI_SHIFT)
    out["cloud_top"] = out[["span_a", "span_b"]].max(axis=1, skipna=False)
    out["cloud_bot"] = out[["span_a", "span_b"]].min(axis=1, skipna=False)
    out["vol_avg"] = v.rolling(C.VOLUME_AVG).mean().shift(1)   # 오늘 제외한 직전 20일 평균
    out["chg"] = c.pct_change() * 100
    return out


def cross_up(a: pd.Series, b) -> pd.Series:
    """a가 b를 아래에서 위로 뚫은 날 True."""
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a > b) & (a.shift(1) <= b.shift(1))


def last_true_ago(mask: pd.Series, lookback: int) -> int | None:
    """최근 lookback봉 안에서 마지막으로 True였던 게 몇 봉 전인지 (0=오늘). 없으면 None."""
    tail = mask.iloc[-lookback:].fillna(False).to_numpy()
    idx = np.flatnonzero(tail)
    if len(idx) == 0:
        return None
    return int(len(tail) - 1 - idx[-1])


def fib_swing(df: pd.DataFrame):
    """
    최근 FIB_LOOKBACK봉 안의 최고점(H)과, 그 이전 구간의 최저점(L)으로 스윙을 잡는다.
    반환 dict 또는 None (의미 있는 스윙이 없음)
    """
    w = df.iloc[-C.FIB_LOOKBACK:]
    if len(w) < 30:
        return None
    ih = int(np.argmax(w["High"].to_numpy()))
    if ih < 5 or ih > len(w) - 4:          # 고점이 너무 처음이거나(저점 구간 없음) 너무 최근(되돌림 전)
        return None
    H = float(w["High"].iloc[ih])
    L = float(w["Low"].iloc[:ih].min())
    if L <= 0 or (H - L) / L * 100 < C.FIB_MIN_SWING_PCT:
        return None
    levels = {str(r): H - r * (H - L) for r in (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1)}
    return {"H": H, "L": L, "high_ago": len(w) - 1 - ih, "levels": levels}
