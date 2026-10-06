"""
report.py — 계산 결과를 웹페이지(site/)로 만든다.

  site/index.html              화면
  site/data.js                 350종목 데이터 + 과거 성과표 (window.RADAR = {...})
  site/c/종목코드.js            종목별 캔들차트 (종목을 눌렀을 때만 불러옴)
  site/assets/burger-king.png  버거대왕 그림 (자르지 않고 그대로)

화면 파일(index.html)과 그림은 run.py 와 같은 폴더에 둔다. (예전처럼 web/ 폴더 안에 있어도 찾는다)
화면 파일 버전이 코드와 안 맞으면 조용히 옛 화면을 쓰지 않고 멈춘다.
"""
from __future__ import annotations

import json
import math
import os
import shutil

import config as C

HERE = os.path.dirname(os.path.abspath(__file__))


def slim(x):
    """소수점 자리 줄이기 + NaN/무한대 → null."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, 2)
    if isinstance(x, dict):
        return {k: slim(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [slim(v) for v in x]
    return x


def find_ui() -> str:
    """버전 표시가 맞는 화면 파일을 찾는다."""
    tried = []
    for p in (os.path.join(HERE, "index.html"), os.path.join(HERE, "web", "index.html")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                body = f.read()
            if f'data-ui="{C.UI_VERSION}"' in body:
                return body
            tried.append(p + " (옛 버전)")
        else:
            tried.append(p + " (없음)")
    raise RuntimeError(f"화면 파일 index.html 이 코드와 맞는 버전({C.UI_VERSION})이 아닙니다: " + " / ".join(tried)
                       + " → 새 index.html 을 run.py 와 같은 폴더에 올려주세요")


ICON_FILES = ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png")
MANIFEST = {
    "name": "버거대왕의 국장 레이더", "short_name": "버거대왕", "start_url": "./", "scope": "./",
    "display": "standalone", "background_color": "#eaf3ff", "theme_color": "#eaf3ff", "lang": "ko",
    "icons": [
        {"src": "assets/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "assets/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "assets/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}


def find_image() -> str:
    for p in (os.path.join(HERE, "burger-king.png"), os.path.join(HERE, "web", "burger-king.png")):
        if os.path.exists(p):
            return p
    raise RuntimeError("burger-king.png 을 찾을 수 없습니다")


def build(recs: list[dict], meta: dict, out_dir: str | None = None, inline_charts: bool = False, fragment: bool = False) -> str:
    """inline_charts=True: 차트를 data.js 안에 모두 넣음(미리보기용). fragment=True: 문서 틀 없이 본문만."""
    body = find_ui()
    out = out_dir or os.path.join(HERE, C.SITE_DIR)
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    cdir = os.path.join(out, "c")
    if os.path.isdir(cdir):
        shutil.rmtree(cdir)
    os.makedirs(cdir)

    fin_text, risk_text, inline, slim_recs = {}, {}, {}, []
    for r in recs:
        r = json.loads(json.dumps(r, default=str))
        ch = r.pop("chart", None)
        if ch and inline_charts:
            inline[r["code"]] = slim(ch)
        elif ch:
            with open(os.path.join(cdir, f"{r['code']}.js"), "w", encoding="utf-8") as f:
                f.write(f'window.__chart&&window.__chart("{r["code"]}",' + json.dumps(slim(ch), separators=(",", ":")) + ");")
        for it in (r.get("fin") or {}).get("items", []):       # 종목마다 똑같은 설명 문구는 한 번만 저장
            fin_text[it["key"]] = {"explain": it.pop("explain", ""), "name": it["name"], "max": it["max"]}
            it.pop("ratio", None)
        for it in (r.get("risk") or {}).get("items", []):
            risk_text[it["key"]] = it.pop("explain", "")
        slim_recs.append(r)
    meta = dict(meta, fin_text=fin_text, risk_text=risk_text)
    js = "window.RADAR=" + json.dumps(slim({"meta": meta, "stocks": slim_recs}), ensure_ascii=False, separators=(",", ":")) + ";"
    if inline:
        js += "\nwindow.__CHARTS=" + json.dumps(inline, separators=(",", ":")) + ";"
    with open(os.path.join(out, "data.js"), "w", encoding="utf-8") as f:
        f.write(js)
    page = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '<meta name="theme-color" content="#eaf3ff">\n'
            '<meta name="mobile-web-app-capable" content="yes">\n'
            '<meta name="apple-mobile-web-app-capable" content="yes">\n'
            '<meta name="apple-mobile-web-app-title" content="버거대왕">\n'
            '<meta name="apple-mobile-web-app-status-bar-style" content="default">\n'
            '<link rel="apple-touch-icon" href="assets/apple-touch-icon.png">\n'
            '<link rel="icon" type="image/png" href="assets/icon-192.png">\n'
            '<link rel="manifest" href="manifest.webmanifest">\n'
            '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(body if fragment else page)
    shutil.copyfile(find_image(), os.path.join(out, "assets", "burger-king.png"))
    for name in ICON_FILES:                                  # 폰 홈 화면 아이콘
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            shutil.copyfile(p, os.path.join(out, "assets", name))
    with open(os.path.join(out, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False, indent=1)
    print(f"[리포트] {out}/index.html  (data.js {len(js) / 1e6:.1f}MB)")
    return out
