"""경제지표 발표 서프라이즈 — 실제(actual) vs 예상(forecast).

ForexFactory 의 actual/forecast 는 '3.0%','250K','1.2M','-0.2%' 같은 문자열이라
숫자만 파싱해 비교한다. 좋고/나쁨(폴리시 방향)은 지표마다 달라 판단하지 않고,
'예상 상회/하회/부합'(beat/miss/inline)만 중립적으로 표기한다.
"""
from __future__ import annotations

import re

# 배수 접미사(천/백만/십억/조) — 단위가 다른 예상·실제도 같은 척도로 비교
_MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_NUM = re.compile(r"[-+]?\d*\.?\d+")


def parse_econ_num(s) -> float | None:
    """'3.0%'→3.0, '250K'→250000, '-0.2%'→-0.2, '1.2M'→1200000. 파싱 불가 시 None."""
    if not s:
        return None
    t = str(s).strip().replace(",", "").replace("%", "").replace("$", "").lstrip("<>≈~ ")
    m = _NUM.search(t)
    if not m:
        return None
    try:
        val = float(m.group())
    except ValueError:
        return None
    suf = t[m.end():m.end() + 1].upper()
    if suf in _MULT:
        val *= _MULT[suf]
    return val


def surprise(actual, forecast) -> dict | None:
    """실제·예상 → {dir: beat|miss|inline, diff}. 둘 다 수치로 파싱돼야 한다."""
    a, f = parse_econ_num(actual), parse_econ_num(forecast)
    if a is None or f is None:
        return None
    d = a - f
    return {"dir": "beat" if d > 0 else "miss" if d < 0 else "inline", "diff": d}
