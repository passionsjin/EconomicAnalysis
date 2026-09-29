"""'이 단계에서 과거엔 어땠나' — 레짐 단계·CNN 공포탐욕 구간별 이후 성과(라이브 전용).

레짐 점수는 이후 '낙폭'은 잘 예고하지만 '수익률' 방향은 예고하지 못한다(5년 소급:
점수↓ → 20일 내 낙폭 2~3배 깊음, 그런데 강한 위험회피 구간의 60일 후 수익률이 가장 높음).
점수를 매수·매도 타이밍으로 오독하지 않도록, 현재 단계에서 과거 실제로 벌어진 일을
'흔들림(20일 내 최대낙폭)'과 '기다린 결과(60일 후 수익률·상승확률)'로 나란히 보여준다.

추가 수집 없이 저장된 history 만 쓴다. 창이 겹치는 표본이라 통계적 유의성은 약하다 —
화면 문구에 표본 수를 함께 적어 확신이 아니라 확률로 읽히게 한다.
"""
from __future__ import annotations

import bisect
import logging
import threading
from typing import Optional

from .. import repository as repo
from . import regime as regime_mod

logger = logging.getLogger(__name__)

RET_N = 60        # '기다린 결과' 창(거래일)
DD_N = 20         # '흔들림' 창(거래일)
MIN_N = 30        # 이보다 표본이 적은 구간은 표시하지 않는다
_SPAN = 1250      # 레짐 소급 거래일(≈5년, HISTORY_POINTS 1300 안쪽)
FNG_FEAR, FNG_GREED = 25, 75   # CNN 극단 구간(통념 임계) — 극단일 때만 한 줄 더 보인다


def forward(pd: list[str], pv: list[float], d: str, ret_n: int = RET_N,
            dd_n: int = DD_N) -> Optional[tuple[float, float]]:
    """d(또는 그 다음 거래일)부터 (ret_n 일 뒤 수익률, dd_n 일 안의 최대낙폭). 미래가 모자라면 None."""
    i = bisect.bisect_left(pd, d)
    if i + max(ret_n, dd_n) >= len(pd):
        return None
    base = pv[i]
    if not base:
        return None
    ret = pv[i + ret_n] / base - 1.0
    dd = min(v / base - 1.0 for v in pv[i:i + dd_n + 1])
    return ret, dd


def _summ(rows: list[tuple[float, float]]) -> dict:
    rets = [r for r, _ in rows]
    dds = sorted(d for _, d in rows)
    n = len(rows)
    return {
        "n": n,
        "ret_mean": sum(rets) / n,
        "hit": sum(1 for r in rets if r > 0) / n,
        "dd_mean": sum(dds) / n,
        "dd_p10": dds[n // 10],     # 나쁜 10% — 하위 10분위 낙폭
    }


def bucket_stats(dates: list[str], labels: list[str], pd: list[str], pv: list[float],
                 ret_n: int = RET_N, dd_n: int = DD_N, min_n: int = MIN_N) -> dict[str, dict]:
    """날짜별 라벨 → 라벨별 이후 성과 요약 + 'all'(전 구간 기준선). 표본 < min_n 은 제외."""
    groups: dict[str, list] = {}
    every: list = []
    for d, lab in zip(dates, labels):
        f = forward(pd, pv, d, ret_n, dd_n)
        if f is None:
            continue
        groups.setdefault(lab, []).append(f)
        every.append(f)
    out = {k: _summ(v) for k, v in groups.items() if len(v) >= min_n}
    if every:
        out["all"] = _summ(every)
    return out


_SAME = 0.005     # 평소와 0.5%p 이내면 '평소 수준'으로 본다


def reading(st: dict, base: dict) -> str:
    """흔들림·기다린 결과를 각각 평소(전 구간)와 비교한 한 구절."""
    dd, dd0 = st["dd_mean"], base["dd_mean"]
    shake = ("흔들림이 평소보다 컸고" if dd < dd0 - _SAME
             else "흔들림이 평소보다 작았고" if dd > dd0 + _SAME
             else "흔들림은 평소 수준이었고")
    r, r0 = st["ret_mean"], base["ret_mean"]
    result = ("기다린 결과는 평소보다 좋았던 구간" if r > r0 + _SAME
              else "기다린 결과는 평소보다 약했던 구간" if r < r0 - _SAME
              else "기다린 결과도 평소 수준이었던 구간")
    return f"{shake} {result}"


def _pct(x: float, signed: bool = True) -> str:
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def _cols(key: str) -> tuple[list[str], list[float]]:
    s = [p for p in repo.get_series(key, 2000) if p.get("value") is not None]
    return [p["date"] for p in s], [p["value"] for p in s]


def _build(regime: dict, fng_value: Optional[float]) -> Optional[dict]:
    stage = regime.get("stage")
    if not stage:
        return None
    hist = regime_mod.regime_history(20, _SPAN)
    if not hist:
        return None
    dates = [h["date"] for h in hist]
    stages = regime_mod.stable_stages([h["score"] for h in hist])
    spd, spv = _cols("sp500")
    ksd, ksv = _cols("kospi")
    sp = bucket_stats(dates, stages, spd, spv)
    ks = bucket_stats(dates, stages, ksd, ksv)
    cur, base = sp.get(stage), sp.get("all")
    if not cur or not base:
        return None
    years = max(1, round(len(dates) / 250))
    short = regime.get("short") or stage

    line = (f"과거 {years}년 중 '{short}' 단계였던 {cur['n']}일 — 이후 20일 안에 S&P500이 평균 "
            f"{_pct(cur['dd_mean'])} 밀렸고(나쁜 10%는 {_pct(cur['dd_p10'])}, 평소 {_pct(base['dd_mean'])}), "
            f"60일 뒤엔 평균 {_pct(cur['ret_mean'])}(오른 경우 {cur['hit'] * 100:.0f}%, 평소 "
            f"{_pct(base['ret_mean'])})였습니다.")
    kcur = ks.get(stage)
    if kcur:
        line += f" 코스피는 60일 뒤 평균 {_pct(kcur['ret_mean'])}(오른 경우 {kcur['hit'] * 100:.0f}%)."

    fng_line = None
    if fng_value is not None and (fng_value <= FNG_FEAR or fng_value >= FNG_GREED):
        fear = fng_value <= FNG_FEAR
        fd, fv = _cols("cnn_fng")
        flabels = ["x" if (v <= FNG_FEAR if fear else v >= FNG_GREED) else "-" for v in fv]
        fs = bucket_stats(fd, flabels, spd, spv).get("x")
        if fs:
            zone = f"{FNG_FEAR} 이하(극단적 공포)" if fear else f"{FNG_GREED} 이상(극단적 탐욕)"
            fng_line = (f"CNN 공포·탐욕이 {zone}였던 {fs['n']}일 — 60일 뒤 S&P500 평균 "
                        f"{_pct(fs['ret_mean'])}(오른 경우 {fs['hit'] * 100:.0f}%), "
                        f"20일 안 평균 흔들림 {_pct(fs['dd_mean'])}.")

    return {"stage": stage, "short": short, "n": cur["n"], "years": years,
            "reading": reading(cur, base), "line": line, "fng_line": fng_line,
            "note": "겹치는 기간을 포함한 단순 집계 — 미래를 보장하지 않는 참고치입니다."}


_cache_lock = threading.Lock()
_cache: dict = {"key": None, "data": None}


def build_calibration(regime: Optional[dict], fng_value: Optional[float] = None) -> Optional[dict]:
    """스냅샷 단위 캐시(flows.build_flows 와 같은 패턴). 실패는 None 으로 격리."""
    if not regime or regime.get("score") is None:
        return None
    try:
        snap = repo.latest_snapshot()
        sid = snap.get("id") if snap else None
    except Exception:  # noqa: BLE001
        sid = None
    key = (sid, regime.get("stage"), fng_value)
    with _cache_lock:
        if sid is not None and _cache["key"] == key:
            return _cache["data"]
    try:
        data = _build(regime, fng_value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("단계별 과거 성과 계산 실패: %s", exc)
        return None
    with _cache_lock:
        _cache["key"], _cache["data"] = key, data
    return data
