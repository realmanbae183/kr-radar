"""
checks.py — 검증 (python -m pytest -q checks.py). 자동 실행 때마다 먼저 돌아간다.
  1) 지표·신호 계산   2) 과거 성과표   3) 네이버·와이즈리포트 해석기   4) 재무·악재   5) 실행 흐름(장중 저장분 재사용)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import pytest

import config as C
import fundamental as F
import history as H
import indicators as I
import run as RUN
import signals as SG
import sources as S
import universe as U


def make_df(close, vol=None, spread=0.01):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2021-01-01", periods=len(close))
    op = np.concatenate([[close[0]], close[:-1]])
    hi = np.maximum(op, close) * (1 + spread)
    lo = np.minimum(op, close) * (1 - spread)
    v = np.full(len(close), 1_000_000.0) if vol is None else np.asarray(vol, float)
    return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": v}, index=idx)


# ───────── 1) 지표·신호 ─────────
def test_rsi_extremes():
    assert I.rsi(pd.Series(np.arange(1, 60, dtype=float))).iloc[-1] == pytest.approx(100)
    assert I.rsi(pd.Series(np.arange(60, 1, -1, dtype=float))).iloc[-1] == pytest.approx(0)


def test_cross_up():
    a = pd.Series([1, 2, 3, 5, 6, 4, 7.0])
    assert list(I.cross_up(a, 4.0)) == [False, False, False, True, False, False, True]


def test_cloud_is_shifted_26():
    d = I.compute_all(make_df(100 + np.sin(np.arange(300) / 7) * 10))
    assert d["span_a"].iloc[-1] == pytest.approx(d["span_a_raw"].iloc[-1 - C.ICHI_SHIFT])
    assert d["cloud_top"].iloc[-1] >= d["cloud_bot"].iloc[-1]


def crash_series():
    """300일 완만한 상승 뒤 5일 동안 급락."""
    return np.concatenate([np.linspace(100, 160, 300), 160 * np.cumprod(np.full(5, 0.955))])


def test_oversold_flags_overlap_on_crash():
    d = I.compute_all(make_df(crash_series()))
    last = d.iloc[-1]
    assert last["os_rsi"] and last["os_bb"] and last["os_gap"] and last["n_os"] == 3
    assert d["os_event"].iloc[-5:].any()                 # 급락 구간에서 신호일이 생김
    assert not d["os_event"].iloc[150:295].any()         # 잔잔히 오르는 동안엔 없음
    assert last["trend_up"]                              # 120일선은 아직 오르는 중


def test_event_is_only_when_a_new_condition_turns_on():
    d = I.compute_all(make_df(crash_series()))
    ev = d["os_event"].to_numpy()
    n = d["n_os"].to_numpy()
    for t in np.flatnonzero(ev):
        assert n[t] >= 1 and n[t] >= n[t - 1]


def test_breakout_needs_volume():
    close = np.concatenate([np.full(200, 100.0) + np.sin(np.arange(200)) * 0.5, [100, 100, 108, 110]])
    vol = np.full(len(close), 1e6)
    quiet = I.compute_all(make_df(close, vol, spread=0.003))
    assert not quiet["bo_event"].iloc[-4:].any()
    vol[-2] = 3e6
    loud = I.compute_all(make_df(close, vol, spread=0.003))
    assert loud["bo_event"].iloc[-2]


def test_breadth_counts_stocks_above_ma20():
    up = I.compute_all(make_df(np.linspace(100, 200, 60)))
    dn = I.compute_all(make_df(np.linspace(200, 100, 60)))
    prepared = {f"U{i}": up for i in range(15)}
    prepared.update({f"D{i}": dn for i in range(5)})
    b = I.breadth_series(prepared)
    assert b.iloc[-1] == pytest.approx(75.0)
    assert I.breadth_bucket(25) == "lt30" and I.breadth_bucket(30) == "30_50" and I.breadth_bucket(50) == "ge50"


# ───────── 2) 과거 성과표 ─────────
def planted_world(n_stocks=30, n=700, seed=3):
    """시장이 가끔 급락했다가 되돌아오는 세상 (급락 뒤 사면 이기도록 심어둠)."""
    g = np.random.default_rng(seed)
    mkt = g.normal(0.0005, 0.004, n)
    for st in range(160, n - 40, 90):
        mkt[st:st + 4] -= 0.035
        mkt[st + 4:st + 12] += 0.02
    prices = {}
    for i in range(n_stocks):
        r = mkt + g.normal(0, 0.004, n)
        prices[f"S{i:02d}"] = make_df(100 * np.exp(np.cumsum(r)))
    idx = make_df(100 * np.exp(np.cumsum(mkt)))
    return prices, idx


def build_world():
    prices, idx = planted_world()
    prepared = {k: I.compute_all(v) for k, v in prices.items()}
    breadth = I.breadth_series(prepared)
    market = I.market_frame(idx["Close"])
    info = {k: {"market": "KOSPI" if i % 2 else "KOSDAQ"} for i, k in enumerate(prepared)}
    return prepared, breadth, market, H.build(prepared, breadth, market, info)


def test_history_finds_planted_edge():
    _, _, _, t = build_world()
    base, o3 = t["baseline"]["all"], t["os"]["3|*|*"]
    assert o3["n"] > 30 and o3["dates"] <= o3["n"]
    assert o3["w10"] > base["w10"] + 15 and o3["m10"] > base["m10"] + 2     # 급락 뒤 매수가 기준선보다 확실히 나음
    assert t["meta"]["breadth_days"]["lt30"] > 0
    assert set(t["hold"]) == {"KOSPI", "KOSDAQ"}


def test_forward_return_matches_by_hand():
    c = np.array([100.0, 110, 121, 133.1, 146.41, 161.05])
    r = H._fwd(c, 2)
    assert r[0] == pytest.approx(21 - C.COST_PCT) and np.isnan(r[-1]) and np.isnan(r[-2])
    lo = c * 0.9
    assert H._mae(c, lo, 2)[0] == pytest.approx((99 / 100 - 1) * 100)      # 다음 이틀 저가 중 최저 = 99


def test_lookup_falls_back_when_sample_is_thin():
    t = {"os": {"2|U|lt30": {"n": 50, "w5": 80}, "2|*|lt30": {"n": 500, "w5": 60}, "2|*|*": {"n": 5000, "w5": 55}}}
    r = H.lookup_os(t, 2, True, "lt30")
    assert r["key"] == "2|*|lt30" and r["level"] == "no_trend"            # 50번짜리 칸은 안 믿고 넓힘
    assert H.grade(r["stat"]) == "A"
    assert H.grade({"n": 50, "w5": 90}) is None                            # 표본이 모자라면 등급 없음
    assert H.grade({"n": 999, "w5": 49}) == "D"
    assert H.lookup_os(None, 2, True, "lt30")["stat"] is None


def test_analyze_reports_event_and_history():
    prepared, breadth, market, t = build_world()
    hits = 0
    for code, d in prepared.items():
        ev = np.flatnonzero((d["os_event"] & d["valid"] & (d["n_os"] >= 2)).to_numpy())
        if len(ev) == 0:
            continue
        cut = d.iloc[:ev[-1] + 1]                         # 신호가 뜬 날을 '오늘'로
        a = SG.analyze(cut, breadth, market, t)
        e = a["os"]["event"]
        assert e and e["ago"] == 0 and e["n"] >= 2
        assert a["os"]["hist"] is not None and a["os"]["hist_label"].startswith("과매도")
        assert len(a["chart"]["sa"]) == len(a["chart"]["c"]) + C.ICHI_SHIFT
        hits += 1
    assert hits >= 5


# ───────── 3) 해석기 ─────────


FCHART = """<?xml version="1.0" encoding="EUC-KR" ?><protocol><chartdata symbol="005930" name="삼성전자" count="3" timeframe="day" precision="0" origintime="19900103">
<item data="20260925|70000|71000|69500|70500|12345678" />
<item data="20260926|70500|72000|70000|71800|15000000" />
<item data="20260929|0|0|0|71800|0" />
</chartdata></protocol>"""


def test_parse_fchart():
    df = S.parse_fchart(FCHART)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 3 and df["Close"].iloc[1] == 71800
    assert df["Open"].iloc[2] == 71800        # 거래정지일 시가 0 → 종가로 채움


WISE = """
<table><tr><td>단위 : 억원, %, 배, 주</td></tr></table>
<table>
<thead>
<tr><th rowspan="2">주요재무정보</th><th colspan="4">연간</th></tr>
<tr><th>2023/12<br>(IFRS연결)</th><th>2024/12<br>(IFRS연결)</th><th>2025/12<br>(IFRS연결)</th><th>2026/12(E)<br>(IFRS연결)</th></tr>
</thead>
<tbody>
<tr><th>매출액</th><td>2,589,355</td><td>3,008,709</td><td>3,200,000</td><td>3,400,000</td></tr>
<tr><th>영업이익</th><td>65,670</td><td>327,260</td><td>300,000</td><td>420,000</td></tr>
<tr><th>영업이익(발표기준)</th><td>65,670</td><td>327,260</td><td>300,000</td><td></td></tr>
<tr><th>당기순이익</th><td>154,871</td><td>344,514</td><td>320,000</td><td>360,000</td></tr>
<tr><th>당기순이익(지배)</th><td>144,734</td><td>336,214</td><td>310,000</td><td>350,000</td></tr>
<tr><th>자본총계</th><td>3,636,677</td><td>4,021,920</td><td>4,300,000</td><td></td></tr>
<tr><th>영업활동현금흐름</th><td>445,227</td><td>729,826</td><td>600,000</td><td></td></tr>
<tr><th>영업이익률</th><td>2.54</td><td>10.88</td><td>9.38</td><td>12.35</td></tr>
<tr><th>ROE(%)</th><td>4.15</td><td>9.03</td><td>7.60</td><td>8.00</td></tr>
<tr><th>부채비율</th><td>25.36</td><td>27.93</td><td>26.00</td><td></td></tr>
<tr><th>EPS(원)</th><td>2,131</td><td>4,950</td><td>4,600</td><td>5,200</td></tr>
<tr><th>BPS(원)</th><td>52,002</td><td>57,981</td><td>61,000</td><td></td></tr>
<tr><th>발행주식수(보통주)</th><td>5,969,782,550</td><td>5,969,782,550</td><td>5,919,637,922</td><td></td></tr>
</tbody></table>"""


def test_parse_fin_summary_keeps_estimate_flag():
    d = S.parse_fin_summary(WISE)
    rows = d["annual"] + d["quarter"]
    periods = [r["period"] for r in rows]
    assert periods == ["2023/12", "2024/12", "2025/12", "2026/12"]
    assert [r["est"] for r in rows] == [False, False, False, True]
    v = rows[1]["v"]
    assert v["op"] == 327260            # '영업이익'이 '영업이익(발표기준)'보다 먼저
    assert v["ni"] == 344514 and v["ni_ctrl"] == 336214
    assert v["ocf"] == 729826 and v["roe"] == 9.03 and v["op_margin"] == 10.88
    assert v["eps"] == 4950 and v["shares"] == 5969782550


MAIN = """<html><body>
<div class="first"><table summary="시가총액 정보"><tr><th>시가총액</th><td><em id="_market_sum">
          475조 3,285
        </em>억원</td></tr><tr><th>상장주식수</th><td>5,919,637,922</td></tr></table></div>
<table summary="투자의견 정보"><tr><th>투자의견<span>l</span>목표주가</th><td><em>4.00</em>매수 <em>l</em> <em>95,000</em></td></tr></table>
<h4><em><a href="#">동일업종비교</a></em> <span><em>(업종명 : <a href="/sise/sise_group_detail.naver?type=upjong&amp;no=278">반도체와반도체장비</a></em></span></h4>
<table summary="기업실적분석">
<thead>
<tr><th rowspan="3">주요재무정보</th><th colspan="2">최근 연간 실적</th><th colspan="2">최근 분기 실적</th></tr>
<tr><th>2024.12</th><th>2025.12(E)</th><th>2026.03</th><th>2026.06</th></tr>
<tr><th>IFRS연결</th><th>IFRS연결</th><th>IFRS연결</th><th>IFRS연결</th></tr>
</thead>
<tbody>
<tr><th>매출액</th><td>3,008,709</td><td>3,300,000</td><td>790,000</td><td>800,000</td></tr>
<tr><th>당좌비율</th><td>187.80</td><td></td><td>190.10</td><td>192.00</td></tr>
<tr><th>ROE(지배주주)</th><td>9.03</td><td>8.5</td><td></td><td></td></tr>
</tbody></table></body></html>"""


def test_parse_main_page():
    d = S.parse_main_page(MAIN)
    assert d["marcap"] == 4753285
    assert d["sector"] == "반도체와반도체장비"
    assert d["target_price"] == 95000 and d["opinion"] == "매수"
    ann = d["perf"]["annual"]
    assert [a["period"] for a in ann] == ["2024/12", "2025/12"]
    assert ann[0]["v"]["quick_ratio"] == 187.8 and ann[1]["est"] is True
    assert [q["period"] for q in d["perf"]["quarter"]] == ["2026/03", "2026/06"]


def test_korean_money():
    assert S.parse_korean_money_eok("475조 285억원") == 4750285     # 억 자리가 4자리 미만이어도 정확히
    assert S.parse_korean_money_eok("3,285억원") == 3285


FRGN = """<table><tr><th>외국인 기관 순매매 거래량</th></tr><tr><td>x</td></tr></table>
<table class="type2">
<tr><th rowspan="2">날짜</th><th rowspan="2">종가</th><th rowspan="2">전일비</th><th rowspan="2">등락률</th><th rowspan="2">거래량</th><th>기관</th><th colspan="3">외국인</th></tr>
<tr><th>순매매량</th><th>순매매량</th><th>보유주수</th><th>보유율</th></tr>
<tr><td>2026.09.29</td><td>71,800</td><td>상승 1,300</td><td>+1.84%</td><td>15,000,000</td><td>+120,000</td><td>-300,000</td><td>3,000,000,000</td><td>50.1%</td></tr>
<tr><td colspan="9"></td></tr>
<tr><td>2026.09.26</td><td>70,500</td><td>하락 500</td><td>-0.70%</td><td>12,000,000</td><td>-50,000</td><td>+80,000</td><td>3,000,300,000</td><td>50.1%</td></tr>
</table>"""


def test_parse_frgn():
    df = S.parse_frgn(FRGN)
    assert len(df) == 2
    assert df["date"].iloc[-1] == pd.Timestamp("2026-09-29")     # 오래된 날짜부터 정렬
    assert df["inst"].iloc[-1] == 120000 and df["foreign"].iloc[-1] == -300000
    assert df["close"].iloc[0] == 70500


MARCAP = """<div class="aside">인기검색종목 <a href="/item/main.naver?code=000660">SK하이닉스</a></div>
<table class="type_2"><thead><tr><th>N</th><th>종목명</th><th>현재가</th><th>시가총액</th></tr></thead><tbody>
<tr><td>1</td><td><a href="/item/main.naver?code=247540" class="tltle">에코프로비엠</a></td><td>150,000</td><td>146,700</td></tr>
<tr><td>2</td><td><a href="/item/main.naver?code=196170" class="tltle">알테오젠</a></td><td>350,000</td><td>186,000</td></tr>
</tbody></table>"""


def test_parse_marcap_ignores_sidebar_links():
    rows = S.parse_marcap_page(MARCAP)
    assert [r["code"] for r in rows] == ["247540", "196170"]
    assert rows[1]["marcap"] == 186000


def test_universe_exclusions():
    assert U.is_excluded("005935", "삼성전자우")        # 우선주
    assert U.is_excluded("123450", "하나15호스팩")
    assert U.is_excluded("069500", "KODEX 200")
    assert not U.is_excluded("068270", "셀트리온")      # 바이오는 포함
    assert not U.is_excluded("047040", "대우건설")      # 이름에 '우'가 있어도 보통주


def test_to_num():
    assert S.to_num("+1,234") == 1234 and S.to_num("-3.2%") == -3.2
    assert S.to_num("-") is None and S.to_num(float("nan")) is None



INTEGRATION = {
    "itemCode": "005930", "stockName": "삼성전자",
    "totalInfos": [{"code": "marketValue", "key": "시총", "value": "475조 3,285억"},
                   {"code": "per", "key": "PER", "value": "15.2배"}],
    "consensusInfo": {"recommMean": "4.00", "priceTargetMean": "95,000", "createDate": "2026.09.29."},
    "dealTrendInfos": [
        {"bizdate": "20260929", "foreignerPureBuyQuant": "-300,000", "organPureBuyQuant": "+120,000",
         "individualPureBuyQuant": "+180,000", "closePrice": "71,800"},
        {"bizdate": "20260926", "foreignerPureBuyQuant": "+80,000", "organPureBuyQuant": "-50,000",
         "individualPureBuyQuant": "-30,000", "closePrice": "70,500"},
        {"bizdate": "20260925", "foreignerPureBuyQuant": "0", "organPureBuyQuant": "0", "closePrice": "70,000"},
    ],
}


def test_parse_integration():
    d = S.parse_integration(INTEGRATION)
    assert d["marcap"] == 4753285
    assert d["target_price"] == 95000 and d["opinion"] == "매수"
    f = d["flows"]
    assert list(f["date"].dt.strftime("%m%d")) == ["0925", "0926", "0929"]     # 오래된 날짜부터
    assert f["inst"].iloc[-1] == 120000 and f["foreign"].iloc[-1] == -300000 and f["close"].iloc[-1] == 71800


def test_parse_flow_json_nested_and_missing():
    nested = {"result": {"list": INTEGRATION["dealTrendInfos"]}}
    assert len(S.parse_flow_json(nested)) == 3
    try:
        S.parse_flow_json({"foo": [{"a": 1}]})
        assert False, "실패해야 함"
    except RuntimeError:
        pass


FIN_JSON = {"financeInfo": {
    "trTitleList": [{"isConsensus": "N", "title": "2024.12.", "key": "202412"},
                    {"isConsensus": "N", "title": "2025.12.", "key": "202512"},
                    {"isConsensus": "Y", "title": "2026.12.", "key": "202612"}],
    "rowList": [
        {"title": "매출액", "columns": {"202412": {"value": "3,008,709"}, "202512": {"value": "3,200,000"}, "202612": {"value": "3,400,000"}}},
        {"title": "당좌비율", "columns": {"202412": {"value": "187.80"}, "202512": {"value": "190.00"}, "202612": {"value": "-"}}},
        {"title": "ROE", "columns": {"202412": {"value": "9.03"}}},
    ]}}


def test_parse_mobile_finance():
    rows = S.parse_mobile_finance(FIN_JSON)
    assert [r["period"] for r in rows] == ["2024/12", "2025/12", "2026/12"]
    assert [r["est"] for r in rows] == [False, False, True]
    assert rows[1]["v"]["quick_ratio"] == 190.0 and rows[0]["v"]["sales"] == 3008709
    assert "quick_ratio" not in rows[2]["v"]          # '-' 는 값 없음


WISE_OVERVIEW = """<table><tr><td><dl>
<dt class="line-left">KSE : 코스피 전기·전자</dt>
<dt class="line-left">WICS : 반도체와반도체장비</dt></dl></td></tr></table>
<table><thead><tr><th>투자의견</th><th>목표주가</th><th>EPS</th><th>PER</th><th>추정기관수</th></tr></thead>
<tbody><tr><td>4.00</td><td>95,000</td><td>5,200</td><td>13.8</td><td>28</td></tr></tbody></table>"""


def test_parse_wise_overview():
    d = S.parse_wise_overview(WISE_OVERVIEW)
    assert d["sector"] == "반도체와반도체장비"
    assert d["target_price"] == 95000 and d["opinion"] == "매수"



# ───────── 4) 재무·악재 ─────────



def fin_rows(**over):
    base = {"sales": 1000, "op": 150, "ni": 120, "ocf": 150, "roe": 15, "op_margin": 15,
            "debt_ratio": 50, "eps": 1000, "bps": 10000}
    base.update(over)
    prev = dict(base, sales=base["sales"] / 1.15)
    return {"annual": [{"period": "2024/12", "est": False, "v": prev},
                       {"period": "2025/12", "est": False, "v": base}], "quarter": []}


FIN_MAIN = {"perf": {"annual": [{"period": "2025/12", "est": False, "v": {"quick_ratio": 150}}], "quarter": []},
        "target_price": None, "opinion": None}


def _rec(code, sector="화학", market="KOSPI", main=FIN_MAIN, **over):
    return {"code": code, "market": market, "sector": sector,
            "fin": F.fin_score(fin_rows(**over), main, close=8000, sector=sector)}


def test_fin_score_is_relative_not_threshold():
    # ROE 15(옛 기준 만점선), 16, 40 — 옛 방식은 셋 다 만점. 새 방식은 줄을 세운다
    recs = [_rec("A", roe=15), _rec("B", roe=16), _rec("C", roe=40), _rec("D", roe=3)]
    F.rank_pass(recs)
    pts = {r["code"]: next(i for i in r["fin"]["items"] if i["key"] == "roe")["points"] for r in recs}
    assert pts["C"] > pts["B"] > pts["A"] > pts["D"]
    assert pts["C"] == pytest.approx(14.0) and pts["D"] == pytest.approx(0.0)
    assert recs[2]["fin"]["rank"]["all"] == [1, 4]
    assert recs[3]["fin"]["rank"]["bottom_pct"] == 0


def test_missing_data_is_excluded_not_zero():
    recs = [_rec("A", main={"perf": None}), _rec("B", roe=5), _rec("C", roe=1)]
    F.rank_pass(recs)
    q = next(i for i in recs[0]["fin"]["items"] if i["key"] == "quick_ratio")
    assert q["status"] == "nodata" and q["points"] is None
    assert recs[0]["fin"]["score"] is not None and recs[0]["fin"]["coverage"] < 1
    assert recs[0]["fin"]["rank"]["all"][0] == 1                 # 없는 항목 때문에 꼴찌가 되지 않는다


def test_loss_company_per_is_worst():
    recs = [_rec("A", eps=-500, ni=-50, roe=-5), _rec("B"), _rec("C", eps=400)]
    F.rank_pass(recs)
    per = next(i for i in recs[0]["fin"]["items"] if i["key"] == "per")
    assert per["points"] == 0 and "적자" in per["display"]


def test_per_is_compared_inside_sector_when_big_enough():
    # 반도체 8종목은 PER 20~27, 은행 8종목은 PER 4~7.5. 전 종목으로 세우면 반도체는 전부 하위지만 업종 안에서는 갈린다
    recs = [_rec(f"S{i}", sector="반도체", eps=8000 / (20 + i)) for i in range(8)] + \
           [_rec(f"B{i}", sector="은행", eps=8000 / (4 + i * 0.5)) for i in range(8)]
    F.rank_pass(recs)
    per = lambda c: next(i for r in recs if r["code"] == c for i in r["fin"]["items"] if i["key"] == "per")
    assert per("S0")["points"] == pytest.approx(8.0) and "반도체" in per("S0")["peer"]
    assert per("B7")["points"] == pytest.approx(0.0)
    assert recs[0]["fin"]["rank"]["sector"][:2] == [1, 8]


def _vrec(w5=60, m5=1.0, n=500, risk=90, bad=0, bottom=50, os_n=2, low=False):
    h = {"n": n, "w5": w5, "m5": m5}
    return {"sig": {"os": {"event": {"n": os_n}, "hist": h, "grade": "A"}, "nh": None, "stats": {"low_value": low, "value20": 3}},
            "fin": {"score": 50, "rank": {"bottom_pct": bottom}}, "risk": {"score": risk, "bad": bad, "unknown": 0}}


def test_verdict_rules():
    assert F.verdict(_vrec())["key"] == "go"
    assert F.verdict(_vrec(w5=49))["key"] == "weak"              # 승률 50% 이하
    assert F.verdict(_vrec(m5=-0.1))["key"] == "weak"            # 평균 수익 마이너스
    assert F.verdict(_vrec(risk=55, bad=2))["key"] == "veto"     # 악재가 크면 승률이 넘어도 비추
    assert F.verdict(_vrec(bottom=10))["key"] == "veto"          # 재무 하위 20%
    assert F.verdict(_vrec(os_n=1))["key"] == "none"             # 하나짜리 과매도는 신호로 안 침
    assert F.verdict(_vrec(n=50))["key"] == "weak"               # 표본 부족
    assert F.verdict(_vrec(low=True))["notes"]                   # 거래 적으면 메모


def test_52week_high_event_fires_once_on_breakout():
    close = [100.0] * 260 + [103, 104, 105, 99, 110]
    d = I.compute_all(make_df(close))
    ev = d["nh_event"].to_numpy()
    assert ev[260] and not ev[261] and not ev[262]               # 넘은 첫날만
    assert ev[264]                                               # 밀렸다가 다시 넘으면 새 신호
    assert ev.sum() == 2


def test_wise_overview_description():
    html = '<h5><span>기업개요</span></h5> <p>[기준:2026.09.14]</p><div class="cmp_comment"><ul class="dot_cmp">' \
           '<li class="dot_cmp" data-cd="1">동사는 <b>반도체</b>를 만듦.</li><li class="dot_cmp">둘째 문장.</li></ul></div> WICS : 반도체 <'
    d = S.parse_wise_overview(html)
    assert d["desc"] == ["동사는 반도체를 만듦.", "둘째 문장."] and d["desc_date"] == "2026.09.14"


def test_groups_get_their_own_table():
    prepared, breadth, market = build_world()[:3]
    codes = list(prepared)
    info = {c: {"market": "KOSPI", "group": "KL" if i % 2 else "KM"} for i, c in enumerate(codes)}
    t = H.build_all(prepared, breadth, market, info)
    assert set(t["groups"]) <= {"KL", "KM"} and "nh" in t and "chase" in t
    assert H.for_group(t, "KQ") is t                              # 묶음 표가 없으면 전 종목 표


def test_financial_sector_skips_debt_items():
    r = F.fin_score(fin_rows(debt_ratio=1200), FIN_MAIN, close=8000, sector="은행")
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



# ───────── 5) 실행 흐름 ─────────
class CountingProvider:
    name = "live"

    def __init__(self, d, n=8):
        self.d, self.n, self.calls = d, n, {"prices": 0, "main": 0, "fin": 0, "flows": 0, "disc": 0}

    def universe(self):
        u, notes = self.d.universe()
        return u[:self.n], notes

    def prices(self, c, bars):
        self.calls["prices"] += 1
        self.bars = bars
        return self.d.prices(c, bars)

    def index(self, bars):
        return self.d.index(bars)

    def main(self, c):
        self.calls["main"] += 1
        return self.d.main(c)

    def fin(self, c, f):
        self.calls["fin"] += 1
        return self.d.fin(c, f)

    def flows(self, c, m=None):
        self.calls["flows"] += 1
        return self.d.flows(c)

    def disclosures(self, c):
        self.calls["disc"] += 1
        return self.d.disclosures(c)


def test_intraday_fetches_prices_only_and_reuses_table(tmp_path, monkeypatch):
    import demo
    monkeypatch.setattr(RUN, "CACHE_PATH", str(tmp_path / "fund.json"))
    p = CountingProvider(demo.DemoProvider(n=24), n=24)
    recs, meta = RUN.run(p)
    assert meta["run_kind"] == "full" and p.bars == C.HISTORY_BARS and p.calls["main"] == 24
    assert meta["table"]["os"] and meta["market"]["breadth"] is not None

    p2 = CountingProvider(demo.DemoProvider(n=24), n=24)
    recs2, meta2 = RUN.run(p2, intraday=True)
    assert meta2["run_kind"] == "intraday" and p2.bars == C.PRICE_BARS
    assert p2.calls["prices"] == 24
    assert p2.calls["main"] == p2.calls["fin"] == p2.calls["flows"] == p2.calls["disc"] == 0
    assert meta2["table"]["meta"]["built"] == meta["table"]["meta"]["built"]          # 성과표는 저장분 그대로
    assert {r["code"]: r["fin"]["score"] for r in recs} == {r["code"]: r["fin"]["score"] for r in recs2}


def test_intraday_without_cache_runs_full(tmp_path, monkeypatch):
    import demo
    monkeypatch.setattr(RUN, "CACHE_PATH", str(tmp_path / "none.json"))
    p = CountingProvider(demo.DemoProvider(n=4), n=4)
    _, meta = RUN.run(p, intraday=True)
    assert meta["run_kind"] == "full" and p.calls["main"] == 4


def test_failed_fetch_uses_saved_copy_and_says_so(tmp_path, monkeypatch):
    import demo
    monkeypatch.setattr(RUN, "CACHE_PATH", str(tmp_path / "fund.json"))
    p = CountingProvider(demo.DemoProvider(n=4), n=4)
    RUN.run(p)
    p.flows = lambda c, m=None: S.Fetch(False, None, "No tables found")
    recs, meta = RUN.run(p)
    assert all("저장분 사용" in r["errors"]["flows"] for r in recs)
    assert meta["health"]["flows"]["stale"] == 4 and meta["health"]["flows"]["fail"] == 0


def test_price_failure_is_reported_not_hidden(tmp_path, monkeypatch):
    import demo
    monkeypatch.setattr(RUN, "CACHE_PATH", str(tmp_path / "fund.json"))
    p = CountingProvider(demo.DemoProvider(n=5), n=5)
    orig = p.prices
    bad = p.universe()[0][0]["code"]
    p.prices = lambda c, bars: S.Fetch(False, None, "요청 실패") if c == bad else orig(c, bars)
    recs, meta = RUN.run(p)
    r = next(x for x in recs if x["code"] == bad)
    assert r["sig"] is None and "prices" in r["errors"] and meta["health"]["prices"]["fail"] == 1


def test_report_refuses_stale_screen(tmp_path, monkeypatch):
    import report
    monkeypatch.setattr(C, "UI_VERSION", "v999-not-there")
    with pytest.raises(RuntimeError):
        report.find_ui()


def test_notify_sends_only_new(tmp_path, monkeypatch):
    import demo
    import notify
    monkeypatch.setattr(RUN, "CACHE_PATH", str(tmp_path / "fund.json"))
    recs, meta = RUN.run(demo.DemoProvider(n=60))
    msg, state = notify.build_message(recs, meta, {})
    msg2, _ = notify.build_message(recs, meta, state)
    assert msg2 is None
    if state["os"] or state["bo"]:
        assert msg and "시장 폭" in msg


def test_dividend_yield_from_integration_and_fallback():
    import sources as S, fundamental as F
    obj = {"totalInfos": [{"code": "marketValue", "key": "시총", "value": "100억"},
                          {"code": "dividendYieldRatio", "key": "배당수익률", "value": "3.25%"}]}
    assert S.parse_integration(obj)["div_yield"] == 3.25
    fin = {"annual": [{"period": "2025/12", "est": False, "v": {"dps": 500.0}}], "quarter": []}
    r = F.fin_score(fin, {}, 10000.0, "전자장비")
    assert r["div_yield"] == 5.0 and "주당배당금" in r["div_src"]
    assert F.fin_score(fin, {"div_yield": 2.0}, 10000.0, "전자장비")["div_yield"] == 2.0
    assert F.fin_score({"annual": [], "quarter": []}, {}, 10000.0, "전자장비")["div_yield"] is None


def test_schedule_calendar_rules_and_timezones():
    import schedule as K
    from datetime import date
    ev = {(e["d"], e["title"]): e for e in K.events(date(2026, 10, 1), date(2027, 3, 31))}
    assert ev[("2026-10-08", "옵션 만기일")]["t"] == "15:20"                       # 10월 둘째 목요일
    assert ("2026-12-10", "선물·옵션 동시만기일") in ev                              # 12월은 동시만기
    assert ev[("2026-10-14", "미국 9월 소비자물가 (CPI)")]["t"] == "21:30"          # 서머타임: 08:30 ET = 21:30 KST
    assert ev[("2026-11-10", "미국 10월 소비자물가 (CPI)")]["t"] == "22:30"         # 서머타임 끝난 뒤
    assert ev[("2026-10-29", "미국 기준금리 결정 (FOMC)")]["t"] == "03:00"          # 미국 28일 14:00 = 한국 29일 새벽
    assert ("2026-10-22", "한국은행 기준금리 결정 (금통위)") in ev
    assert K.nth_weekday(2027, 1, 3, 2).isoformat() == "2027-01-14"
    b = K.build(date(2026, 10, 7))
    assert b["today"] == "2026-10-07" and b["events"] == sorted(b["events"], key=lambda x: (x["d"], x["t"] or "99", -x["imp"]))


def test_fred_calendar_merge_keeps_manual_and_adds_new():
    import schedule as K
    from datetime import date
    rows = [{"release_name": "Consumer Price Index", "date": "2026-10-14"},          # 손으로 넣은 것과 겹침 → 버림
            {"release_name": "Producer Price Index", "date": "2026-10-15"},          # 새 일정
            {"release_name": "Producer Price Index", "date": "2026-10-15"},          # 중복
            {"release_name": "Some Other Release", "date": "2026-10-16"}]
    auto = K.fred_events("k", date(2026, 10, 1), date(2026, 10, 31), get=lambda u, p: {"release_dates": rows})
    assert [e["title"] for e in auto] == ["미국 소비자물가 (CPI)", "미국 생산자물가 (PPI)", "미국 생산자물가 (PPI)"]
    merged = K._merge(K.events(date(2026, 10, 1), date(2026, 10, 31)), auto)
    assert sum(1 for e in merged if e["d"] == "2026-10-14" and "소비자물가" in e["title"]) == 1
    assert sum(1 for e in merged if "PPI" in e["title"]) == 1 and all("kind" not in e for e in merged)
    assert K.build(date(2026, 10, 7), fred_key="")["auto"] == 0
