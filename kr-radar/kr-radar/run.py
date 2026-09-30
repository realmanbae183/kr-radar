"""
run.py — 실행 입구

  python run.py                 정식 실행: 350종목 전부 새로 받기 → site/ 웹페이지 + 텔레그램 알림
  python run.py --intraday      장중 가벼운 실행: 시세만 새로 받고 재무·수급·공시는 저장분 재사용 (알림 없음)
  python run.py --demo          가상 종목(샘플 데이터)으로 화면만 확인
  python run.py --limit 20      앞 20종목만 (빠른 점검용)
  python run.py --diagnose      데이터 출처별로 제대로 읽히는지 점검만
  python run.py --probe         데이터 출처의 실제 응답 원본을 state/probe/ 에 저장 (고칠 때 참고용)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd

import config as C
import fundamental as F
import signals as SG
import sources as S

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "cache", "fund.json")


# ─────────────────────────────────────────────
# 저장분(캐시): 정식 실행에서 받은 재무·수급·공시를 장중 실행이 다시 쓴다
# ─────────────────────────────────────────────
def _flows_to_json(df):
    if df is None:
        return None
    d = df.copy()
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    return d.to_dict("records")


def _flows_from_json(recs):
    if not recs:
        return None
    d = pd.DataFrame(recs)
    d["date"] = pd.to_datetime(d["date"])
    return d


def load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"), default=str)


# ─────────────────────────────────────────────
# 데이터 공급기
# ─────────────────────────────────────────────
class NaverProvider:
    """실제 데이터: 네이버 금융(모바일 API·차트) + 와이즈리포트 + DART."""
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

    def flows(self, code, main=None):
        return S.fetch_flows(code, (main or {}).get("_flows"))

    def disclosures(self, code):
        if self.dart.ok:
            return self.dart.disclosures(code)
        r = S.fetch_disclosures_mobile(code)
        if r.ok:                      # DART 키가 없을 때 예비: 네이버 공시 목록
            return r
        return S.Fetch(False, None, self.dart.error + " / " + r.error)


def process_stock(p, s: dict, cache: dict, intraday: bool) -> tuple[dict, dict]:
    """한 종목 처리. 반환: (웹페이지용 기록, 저장분에 넣을 원재료)"""
    code = s["code"]
    errs: dict[str, str] = {}
    cached = (cache.get("stocks") or {}).get(code) or {}
    at = cache.get("saved", "")[:16].replace("T", " ")
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

    def get(key, fetch, conv_in=lambda x: x):
        """intraday면 저장분, 아니면 새로 받고 실패 시 저장분으로 대신 (그 사실을 기록)."""
        if intraday and cached.get(key) is not None:
            return conv_in(cached[key]), "cached"
        r = fetch()
        if r.ok:
            return r.data, "ok"
        if cached.get(key) is not None:
            errs[key] = f"오늘 조회 실패 → {at} 저장분 사용 ({str(r.error)[:120]})"
            return conv_in(cached[key]), "stale"
        errs[key] = r.error or "조회 실패"
        return None, "fail"

    main, st_main = get("main", lambda: p.main(code))
    fin_y, _ = get("fin_y", lambda: p.fin(code, "Y"))
    fin_q, _ = get("fin_q", lambda: p.fin(code, "Q"))
    flows, st_flow = get("flows", lambda: p.flows(code, main), _flows_from_json)
    disc, _ = get("dart", lambda: p.disclosures(code))
    if st_main == "ok" and main and main.get("_partial_errors"):
        errs["main_partial"] = " / ".join(main["_partial_errors"])[:300]

    rec["sector"] = (main or {}).get("sector") or cached.get("sector")
    if not rec["marcap"] and main and main.get("marcap"):
        rec["marcap"] = main["marcap"]
    close = chart["stats"]["close"] if chart else None
    if not rec["marcap"] and fin_y and close:
        sh = next((r["v"].get("shares") for r in reversed(fin_y["annual"]) if r["v"].get("shares")), None)
        if sh:
            rec["marcap"] = sh * close / 1e8

    fin = F.fin_score(fin_y, main, close, rec["sector"])
    risk = F.risk_screen(prices, fin_y, fin_q, flows, errs.get("flows", ""),
                         disc, errs.get("dart", ""), rec["marcap"], errs)
    total = F.total_score(chart["score"] if chart else None, fin["score"], risk["score"])
    rec.update({"chart": chart, "fin": fin, "risk": risk, "total": total, "errors": errs})

    main_clean = {k: v for k, v in (main or {}).items() if not k.startswith("_")} if main else None
    raw = {"main": main_clean, "fin_y": fin_y, "fin_q": fin_q,
           "flows": _flows_to_json(flows) if isinstance(flows, pd.DataFrame) else None,
           "dart": disc, "sector": rec["sector"]}
    return rec, raw


def market_phase(intraday: bool, as_of: str | None) -> dict:
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    if not intraday:
        return {"key": "close", "label": "종가 확정", "now": now.strftime("%m/%d %H:%M")}
    if as_of and as_of < today:
        return {"key": "holiday", "label": "휴장·개장 전 (직전 거래일 종가)", "now": now.strftime("%m/%d %H:%M")}
    if now.hour * 60 + now.minute >= 15 * 60 + 30:
        return {"key": "close", "label": "종가", "now": now.strftime("%m/%d %H:%M")}
    return {"key": "intraday", "label": "장중 잠정", "now": now.strftime("%m/%d %H:%M")}


def run(provider, limit: int | None = None, intraday: bool = False) -> tuple[list[dict], dict]:
    t0 = time.time()
    cache = load_cache() if provider.name == "live" else {}
    if intraday and not cache.get("stocks"):
        print("[장중] 저장분이 없어서 이번엔 전부 새로 받습니다")
        intraday_eff = False
    else:
        intraday_eff = intraday

    if intraday_eff and cache.get("universe"):
        uni, notes = cache["universe"], list(cache.get("notes", []))
    else:
        uni, notes = provider.universe()
    if limit:
        uni = uni[:limit]
    print(f"[유니버스] {len(uni)}종목 ({'장중 가벼운 실행' if intraday_eff else '정식 실행'})")
    for n in notes:
        print("  ·", n)

    recs, raws = [], {}
    with ThreadPoolExecutor(max_workers=C.MAX_WORKERS) as ex:
        futs = {ex.submit(process_stock, provider, s, cache, intraday_eff): s for s in uni}
        for i, fu in enumerate(as_completed(futs), 1):
            s = futs[fu]
            try:
                rec, raw = fu.result()
                recs.append(rec)
                raws[s["code"]] = raw
            except Exception as e:
                traceback.print_exc()
                recs.append({"code": s["code"], "name": s["name"], "market": s["market"], "chart": None,
                             "fin": None, "risk": None, "total": {"score": None}, "errors": {"fatal": str(e)}})
            if i % 25 == 0 or i == len(uni):
                print(f"  {i}/{len(uni)} 처리")

    health = {}
    for key, label in (("prices", "시세"), ("main", "종목 기본정보"), ("fin_y", "연간 재무"), ("fin_q", "분기 재무"),
                       ("flows", "기관·외국인 수급"), ("dart", "공시")):
        fail = [r for r in recs if key in (r.get("errors") or {}) and "저장분 사용" not in r["errors"][key]]
        stale = [r for r in recs if key in (r.get("errors") or {}) and "저장분 사용" in r["errors"][key]]
        sample = fail[0]["errors"][key] if fail else (stale[0]["errors"][key] if stale else "")
        h = {"label": label, "ok": len(recs) - len(fail) - len(stale), "fail": len(fail), "stale": len(stale),
             "sample_error": sample}
        if intraday_eff and key != "prices":
            h["note"] = f"장중 실행 — {cache.get('saved', '')[:16].replace('T', ' ')} 정식 실행 저장분 사용"
        health[key] = h

    as_of = max((r["chart"]["stats"]["date"] for r in recs if r.get("chart")), default=None)
    if provider.name == "demo":
        phase = {"key": "close", "label": "샘플", "now": datetime.now(KST).strftime("%m/%d %H:%M")}
    else:
        phase = market_phase(intraday, as_of)
    meta = {
        "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "as_of": as_of,
        "phase": phase,
        "count": len(recs),
        "signals": sum(1 for r in recs if r.get("chart") and r["chart"]["is_signal"]),
        "notes": notes,
        "health": health,
        "mode": provider.name,
        "run_kind": "intraday" if intraday_eff else "full",
        "fund_saved": cache.get("saved") if intraday_eff else datetime.now(KST).isoformat(timespec="minutes"),
        "elapsed_sec": round(time.time() - t0),
        "weights": C.TOTAL_WEIGHTS,
        "signal_meta": {k: {"name": v[0], "group": v[1], "desc": v[2]} for k, v in SG.SIGNAL_META.items()},
        "chart_points": C.CHART_POINTS,
        "regime_adjust": C.REGIME_ADJUST,
        "signal_min": C.SIGNAL_MIN_SCORE,
        "lookback": C.SIGNAL_LOOKBACK,
        "us_url": C.US_SCREENER_URL,
    }

    # 정식 실행이면 저장분 갱신 (다음 장중 실행이 씀). 일부만 돌린 --limit 실행은 저장하지 않음
    if provider.name == "live" and not intraday_eff and not limit:
        save_cache({"saved": datetime.now(KST).isoformat(timespec="minutes"), "universe": uni, "notes": notes,
                    "stocks": raws})
        print(f"[저장분] {CACHE_PATH}")

    print(f"[완료] {len(recs)}종목, 시그널 {meta['signals']}개, {meta['elapsed_sec']}초, {phase['label']}")
    for h in health.values():
        if h["fail"] or h["stale"]:
            print(f"  ! {h['label']} 실패 {h['fail']} · 저장분 대체 {h['stale']} — 예: {h['sample_error'][:200]}")
    return recs, meta


def diagnose():
    """데이터 출처가 제대로 읽히는지 점검."""
    print("=== 데이터 출처 점검 ===")
    for code, nm in (("005930", "삼성전자"), ("247540", "에코프로비엠")):
        print(f"\n[{nm} {code}]")
        r = S.fetch_prices(code)
        print(" 시세:", "OK" if r.ok else "실패", (f"{len(r.data)}봉, 마지막 {r.data.index[-1].date()} 종가 {r.data['Close'].iloc[-1]:,.0f}" if r.ok else r.error))
        m = S.fetch_main(code)
        print(" 기본정보:", "OK" if m.ok else "실패",
              json.dumps({k: v for k, v in (m.data or {}).items() if k not in ('perf', '_flows')}, ensure_ascii=False, default=str)[:600])
        if m.data and m.data.get("perf"):
            print("   당좌비율:", [(x["period"], x["est"], x["v"].get("quick_ratio")) for x in m.data["perf"]["annual"]])
        for fq in ("Y", "Q"):
            r = S.fetch_fin_summary(code, fq)
            if r.ok:
                rows = r.data["annual"] + r.data["quarter"]
                print(f" 재무({fq}): OK", [(x["period"], "E" if x["est"] else "", x["v"].get("op"), x["v"].get("ocf")) for x in rows])
            else:
                print(f" 재무({fq}): 실패", r.error)
        r = S.fetch_flows(code, (m.data or {}).get("_flows"))
        print(" 수급:", "OK" if r.ok else "실패", (f"{len(r.data)}일 " + str(r.data.tail(2).to_dict('records')) if r.ok else r.error))
        r = S.fetch_disclosures_mobile(code)
        print(" 공시(네이버):", "OK" if r.ok else "실패", (r.data[:3] if r.ok else r.error))
    d = S.Dart(os.getenv("DART_API_KEY"))
    print("\nDART:", "OK" if d.ok else "사용 안 함/실패", d.error or f"회사코드 {len(d.corp)}개")
    q = S.fetch_marcap_rank(1, 1)
    print("코스닥 시총 순위:", "OK" if q.ok else "실패", (q.data or [])[:3], q.error)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--intraday", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    a = ap.parse_args()

    if a.probe:
        out = os.path.join(HERE, C.STATE_DIR, "probe")
        summ = S.probe(out)
        print(json.dumps(summ, ensure_ascii=False, indent=1))
        return
    if a.diagnose:
        diagnose()
        return
    if a.demo:
        import demo
        provider = demo.DemoProvider()
    else:
        provider = NaverProvider()
    recs, meta = run(provider, a.limit, intraday=a.intraday)
    if not a.demo and sum(1 for r in recs if r.get("chart")) < len(recs) * 0.5:
        print("!! 시세가 절반 이상 실패 — 페이지를 갱신하지 않고 종료 (이전 페이지 유지)")
        sys.exit(1)

    import report
    report.build(recs, meta)
    if not a.no_notify and not a.demo and meta["run_kind"] == "full" and not a.limit:
        import notify
        notify.run(recs, meta)


if __name__ == "__main__":
    main()
