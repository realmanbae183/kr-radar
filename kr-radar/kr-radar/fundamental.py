"""
fundamental.py — ④-b 재무 점수(교과서 기준 100점) + ④ 악재 스크리닝(100점 감점식) + 총점

재무 점수: 지표마다 '만점 기준'과 '0점 기준' 사이를 직선으로 부분점수.
           데이터가 없는 지표는 빼고 나머지로 100점 환산 (없는 걸 0점 처리하지 않음).
악재 점수: 100점에서 출발해 악재가 하나씩 확인될 때마다 감점.
           조회에 실패한 항목은 '확인 불가'로 따로 표시 (깨끗하다고 치지 않음).
"""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

import config as C

FIN_NAMES = {
    "roe": ("ROE", "자기자본이익률 — 주주 돈 100원으로 1년에 몇 원을 벌었나", "%"),
    "op_margin": ("영업이익률", "매출 100원 중 본업으로 남긴 이익", "%"),
    "cashflow": ("이익의 질 (현금흐름)", "장부상 이익만큼 실제 현금이 들어왔나 (영업현금흐름 ÷ 순이익)", "배"),
    "debt_ratio": ("부채비율", "빚 ÷ 자기자본. 낮을수록 튼튼", "%"),
    "quick_ratio": ("당좌비율", "1년 안에 갚을 빚 대비 바로 현금화할 수 있는 자산. 높을수록 단기 부도 위험↓", "%"),
    "growth": ("매출 성장률", "최근 결산연도 매출이 전년보다 얼마나 늘었나", "%"),
    "per": ("PER", "주가 ÷ 주당순이익. 이익 대비 몇 배에 거래되나 (낮을수록 싸다)", "배"),
    "pbr": ("PBR", "주가 ÷ 주당순자산. 장부가치 대비 몇 배 (낮을수록 싸다)", "배"),
}


def _actual_rows(rows: list[dict]) -> list[dict]:
    return sorted([r for r in rows if not r["est"] and r["v"]], key=lambda r: r["period"])


def _latest(rows: list[dict], key: str):
    for r in reversed(rows):
        if key in r["v"] and r["v"][key] is not None:
            return r["v"][key], r["period"]
    return None, None


def _lin(v: float, full: float, zero: float) -> float:
    """만점 기준(full)~0점 기준(zero) 사이 직선 비율 0~1."""
    if full == zero:
        return 1.0 if v >= full else 0.0
    t = (v - zero) / (full - zero)
    return max(0.0, min(1.0, t))


def is_financial(sector: str | None) -> bool:
    return bool(sector) and any(k in sector for k in C.FINANCIAL_SECTOR_KEYWORDS)


def fin_metrics(fin_y: dict | None, main: dict | None, close: float | None) -> dict:
    """원재료 표 → 지표 값 {key: (값, 기준기간, 표시글)}"""
    out: dict[str, tuple] = {}
    ann = _actual_rows(fin_y["annual"]) if fin_y else []
    perf = _actual_rows(main["perf"]["annual"]) if main and main.get("perf") else []
    src = ann or perf

    v, p = _latest(src, "roe")
    if v is None and perf:
        v, p = _latest(perf, "roe")
    if v is not None:
        out["roe"] = (v, p, f"{v:.1f}%")

    v, p = _latest(src, "op_margin")
    if v is None:
        op, p1 = _latest(src, "op")
        sa, _ = _latest(src, "sales")
        if op is not None and sa:
            v, p = op / sa * 100, p1
    if v is not None:
        out["op_margin"] = (v, p, f"{v:.1f}%")

    ocf, p = _latest(ann, "ocf")
    ni, _ = _latest(ann, "ni")
    if ni is None:
        ni, _ = _latest(ann, "ni_ctrl")
    if ocf is not None and ni is not None:
        if ni > 0:
            r = ocf / ni
            out["cashflow"] = (r, p, f"{r:.2f}배")
        else:
            r = 0.5 if ocf > 0 else 0.0
            out["cashflow"] = (r, p, "순이익 적자 · 영업현금 " + ("흑자" if ocf > 0 else "적자"))

    v, p = _latest(src, "debt_ratio")
    if v is None:
        li, p1 = _latest(src, "liab")
        eq, _ = _latest(src, "equity")
        if li is not None and eq:
            v, p = li / eq * 100, p1
    if v is not None:
        out["debt_ratio"] = (v, p, f"{v:.0f}%")

    v, p = _latest(perf, "quick_ratio")
    if v is not None:
        out["quick_ratio"] = (v, p, f"{v:.0f}%")

    sales = [(r["period"], r["v"].get("sales")) for r in src if r["v"].get("sales")]
    if len(sales) >= 2 and sales[-2][1] and sales[-2][1] > 0:
        g = (sales[-1][1] / sales[-2][1] - 1) * 100
        out["growth"] = (g, sales[-1][0], f"{g:+.1f}%")

    eps, p = _latest(src, "eps")
    if eps is not None and close:
        if eps > 0:
            per = close / eps
            out["per"] = (per, p, f"{per:.1f}배")
        else:
            out["per"] = (float("inf"), p, "적자 (계산 불가)")
    bps, p = _latest(src, "bps")
    if bps and bps > 0 and close:
        pbr = close / bps
        out["pbr"] = (pbr, p, f"{pbr:.2f}배")
    return out


def fin_score(fin_y, main, close, sector) -> dict:
    m = fin_metrics(fin_y, main, close)
    fin_sector = is_financial(sector)
    items, earned, avail, applicable = [], 0.0, 0.0, 0.0
    for key, pts, full, zero in C.FIN_ITEMS:
        name, explain, _ = FIN_NAMES[key]
        if fin_sector and key in C.FIN_SKIP_FOR_FINANCIALS:
            items.append({"key": key, "name": name, "explain": explain, "max": pts, "points": None,
                          "status": "skip", "display": "금융업이라 제외", "period": None})
            continue
        applicable += pts
        if key not in m:
            items.append({"key": key, "name": name, "explain": explain, "max": pts, "points": None,
                          "status": "nodata", "display": "데이터 없음", "period": None})
            continue
        v, period, disp = m[key]
        ratio = 0.0 if v == float("inf") else _lin(v, full, zero)
        got = round(pts * ratio, 1)
        earned += got
        avail += pts
        cmp = "이하" if full < zero else "이상"
        items.append({"key": key, "name": name, "explain": explain, "max": pts, "points": got,
                      "ratio": ratio, "status": "ok", "display": disp, "period": period,
                      "rule": f"{full:g}{FIN_NAMES[key][2]} {cmp} 만점 · {zero:g}{FIN_NAMES[key][2]} {'이상' if cmp=='이하' else '이하'} 0점"})
    coverage = avail / applicable if applicable else 0
    score = round(earned / avail * 100) if avail and coverage >= C.FIN_MIN_COVERAGE else None
    upside = None
    if main and main.get("target_price") and close:
        upside = (main["target_price"] / close - 1) * 100
    return {"score": score, "grade": grade(score), "items": items, "coverage": round(coverage, 2),
            "financial_sector": fin_sector,
            "target_price": main.get("target_price") if main else None,
            "opinion": main.get("opinion") if main else None, "upside": upside}


def grade(score):
    if score is None:
        return "-"
    return "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D" if score >= 35 else "F"


# ─────────────────────────────────────────────
# ④ 악재 스크리닝
# ─────────────────────────────────────────────
def _classify_disclosures(items: list[dict]) -> tuple[list, list]:
    bad, warn = [], []
    for it in items:
        t = re.sub(r"\s+", "", it["title"])
        if any(k.replace(" ", "") in t for k in C.DISCLOSURE_IGNORE_KEYWORDS):
            continue
        if any(k.replace(" ", "") in t for k in C.DISCLOSURE_BAD_KEYWORDS):
            bad.append(it)
        elif any(k.replace(" ", "") in t for k in C.DISCLOSURE_WARN_KEYWORDS):
            warn.append(it)
    return bad, warn


def _prev_year(period: str) -> str:
    y, m = period.split("/")
    return f"{int(y) - 1}/{m}"


def risk_screen(prices: pd.DataFrame | None, fin_y, fin_q, flows: pd.DataFrame | None, flows_err: str,
                disc: list | None, disc_err: str, marcap_eok: float | None, errs: dict) -> dict:
    P = C.RISK_PENALTY
    items = []

    def add(key, name, status, detail, penalty=0, links=None, explain=""):
        items.append({"key": key, "name": name, "status": status, "detail": detail,
                      "penalty": penalty, "links": links or [], "explain": explain})

    # 1) 공시
    ex = "최근 90일 DART 공시 중 유상증자·전환사채(CB)·감자·횡령 같은 주주가치 훼손 공시가 있었나"
    if disc is None:
        add("disclosure", "공시 리스크", "unknown", disc_err or "조회 실패", explain=ex)
    else:
        bad, warn = _classify_disclosures(disc)
        if bad:
            add("disclosure", "공시 리스크", "bad", f"위험 공시 {len(bad)}건: " + ", ".join(b["title"] for b in bad[:3]),
                P["disclosure_bad"], bad[:5], ex)
        elif warn:
            add("disclosure", "공시 리스크", "warn", f"주의 공시 {len(warn)}건: " + ", ".join(w["title"] for w in warn[:3]),
                P["disclosure_warn"], warn[:5], ex)
        else:
            add("disclosure", "공시 리스크", "ok", f"최근 {C.DISCLOSURE_DAYS}일 공시 {len(disc)}건 중 위험 공시 없음", explain=ex)

    # 2) 최근 분기 실적
    ex = "가장 최근 분기 영업이익을 1년 전 같은 분기와 비교"
    q = _actual_rows(fin_q["quarter"]) if fin_q else []
    qmap = {r["period"]: r["v"] for r in q}
    if not q:
        add("earnings", "분기 실적", "unknown", errs.get("fin_q") or "분기 실적 데이터 없음", explain=ex)
    else:
        last = q[-1]
        prev = qmap.get(_prev_year(last["period"]))
        op_now = last["v"].get("op")
        op_prev = prev.get("op") if prev else None
        if op_now is None or op_prev is None:
            add("earnings", "분기 실적", "unknown", "비교할 전년 동기 실적 없음", explain=ex)
        elif op_prev > 0 and op_now < 0:
            add("earnings", "분기 실적", "bad", f"{last['period']} 영업이익 적자 전환 ({op_prev:,.0f} → {op_now:,.0f}억)",
                P["earnings_turn_loss"], explain=ex)
        elif op_prev > 0 and (op_now / op_prev - 1) * 100 <= C.EARNINGS_DROP_PCT:
            chg = (op_now / op_prev - 1) * 100
            add("earnings", "분기 실적", "warn", f"{last['period']} 영업이익 전년 동기 대비 {chg:.0f}% ({op_prev:,.0f} → {op_now:,.0f}억)",
                P["earnings_drop"], explain=ex)
        else:
            chg = (op_now / op_prev - 1) * 100 if op_prev > 0 else None
            txt = f"전년 동기 대비 {chg:+.0f}%" if chg is not None else ("적자 지속" if op_now < 0 else "흑자 전환")
            status = "warn" if (op_now < 0 and op_prev < 0) else "ok"
            add("earnings", "분기 실적", status, f"{last['period']} 영업이익 {op_now:,.0f}억 ({txt})",
                P["earnings_drop"] if status == "warn" else 0, explain=ex)

    # 3) 영업이익률 추세
    ex = "최근 4개 분기 영업이익률이 3번 연속 떨어졌나 (수익성이 계속 나빠지는 중인지)"
    m = [(r["period"], r["v"].get("op_margin")) for r in q if r["v"].get("op_margin") is not None]
    if len(m) < 4:
        add("margin", "영업이익률 추세", "unknown", "분기 영업이익률 4개 미만", explain=ex)
    else:
        last4 = [x[1] for x in m[-4:]]
        txt = " → ".join(f"{x:.1f}%" for x in last4)
        if last4[0] > last4[1] > last4[2] > last4[3]:
            add("margin", "영업이익률 추세", "warn", f"3분기 연속 하락: {txt}", P["margin_down"], explain=ex)
        else:
            add("margin", "영업이익률 추세", "ok", txt, explain=ex)

    # 4) 올해 추정 실적 (증권사 컨센서스)
    ex = "증권사들이 예상하는 올해 영업이익이 작년 실제 실적보다 낮은가 (감익 예상)"
    if fin_y:
        ests = sorted([r for r in fin_y["annual"] if r["est"] and r["v"].get("op") is not None], key=lambda r: r["period"])
        acts = _actual_rows(fin_y["annual"])
        this_year = str(datetime.now().year)
        est = next((r for r in ests if r["period"].startswith(this_year)), ests[0] if ests else None)
        act = next((r for r in reversed(acts) if est and r["period"] < est["period"] and r["v"].get("op") is not None), None)
        if not est:
            add("guidance", "올해 실적 전망", "na", "증권사 추정치 없음 (커버하는 애널리스트 없음)", explain=ex)
        elif not act:
            add("guidance", "올해 실적 전망", "unknown", "비교할 작년 실적 없음", explain=ex)
        else:
            a, e = act["v"]["op"], est["v"]["op"]
            chg = (e / a - 1) * 100 if a > 0 else None
            txt = f"{act['period']} {a:,.0f}억 → {est['period']}(E) {e:,.0f}억" + (f" ({chg:+.0f}%)" if chg is not None else "")
            if e < a:
                add("guidance", "올해 실적 전망", "warn", "감익 예상: " + txt, P["guidance_down"], explain=ex)
            else:
                add("guidance", "올해 실적 전망", "ok", "증익 예상: " + txt, explain=ex)
    else:
        add("guidance", "올해 실적 전망", "unknown", errs.get("fin_y") or "연간 재무 데이터 없음", explain=ex)

    # 5) 기관·외국인 수급
    ex = f"최근 {C.FLOW_DAYS}거래일 기관+외국인 순매수 금액이 시가총액 대비 얼마인가 (큰손이 팔고 있나)"
    if flows is None or len(flows) < 10:
        add("flow", "기관·외국인 수급", "unknown", flows_err or "매매동향 데이터 부족", explain=ex)
    else:
        f = flows.tail(C.FLOW_DAYS)
        inst = float((f["inst"].fillna(0) * f["close"]).sum()) / 1e8
        frgn = float((f["foreign"].fillna(0) * f["close"]).sum()) / 1e8
        tot = inst + frgn
        pct = tot / marcap_eok * 100 if marcap_eok else None
        txt = f"기관 {inst:+,.0f}억 · 외국인 {frgn:+,.0f}억 (합계 {tot:+,.0f}억" + (f", 시총의 {pct:+.2f}%)" if pct is not None else ")")
        extra = {"inst": round(inst, 1), "foreign": round(frgn, 1), "pct": pct}
        if pct is None:
            add("flow", "기관·외국인 수급", "unknown", txt + " — 시가총액 몰라서 비교 불가", explain=ex)
        elif pct <= -C.FLOW_SELL_PCT:
            add("flow", "기관·외국인 수급", "bad", "대량 순매도: " + txt, P["flow_sell"], explain=ex)
        elif pct <= -C.FLOW_SELL_MILD_PCT:
            add("flow", "기관·외국인 수급", "warn", "순매도: " + txt, P["flow_sell_mild"], explain=ex)
        else:
            add("flow", "기관·외국인 수급", "ok", ("순매수: " if tot >= 0 else "소폭 순매도: ") + txt, explain=ex)
        items[-1]["flow"] = extra

    # 6) 하한가·급락
    ex = "최근 20거래일 안에 하한가(-29% 이하)나 하루 -15% 이상 폭락이 있었나. 하한가 땐 거래량이 말라서 지표가 왜곡됨"
    if prices is None or len(prices) < 21:
        add("shock", "하한가·급락 이력", "unknown", "시세 데이터 부족", explain=ex)
    else:
        chg = prices["Close"].pct_change().iloc[-20:] * 100
        worst = float(chg.min())
        day = chg.idxmin().strftime("%m/%d")
        if worst <= C.LIMIT_DOWN_PCT:
            add("shock", "하한가·급락 이력", "bad", f"{day} 하한가 ({worst:.1f}%)", P["limit_down"], explain=ex)
        elif worst <= C.CRASH_DAY_PCT:
            add("shock", "하한가·급락 이력", "warn", f"{day} 하루 {worst:.1f}% 급락", P["crash_day"], explain=ex)
        else:
            add("shock", "하한가·급락 이력", "ok", f"최근 20일 최대 하락 {worst:.1f}% ({day})", explain=ex)

    penalty = sum(i["penalty"] for i in items)
    score = max(0, 100 - penalty)
    unknown = sum(1 for i in items if i["status"] == "unknown")
    level = "clean" if score >= 80 else "caution" if score >= 60 else "danger"
    return {"score": score, "level": level, "items": items, "unknown": unknown,
            "bad": sum(1 for i in items if i["status"] == "bad"),
            "warn": sum(1 for i in items if i["status"] == "warn")}


def total_score(chart: int | None, fin: int | None, risk: int | None) -> dict:
    parts = {"chart": chart, "fin": fin, "risk": risk}
    w = {k: v for k, v in C.TOTAL_WEIGHTS.items() if parts[k] is not None}
    tw = sum(w.values())
    if not tw:
        return {"score": None, "weights": {}, "missing": list(parts)}
    s = sum(parts[k] * w[k] for k in w) / tw
    return {"score": round(s), "weights": {k: round(v / tw, 3) for k, v in w.items()},
            "missing": [k for k, v in parts.items() if v is None]}
