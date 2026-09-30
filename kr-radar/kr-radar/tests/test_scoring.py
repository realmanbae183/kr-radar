"""재무 점수·악재 스크리닝·총점 검증"""
import numpy as np
import pandas as pd
import pytest

import fundamental as F


def fin_rows(**over):
    base = {"sales": 1000, "op": 150, "ni": 120, "ocf": 150, "roe": 15, "op_margin": 15,
            "debt_ratio": 50, "eps": 1000, "bps": 10000}
    base.update(over)
    prev = dict(base, sales=base["sales"] / 1.15)
    return {"annual": [{"period": "2024/12", "est": False, "v": prev},
                       {"period": "2025/12", "est": False, "v": base}], "quarter": []}


MAIN = {"perf": {"annual": [{"period": "2025/12", "est": False, "v": {"quick_ratio": 150}}], "quarter": []},
        "target_price": None, "opinion": None}


def test_perfect_textbook_company_scores_100():
    r = F.fin_score(fin_rows(), MAIN, close=8000, sector="화학")   # PER 8, PBR 0.8
    assert r["score"] == 100 and r["grade"] == "A"


def test_partial_points_are_linear():
    r = F.fin_score(fin_rows(roe=7.5), MAIN, close=8000, sector="화학")
    roe = next(i for i in r["items"] if i["key"] == "roe")
    assert roe["points"] == pytest.approx(6.0)                    # 12점 × 50%


def test_missing_data_is_excluded_not_zero():
    r = F.fin_score(fin_rows(), {"perf": None}, close=8000, sector="화학")   # 당좌비율 없음
    q = next(i for i in r["items"] if i["key"] == "quick_ratio")
    assert q["status"] == "nodata"
    assert r["score"] == 100                                     # 나머지로 100점 환산
    assert r["coverage"] == pytest.approx(0.85)


def test_loss_company_per_is_zero_points():
    r = F.fin_score(fin_rows(eps=-500, ni=-50, roe=-5), MAIN, close=8000, sector="화학")
    per = next(i for i in r["items"] if i["key"] == "per")
    assert per["points"] == 0 and "적자" in per["display"]


def test_financial_sector_skips_debt_items():
    r = F.fin_score(fin_rows(debt_ratio=1200), MAIN, close=8000, sector="은행")
    skipped = {i["key"] for i in r["items"] if i["status"] == "skip"}
    assert {"debt_ratio", "quick_ratio", "op_margin"} <= skipped
    assert r["financial_sector"]


def prices(n=40, crash_at=None, crash=-30.0):
    c = np.full(n, 10000.0)
    if crash_at is not None:
        c[crash_at:] = c[crash_at:] * (1 + crash / 100)
    return pd.DataFrame({"Close": c}, index=pd.bdate_range("2026-08-01", periods=n))


def q_rows(op_prev, op_now, margins=(10, 9, 8, 7)):
    ps = ["2025/03", "2025/06", "2025/09", "2025/12", "2026/03", "2026/06"]
    ops = [100, 100, 100, 100, 100, op_now]
    ops[1] = op_prev                         # 2025/06 = 1년 전 같은 분기
    rows = [{"period": p, "est": False, "v": {"op": o}} for p, o in zip(ps, ops)]
    for r, m in zip(rows[-4:], margins):
        r["v"]["op_margin"] = m
    return {"annual": [], "quarter": rows}


def test_risk_detects_bad_disclosure_but_ignores_bonus_issue():
    disc = [{"date": "20260901", "title": "주요사항보고서(유상증자결정)", "url": "u"},
            {"date": "20260902", "title": "무상증자결정", "url": "u"}]
    r = F.risk_screen(prices(), None, None, None, "x", disc, "", 10000, {})
    d = next(i for i in r["items"] if i["key"] == "disclosure")
    assert d["status"] == "bad" and len(d["links"]) == 1
    only_bonus = F.risk_screen(prices(), None, None, None, "x", disc[1:], "", 10000, {})
    assert next(i for i in only_bonus["items"] if i["key"] == "disclosure")["status"] == "ok"


def test_failed_lookup_is_unknown_not_clean():
    r = F.risk_screen(prices(), None, None, None, "네트워크 오류", None, "DART 인증키 없음", None, {})
    st = {i["key"]: i["status"] for i in r["items"]}
    assert st["disclosure"] == "unknown" and st["flow"] == "unknown" and st["earnings"] == "unknown"
    assert r["unknown"] >= 3


def test_earnings_turn_to_loss_and_margin_down():
    r = F.risk_screen(prices(), None, q_rows(op_prev=100, op_now=-20), None, "", [], "", 10000, {})
    st = {i["key"]: i["status"] for i in r["items"]}
    assert st["earnings"] == "bad" and st["margin"] == "warn"


def test_limit_down_detected():
    r = F.risk_screen(prices(crash_at=35, crash=-30), None, None, None, "", [], "", 10000, {})
    assert next(i for i in r["items"] if i["key"] == "shock")["status"] == "bad"


def test_flow_selling_pct_of_marcap():
    t = pd.bdate_range("2026-09-01", periods=20)
    fl = pd.DataFrame({"date": t, "close": 10000.0, "inst": -60000.0, "foreign": 0.0})   # 20일 × 6억 = -120억
    r = F.risk_screen(prices(), None, None, fl, "", [], "", 10000, {})               # 시총 1조
    f = next(i for i in r["items"] if i["key"] == "flow")
    assert f["status"] == "bad" and f["flow"]["pct"] == pytest.approx(-1.2)


def test_total_reweights_when_part_missing():
    t = F.total_score(80, None, 60)
    assert t["missing"] == ["fin"]
    assert t["score"] == round((80 * 0.40 + 60 * 0.25) / 0.65)
