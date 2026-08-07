"""회전지도 기준 좌표의 **독립 구현** + 검증용 픽스쳐 동결.

verify_flows.py 가 살아 있는 DB 를 읽으면 장중 진행형 마지막 봉이 확정될 때마다
좌표가 이동해 검사가 깨진다. 그래서 필요한 시계열을 픽스쳐로 동결한다.

**이 모듈은 app.analysis.flows 를 import 하지 않는다** — 스펙 4.3절 공식을 독립
구현한다. 두 독립 구현이 같은 고정 데이터에서 일치하는지가 검증의 핵심이다.
verify_flows.py 가 이 모듈을 import 해 매 실행마다 그 대조를 수행하므로,
어느 한쪽만 바뀌면 즉시 드러난다.

실행(픽스쳐는 건드리지 않고 골든만 다시 출력):
  .\\.venv\\Scripts\\python.exe scripts/gen_flows_golden.py

실행(DB 에서 픽스쳐를 새로 동결 — 골든 좌표가 전부 무효가 되므로 신중히):
  .\\.venv\\Scripts\\python.exe scripts/gen_flows_golden.py --refreeze
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "economic.db"
FIXTURE = ROOT / "scripts" / "fixtures" / "flows_history.json"
sys.path.insert(0, str(ROOT))
from app.config import settings  # noqa: E402  (flows 는 import 하지 않는다 — 독립성 유지)

POINTS = settings.history_points   # flows._POINTS 와 같은 출처를 읽어 자동 동기

# (지표키, 라벨, 환율키, 연산) — flows._ASSETS / _REGIONS 와 같은 구성
ASSET = [("sp500", "S&P500", "", ""), ("nasdaq", "나스닥", "", ""),
         ("kospi", "코스피", "usdkrw", "/"), ("tlt", "미 장기국채", "", ""),
         ("hyg", "하이일드채", "", ""), ("gold", "금", "", ""),
         ("copper", "구리", "", ""), ("wti", "WTI", "", ""),
         ("btc", "비트코인", "", ""), ("usdkrw", "원/달러", "", "")]
REGION = [("sp500", "미국", "", ""), ("kospi", "한국", "usdkrw", "/"),
          ("eustoxx", "유럽", "eurusd", "*"), ("shanghai", "중국본토", "usdcny", "/"),
          ("hangseng", "홍콩", "", ""), ("nikkei", "일본", "usdjpy", "/")]
ASSET9 = [s for s in ASSET if s[0] != "tlt"]        # tlt 이전의 독립 오라클
# 픽스쳐에 담을 전체 시계열(위 두 지도 + 환율 + vix 위험성격용)
FIX_KEYS = sorted({k for k, _, _, _ in ASSET + REGION}
                  | {"usdkrw", "eurusd", "usdcny", "usdjpy", "vix"})

SPECS = [("자산군9(오라클)", ASSET9, "GOLDEN_ASSET_9"),
         ("자산군", ASSET, "GOLDEN_ASSET"),
         ("지역", REGION, "GOLDEN_REGION")]


# ── 순수 계산부 (import 해서 쓴다 — 부작용 없음) ────────────────────────────

def norm(vals: list[float], win: int) -> list[float | None]:
    """롤링 z-정규화 — 모집단 표준편차(스펙 4.3절)."""
    out: list[float | None] = [None] * len(vals)
    for i in range(win - 1, len(vals)):
        w = vals[i - win + 1:i + 1]
        m = sum(w) / win
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / win)
        out[i] = 100.0 + (vals[i] - m) / sd if sd else 100.0
    return out


def rrg(spec, series) -> tuple[list[str], dict[str, tuple[float, float]]]:
    """스펙 4.3절 공식의 독립 구현. (공통 거래일, {키: (x, y)}) 를 돌려준다."""
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


def load_fixture() -> dict[str, list[tuple[str, float]]]:
    """동결된 픽스쳐를 읽는다. DB 를 건드리지 않는다."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {k: [(d, v) for d, v in rows] for k, rows in data["series"].items()}


# ── 동결(부작용 있음 — __main__ 에서만 호출한다) ───────────────────────────

def freeze_from_db() -> dict[str, list[tuple[str, float]]]:
    """DB 에서 시계열을 읽어 픽스쳐를 새로 쓴다. 기존 골든 좌표를 전부 무효화한다."""
    con = sqlite3.connect(DB)
    series = {}
    for key in FIX_KEYS:
        rows = con.execute(
            "SELECT date, value FROM history WHERE key=? AND value IS NOT NULL "
            "ORDER BY date DESC LIMIT ?", (key, POINTS)).fetchall()
        series[key] = [(d, round(v, 4)) for d, v in reversed(rows)]
    con.close()
    short = [f"{k}({len(v)}개)" for k, v in series.items() if len(v) < 200]
    if short:
        raise SystemExit(f"이력이 부족해 픽스쳐로 부적합: {', '.join(short)}")

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps({
        "generated": date.today().isoformat(),
        "note": "회전지도 검증용 동결 시계열. 살아 있는 DB 대신 이 파일로 검증한다. "
                "재동결하면 골든 좌표가 전부 무효가 되므로 --refreeze 후 반드시 "
                "출력된 골든을 verify_flows.py 에 반영해야 한다.",
        "series": {k: [list(p) for p in v] for k, v in series.items()},
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return series


def main() -> None:
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")      # cp949 콘솔 크래시 방지
    ap = argparse.ArgumentParser(description="회전지도 골든 좌표 산출")
    ap.add_argument("--refreeze", action="store_true",
                    help="DB 에서 픽스쳐를 새로 동결한다(기존 골든이 전부 무효가 된다)")
    args = ap.parse_args()

    if args.refreeze:
        series = freeze_from_db()
        print(f"픽스쳐 재동결: {FIXTURE}  시계열 {len(series)}개  "
              f"{FIXTURE.stat().st_size // 1024}KB")
        print("주의: 아래 골든을 verify_flows.py 에 반드시 반영할 것.\n")
    else:
        series = load_fixture()
        print(f"기존 픽스쳐 사용: {FIXTURE} (재동결하려면 --refreeze)")
        print("검증 실패를 조사할 때는 이 모드로 — 재동결하면 조사 대상이 사라진다.\n")

    for name, spec, var in SPECS:
        dates, pts = rrg(spec, series)
        print(f"# {name} — 교집합 {len(dates)}일, {dates[0]} ~ {dates[-1]}")
        print(f"{var} = {{")
        for k, lbl, _, _ in spec:
            print(f'    "{k}": ({pts[k][0]}, {pts[k][1]}),   # {lbl}')
        print(f"}}   # days = {len(dates)}\n")


if __name__ == "__main__":
    main()
