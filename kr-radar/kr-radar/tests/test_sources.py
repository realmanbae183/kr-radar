"""네이버/와이즈리포트 페이지 해석기 검증 — 실제 페이지와 같은 모양의 작은 HTML로 시험"""
import pandas as pd

import sources as S
import universe as U

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
