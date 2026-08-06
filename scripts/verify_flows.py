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

# 스펙 문서에서 나온 독립 오라클 — tlt 를 뺀 9종. 구현 출력으로 덮어쓰지 말 것.
# 이 값이 깨지면 _rrg 의 계산이 바뀐 것이다.
GOLDEN_ASSET_9 = {
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
# 회귀 잠금 — tlt 포함 10종. Task 2 구현 시점 출력을 고정한 값이라
# 독립 검증력은 없다(계산 검증은 GOLDEN_ASSET_9 담당). 좌표가 흔들리면 여기서 잡힌다.
GOLDEN_ASSET = {
    "copper": (101.97, 100.72),
    "sp500":  (101.53, 99.57),
    "nasdaq": (101.47, 100.62),
    "gold":   (100.53, 103.04),
    "hyg":    (100.42, 98.88),
    "tlt":    (99.80, 98.62),
    "btc":    (99.49, 100.19),
    "kospi":  (99.44, 99.56),
    "usdkrw": (99.21, 97.38),
    "wti":    (99.02, 99.35),
}
GOLDEN_REGION = {
    "sp500":    (101.30, 100.19),
    "eustoxx":  (101.05, 100.61),
    "nikkei":   (101.00, 100.00),
    "hangseng": (100.23, 100.63),
    "kospi":    (99.16, 99.34),
    "shanghai": (98.69, 98.64),
}
GOLDEN_DAYS = {"자산군9": 744, "자산군": 744, "지역": 915}

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


# tlt 를 뺀 9종으로 계산해 스펙 기준 좌표와 대조 — 계산 로직의 독립 검증
_spec9 = [s for s in flows._ASSETS if s[0] != "tlt"]
check("자산군9(오라클)", flows._rrg(_spec9), GOLDEN_ASSET_9, GOLDEN_DAYS["자산군9"])
print()
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
