"""
universe.py — 350종목 명단 만들기
  코스피: 코스피200 공식 명단 (실패하면 코스피 시총 상위 200으로 대체하고 그 사실을 기록)
  코스닥: 시총 상위 150 (코스닥150 공식 명단은 거래소 로그인이 필요해서 대체)
바이오 포함 (사용자 결정). 스팩·ETF·ETN·리츠·우선주는 제외.
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
    """반환: (종목 목록, 경고 메시지들)"""
    notes: list[str] = []
    stocks: list[dict] = []

    k200 = S.fetch_kospi200()
    if k200.ok:
        stocks += [{"code": c, "name": n, "market": "KOSPI"} for c, n in k200.data if not is_excluded(c, n)]
    else:
        notes.append(k200.error + " → 코스피 시총 상위 200으로 대체")
        print("  ! 코스피200 명단 실패:", k200.error)
        rank = S.fetch_marcap_rank(0, 6)
        if not rank.ok:
            raise RuntimeError("코스피 종목 명단을 어느 곳에서도 못 받음: " + rank.error + " / 코스피200: " + k200.error)
        picked = [r for r in rank.data if not is_excluded(r["code"], r["name"])][: C.KOSPI_COUNT]
        stocks += [{"code": r["code"], "name": r["name"], "market": "KOSPI", "marcap": r["marcap"]} for r in picked]

    kq = S.fetch_marcap_rank(1, 5)
    if not kq.ok:
        raise RuntimeError("코스닥 종목 명단을 못 받음: " + kq.error)
    picked = [r for r in kq.data if not is_excluded(r["code"], r["name"])][: C.KOSDAQ_COUNT]
    stocks += [{"code": r["code"], "name": r["name"], "market": "KOSDAQ", "marcap": r["marcap"]} for r in picked]
    notes.append("코스닥은 공식 코스닥150 명단 대신 '시가총액 상위 150'을 사용")

    # 중복 제거
    seen, uniq = set(), []
    for s in stocks:
        if s["code"] not in seen:
            seen.add(s["code"])
            uniq.append(s)
    return uniq, notes
