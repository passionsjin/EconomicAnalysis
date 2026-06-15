"""지표 통계 — 역사적 백분위·z-score·다기간 모멘텀.

history 시계열을 재활용해 '현재값의 의미'(분포 내 위치, 추세 모멘텀)를 계산한다.
거시분석가가 점값(VIX 22)만으로는 알 수 없는 '1년 중 83%ile' 같은 맥락을 제공.
"""
from __future__ import annotations

import math

from .. import repository as repo

# 다기간 모멘텀 거래일 근사
_PERIODS = {"w1": 5, "m1": 21, "m3": 63}


def _vals(series: list[dict]) -> list[float]:
    return [p["value"] for p in series if p.get("value") is not None]


def percentile_rank(value: float | None, hist: list[float]) -> int | None:
    if value is None or len(hist) < 12:
        return None
    below = sum(1 for v in hist if v <= value)
    return round(below / len(hist) * 100)


def zscore(value: float | None, hist: list[float]) -> float | None:
    if value is None or len(hist) < 12:
        return None
    n = len(hist)
    mean = sum(hist) / n
    var = sum((v - mean) ** 2 for v in hist) / n
    if var <= 0:
        return None
    return (value - mean) / math.sqrt(var)


def context(key: str, value: float | None, window: int = 252) -> dict | None:
    """역사적 분포 맥락: 백분위 + z-score + 최근 N개 범위."""
    vals = _vals(repo.get_series(key, window))
    if len(vals) < 12 or value is None:
        return None
    z = zscore(value, vals)
    return {
        "percentile": percentile_rank(value, vals),
        "zscore": round(z, 2) if z is not None else None,
        "n": len(vals), "min": min(vals), "max": max(vals),
        "anomaly": bool(z is not None and abs(z) >= 3),  # |z|≥3 = 통계적 이상치
    }


def _momentum_from(vd: list[tuple[str, float]]) -> dict | None:
    if len(vd) < 2:
        return None
    cur = vd[-1][1]

    def ret(periods: int) -> float | None:
        base = vd[max(0, len(vd) - 1 - periods)][1]
        return (cur / base - 1.0) * 100.0 if base not in (None, 0) else None

    out = {k: ret(p) for k, p in _PERIODS.items()}
    cur_year = vd[-1][0][:4]
    ytd_base = next((v for d, v in vd if d[:4] == cur_year), None)
    out["ytd"] = (cur / ytd_base - 1.0) * 100.0 if ytd_base not in (None, 0) else None
    return out


def momentum(key: str) -> dict | None:
    """다기간 변화율(%). 일별 지표에만 의미 — 1W/1M/3M/YTD."""
    vd = [(p["date"], p["value"]) for p in repo.get_series(key, 300)
          if p.get("value") is not None]
    return _momentum_from(vd)


def enrich(key: str, value: float | None, freq: str, window: int = 252) -> dict:
    """history 1회 조회로 백분위·z-score·이상치 + (일별)다기간 모멘텀을 함께 계산."""
    series = repo.get_series(key, max(window, 300))
    vals = _vals(series)
    ctx = None
    if len(vals) >= 12 and value is not None:
        recent = vals[-window:]
        z = zscore(value, recent)
        ctx = {
            "percentile": percentile_rank(value, recent),
            "zscore": round(z, 2) if z is not None else None,
            "anomaly": bool(z is not None and abs(z) >= 3),
            "min": min(recent), "max": max(recent), "n": len(recent),
        }
    mom = None
    if freq == "D":
        vd = [(p["date"], p["value"]) for p in series if p.get("value") is not None]
        mom = _momentum_from(vd)
    return {"ctx": ctx, "momentum": mom}
