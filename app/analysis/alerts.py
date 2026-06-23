"""시장 리스크 경보 — 핵심 위험게이지가 임계선을 넘으면 경고(지킴/하방보호 정면).

게이지 7종: VIX·MOVE(채권변동성)·HY신용스프레드·금융여건(NFCI)·수익률곡선(10Y−3M)은
절대 임계선, 순유동성은 4주 추세, 레짐은 점수/전환으로 판정. 각 게이지는 danger/warn/clear 3단계.
현재값이 평온하면 false alarm 없이 'clear' — 강한 위험선호 국면에선 전부 clear 가 정상.

임계선 근거(주석): 시장 통용 수준. 사용자(한국 글로벌매크로 배분가)의 '언제 방어로
전환?' 판단 시간을 줄이는 게 목적 — 절대수준은 표준 스트레스 라인, 추세는 조기경보.
"""
from __future__ import annotations

from typing import Optional

from .. import repository as repo

# 절대수준 게이지: dir="high"(값↑=위험) / "low"(값↓=위험)
# warn=경계 진입, danger=위험. unit/decimals 는 표시용.
_LEVEL_SPECS = [
    {"key": "vix", "label": "VIX 변동성", "dir": "high", "warn": 20.0, "danger": 30.0,
     "unit": "", "dec": 1, "msg": "주식 변동성 급등 — 헤지·현금비중 점검",
     "thr": "경계 ≥20 · 위험 ≥30"},
    {"key": "move", "label": "MOVE 채권변동성", "dir": "high", "warn": 130.0, "danger": 150.0,
     "unit": "", "dec": 0, "msg": "채권 변동성 급등 — 금리 불확실성·시장 스트레스, 방어 점검",
     "thr": "경계 ≥130 · 위험 ≥150"},
    {"key": "us_hy_spread", "label": "HY 신용스프레드", "dir": "high", "warn": 5.0, "danger": 7.0,
     "unit": "%", "dec": 2, "msg": "하이일드 신용경색 — 위험자산 비중 축소 신호",
     "thr": "경계 ≥5% · 위험 ≥7%"},
    {"key": "us_nfci", "label": "금융여건(NFCI)", "dir": "high", "warn": 0.0, "danger": 0.5,
     "unit": "", "dec": 2, "msg": "금융여건 긴축 전환 — 유동성 위축",
     "thr": "경계 ≥0 · 위험 ≥0.5 (0=장기평균)"},
    {"key": "us_t10y3m", "label": "수익률곡선(10Y−3M)", "dir": "low", "warn": 0.2, "danger": 0.0,
     "unit": "%p", "dec": 2, "msg": "장단기 금리 역전 임박/발생 — 침체 선행신호",
     "thr": "경계 ≤0.2 · 위험 ≤0 (역전)"},
]


def _fmt(v: Optional[float], unit: str, dec: int) -> str:
    if v is None:
        return "—"
    return f"{v:.{dec}f}{unit}"


def _level_gauge(spec: dict, obs: dict) -> dict:
    row = obs.get(spec["key"]) or {}
    # 수집 실패(ok=0)면 stale 값으로 오발화하지 않도록 na 처리
    v = row.get("value") if (row.get("ok", 0) and row.get("value") is not None) else None
    base = {"key": spec["key"], "label": spec["label"], "msg": spec["msg"], "thr": spec["thr"]}
    if v is None:
        return {**base, "level": "na", "value_fmt": "—", "detail": "데이터 없음"}
    warn, danger = spec["warn"], spec["danger"]
    if spec["dir"] == "high":
        level = "danger" if v >= danger else "warn" if v >= warn else "clear"
    else:  # low: 값이 낮을수록 위험
        level = "danger" if v <= danger else "warn" if v <= warn else "clear"
    return {**base, "level": level, "value_fmt": _fmt(v, spec["unit"], spec["dec"]),
            "detail": spec["thr"]}


def _netliq_gauge() -> dict:
    """순유동성(연준−RRP−TGA, 주별): 4주 추세 하락이면 경보(레벨 아닌 방향이 신호)."""
    base = {"key": "us_net_liq", "label": "순유동성(4주추세)",
            "msg": "유동성 흡수 — 위험자산 상방 연료 감소", "thr": "경계 −2% · 위험 −5% (4주)"}
    vals = [p["value"] for p in repo.get_series("us_net_liq", 12) if p.get("value") is not None]
    if len(vals) < 5:
        return {**base, "level": "na", "value_fmt": "—", "detail": "데이터 부족"}
    cur, prev = vals[-1], vals[-5]   # 4주 전(주별 4포인트)
    chg = (cur / prev - 1) * 100 if prev else None
    if chg is None:
        return {**base, "level": "na", "value_fmt": f"{cur:.2f}T$", "detail": "—"}
    level = "danger" if chg <= -5 else "warn" if chg <= -2 else "clear"
    return {**base, "level": level, "value_fmt": f"{cur:.2f}T$", "detail": f"4주 {chg:+.1f}%"}


def _regime_gauge(regime: Optional[dict]) -> dict:
    """레짐 위험선호 점수: 위험회피권 진입(<43) 또는 급락(어제 대비 ≤−8)이면 경보."""
    base = {"key": "regime", "label": "레짐 전환", "msg": "위험선호→회피 전환 — 방어 비중 확대 검토",
            "thr": "경계 <43 또는 어제 대비 ≤−8 · 위험 <31"}
    if not regime or regime.get("score") is None:
        return {**base, "level": "na", "value_fmt": "—", "detail": "데이터 없음"}
    score = regime["score"]
    delta = regime.get("delta")
    if score <= 31:
        level = "danger"
    elif score <= 42 or (delta is not None and delta <= -8):
        level = "warn"
    else:
        level = "clear"
    dtxt = f" · 어제 대비 {delta:+d}" if delta is not None else ""
    return {**base, "level": level, "value_fmt": f"{score}/100", "detail": f"{regime.get('short','')}{dtxt}"}


_ORDER_RANK = {"danger": 0, "warn": 1, "clear": 2, "na": 3}


def evaluate_alerts(obs: dict, regime: Optional[dict] = None) -> dict:
    """관측값+레짐 → 리스크 경보 묶음. gauges=전체 7종(상태표시줄), active=발화분(danger/warn)."""
    gauges = [_level_gauge(spec, obs) for spec in _LEVEL_SPECS]
    gauges.append(_netliq_gauge())
    gauges.append(_regime_gauge(regime))

    active = [g for g in gauges if g["level"] in ("warn", "danger")]
    active.sort(key=lambda g: _ORDER_RANK[g["level"]])
    n_danger = sum(1 for g in active if g["level"] == "danger")
    n_warn = sum(1 for g in active if g["level"] == "warn")
    level = "danger" if n_danger else "warn" if n_warn else "clear"
    return {"level": level, "n_danger": n_danger, "n_warn": n_warn,
            "active": active, "gauges": gauges}
