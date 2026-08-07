"""회전지도 검증용 픽스쳐 동결 + 기준 좌표 산출.

verify_flows.py 가 살아 있는 DB 를 읽으면 장중 진행형 마지막 봉이 확정될 때마다
좌표가 이동해 검사가 깨진다. 그래서 필요한 시계열을 픽스쳐로 동결한다.

이 스크립트는 app.analysis.flows 를 import 하지 않는다 — 스펙 4.3절 공식을
독립 구현해 골든을 낸다. 두 개의 독립 구현이 같은 고정 데이터에서 일치하는지가
verify_flows.py 가 검증하는 것이다.

재생성이 필요할 때만 실행한다(데이터 갱신은 골든을 무효화하므로 신중히):
  .\\.venv\\Scripts\\python.exe scripts/gen_flows_golden.py
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "economic.db"
OUT = ROOT / "scripts" / "fixtures" / "flows_history.json"
POINTS = 1300          # flows._POINTS 와 같아야 한다

# (지표키, 라벨, 환율키, 연산) — flows._ASSETS / _REGIONS 와 같은 구성
ASSET = [("sp500", "S&P500", "", ""), ("nasdaq", "나스닥", "", ""),
         ("kospi", "코스피", "usdkrw", "/"), ("tlt", "미 장기국채", "", ""),
         ("hyg", "하이일드채", "", ""), ("gold", "금", "", ""),
         ("copper", "구리", "", ""), ("wti", "WTI", "", ""),
         ("btc", "비트코인", "", ""), ("usdkrw", "원/달러", "", "")]
REGION = [("sp500", "미국", "", ""), ("kospi", "한국", "usdkrw", "/"),
          ("eustoxx", "유럽", "eurusd", "*"), ("shanghai", "중국본토", "usdcny", "/"),
          ("hangseng", "홍콩", "", ""), ("nikkei", "일본", "usdjpy", "/")]
# 픽스쳐에 담을 전체 시계열(위 두 지도 + 환율 + vix 위험성격용)
FIX_KEYS = sorted({k for k, _, _, _ in ASSET + REGION}
                  | {"usdkrw", "eurusd", "usdcny", "usdjpy", "vix"})


def load_db(key: str) -> list[tuple[str, float]]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT date, value FROM history WHERE key=? AND value IS NOT NULL "
        "ORDER BY date DESC LIMIT ?", (key, POINTS)).fetchall()
    con.close()
    return [(d, round(v, 4)) for d, v in reversed(rows)]


def norm(vals: list[float], win: int) -> list[float | None]:
    """롤링 z-정규화 — 모집단 표준편차(스펙 4.3절)."""
    out: list[float | None] = [None] * len(vals)
    for i in range(win - 1, len(vals)):
        w = vals[i - win + 1:i + 1]
        m = sum(w) / win
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / win)
        out[i] = 100.0 + (vals[i] - m) / sd if sd else 100.0
    return out


def rrg(spec, series):
    need = {k for k, _, _, _ in spec} | {f for _, _, f, _ in spec if f}
    S = {k: dict(series[k]) for k in need}
    dates = [d for d in sorted(S[spec[0][0]]) if all(S[k].get(d) is not None for k in need)]
    P = {}
    for k, _, fk, op in spec:
        if not fk:
            P[k] = [S[k][d] for d in dates]
        elif op == "/":
            P[k] = [S[k][d] / S[fk][d] for d in dates]
        else:
            P[k] = [S[k][d] * S[fk][d] for d in dates]
    order = [k for k, _, _, _ in spec]
    N = {k: [100.0 * v / P[k][0] for v in P[k]] for k in order}
    B = [sum(N[k][i] for k in order) / len(order) for i in range(len(dates))]
    out = {}
    for k in order:
        rs = [100.0 * N[k][i] / B[i] for i in range(len(dates))]
        r = norm(rs, 63)
        m = norm([v if v is not None else 100.0 for v in r], 21)
        out[k] = (round(r[-1], 2), round(m[-1], 2))
    return dates, out


series = {k: load_db(k) for k in FIX_KEYS}
for k, v in series.items():
    if len(v) < 200:
        print(f"경고: {k} 이력이 {len(v)}개뿐 — 픽스쳐로 부적합")
        sys.exit(1)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    "generated": date.today().isoformat(),
    "note": "회전지도 검증용 동결 시계열. 살아 있는 DB 대신 이 파일로 검증한다. "
            "재생성하면 골든 좌표가 무효가 되므로 scripts/gen_flows_golden.py 를 다시 돌려야 한다.",
    "series": series,
}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"픽스쳐 저장: {OUT}  시계열 {len(series)}개  {OUT.stat().st_size // 1024}KB")

spec9 = [s for s in ASSET if s[0] != "tlt"]
for name, spec, var in [("자산군9(오라클)", spec9, "GOLDEN_ASSET_9"),
                        ("자산군", ASSET, "GOLDEN_ASSET"),
                        ("지역", REGION, "GOLDEN_REGION")]:
    dates, pts = rrg(spec, series)
    print(f"\n# {name} — 교집합 {len(dates)}일, {dates[0]} ~ {dates[-1]}")
    print(f"{var} = {{")
    for k, lbl, _, _ in spec:
        print(f'    "{k}": ({pts[k][0]}, {pts[k][1]}),   # {lbl}')
    print(f"}}   # days = {len(dates)}")
