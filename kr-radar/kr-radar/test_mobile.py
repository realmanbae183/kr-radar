"""모바일 API(JSON)·와이즈리포트 기업개요 해석기 + 장중 실행(저장분 재사용) 검증"""
import pandas as pd

import run as RUN
import sources as S

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


# ── 장중 실행: 시세만 새로, 나머지는 저장분 ──
class CountingProvider:
    name = "live"

    def __init__(self, demo):
        self.d, self.calls = demo, {"prices": 0, "main": 0, "fin": 0, "flows": 0, "disc": 0}

    def universe(self):
        u, n = self.d.universe()
        return u[:6], n

    def prices(self, c):
        self.calls["prices"] += 1
        return self.d.prices(c)

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


def test_intraday_reuses_cache(tmp_path, monkeypatch):
    import demo
    monkeypatch.setattr(RUN, "CACHE_PATH", str(tmp_path / "fund.json"))
    p = CountingProvider(demo.DemoProvider(n=6))
    recs, meta = RUN.run(p)                                   # 정식 실행 → 저장분 생성
    assert meta["run_kind"] == "full" and (tmp_path / "fund.json").exists()
    full_calls = dict(p.calls)
    assert full_calls["main"] == 6 and full_calls["flows"] == 6

    p2 = CountingProvider(demo.DemoProvider(n=6))
    recs2, meta2 = RUN.run(p2, intraday=True)                 # 장중 실행
    assert meta2["run_kind"] == "intraday"
    assert p2.calls["prices"] == 6
    assert p2.calls["main"] == p2.calls["fin"] == p2.calls["flows"] == p2.calls["disc"] == 0
    # 저장분으로 계산한 재무 점수는 정식 실행과 같아야 함
    a = {r["code"]: r["fin"]["score"] for r in recs}
    b = {r["code"]: r["fin"]["score"] for r in recs2}
    assert a == b
    assert all(r["risk"]["items"][4]["status"] != "unknown" for r in recs2)   # 수급도 저장분 사용


def test_failed_fetch_uses_cache_and_says_so(tmp_path, monkeypatch):
    import demo
    from sources import Fetch
    monkeypatch.setattr(RUN, "CACHE_PATH", str(tmp_path / "fund.json"))
    p = CountingProvider(demo.DemoProvider(n=3))
    RUN.run(p)
    p.flows = lambda c, m=None: Fetch(False, None, "No tables found")
    recs, meta = RUN.run(p)
    assert all("저장분 사용" in r["errors"]["flows"] for r in recs)
    assert meta["health"]["flows"]["stale"] == 3 and meta["health"]["flows"]["fail"] == 0
