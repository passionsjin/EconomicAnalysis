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


# ── 위험선호 점수(레짐 v2) ───────────────────────────────────────────
# 여러 신호를 [-1,+1] 기여도로 정규화해 가중 평균 → 0~100 (50=중립, 높을수록 위험선호).
# 입력: VIX·HY스프레드(수준+방향)·NFCI·주가추세·섹터폭(경기-방어)·달러·실질금리·장단기차.
_SIG_WEIGHTS = {
    "vix": 0.18, "hy": 0.16, "nfci": 0.14, "equity": 0.14,
    "breadth": 0.12, "dollar": 0.10, "real": 0.08, "curve": 0.08,
}
_SIG_LABEL = {
    "vix": "VIX(변동성)", "hy": "HY 신용스프레드", "nfci": "금융여건(NFCI)",
    "equity": "주가 추세", "breadth": "섹터폭(경기-방어)", "dollar": "달러",
    "real": "실질금리", "curve": "장단기차(10Y-2Y)",
}
_CYCLICAL = ["xlk", "xly", "xlf", "xli", "xlb"]   # 경기민감 섹터
_DEFENSIVE = ["xlp", "xlu", "xlv"]                # 방어 섹터
_NEEDED = (["vix", "us_hy_spread", "us_nfci", "sp500", "dxy", "us_real10y", "us_10y2y"]
           + _CYCLICAL + _DEFENSIVE)


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _load_aligned(window: int, span: int) -> tuple[list[str], dict[str, list]]:
    """sp500 거래일을 축으로 필요한 모든 시계열을 forward-fill 정렬.

    빈도가 다른 시리즈(주별 NFCI 등)도 '해당일 이전 마지막 값'으로 채워 한 축에 맞춘다.
    """
    import bisect
    need = max(span + window + 5, 60)
    raw = {k: [(p["date"], p["value"]) for p in repo.get_series(k, need)
               if p.get("value") is not None] for k in _NEEDED}
    base = raw.get("sp500") or []
    axis = [d for d, _ in base][-(span + window):]
    if not axis:
        return [], {}
    aligned: dict[str, list] = {}
    for k, s in raw.items():
        ds = [d for d, _ in s]
        vs = [v for _, v in s]
        col = []
        for d in axis:
            j = bisect.bisect_right(ds, d) - 1   # 해당일 이전 마지막 관측
            col.append(vs[j] if j >= 0 else None)
        aligned[k] = col
    return axis, aligned


def _signals_at(aligned: dict[str, list], i: int, window: int) -> dict[str, tuple]:
    """index i 의 신호별 (기여도[-1,1], 표시값). +=위험선호 방향."""
    def g(k):
        c = aligned.get(k)
        return c[i] if c and 0 <= i < len(c) else None

    def gw(k):
        c = aligned.get(k)
        return c[i - window] if c and 0 <= i - window < len(c) else None

    sig: dict[str, tuple] = {}

    vix = g("vix")
    if vix is not None:
        sig["vix"] = (_clamp((19 - vix) / 7), vix)            # 낮을수록 위험선호

    hy, hy0 = g("us_hy_spread"), gw("us_hy_spread")
    if hy is not None:
        c_lvl = _clamp((4.5 - hy) / 1.5)                      # 타이트할수록 위험선호
        c_dir = _clamp(-(hy - hy0) / 0.2) if hy0 is not None else 0.0  # 축소=위험선호
        sig["hy"] = (0.6 * c_lvl + 0.4 * c_dir, hy)

    nfci = g("us_nfci")
    if nfci is not None:
        sig["nfci"] = (_clamp(-nfci / 0.5), nfci)             # 음수(완화)=위험선호

    spx, spx0 = g("sp500"), gw("sp500")
    if spx is not None and spx0:
        chg = (spx / spx0 - 1) * 100
        sig["equity"] = (_clamp(chg / 4), chg)

    def avg_chg(keys):
        out = [(g(k) / gw(k) - 1) * 100 for k in keys if g(k) is not None and gw(k)]
        return sum(out) / len(out) if out else None

    cyc, dfn = avg_chg(_CYCLICAL), avg_chg(_DEFENSIVE)
    if cyc is not None and dfn is not None:
        sig["breadth"] = (_clamp((cyc - dfn) / 5), cyc - dfn)  # 경기민감 우위=위험선호

    dxy, dxy0 = g("dxy"), gw("dxy")
    if dxy is not None and dxy0:
        chg = (dxy / dxy0 - 1) * 100
        sig["dollar"] = (_clamp(-chg / 3), chg)               # 달러 약세=위험선호

    real, real0 = g("us_real10y"), gw("us_real10y")
    if real is not None and real0 is not None:
        sig["real"] = (_clamp(-(real - real0) / 0.3), real - real0)  # 실질금리 상승=비우호

    curve = g("us_10y2y")
    if curve is not None:
        sig["curve"] = (_clamp(curve / 0.5), curve)           # 역전(음수)=비우호

    return sig


def _composite(sig: dict[str, tuple]):
    """신호 dict → (score 0~100, 가중평균[-1,1]). 가용 신호만으로 가중 재정규화."""
    num = sum(_SIG_WEIGHTS[n] * c for n, (c, _v) in sig.items() if n in _SIG_WEIGHTS)
    den = sum(_SIG_WEIGHTS[n] for n in sig if n in _SIG_WEIGHTS)
    if den <= 0:
        return None, None
    wavg = num / den
    return round(50 + 50 * wavg), wavg


def _classify(score: int) -> tuple[str, str, str]:
    if score >= 70:
        return "강한 위험선호(Strong Risk-On)", "강한 위험선호", "good"
    if score >= 58:
        return "위험선호(Risk-On)", "위험 선호", "good"
    if score >= 43:
        return "중립/전환", "중립", "warn"
    if score >= 31:
        return "위험회피(Risk-Off)", "위험 회피", "bad"
    return "강한 위험회피(Strong Risk-Off)", "강한 위험회피", "bad"


def _fmt_sig(name: str, v: float) -> str:
    return {
        "vix": f"VIX {v:.1f}", "hy": f"HY {v:.2f}%", "nfci": f"NFCI {v:+.2f}",
        "equity": f"주식 {v:+.1f}%", "breadth": f"경기-방어 {v:+.1f}%p",
        "dollar": f"달러 {v:+.1f}%", "real": f"실질금리 {v:+.2f}%p", "curve": f"10Y-2Y {v:+.2f}",
    }.get(name, name)


def _empty_regime(window: int) -> dict:
    return {"label": "판정 불가", "short": "판정 불가", "tone": "warn", "score": None,
            "meaning": "데이터 부족으로 시장 분위기를 계산할 수 없습니다.",
            "tip": "데이터가 더 쌓이면 표시됩니다.", "drivers": [], "components": [],
            "vix": None, "hy_spread": None, "spx_window_chg": None, "window": window}


def detect_regime(window: int = 20) -> dict:
    """다중 신호 합성 0~100 위험선호 점수로 시장 분위기 판정(레짐 v2)."""
    axis, aligned = _load_aligned(window, span=1)
    if not axis:
        return _empty_regime(window)
    sig = _signals_at(aligned, len(axis) - 1, window)
    score, _wavg = _composite(sig)
    if score is None:
        return _empty_regime(window)

    label, short, tone = _classify(score)
    ranked = sorted(sig.items(), key=lambda kv: abs(_SIG_WEIGHTS.get(kv[0], 0) * kv[1][0]),
                    reverse=True)
    drivers = [f"{_fmt_sig(n, v)}({'우호' if c > 0 else '비우호'})"
               for n, (c, v) in ranked if abs(c) > 0.15][:5]
    components = [{"name": n, "label": _SIG_LABEL.get(n, n), "contrib": round(c, 2),
                   "weight": _SIG_WEIGHTS.get(n, 0), "value": round(v, 3)}
                  for n, (c, v) in ranked]

    if tone == "good":
        meaning = "투자자가 위험자산을 적극 사들이는 국면 (주가↑·변동성↓·신용/유동성 우호)."
    elif tone == "bad":
        meaning = "투자자가 안전자산으로 피신하는 국면 (주가↓·변동성↑·신용/금융여건 악화)."
    else:
        meaning = "위험 선호와 회피가 팽팽하거나 방향이 전환되는 국면 (뚜렷한 쏠림 없음)."

    intro = ("시장 분위기 = VIX·신용스프레드·금융여건(NFCI)·주가추세·섹터폭·달러·"
             "실질금리·장단기차를 합성한 0~100 위험선호 점수(50=중립, 높을수록 위험선호).")
    driver_txt = ", ".join(drivers) if drivers else "특이 동인 없음"
    tip = f"{intro}\n\n현재: {score}/100 {short} — {meaning}\n주요 동인: {driver_txt}"

    return {"label": label, "short": short, "tone": tone, "score": score,
            "meaning": meaning, "tip": tip, "drivers": drivers, "components": components,
            "vix": sig.get("vix", (None, None))[1],
            "hy_spread": sig.get("hy", (None, None))[1],
            "spx_window_chg": sig.get("equity", (None, None))[1], "window": window}


def regime_history(window: int = 20, span: int = 90) -> list[dict]:
    """최근 span 거래일의 위험선호 점수 추이(스파크라인용)."""
    axis, aligned = _load_aligned(window, span)
    if not axis:
        return []
    out = []
    for i in range(window, len(axis)):
        score, _w = _composite(_signals_at(aligned, i, window))
        if score is not None:
            out.append({"date": axis[i], "score": score})
    return out


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
