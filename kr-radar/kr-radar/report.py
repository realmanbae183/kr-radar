"""
report.py — 계산 결과를 웹페이지(site/)로 만든다.

  site/index.html        화면 (web/index.html 을 HTML 문서로 감쌈)
  site/data.js           350종목 데이터 (window.RADAR = {...})
  site/assets/burger-king.png   버거대왕 모델 시트 원본 (자르지 않고 그대로)
"""
from __future__ import annotations

import json
import math
import os
import shutil

import config as C

HERE = os.path.dirname(os.path.abspath(__file__))


def slim(x):
    """소수점 자리 줄이기 + NaN/무한대 → null (파일 크기 줄이기)."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, 2)
    if isinstance(x, dict):
        return {k: slim(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [slim(v) for v in x]
    return x


def build(recs: list[dict], meta: dict, out_dir: str | None = None, inline_charts: bool = False, fragment: bool = False) -> str:
    """inline_charts=True: 차트를 data.js 안에 모두 넣음 (파일 1개로 미리보기할 때). fragment=True: 문서 틀 없이 본문만."""
    out = out_dir or os.path.join(HERE, C.SITE_DIR)
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    # 캔들차트 데이터는 종목별 작은 파일(site/c/코드.js)로 나눠서, 종목을 눌렀을 때만 불러온다
    cdir = os.path.join(out, "c")
    if os.path.isdir(cdir):
        shutil.rmtree(cdir)
    os.makedirs(cdir)
    fin_text, risk_text = {}, {}
    inline = {}
    slim_recs = []
    for r in recs:
        r = json.loads(json.dumps(r, default=str))          # 원본을 건드리지 않도록 복사
        ch = (r.get("chart") or {}).pop("chart", None)
        if ch and inline_charts:
            inline[r["code"]] = slim(ch)
        elif ch:
            with open(os.path.join(cdir, f"{r['code']}.js"), "w", encoding="utf-8") as f:
                f.write(f'window.__chart&&window.__chart("{r["code"]}",'
                        + json.dumps(slim(ch), separators=(",", ":")) + ");")
        # 종목마다 똑같이 반복되는 설명 문구는 한 번만 저장
        for it in (r.get("fin") or {}).get("items", []):
            fin_text[it["key"]] = {"explain": it.pop("explain", ""), "rule": it.pop("rule", None) or fin_text.get(it["key"], {}).get("rule")}
        for it in (r.get("risk") or {}).get("items", []):
            risk_text[it["key"]] = it.pop("explain", "")
        slim_recs.append(r)
    meta = dict(meta, fin_text=fin_text, risk_text=risk_text)
    payload = {"meta": meta, "stocks": slim_recs}
    js = "window.RADAR=" + json.dumps(slim(payload), ensure_ascii=False, separators=(",", ":")) + ";"
    if inline:
        js += "\nwindow.__CHARTS=" + json.dumps(inline, separators=(",", ":")) + ";"
    with open(os.path.join(out, "data.js"), "w", encoding="utf-8") as f:
        f.write(js)
    with open(os.path.join(HERE, "web", "index.html"), encoding="utf-8") as f:
        body = f.read()
    # GitHub Pages용: 완전한 HTML 문서로 감싼다 (web/index.html 은 미리보기 페이지와 같은 본문)
    page = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(body if fragment else page)
    shutil.copyfile(os.path.join(HERE, "web", "burger-king.png"), os.path.join(out, "assets", "burger-king.png"))
    print(f"[리포트] {out}/index.html  (data.js {len(js) / 1e6:.1f}MB)")
    return out
