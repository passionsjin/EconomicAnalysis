"""자산간 롤링 상관 매트릭스 + 리스크온/오프 레짐 판정.

history 테이블(일별 시계열)에서 일간 수익률을 만들어 상관을 계산한다.
36개 고립 지표만으로는 보이지 않는 '시장 레짐'(상호작용)을 드러낸다.
"""
from __future__ import annotations

import math

from .. import repository as repo

# 상관/레짐에 쓰는 핵심 자산(라벨은 화면 표시용)
ASSETS: list[tuple[str, str]] = [
    ("sp500", "S&P500"), ("nasdaq", "나스닥"), ("kospi", "코스피"),
    ("us10y", "미10Y"), ("dxy", "달러"), ("gold", "금"),
    ("wti", "WTI"), ("btc", "BTC"), ("vix", "VIX"), ("usdkrw", "원/달러"),
]


def _returns(series: list[dict]) -> dict[str, float]:
    """[(date,value)] → {date: 일간수익률}. 인접일 비교."""
    vals = [(p["date"], p["value"]) for p in series if p.get("value") is not None]
    out: dict[str, float] = {}
    for (d0, v0), (d1, v1) in zip(vals, vals[1:]):
        if v0 not in (None, 0):
            out[d1] = v1 / v0 - 1.0
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def correlation_matrix(keys: list[str], window: int = 30) -> list[list[float | None]]:
    """키들의 최근 `window` 일간수익률 기준 피어슨 상관 행렬."""
    rets = {k: _returns(repo.get_series(k, window + 45)) for k in keys}
    matrix: list[list[float | None]] = []
    for ka in keys:
        row: list[float | None] = []
        for kb in keys:
            if ka == kb:
                row.append(1.0)
                continue
            common = sorted(set(rets[ka]) & set(rets[kb]))[-window:]
            c = _pearson([rets[ka][d] for d in common], [rets[kb][d] for d in common])
            row.append(round(c, 2) if c is not None else None)
        matrix.append(row)
    return matrix


def _window_change_pct(key: str, window: int) -> float | None:
    s = [p for p in repo.get_series(key, window + 5) if p.get("value") is not None]
    if len(s) < 2:
        return None
    base = s[-min(len(s), window + 1)]["value"]
    cur = s[-1]["value"]
    if base in (None, 0):
        return None
    return (cur / base - 1.0) * 100.0


def detect_regime(window: int = 20) -> dict:
    """VIX 수준 · HY 스프레드 방향 · 주식 추세로 리스크온/오프 점수화."""
    vix_s = repo.get_series("vix", 5)
    vix = vix_s[-1]["value"] if vix_s else None

    hy_s = [p for p in repo.get_series("us_hy_spread", window + 5) if p.get("value") is not None]
    hy = hy_s[-1]["value"] if hy_s else None
    hy_chg = None
    if len(hy_s) > 1:
        hy_chg = hy - hy_s[-min(len(hy_s), window + 1)]["value"]

    spx_chg = _window_change_pct("sp500", window)

    score = 0
    drivers: list[str] = []
    if vix is not None:
        if vix < 17:
            score += 1; drivers.append(f"VIX {vix:.1f}(낮음)")
        elif vix > 25:
            score -= 1; drivers.append(f"VIX {vix:.1f}(높음)")
    if hy_chg is not None:
        if hy_chg < -0.1:
            score += 1; drivers.append(f"HY스프레드 축소({hy_chg:+.2f}%p)")
        elif hy_chg > 0.1:
            score -= 1; drivers.append(f"HY스프레드 확대({hy_chg:+.2f}%p)")
    if spx_chg is not None:
        if spx_chg > 1:
            score += 1; drivers.append(f"주식 {spx_chg:+.1f}%")
        elif spx_chg < -1:
            score -= 1; drivers.append(f"주식 {spx_chg:+.1f}%")

    if score >= 2:
        label, short, tone = "위험선호(Risk-On)", "위험 선호", "good"
        meaning = "투자자가 주식·암호화폐 같은 위험자산을 사들이는 국면 (주가↑·변동성↓·신용여건 양호)."
    elif score <= -2:
        label, short, tone = "위험회피(Risk-Off)", "위험 회피", "bad"
        meaning = "투자자가 국채·달러·금 같은 안전자산으로 피신하는 국면 (주가↓·변동성↑·신용여건 악화)."
    else:
        label, short, tone = "중립/전환", "중립", "warn"
        meaning = "위험 선호와 회피가 팽팽하거나 방향이 바뀌는 국면 (뚜렷한 쏠림 없음)."

    # 화면 툴팁: 개념 한 줄 + 현재 상태 의미 + 판정 근거
    help_intro = ("시장 분위기 = 투자자들이 위험을 감수하는지(위험 선호) "
                  "회피하는지(위험 회피)를 VIX(공포지수)·하이일드 신용스프레드·주가 추세로 자동 판정.")
    driver_txt = ", ".join(drivers) if drivers else "특이 신호 없음"
    tip = f"{help_intro}\n\n현재: {short} — {meaning}\n근거: {driver_txt}"

    return {
        "label": label, "short": short, "tone": tone, "score": score,
        "meaning": meaning, "tip": tip, "drivers": drivers,
        "vix": vix, "hy_spread": hy, "spx_window_chg": spx_chg, "window": window,
    }


def snapshot(window: int = 30) -> dict:
    """대시보드/브리핑용 상관·레짐 요약."""
    keys = [k for k, _ in ASSETS]
    return {
        "labels": [lbl for _, lbl in ASSETS],
        "keys": keys,
        "window": window,
        "matrix": correlation_matrix(keys, window),
        "regime": detect_regime(min(window, 20)),
    }


def notable_pairs(snap: dict, threshold: float = 0.6, limit: int = 6) -> list[str]:
    """LLM 브리핑용: |상관| 이 큰 자산쌍을 텍스트로."""
    labels, m = snap["labels"], snap["matrix"]
    pairs = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            c = m[i][j]
            if c is not None and abs(c) >= threshold:
                pairs.append((abs(c), f"{labels[i]}↔{labels[j]} {c:+.2f}"))
    pairs.sort(reverse=True)
    return [p[1] for p in pairs[:limit]]
