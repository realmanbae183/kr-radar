"""
run.py — 실행 입구

  python run.py                 실제 데이터(네이버·DART)로 350종목 레이팅 → site/ 에 웹페이지 생성
  python run.py --demo          가상 종목(샘플 데이터)으로 화면만 확인
  python run.py --limit 20      앞 20종목만 (빠른 점검용)
  python run.py --diagnose      데이터 출처별로 제대로 읽히는지 점검만 하고 끝냄
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import config as C
import fundamental as F
import signals as SG
import sources as S

KST = timezone(timedelta(hours=9))


class NaverProvider:
    """실제 데이터: 네이버 금융 + DART."""
    name = "live"

    def __init__(self):
        self.dart = S.Dart(os.getenv("DART_API_KEY"))

    def universe(self):
        import universe
        return universe.build_universe()

    def prices(self, code):
        return S.fetch_prices(code)

    def main(self, code):
        return S.fetch_main(code)

    def fin(self, code, freq):
        return S.fetch_fin_summary(code, freq)

    def flows(self, code):
        return S.fetch_flows(code)

    def disclosures(self, code):
        return self.dart.disclosures(code)


def process_stock(p, s: dict) -> dict:
    code = s["code"]
    errs: dict[str, str] = {}
    rec = {"code": code, "name": s["name"], "market": s["market"], "sector": None, "marcap": s.get("marcap")}

    pr = p.prices(code)
    prices = pr.data if pr.ok else None
    if not pr.ok:
        errs["prices"] = pr.error

    chart = None
    if prices is not None:
        try:
            chart = SG.analyze_chart(prices)
            chart.pop("_df", None)
        except Exception as e:
            errs["chart"] = f"차트 계산 오류: {e}"

    mn = p.main(code)
    main = mn.data if mn.ok else None
    if not mn.ok:
        errs["main"] = mn.error
    fy = p.fin(code, "Y")
    fin_y = fy.data if fy.ok else None
    if not fy.ok:
        errs["fin_y"] = fy.error
    fq = p.fin(code, "Q")
    fin_q = fq.data if fq.ok else None
    if not fq.ok:
        errs["fin_q"] = fq.error
    fl = p.flows(code)
    if not fl.ok:
        errs["flows"] = fl.error
    ds = p.disclosures(code)
    if not ds.ok:
        errs["dart"] = ds.error

    rec["sector"] = (main or {}).get("sector")
    if not rec["marcap"] and main and main.get("marcap"):
        rec["marcap"] = main["marcap"]
    close = chart["stats"]["close"] if chart else None
    if not rec["marcap"] and fin_y and close:
        sh = next((r["v"].get("shares") for r in reversed(fin_y["annual"]) if r["v"].get("shares")), None)
        if sh:
            rec["marcap"] = sh * close / 1e8

    fin = F.fin_score(fin_y, main, close, rec["sector"])
    risk = F.risk_screen(prices, fin_y, fin_q, fl.data if fl.ok else None, fl.error,
                         ds.data if ds.ok else None, ds.error, rec["marcap"], errs)
    total = F.total_score(chart["score"] if chart else None, fin["score"], risk["score"])
    rec.update({"chart": chart, "fin": fin, "risk": risk, "total": total, "errors": errs})
    return rec


def run(provider, limit: int | None = None) -> tuple[list[dict], dict]:
    t0 = time.time()
    uni, notes = provider.universe()
    if limit:
        uni = uni[:limit]
    print(f"[유니버스] {len(uni)}종목")
    for n in notes:
        print("  ·", n)

    recs = []
    with ThreadPoolExecutor(max_workers=C.MAX_WORKERS) as ex:
        futs = {ex.submit(process_stock, provider, s): s for s in uni}
        for i, fu in enumerate(as_completed(futs), 1):
            s = futs[fu]
            try:
                recs.append(fu.result())
            except Exception as e:
                traceback.print_exc()
                recs.append({"code": s["code"], "name": s["name"], "market": s["market"], "chart": None,
                             "fin": None, "risk": None, "total": {"score": None}, "errors": {"fatal": str(e)}})
            if i % 25 == 0 or i == len(uni):
                print(f"  {i}/{len(uni)} 처리")

    # 데이터 출처별 성공/실패 집계 (화면 아래 '데이터 상태'에 표시)
    health = {}
    for key, label in (("prices", "시세"), ("main", "종목 메인"), ("fin_y", "연간 재무"), ("fin_q", "분기 재무"),
                       ("flows", "기관·외국인 수급"), ("dart", "DART 공시")):
        fail = [r for r in recs if key in (r.get("errors") or {})]
        sample = fail[0]["errors"][key] if fail else ""
        health[key] = {"label": label, "ok": len(recs) - len(fail), "fail": len(fail), "sample_error": sample}

    meta = {
        "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "as_of": max((r["chart"]["stats"]["date"] for r in recs if r.get("chart")), default=None),
        "count": len(recs),
        "signals": sum(1 for r in recs if r.get("chart") and r["chart"]["is_signal"]),
        "notes": notes,
        "health": health,
        "mode": provider.name,
        "elapsed_sec": round(time.time() - t0),
        "weights": C.TOTAL_WEIGHTS,
        "signal_meta": {k: {"name": v[0], "group": v[1], "desc": v[2]} for k, v in SG.SIGNAL_META.items()},
        "chart_points": C.CHART_POINTS,
        "regime_adjust": C.REGIME_ADJUST,
        "signal_min": C.SIGNAL_MIN_SCORE,
        "lookback": C.SIGNAL_LOOKBACK,
    }
    print(f"[완료] {len(recs)}종목, 시그널 {meta['signals']}개, {meta['elapsed_sec']}초")
    for h in health.values():
        if h["fail"]:
            print(f"  ! {h['label']} 실패 {h['fail']}건 — 예: {h['sample_error']}")
    return recs, meta


def diagnose():
    """데이터 출처가 제대로 읽히는지 점검 (첫 실행 때 반드시 확인)."""
    print("=== 데이터 출처 점검 ===")
    for code, nm in (("005930", "삼성전자"), ("247540", "에코프로비엠")):
        print(f"\n[{nm} {code}]")
        r = S.fetch_prices(code)
        print(" 시세:", "OK" if r.ok else "실패", (f"{len(r.data)}봉, 마지막 {r.data.index[-1].date()} 종가 {r.data['Close'].iloc[-1]:,.0f}" if r.ok else r.error))
        r = S.fetch_main(code)
        print(" 메인:", "OK" if r.ok else "실패", (json.dumps({k: v for k, v in r.data.items() if k != 'perf'}, ensure_ascii=False) if r.data else r.error))
        if r.data and r.data.get("perf"):
            a = r.data["perf"]["annual"]
            print("   기업실적분석 연간:", [(x["period"], x["est"], x["v"].get("quick_ratio")) for x in a])
        for fq in ("Y", "Q"):
            r = S.fetch_fin_summary(code, fq)
            if r.ok:
                rows = r.data["annual"] + r.data["quarter"]
                print(f" 재무({fq}): OK", [(x["period"], "E" if x["est"] else "", x["v"].get("op"), x["v"].get("roe"), x["v"].get("ocf")) for x in rows])
            else:
                print(f" 재무({fq}): 실패", r.error)
        r = S.fetch_flows(code)
        print(" 수급:", "OK" if r.ok else "실패", (r.data.tail(3).to_dict("records") if r.ok else r.error))
    d = S.Dart(os.getenv("DART_API_KEY"))
    print("\nDART:", "OK" if d.ok else "사용 안 함/실패", d.error or f"회사코드 {len(d.corp)}개")
    if d.ok:
        r = d.disclosures("005930")
        print(" 삼성전자 공시:", "OK" if r.ok else "실패", (r.data[:3] if r.ok else r.error))
    k = S.fetch_kospi200()
    print("\n코스피200 명단:", "OK" if k.ok else "실패", len(k.data or []), (k.data or [])[:3], k.error)
    q = S.fetch_marcap_rank(1, 1)
    print("코스닥 시총 순위:", "OK" if q.ok else "실패", (q.data or [])[:3], q.error)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    a = ap.parse_args()

    if a.diagnose:
        diagnose()
        return
    if a.demo:
        import demo
        provider = demo.DemoProvider()
    else:
        provider = NaverProvider()
    recs, meta = run(provider, a.limit)
    if not a.demo and sum(1 for r in recs if r.get("chart")) < len(recs) * 0.5:
        print("!! 시세가 절반 이상 실패 — 페이지를 갱신하지 않고 종료 (어제 페이지 유지)")
        sys.exit(1)

    import report
    report.build(recs, meta)
    if not a.no_notify and not a.demo:
        import notify
        notify.run(recs, meta)


if __name__ == "__main__":
    main()
