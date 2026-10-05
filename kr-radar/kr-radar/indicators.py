"""
indicators.py — 일봉에 지표와 신호 표시를 붙인다 (계산만. 해석은 signals.py, 과거 성과는 history.py)

쓰는 지표
  이동평균선  최근 20·60·120일 종가 평균
  RSI        최근 14일 동안 오른 힘의 비중(0~100). 30 아래 = 과하게 빠짐
  볼린저 z    종가가 20일 평균에서 '평소 흔들림'의 몇 배만큼 떨어져 있나. -2 아래 = 볼린저 하단 이탈
  이격       종가가 20일선보다 몇 % 위/아래인가
  ATR        그 종목의 평소 하루 변동폭
  일목 구름대  선행스팬 A·B 사이 띠 (돌파·목표가 참고용)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


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


def cross_up(a: pd.Series, b) -> pd.Series:
    """a가 b를 아래에서 위로 뚫은 날 True."""
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a > b) & (a.shift(1) <= b.shift(1))


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c, h, l, v = out["Close"], out["High"], out["Low"], out["Volume"]
    out["ma20"], out["ma60"], out["ma120"] = sma(c, 20), sma(c, 60), sma(c, 120)
    out["rsi"] = rsi(c)
    macd = ema(c, C.MACD_FAST) - ema(c, C.MACD_SLOW)
    out["macd"], out["macd_sig"] = macd, ema(macd, C.MACD_SIGNAL)

    sd = c.rolling(20).std(ddof=0)
    out["bb_low"] = out["ma20"] - C.OS_BB_Z * sd
    out["z"] = (c - out["ma20"]) / sd.replace(0, np.nan)
    out["gap"] = (c / out["ma20"] - 1) * 100
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14).mean()
    out["atrgap"] = (out["ma20"] - c) / out["atr"].replace(0, np.nan)

    # 일목 구름대: 오늘 계산한 값은 26칸 앞에 그려진다 → 오늘 가격 옆 구름 = 26일 전 계산값
    tenkan = (h.rolling(C.ICHI_TENKAN).max() + l.rolling(C.ICHI_TENKAN).min()) / 2
    kijun = (h.rolling(C.ICHI_KIJUN).max() + l.rolling(C.ICHI_KIJUN).min()) / 2
    out["span_a_raw"] = (tenkan + kijun) / 2
    out["span_b_raw"] = (h.rolling(C.ICHI_SENKOU_B).max() + l.rolling(C.ICHI_SENKOU_B).min()) / 2
    out["span_a"] = out["span_a_raw"].shift(C.ICHI_SHIFT)
    out["span_b"] = out["span_b_raw"].shift(C.ICHI_SHIFT)
    out["cloud_top"] = out[["span_a", "span_b"]].max(axis=1, skipna=False)
    out["cloud_bot"] = out[["span_a", "span_b"]].min(axis=1, skipna=False)

    out["vol_avg"] = v.rolling(C.VOLUME_AVG).mean().shift(1)     # 오늘을 뺀 직전 20일 평균
    out["vol_mult"] = v / out["vol_avg"].replace(0, np.nan)
    out["chg"] = c.pct_change() * 100
    out["trend_up"] = (out["ma120"] > out["ma120"].shift(C.TREND_SLOPE_DAYS)).fillna(False)

    # ── 신호 표시 ──
    out["os_rsi"] = (out["rsi"] < C.OS_RSI).fillna(False)
    out["os_bb"] = (out["z"] < -C.OS_BB_Z).fillna(False)
    out["os_gap"] = (out["gap"] <= -C.OS_GAP_PCT).fillna(False)
    out["n_os"] = out["os_rsi"].astype(int) + out["os_bb"].astype(int) + out["os_gap"].astype(int)
    # 과매도 '신호일' = 세 조건 중 하나가 새로 켜진 날 (측정에 쓴 정의와 같음)
    newly = pd.Series(False, index=out.index)
    for k in ("os_rsi", "os_bb", "os_gap"):
        newly |= out[k] & ~out[k].shift(1, fill_value=False)
    out["os_event"] = newly
    # 돌파 '신호일' = 종가가 구름대 상단을 뚫고 올라갔고 거래량이 1.5배 이상
    out["bo_event"] = (cross_up(c, out["cloud_top"]) & (out["vol_mult"] >= C.BREAKOUT_VOLUME)).fillna(False)
    # 구름대 진입일 = 어제는 구름 아래, 오늘은 구름 안
    out["cloud_in"] = ((c.shift(1) < out["cloud_bot"].shift(1)) & (c >= out["cloud_bot"]) & (c < out["cloud_top"])).fillna(False)
    out["valid"] = out["ma120"].notna() & out["cloud_top"].notna()
    return out


def cloud_conditions(d: pd.DataFrame, t: int) -> dict[str, bool]:
    """구름대 진입일(t)에 붙어 있던 조건들 — 측정에서 상단 도달 확률을 올린 것만."""
    r = d.iloc[t]
    return {
        "구름 얇음": bool((r["cloud_top"] / r["cloud_bot"] - 1) * 100 < C.CLOUD_THIN_PCT),
        "진입일 거래량 1.5배": bool(r["vol_mult"] >= C.BREAKOUT_VOLUME) if not pd.isna(r["vol_mult"]) else False,
        "RSI 50 이상": bool(r["rsi"] >= 50) if not pd.isna(r["rsi"]) else False,
        "MACD가 시그널 위": bool(r["macd"] > r["macd_sig"]),
        "120일선 상승 중": bool(r["trend_up"]),
        "앞쪽 구름이 양운": bool(r["span_a_raw"] > r["span_b_raw"]) if not pd.isna(r["span_b_raw"]) else False,
    }


def breadth_series(prepared: dict[str, pd.DataFrame]) -> pd.Series:
    """시장 폭: 날짜별로 '종가가 20일선 위인 종목'의 비율(%)."""
    above = pd.DataFrame({k: (d["Close"] > d["ma20"]) & d["ma20"].notna() for k, d in prepared.items()})
    has = pd.DataFrame({k: d["ma20"].notna() for k, d in prepared.items()})
    n = has.sum(axis=1)
    return (above.sum(axis=1) / n.where(n >= 20) * 100)      # 종목이 20개도 안 잡힌 날은 계산하지 않음


def breadth_bucket(x) -> str:
    if x is None or pd.isna(x):
        return "*"
    lo, hi = C.BREADTH_BUCKETS
    return "lt30" if x < lo else "30_50" if x < hi else "ge50"


def market_frame(index_close: pd.Series) -> pd.DataFrame:
    """코스피 지수: 20일선·120일선 위인지, 최근 변동성(연율 %)."""
    m = pd.DataFrame({"close": index_close})
    m["ma20"], m["ma120"] = sma(index_close, 20), sma(index_close, 120)
    m["up20"] = (m["close"] > m["ma20"]).fillna(False)
    m["up120"] = (m["close"] > m["ma120"]).fillna(False)
    m["vol"] = index_close.pct_change().rolling(20).std() * np.sqrt(252) * 100
    return m
