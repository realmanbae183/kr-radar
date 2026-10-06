"""
universe.py — 종목 명단 만들기
  코스피: 전 종목 (보통주). 시가총액 상위 200 = '코스피 대형'(KL), 나머지 = '코스피 중소형'(KM)
  코스닥: 시가총액 상위 150 (KQ)
스팩·ETF·ETN·리츠·우선주는 제외. 바이오 포함(사용자 결정).
묶음을 나누는 이유: 2026-10-06 측정에서 과매도 겹침은 모든 묶음에서 통했지만,
거래량 급등 추격은 코스닥150에서만 플러스였고 코스피 중소형에서는 손해였다. 등급도 묶음별 과거 기록으로 매긴다.
"""
from __future__ import annotations

import config as C
import sources as S


def is_excluded(code: str, name: str) -> bool:
    if C.EXCLUDE_PREFERRED and not code.endswith("0"):   # 보통주 코드는 끝자리가 0, 우선주는 5·7·9·K 등
        return True
    if any(k in name for k in C.EXCLUDE_NAME_KEYWORDS):
        return True
    if name.startswith(S.ETF_BRANDS):
        return True
    return False


def build_universe() -> tuple[list[dict], list[str]]:
    """반환: (종목 목록, 경고 메시지들). 종목마다 group(KL/KM/KQ)이 붙는다."""
    notes: list[str] = []
    kp = S.fetch_marcap_json("KOSPI", 60)          # ETF가 섞여 있어 넉넉히 넘긴다 (빈 페이지에서 멈춤)
    if not kp.data or len(kp.data) < C.KOSPI_COUNT:
        raise RuntimeError("코스피 종목 명단을 못 받음: " + str(kp.error))
    if not kp.ok:
        notes.append("코스피 명단을 끝까지 못 받음 (일부만 사용): " + str(kp.error)[:150])
    kq = S.fetch_marcap_json("KOSDAQ", 6)
    if not kq.data:
        raise RuntimeError("코스닥 종목 명단을 못 받음: " + str(kq.error))

    stocks, seen = [], set()
    rows = sorted([r for r in kp.data if not is_excluded(r["code"], r["name"])], key=lambda r: -(r.get("marcap") or 0))
    for i, r in enumerate(rows):
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        stocks.append({"code": r["code"], "name": r["name"], "market": "KOSPI", "marcap": r.get("marcap"),
                       "group": "KL" if i < C.KOSPI_LARGE else "KM", "halt": bool(r.get("halt"))})
    n_kospi = len(stocks)
    rows = sorted([r for r in kq.data if not is_excluded(r["code"], r["name"])], key=lambda r: -(r.get("marcap") or 0))
    for r in rows[: C.KOSDAQ_COUNT]:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        stocks.append({"code": r["code"], "name": r["name"], "market": "KOSDAQ", "marcap": r.get("marcap"),
                       "group": "KQ", "halt": bool(r.get("halt"))})
    notes.append(f"코스피 전 종목 {n_kospi}개(보통주) + 코스닥 시가총액 상위 {len(stocks) - n_kospi}개")
    return stocks, notes
