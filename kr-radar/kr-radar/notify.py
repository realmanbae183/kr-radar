"""
notify.py — 텔레그램 알림. 정식 실행(장 마감 후) 때만, '새로 뜬 것'만 보낸다.
  새 과매도 후보(2개 이상 겹침) · 새 돌파 후보 · 오늘 시장 폭
어제까지의 목록은 state/prev_signals.json 에 저장해 두고 비교한다.
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 가 없으면 아무것도 안 보낸다.
"""
from __future__ import annotations

import json
import os

import requests

import config as C

HERE = os.path.dirname(os.path.abspath(__file__))


def _send(text: str) -> None:
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        print("[알림] 텔레그램 설정 없음 → 건너뜀")
        return
    for i in range(0, len(text), 3800):
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": text[i:i + 3800], "disable_web_page_preview": "true"}, timeout=20)
        if not r.ok:
            print("[알림] 전송 실패:", r.text[:200])


def _hist_line(h: dict | None) -> str:
    if not h:
        return "과거 기록 없음"
    return f"과거 {h['n']:,}번 · 5일 승률 {h.get('w5')}% · 평균 {h.get('m5', 0):+.1f}%"


def build_message(recs: list[dict], meta: dict, prev: dict) -> tuple[str | None, dict]:
    os_now, bo_now = {}, {}
    for r in recs:
        sig = r.get("sig") or {}
        e = (sig.get("os") or {}).get("event")
        if e and e["n"] >= C.OS_MIN_COUNT:
            os_now[r["code"] + "@" + e["date"]] = r
        if sig.get("bo"):
            bo_now[r["code"] + "@" + sig["bo"]["date"]] = r
    state = {"os": sorted(os_now), "bo": sorted(bo_now)}
    new_os = [os_now[k] for k in os_now if k not in set(prev.get("os", []))]
    new_bo = [bo_now[k] for k in bo_now if k not in set(prev.get("bo", []))]
    if not new_os and not new_bo:
        return None, state

    m = meta["market"]
    lines = [f"버거대왕 국장 레이더 · {meta.get('as_of')} 종가",
             f"시장 폭 {m.get('breadth')}% ({m.get('above')}/{m.get('stocks')}종목이 20일선 위)"]
    if m.get("bucket") == "lt30":
        lines.append("다 같이 빠진 날 — 과거 과매도 매수가 잘 먹히던 장")
    grade_rank = {"A": 0, "B": 1, "C": 2, "D": 3, None: 4}
    if new_os:
        new_os.sort(key=lambda r: (grade_rank[r["sig"]["os"]["grade"]], -r["sig"]["os"]["event"]["n"]))
        lines += ["", f"과매도 후보 새로 {len(new_os)}종목"]
        for r in new_os[:15]:
            o = r["sig"]["os"]
            e = o["event"]
            on = " · ".join(n for k, n in (("rsi", f"RSI {e['rsi']}"), ("bb", "볼린저 하단"), ("gap", f"이격 {e['gap']}%")) if e["flags"][k])
            v = (r.get("verdict") or {})
            warn = {"go": " ✅조건 통과", "veto": " ⛔비추", "weak": " ·승률 미달"}.get(v.get("key") if v.get("signal") == "os" else None, "")
            lines.append(f"[{o['grade'] or '-'}] {r['name']}({r['code']}) {e['n']}개 겹침 — {on}{warn}")
            lines.append(f"     {_hist_line(o['hist'])}")
        if len(new_os) > 15:
            lines.append(f"… 외 {len(new_os) - 15}종목")
    if new_bo:
        lines += ["", f"거래량 실린 구름대 돌파 새로 {len(new_bo)}종목"]
        for r in new_bo[:8]:
            b = r["sig"]["bo"]
            lines.append(f"[{b['grade'] or '-'}] {r['name']}({r['code']}) 거래량 {b['vol_mult']}배 — {_hist_line(b['hist'])}")
    url = os.getenv("SITE_URL")
    if url:
        lines += ["", url]
    return "\n".join(lines), state


def run(recs: list[dict], meta: dict) -> None:
    path = os.path.join(HERE, C.STATE_DIR, "prev_signals.json")
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
        if not isinstance(prev, dict) or "os" not in prev:
            prev = {}                       # 옛 형식 파일이면 처음부터
    except (FileNotFoundError, json.JSONDecodeError):
        prev = {}
    msg, state = build_message(recs, meta, prev)
    if msg:
        _send(msg)
    else:
        print("[알림] 새로 뜬 신호 없음")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0)
