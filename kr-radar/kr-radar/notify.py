"""
notify.py — 텔레그램 알림. '변화'만 보낸다 (미장 버전과 같은 방식).
  🆕 새로 시그널이 뜬 종목   ↩️ 시그널이 사라진 종목
어제 목록은 state/prev_signals.json 에 저장해 두고 오늘과 비교한다.
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
    for i in range(0, len(text), 3800):             # 텔레그램 한 번에 4096자 제한
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": text[i:i + 3800], "disable_web_page_preview": "true"}, timeout=20)
        if not r.ok:
            print("[알림] 전송 실패:", r.text[:200])


def build_message(recs: list[dict], meta: dict, prev: dict) -> tuple[str | None, dict]:
    now = {r["code"]: r for r in recs if r.get("chart") and r["chart"]["is_signal"]}
    new = [now[c] for c in now if c not in prev]
    gone = [c for c in prev if c not in now]
    state = {c: {"name": r["name"], "score": r["chart"]["score"]} for c, r in now.items()}
    if not new and not gone:
        return None, state
    new.sort(key=lambda r: -(r["total"]["score"] or 0))
    lines = [f"👑 버거대왕 국장 레이더 · {meta.get('as_of')} 종가", f"시그널 {len(now)}종목 (새로 뜬 {len(new)} · 사라진 {len(gone)})", ""]
    for r in new[:20]:
        c = r["chart"]
        sig = ", ".join(meta["signal_meta"][k]["name"] for k in c["hits"]
                        if k in meta["signal_meta"] and k not in ("macd_gc_below0",))
        lines.append(f"🆕 {r['name']}({r['code']}) 총점 {r['total']['score']} · 차트 {c['score']} · {c['regime']['icon']}{c['regime']['name']}")
        lines.append(f"    {sig}")
    if len(new) > 20:
        lines.append(f"… 외 {len(new) - 20}종목")
    if gone:
        lines.append("")
        lines.append("↩️ 시그널 사라짐: " + ", ".join(prev[c]["name"] for c in gone[:30]))
    url = os.getenv("SITE_URL")
    if url:
        lines += ["", url]
    return "\n".join(lines), state


def run(recs: list[dict], meta: dict) -> None:
    path = os.path.join(HERE, C.STATE_DIR, "prev_signals.json")
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        prev = {}
    msg, state = build_message(recs, meta, prev)
    if msg:
        _send(msg)
    else:
        print("[알림] 어제와 변화 없음")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0)
