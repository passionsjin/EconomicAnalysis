"""자금 회전지도 기준 좌표 검증.

스펙 docs/superpowers/specs/2026-08-06-capital-flow-map-design.md 6절의 기준 좌표와
구현 결과가 일치하는지 확인한다. 값이 어긋나면 계산 오류다.

실행:  .\\.venv\\Scripts\\python.exe scripts/verify_flows.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")          # cp949 콘솔 크래시 방지
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import flows                     # noqa: E402

# ── 검증은 동결 픽스쳐로 한다 ──
# 살아 있는 DB 를 읽으면 장중 진행형 마지막 봉이 확정될 때마다 좌표가 이동해
# 검사가 무의미해진다(2026-08-06 실제 발생). repo 접근만 픽스쳐로 갈아끼운다.
_FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "flows_history.json")
                  .read_text(encoding="utf-8"))


def _fixture_batch(keys, points: int = 60):
    return {k: [{"date": d, "value": v} for d, v in _FIX["series"].get(k, [])][-points:]
            for k in keys}


flows.repo.get_series_batch = _fixture_batch

TOL = 0.02          # 부동소수 오차 허용치

# 독립 오라클 — scripts/gen_flows_golden.py 가 flows.py 를 import 하지 않고
# 스펙 4.3절 공식을 직접 구현해 산출한 값. 동결 픽스쳐 기준이라 재현 가능하다.
# flows.py 의 출력으로 덮어쓰지 말 것 — 두 독립 구현의 일치가 이 검사의 전부다.
GOLDEN_ASSET_9 = {
    "sp500": (101.44, 99.4),
    "nasdaq": (101.25, 100.13),
    "kospi": (99.48, 99.61),
    "hyg": (100.49, 99.1),
    "gold": (100.22, 102.5),
    "copper": (102.02, 100.85),
    "wti": (99.04, 99.37),
    "btc": (99.55, 100.35),
    "usdkrw": (99.27, 97.41),
}
# 10종 회귀 잠금 — 위와 같은 독립 산출.
GOLDEN_ASSET = {
    "sp500": (101.49, 99.48),
    "nasdaq": (101.29, 100.25),
    "kospi": (99.48, 99.62),
    "tlt": (99.89, 98.82),
    "hyg": (100.52, 99.1),
    "gold": (100.22, 102.56),
    "copper": (102.06, 100.93),
    "wti": (99.05, 99.39),
    "btc": (99.56, 100.39),
    "usdkrw": (99.25, 97.41),
}
GOLDEN_REGION = {
    "sp500": (101.16, 100.02),
    "kospi": (99.19, 99.38),
    "eustoxx": (101.06, 100.62),
    "shanghai": (98.66, 98.59),
    "hangseng": (100.24, 100.64),
    "nikkei": (101.01, 100.02),
}
GOLDEN_DAYS = {"자산군9": 744, "자산군": 744, "지역": 914}

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

# ── 독립 구현 대조 (매 실행) ──
# 위 GOLDEN_* 은 사람이 한 번 붙여넣은 정적 값이라, gen_flows_golden 쪽이 나중에
# 바뀌면 아무도 눈치채지 못한다. 여기서 두 구현을 같은 픽스쳐로 매번 직접 돌려
# 대조한다 — 이 대조가 "두 독립 구현의 일치"라는 검증의 근거를 실시간으로 유지한다.
print()
import gen_flows_golden as oracle                                    # noqa: E402

_fix_series = oracle.load_fixture()
for _name, _spec, _ in oracle.SPECS:
    _dates, _pts = oracle.rrg(_spec, _fix_series)
    _keys = [s[0] for s in _spec]
    # flows 쪽 동일 스펙 구성 — 라벨·자산군은 좌표에 무관하므로 키·환율 규칙만 맞춘다
    _flows_spec = [(k, lbl, "equity", fk, op) for k, lbl, fk, op in _spec]
    _got = flows._rrg(_flows_spec)
    if not _got:
        failures.append(f"[독립대조] {_name} 계산 실패")
        continue
    if _got["days"] != len(_dates):
        failures.append(f"[독립대조] {_name} 거래일 {_got['days']} != 오라클 {len(_dates)}")
    _bad = [(r["key"], r["x"], r["y"], *_pts[r["key"]]) for r in _got["rows"]
            if abs(r["x"] - _pts[r["key"]][0]) > TOL or abs(r["y"] - _pts[r["key"]][1]) > TOL]
    if _bad:
        failures.append(f"[독립대조] {_name} 좌표 불일치 {_bad}")
    print(f"[독립대조] {_name:14s} {len(_keys)}종 · {len(_dates)}일 — "
          f"{'일치' if not _bad else '불일치'}")

# ── Task 3 요소 검증 ──
bundle = flows.build_flows()
print()
print(f"[묶음] asset={'있음' if bundle['asset'] else '없음'} "
      f"region={'있음' if bundle['region'] else '없음'} "
      f"ranks={len(bundle['ranks'])}개")

if bundle["asset"]:
    missing = [r["key"] for r in bundle["asset"]["rows"] if r.get("risk") is None]
    if missing:
        failures.append(f"[위험성격] risk 누락: {missing}")
    else:
        for r in sorted(bundle["asset"]["rows"], key=lambda r: r["risk"]):
            print(f"   risk {r['key']:9s} {r['risk']:+7.3f}%")
        # 방향 상식 검증: 비트코인은 공격(음수), 원/달러는 방어(양수)여야 한다
        by = {r["key"]: r["risk"] for r in bundle["asset"]["rows"]}
        if by.get("btc", 0) >= 0:
            failures.append(f"[위험성격] btc 가 공격(음수)이 아님: {by.get('btc')}")
        if by.get("usdkrw", 0) <= 0:
            failures.append(f"[위험성격] usdkrw 가 방어(양수)가 아님: {by.get('usdkrw')}")

# 순위표 기준값 (현재순위, 3개월전순위, 3개월수익률%).
# 값을 고정해두지 않았던 탓에, 룩백을 '봉 개수'로 세어 주말 거래 자산(btc)이
# 훨씬 짧은 기간을 보던 버그가 변이 테스트를 통과해버렸다 — btc 가 +5.48%(2위)로
# 표시됐지만 실제 3개월은 -19.86%(14위)였다. 구조만 검사하면 이런 결함은 안 잡힌다.
GOLDEN_RANK = {
    "copper": (1, 7, 7.33),      "eustoxx": (2, 9, 5.73),
    "sp500": (3, 6, 5.08),       "nikkei": (4, 3, 2.81),
    "nasdaq": (5, 4, 2.1),       "hyg": (6, 10, -0.5),
    "hangseng": (7, 13, -1.14),  "usdkrw": (8, 11, -2.32),
    "tlt": (9, 12, -3.65),       "shanghai": (10, 8, -5.93),
    "gold": (11, 14, -9.13),     "kospi": (12, 2, -14.05),
    "wti": (13, 1, -18.09),      "btc": (14, 5, -19.86),
}

if not bundle["ranks"]:
    failures.append("[순위표] 비어 있음")
else:
    for r in bundle["ranks"][:5]:
        print(f"   rank {r['label']:10s} {r['prev']:2d}위 -> {r['now']:2d}위 ({r['shift']:+d})")
    shifts = {r["key"]: r["now"] for r in bundle["ranks"]}
    if len(shifts) != len(bundle["ranks"]):
        failures.append("[순위표] key 중복")
    if sorted(r["now"] for r in bundle["ranks"]) != list(range(1, len(bundle["ranks"]) + 1)):
        failures.append("[순위표] now 순위가 1..N 연속이 아님")
    got_rank = {r["key"]: (r["now"], r["prev"], r["m3"]) for r in bundle["ranks"]}
    if set(got_rank) != set(GOLDEN_RANK):
        failures.append(f"[순위표] 구성 불일치: {sorted(set(got_rank) ^ set(GOLDEN_RANK))}")
    for key, exp in GOLDEN_RANK.items():
        got = got_rank.get(key)
        if got is None:
            failures.append(f"[순위표] {key} 누락")
        elif got[:2] != exp[:2] or abs(got[2] - exp[2]) > 0.05:
            failures.append(f"[순위표] {key} 불일치 {got} != {exp}")
    print(f"   순위 기준값 {len(GOLDEN_RANK)}개 대조 완료")

# 꼬리·위험성격·요약 기준값 — 값을 고정하지 않으면 결함이 조용히 지나간다.
# (순위표가 정확히 그렇게 25%p 틀린 채 변이 테스트 5종을 통과했다.)
GOLDEN_RISK = {
    "copper": -0.737, "sp500": -1.558, "nasdaq": -1.971, "hyg": -0.455, "gold": 0.103,
    "tlt": -0.288, "btc": -1.88, "kospi": -0.92, "usdkrw": 0.251, "wti": 0.287,
}
GOLDEN_TAIL_GOLD = [
    ("2026-06-10", 98.479, 100.692), ("2026-06-17", 98.905, 101.914),
    ("2026-06-25", 98.542, 99.915),  ("2026-07-02", 99.144, 102.604),
    ("2026-07-13", 98.974, 100.78),  ("2026-07-21", 99.067, 100.849),
    ("2026-07-28", 99.644, 102.259), ("2026-08-05", 100.217, 102.555),
]
GOLDEN_SUMMARY = ("지난 8주 상대강도가 앞선 자산은 나스닥 · 금, "
                  "주도권에서 밀린 자산은 S&P500 · 하이일드채 · 원/달러입니다.")

if bundle["asset"]:
    got_risk = {r["key"]: r["risk"] for r in bundle["asset"]["rows"]}
    for key, exp in GOLDEN_RISK.items():
        got = got_risk.get(key)
        if got is None or abs(got - exp) > 0.005:
            failures.append(f"[위험성격] {key} 불일치 {got} != {exp}")
    gold_row = next((r for r in bundle["asset"]["rows"] if r["key"] == "gold"), None)
    if not gold_row:
        failures.append("[꼬리] gold 행 없음")
    else:
        got_tail = [(p["date"], p["x"], p["y"]) for p in gold_row["tail"]]
        if len(got_tail) != len(GOLDEN_TAIL_GOLD):
            failures.append(f"[꼬리] gold 점 개수 {len(got_tail)} != {len(GOLDEN_TAIL_GOLD)}")
        else:
            for g, e in zip(got_tail, GOLDEN_TAIL_GOLD):
                if g[0] != e[0] or abs(g[1] - e[1]) > TOL or abs(g[2] - e[2]) > TOL:
                    failures.append(f"[꼬리] gold {g} != {e}")
    print(f"   risk {len(GOLDEN_RISK)}개 · gold 꼬리 {len(GOLDEN_TAIL_GOLD)}점 대조 완료")

if bundle["summary"] and bundle["summary"].get("text") != GOLDEN_SUMMARY:
    failures.append(f"[요약] 문장 불일치\n      got: {bundle['summary'].get('text')}"
                    f"\n      exp: {GOLDEN_SUMMARY}")

if not bundle["summary"] or not bundle["summary"].get("text"):
    failures.append("[요약] text 없음")
else:
    print(f"   요약: {bundle['summary']['text']}")

print()
if failures:
    print(f"실패 {len(failures)}건")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("전부 통과")
