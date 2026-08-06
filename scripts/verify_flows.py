"""자금 회전지도 기준 좌표 검증.

스펙 docs/superpowers/specs/2026-08-06-capital-flow-map-design.md 6절의 기준 좌표와
구현 결과가 일치하는지 확인한다. 값이 어긋나면 계산 오류다.

실행:  .\\.venv\\Scripts\\python.exe scripts/verify_flows.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")          # cp949 콘솔 크래시 방지
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import flows                     # noqa: E402

TOL = 0.02          # 부동소수 오차 허용치

# 기준일 2026-08-05. tlt 수집 전 9종 기준.
GOLDEN_ASSET = {
    "copper": (101.93, 100.62),
    "sp500":  (101.48, 99.48),
    "nasdaq": (101.43, 100.49),
    "gold":   (100.53, 102.99),
    "hyg":    (100.39, 98.88),
    "btc":    (99.48, 100.15),
    "kospi":  (99.43, 99.54),
    "usdkrw": (99.23, 97.38),
    "wti":    (99.01, 99.33),
}
GOLDEN_REGION = {
    "sp500":    (101.30, 100.19),
    "eustoxx":  (101.05, 100.61),
    "nikkei":   (101.00, 100.00),
    "hangseng": (100.23, 100.63),
    "kospi":    (99.16, 99.34),
    "shanghai": (98.69, 98.64),
}
GOLDEN_DAYS = {"자산군": 744, "지역": 915}

failures: list[str] = []


def check(name: str, m: dict | None, golden: dict, days: int) -> None:
    if m is None:
        failures.append(f"[{name}] 지도가 None — 데이터 부족 또는 계산 실패")
        return
    print(f"[{name}] as_of={m['as_of']}  거래일={m['days']}  자산={len(m['rows'])}개")
    if m["days"] != days:
        failures.append(f"[{name}] 공통 거래일 {m['days']} != 기준 {days}")
    got = {r["key"]: (r["x"], r["y"]) for r in m["rows"]}
    if set(got) != set(golden):
        failures.append(f"[{name}] 자산 구성 불일치: {sorted(got)} != {sorted(golden)}")
    for key, (gx, gy) in golden.items():
        if key not in got:
            failures.append(f"[{name}] {key} 누락")
            continue
        x, y = got[key]
        ok = abs(x - gx) <= TOL and abs(y - gy) <= TOL
        print(f"   {'OK  ' if ok else 'FAIL'} {key:9s} x={x:7.2f}(기준 {gx:7.2f})  "
              f"y={y:7.2f}(기준 {gy:7.2f})")
        if not ok:
            failures.append(f"[{name}] {key} 좌표 불일치 ({x:.2f},{y:.2f}) != ({gx},{gy})")
    for r in m["rows"]:
        if not r["tail"]:
            failures.append(f"[{name}] {r['key']} 꼬리 비어 있음")
        if r["quadrant"] not in ("주도", "약화", "개선", "지체"):
            failures.append(f"[{name}] {r['key']} 사분면 값 이상: {r['quadrant']}")


check("자산군", flows.asset_map(), GOLDEN_ASSET, GOLDEN_DAYS["자산군"])
print()
check("지역", flows.region_map(), GOLDEN_REGION, GOLDEN_DAYS["지역"])

print()
if failures:
    print(f"실패 {len(failures)}건")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("전부 통과")
