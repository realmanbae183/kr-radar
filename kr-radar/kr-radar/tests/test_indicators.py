"""지표·시그널·국면 판정 검증 (python -m pytest tests)"""
import numpy as np
import pandas as pd
import pytest

import config as C
import indicators as I
import signals as SG


def make_df(close, vol=None, spread=0.01):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2025-01-01", periods=len(close))
    op = np.concatenate([[close[0]], close[:-1]])
    hi = np.maximum(op, close) * (1 + spread)
    lo = np.minimum(op, close) * (1 - spread)
    v = np.full(len(close), 1_000_000.0) if vol is None else np.asarray(vol, float)
    return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": v}, index=idx)


# ── 기본 지표 ──
def test_rsi_all_up_is_100_all_down_is_0():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert I.rsi(up).iloc[-1] == pytest.approx(100)
    dn = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert I.rsi(dn).iloc[-1] == pytest.approx(0)


def test_rsi_matches_wilder_by_hand():
    rng = np.random.default_rng(1)
    c = pd.Series(100 + rng.normal(0, 1, 80).cumsum())
    d = c.diff()
    au = d.clip(lower=0).iloc[1:15].mean()
    ad = (-d).clip(lower=0).iloc[1:15].mean()
    for x in d.iloc[15:]:
        au = (au * 13 + max(x, 0)) / 14
        ad = (ad * 13 + max(-x, 0)) / 14
    manual = 100 - 100 / (1 + au / ad)
    # ewm 초기값 차이로 초반엔 다르지만 80봉이면 거의 같아짐
    assert I.rsi(c).iloc[-1] == pytest.approx(manual, abs=0.5)


def test_cross_up_and_ago():
    a = pd.Series([1, 2, 3, 5, 6, 4, 7.0])
    b = pd.Series([4.0] * 7)
    m = I.cross_up(a, b)
    assert list(m) == [False, False, False, True, False, False, True]
    assert I.last_true_ago(m, 5) == 0
    assert I.last_true_ago(m.iloc[:-1], 5) == 2
    assert I.last_true_ago(pd.Series([False] * 7), 5) is None


def test_ichimoku_cloud_is_shifted_26():
    df = make_df(100 + np.sin(np.arange(200) / 7) * 10)
    d = I.compute_all(df)
    assert d["span_a"].iloc[-1] == pytest.approx(d["span_a_raw"].iloc[-1 - C.ICHI_SHIFT])
    assert d["span_b"].iloc[-1] == pytest.approx(d["span_b_raw"].iloc[-1 - C.ICHI_SHIFT])
    assert d["cloud_top"].iloc[-1] >= d["cloud_bot"].iloc[-1]


# ── 시그널 ──
def test_macd_golden_cross_after_v_bottom():
    close = np.concatenate([np.linspace(200, 120, 150), np.linspace(120, 126, 4)])
    d = I.compute_all(make_df(close))
    sig, _ = SG.detect_signals(d)
    assert sig["macd_gc"]["hit"]
    assert sig["macd_gc_below0"]["hit"]          # 바닥권(0선 아래)에서 발생


def test_no_signal_in_steady_downtrend():
    d = I.compute_all(make_df(np.linspace(300, 100, 250)))
    sig, _ = SG.detect_signals(d)
    assert not sig["macd_gc"]["hit"]
    assert not sig["cloud_breakout"]["hit"]
    assert not sig["ma20_break"]["hit"]


def test_cloud_breakout_with_volume():
    close = np.concatenate([np.full(200, 100.0) + np.sin(np.arange(200)) * 0.5, [100, 100, 108, 110]])
    vol = np.full(len(close), 1e6)
    vol[-2] = 3e6                                # 돌파한 날 거래량 3배
    d = I.compute_all(make_df(close, vol, spread=0.003))
    sig, _ = SG.detect_signals(d)
    assert sig["cloud_breakout"]["hit"] and sig["cloud_breakout"]["ago"] == 1
    assert sig["volume_confirm"]["hit"]
    assert sig["ma20_break"]["hit"]


def test_fibonacci_support_in_golden_zone():
    up = np.linspace(100, 150, 60)            # 100 → 150 상승 (스윙 50%)
    down = np.linspace(150, 122, 20)          # 되돌림 (150-122)/50 = 56%
    hold = np.array([122.5, 123, 123.5])
    close = np.concatenate([np.full(120, 100.0), up, down, hold])
    d = I.compute_all(make_df(close, spread=0.004))
    sig, fib = SG.detect_signals(d)
    assert fib is not None
    assert 0.5 < fib["retr_now"] < 0.6
    assert sig["fib_support"]["hit"]


def test_fibonacci_broken_is_not_support():
    close = np.concatenate([np.full(120, 100.0), np.linspace(100, 150, 60), np.linspace(150, 108, 23)])
    d = I.compute_all(make_df(close, spread=0.004))
    sig, _ = SG.detect_signals(d)
    assert not sig["fib_support"]["hit"]      # 84% 되돌림 = 황금구간 붕괴


# ── 국면 ──
def test_regime_uptrend_pullback():
    close = np.concatenate([np.linspace(100, 200, 250), np.linspace(200, 186, 8)])
    r = SG.analyze_chart(make_df(close))
    assert r["regime"]["key"] == "uptrend_pullback"


def test_regime_downtrend_and_bounce_label():
    close = np.concatenate([np.linspace(300, 120, 250), [124, 127]])
    r = SG.analyze_chart(make_df(close))
    assert r["regime"]["key"] == "downtrend"


def test_regime_pending_right_after_crash():
    close = np.concatenate([np.linspace(100, 200, 250), np.linspace(200, 140, 10)])
    r = SG.analyze_chart(make_df(close))
    assert r["regime"]["key"] == "pending"     # 고점 10봉 전, -30% → 판정 유보


def test_chart_score_is_capped_and_adjusted():
    sig = {k: {"hit": True, "ago": 0} for k in C.CHART_POINTS}
    score, parts = SG.chart_score(sig, {"key": "uptrend_pullback"})
    assert score == 100
    score, _ = SG.chart_score({k: {"hit": False, "ago": None} for k in C.CHART_POINTS}, {"key": "downtrend"})
    assert score == 0                           # 음수로 내려가지 않음


def test_chart_payload_has_future_cloud():
    r = SG.analyze_chart(make_df(100 + np.sin(np.arange(300) / 9) * 8))
    ch = r["chart"]
    assert len(ch["c"]) == C.CHART_BARS
    assert len(ch["sa"]) == C.CHART_BARS + C.ICHI_SHIFT
