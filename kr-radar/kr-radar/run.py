"""
run.py — 실행 입구

  python run.py                 정식 실행: 5년치 시세로 과거 성과표를 다시 만들고, 재무·수급·공시까지 전부 새로 받음 → 텔레그램 알림
  python run.py --intraday      장중 가벼운 실행: 시세만 새로 받고 나머지는 저장분 재사용 (알림 없음)
  python run.py --demo          가상 종목(샘플 데이터)으로 화면만 확인
  python run.py --limit 20      앞 20종목만 (빠른 점검용)
  python run.py --diagnose      데이터 출처별로 제대로 읽히는지 점검만
  python run.py --probe         데이터 출처의 실제 응답 원본을 state/probe/ 에 저장

흐름
  1) 350종목 + 코스피 지수 시세를 받는다
  2) 지표를 붙이고, 시장 폭(20일선 위 종목 비율)을 계산한다
  3) (정식) 과거 성과표를 만든다 / (장중) 저장해 둔 성과표를 쓴다
  4) 종목마다 지금 상태를 읽고 성과표에서 같은 상태의 기록을 붙인다 + 재무 점수·악재 스크리닝
  5) 웹페이지를 만든다
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

import config as C
import fundamental as F
import history as H
import indicators as I
import signals as SG
import sources as S

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "cache", "fund.json")


# ─────────────────────────────────────────────
# 저장분: 정식 실행에서 받은 재무·수급·공시와 과거 성과표를 장중 실행이 다시 쓴다
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


class NaverProvider:
    """실제 데이터: 네이버 금융(모바일 API·차트) + 와이즈리포트 + DART."""
    name = "live"

    def __init__(self):
        self.dart = S.Dart(os.getenv("DART_API_KEY"))

    def universe(self):
        import universe
        return universe.build_universe()

    def prices(self, code, bars):
        return S.fetch_prices(code, bars)

    def index(self, bars):
        return S.fetch_prices("KOSPI", bars)

    def main(self, code):
        return S.fetch_main(code)

    def fin(self, code, freq):
        return S.fetch_fin_summary(code, freq)

    def flows(self, code, main=None):
        return S.fetch_flows(code, (main or {}).get("_flows"))

    def today(self, recs=None):
        cap = {}
        for r in recs or []:
            if r.get("sector") and r.get("marcap"):
                cap[r["sector"]] = cap.get(r["sector"], 0) + r["marcap"]
        return S.fetch_today(cap)

    def disclosures(self, code):
        if self.dart.ok:
            return self.dart.disclosures(code)
        r = S.fetch_disclosures_mobile(code)
        if r.ok:                      # DART 키가 없을 때 예비: 네이버 공시 목록
            return r
        return S.Fetch(False, None, self.dart.error + " / " + r.error)


def fundamentals(p, s: dict, cache: dict, intraday: bool, errs: dict) -> dict:
    """재무·수급·공시 원재료. 장중이면 저장분, 정식이면 새로 받고 실패 시 저장분으로 대신(그 사실을 기록)."""
    code = s["code"]
    cached = (cache.get("stocks") or {}).get(code) or {}
    at = cache.get("saved", "")[:16].replace("T", " ")

    def get(key, fetch, conv=lambda x: x):
        if intraday and cached.get(key) is not None:
            return conv(cached[key])
        r = fetch()
        if r.ok:
            return r.data
        if cached.get(key) is not None:
            errs[key] = f"오늘 조회 실패 → {at} 저장분 사용 ({str(r.error)[:120]})"
            return conv(cached[key])
        errs[key] = r.error or "조회 실패"
        return None

    main = get("main", lambda: p.main(code))
    fin_y = get("fin_y", lambda: p.fin(code, "Y"))
    fin_q = get("fin_q", lambda: p.fin(code, "Q"))
    flows = get("flows", lambda: p.flows(code, main), _flows_from_json)
    disc = get("dart", lambda: p.disclosures(code))
    if main and main.get("_partial_errors") and "main" not in errs:
        errs["main_partial"] = " / ".join(main["_partial_errors"])[:300]
    return {"main": main, "fin_y": fin_y, "fin_q": fin_q, "flows": flows, "dart": disc,
            "sector": (main or {}).get("sector") or cached.get("sector")}


def build_record(p, s, d, breadth, market, table, cache, intraday, price_err) -> tuple[dict, dict]:
    code = s["code"]
    errs: dict[str, str] = {}
    if price_err:
        errs["prices"] = price_err
    rec = {"code": code, "name": s["name"], "market": s["market"], "sector": None, "marcap": s.get("marcap"),
           "group": s.get("group") or ("KQ" if s["market"] == "KOSDAQ" else "KL"), "halt": bool(s.get("halt"))}

    sig = None
    if d is not None:
        try:
            sig = SG.analyze(d, breadth, market, table, rec["group"])
        except Exception as e:
            errs["chart"] = f"신호 계산 오류: {e}"
    raw = fundamentals(p, s, cache, intraday, errs)
    main, fin_y = raw["main"], raw["fin_y"]
    rec["sector"] = raw["sector"]
    rec["desc"] = (main or {}).get("desc")
    rec["desc_date"] = (main or {}).get("desc_date")
    if not rec["marcap"] and main and main.get("marcap"):
        rec["marcap"] = main["marcap"]
    close = sig["stats"]["close"] if sig else None
    if not rec["marcap"] and fin_y and close:
        sh = next((r["v"].get("shares") for r in reversed(fin_y["annual"]) if r["v"].get("shares")), None)
        if sh:
            rec["marcap"] = sh * close / 1e8

    fin = F.fin_score(fin_y, main, close, rec["sector"])
    risk = F.risk_screen(d[["Close"]] if d is not None else None, fin_y, raw["fin_q"], raw["flows"], errs.get("flows", ""),
                         raw["dart"], errs.get("dart", ""), rec["marcap"], errs)
    chart = sig.pop("chart") if sig else None
    rec.update({"sig": sig, "chart": chart, "fin": fin, "risk": risk, "errors": errs})
    main_clean = {k: v for k, v in (main or {}).items() if not k.startswith("_")} if main else None
    store = {"main": main_clean, "fin_y": fin_y, "fin_q": raw["fin_q"],
             "flows": _flows_to_json(raw["flows"]) if isinstance(raw["flows"], pd.DataFrame) else None,
             "dart": raw["dart"], "sector": rec["sector"]}
    return rec, store


def market_phase(intraday: bool, as_of: str | None) -> dict:
    now = datetime.now(KST)
    stamp = now.strftime("%m/%d %H:%M")
    if not intraday:
        return {"key": "close", "label": "종가 확정", "now": stamp}
    if as_of and as_of < now.strftime("%Y-%m-%d"):
        return {"key": "holiday", "label": "휴장·개장 전", "now": stamp}
    if now.hour * 60 + now.minute >= 15 * 60 + 30:
        return {"key": "close", "label": "종가", "now": stamp}
    return {"key": "intraday", "label": "장중 잠정", "now": stamp}


def run(provider, limit: int | None = None, intraday: bool = False) -> tuple[list[dict], dict]:
    t0 = time.time()
    live = provider.name == "live"
    cache = load_cache() if live else {}
    if cache and cache.get("ver") != C.CACHE_VER:
        print("[저장분] 형식이 옛것이라 버리고 새로 받습니다")
        cache = {}
    intraday_eff = intraday and bool(cache.get("stocks")) and bool(cache.get("table"))
    if intraday and not intraday_eff:
        print("[장중] 저장분이 없어서 이번엔 정식 실행으로 돕니다")

    if intraday_eff and cache.get("universe"):
        uni, notes = cache["universe"], list(cache.get("notes", []))
    else:
        uni, notes = provider.universe()
    if limit:
        uni = uni[:limit]
    bars = C.PRICE_BARS if intraday_eff else C.HISTORY_BARS
    print(f"[1/4] {len(uni)}종목 시세 받는 중 ({'장중 가벼운 실행' if intraday_eff else '정식 실행'}, {bars}봉)")

    # 1) 시세
    def one(s):
        try:
            r = provider.prices(s["code"], bars)
            return s["code"], (r.data if r.ok else None), ("" if r.ok else r.error)
        except Exception as e:
            return s["code"], None, str(e)

    prices, perr = {}, {}
    with ThreadPoolExecutor(max_workers=C.MAX_WORKERS) as ex:
        for code, df, err in ex.map(one, uni):
            if df is not None:
                prices[code] = df
            else:
                perr[code] = err or "시세 조회 실패"
    ix = provider.index(bars)

    # 2) 지표 + 시장 폭
    prepared = {}
    for code, df in prices.items():
        try:
            prepared[code] = I.compute_all(df)
        except Exception as e:
            perr[code] = f"지표 계산 오류: {e}"
    breadth = I.breadth_series(prepared) if prepared else pd.Series(dtype=float)
    if ix.ok:
        market = I.market_frame(ix.data["Close"])
        market_src = "코스피 지수"
    else:
        eq = pd.DataFrame({k: v["Close"] / v["Close"].iloc[0] for k, v in prices.items()}).mean(axis=1)
        market = I.market_frame(eq)
        market_src = "350종목 평균 (코스피 지수 조회 실패)"
        notes.append("코스피 지수를 못 받아서 350종목 평균으로 시장을 판단: " + str(ix.error)[:120])
    print(f"[2/4] 지표 계산 완료 · 시세 실패 {len(perr)}종목 · 시장 폭 {breadth.dropna().iloc[-1]:.0f}%" if len(breadth.dropna()) else "[2/4] 지표 계산 완료")

    # 3) 과거 성과표
    info = {u["code"]: u for u in uni}
    if intraday_eff:
        table = cache["table"]
    else:
        table = H.build_all(prepared, breadth, market, info)
    tm = table.get("meta", {})
    print(f"[3/4] 과거 성과표: {tm.get('from')} ~ {tm.get('to')} · {tm.get('stocks')}종목 ({'저장분' if intraday_eff else '새로 계산'})")

    # 4) 종목별 상태 + 재무·악재
    recs, stores = [], {}

    def two(s):
        try:
            return build_record(provider, s, prepared.get(s["code"]), breadth, market, table, cache, intraday_eff,
                                perr.get(s["code"]))
        except Exception as e:
            traceback.print_exc()
            return ({"code": s["code"], "name": s["name"], "market": s["market"], "sig": None, "chart": None,
                     "fin": None, "risk": None, "errors": {"fatal": str(e)}}, None)

    with ThreadPoolExecutor(max_workers=C.MAX_WORKERS) as ex:
        for i, (rec, store) in enumerate(ex.map(two, uni), 1):
            recs.append(rec)
            if store is not None:
                stores[rec["code"]] = store
            if i % 50 == 0 or i == len(uni):
                print(f"  {i}/{len(uni)} 처리")

    # 전 종목이 모인 뒤에야 할 수 있는 것: 재무 상대평가·순위, 많이 빠진 순위, 최종 판정
    F.rank_pass(recs)
    with_sig = [r for r in recs if r.get("sig")]
    for key in ("rsi", "gap"):
        order = sorted((r for r in with_sig if r["sig"]["stats"].get(key) is not None), key=lambda r: r["sig"]["stats"][key])
        for i, r in enumerate(order, 1):
            r["sig"]["stats"][key + "_rank"] = [i, len(order)]
    for r in recs:
        r["verdict"] = F.verdict(r)

    health = {}
    for key, label in (("prices", "시세"), ("main", "종목 기본정보"), ("fin_y", "연간 재무"), ("fin_q", "분기 재무"),
                       ("flows", "기관·외국인 수급"), ("dart", "공시")):
        fail = [r for r in recs if key in (r.get("errors") or {}) and "저장분 사용" not in r["errors"][key]]
        stale = [r for r in recs if key in (r.get("errors") or {}) and "저장분 사용" in r["errors"][key]]
        h = {"label": label, "ok": len(recs) - len(fail) - len(stale), "fail": len(fail), "stale": len(stale),
             "sample_error": (fail[0]["errors"][key] if fail else (stale[0]["errors"][key] if stale else ""))[:300]}
        if intraday_eff and key != "prices":
            h["note"] = f"장중 실행 — {cache.get('saved', '')[:16].replace('T', ' ')} 정식 실행 저장분 사용"
        health[key] = h

    as_of = max((r["sig"]["stats"]["date"] for r in recs if r.get("sig")), default=None)
    phase = {"key": "close", "label": "샘플", "now": datetime.now(KST).strftime("%m/%d %H:%M")} if not live \
        else market_phase(intraday, as_of)

    def is_os(r):
        e = (r.get("sig") or {}).get("os", {}).get("event")
        return bool(e and e["n"] >= C.OS_MIN_COUNT)

    meta = {
        "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "as_of": as_of, "phase": phase, "count": len(recs),
        "os_count": sum(1 for r in recs if is_os(r)),
        "os_today": sum(1 for r in recs if is_os(r) and r["sig"]["os"]["event"]["ago"] == 0),
        "bo_count": sum(1 for r in recs if (r.get("sig") or {}).get("bo")),
        "nh_count": sum(1 for r in recs if (r.get("sig") or {}).get("nh")),
        "go_count": sum(1 for r in recs if r["verdict"]["key"] == "go"),
        "groups": C.GROUP_NAMES,
        "group_count": {g: sum(1 for r in recs if r.get("group") == g) for g in C.GROUP_NAMES},
        "market": SG.market_summary(breadth, market, table, len(prepared)), "market_src": market_src,
        "table": table, "notes": notes, "health": health, "mode": provider.name,
        "run_kind": "intraday" if intraday_eff else "full",
        "fund_saved": cache.get("saved") if intraday_eff else datetime.now(KST).isoformat(timespec="minutes"),
        "elapsed_sec": round(time.time() - t0),
        "rules": {"os_rsi": C.OS_RSI, "os_bb": C.OS_BB_Z, "os_gap": C.OS_GAP_PCT, "min_count": C.OS_MIN_COUNT,
                  "lookback": C.SIGNAL_LOOKBACK, "min_sample": C.MIN_SAMPLE, "grade": C.GRADE_RULE,
                  "cost": C.COST_PCT, "bo_vol": C.BREAKOUT_VOLUME, "win_min": C.WIN_MIN, "fin_veto": C.FIN_VETO_PCT,
                  "low_value": C.LOW_VALUE_EOK, "sector_min": C.FIN_SECTOR_MIN},
        "us_url": C.US_SCREENER_URL, "refresh_url": C.REFRESH_URL, "ui": C.UI_VERSION,
    }

    if live and not intraday_eff and not limit:
        save_cache({"ver": C.CACHE_VER, "saved": datetime.now(KST).isoformat(timespec="minutes"), "universe": uni, "notes": notes,
                    "stocks": stores, "table": table})
        print(f"[저장분] {CACHE_PATH}")

    m = meta["market"]
    print(f"[4/4] 완료 {len(recs)}종목 · 과매도 후보 {meta['os_count']}(오늘 {meta['os_today']}) · 돌파 {meta['bo_count']} · 신고가 {meta['nh_count']} · 추천 {meta['go_count']} · "
          f"시장 폭 {m['breadth']}% · {meta['elapsed_sec']}초 · {phase['label']}")
    for h in health.values():
        if h["fail"] or h["stale"]:
            print(f"  ! {h['label']} 실패 {h['fail']} · 저장분 대체 {h['stale']} — 예: {h['sample_error'][:200]}")
    try:                                     # 증시캘린더 (공식 일정표 + 규칙 계산, 인터넷 불필요)
        import schedule
        meta["calendar"] = schedule.build(cache_path=os.path.join(HERE, "cache", "fred_calendar.json"))
        if meta["calendar"].get("note"):
            meta.setdefault("notes", []).append(meta["calendar"]["note"])
    except Exception as e:
        meta.setdefault("notes", []).append(f"증시캘린더 실패: {e}")
    if hasattr(provider, "today"):          # 오늘의 국장 (시장 요약 한 장)
        try:
            meta["today"] = provider.today(recs)
            for msg in (meta["today"].get("errors") or [])[:4]:
                meta.setdefault("notes", []).append("오늘의 국장 일부 실패 — " + str(msg)[:140])
        except Exception as e:
            meta.setdefault("notes", []).append(f"오늘의 국장 데이터 실패: {e}")
    return recs, meta


def diagnose():
    print("=== 데이터 출처 점검 ===")
    for code, nm in (("005930", "삼성전자"), ("247540", "에코프로비엠"), ("KOSPI", "코스피 지수")):
        r = S.fetch_prices(code, C.HISTORY_BARS)
        print(f"[{nm}] 시세:", "OK" if r.ok else "실패",
              (f"{len(r.data)}봉, {r.data.index[0].date()} ~ {r.data.index[-1].date()} 종가 {r.data['Close'].iloc[-1]:,.0f}" if r.ok else r.error))
        if code == "KOSPI":
            continue
        m = S.fetch_main(code)
        print("  기본정보:", "OK" if m.ok else "실패",
              json.dumps({k: v for k, v in (m.data or {}).items() if k not in ('perf', '_flows')}, ensure_ascii=False, default=str)[:500])
        for fq in ("Y", "Q"):
            r = S.fetch_fin_summary(code, fq)
            print(f"  재무({fq}):", "OK" if r.ok else "실패", "" if r.ok else r.error)
        r = S.fetch_flows(code, (m.data or {}).get("_flows"))
        print("  수급:", "OK" if r.ok else "실패", (f"{len(r.data)}일" if r.ok else r.error))
        r = S.fetch_disclosures_mobile(code)
        print("  공시(네이버):", "OK" if r.ok else "실패", (f"{len(r.data)}건" if r.ok else r.error))
    d = S.Dart(os.getenv("DART_API_KEY"))
    print("DART:", "OK" if d.ok else "사용 안 함/실패", d.error or f"회사코드 {len(d.corp)}개")


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
        print(json.dumps(S.probe(os.path.join(HERE, C.STATE_DIR, "probe")), ensure_ascii=False, indent=1))
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
    if not a.demo and sum(1 for r in recs if r.get("sig")) < len(recs) * 0.5:
        print("!! 시세가 절반 이상 실패 — 페이지를 갱신하지 않고 종료 (이전 페이지 유지)")
        sys.exit(1)

    import report
    report.build(recs, meta)
    if not a.no_notify and not a.demo and not a.limit:
        import notify
        notify.alert_top(recs, meta)                 # A·B 등급이 새로 뜨면 장중에도 바로
        if meta["run_kind"] == "full":
            notify.run(recs, meta)


if __name__ == "__main__":
    main()
