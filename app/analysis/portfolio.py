"""보유 포트폴리오 리스크 — 원화 기준 VaR·변동성·손익(결정층 '지킴' 개인화).

사용자가 입력한 자산별 보유액(원화)으로 포트폴리오의 일간 '원화 수익률' 분포를 만들어
역사적 VaR(95/99%)·연변동성·최근 손익을 계산한다. 각 자산 가격에 환율 시계열을 곱해
원화 환산 후 수익률을 내므로 FX(환율 변동) 위험이 자연히 포함된다 — 한국 거주자의
실제 손익 기준 위험. 보유 정보는 서버에 저장하지 않고 요청 시 계산만 한다(프론트 localStorage).
"""
from __future__ import annotations

import math

from .. import repository as repo
from ..config import INDICATOR_BY_KEY, KRW_CONVERTIBLE_CATEGORIES

_VAR_POINTS = 520    # 분포 추정에 쓰는 최대 거래일(~2년)
_MIN_OVERLAP = 60    # 최소 공통 관측(이보다 적으면 계산 불가)


def holdable_assets() -> list[dict]:
    """보유 입력 가능한 '가격자산'(주식·섹터·원자재·암호화폐). KOSPI 등 원화자산 포함."""
    out = []
    for ind in INDICATOR_BY_KEY.values():
        if ind.category in KRW_CONVERTIBLE_CATEGORIES:
            out.append({"key": ind.key, "label": ind.label,
                        "category": ind.category, "ccy": ind.ccy})
    # 화면 편집기 정렬: 카테고리 → 라벨
    out.sort(key=lambda x: (x["category"], x["label"]))
    return out


def _fx_krw_map(sm: dict) -> dict:
    """통화 → {date: 원화/1단위} 시계열. 보유 FX(usdkrw 등)로 교차환율 구성."""
    def d(k):
        return {p["date"]: p["value"] for p in sm.get(k, []) if p.get("value")}
    usdkrw, usdjpy, eurusd, usdcny = d("usdkrw"), d("usdjpy"), d("eurusd"), d("usdcny")
    fx: dict = {"USD": usdkrw}
    fx["JPY"] = {t: usdkrw[t] / usdjpy[t] for t in usdkrw if t in usdjpy and usdjpy[t]}
    fx["EUR"] = {t: eurusd[t] * usdkrw[t] for t in usdkrw if t in eurusd}
    fx["CNY"] = {t: usdkrw[t] / usdcny[t] for t in usdkrw if t in usdcny and usdcny[t]}
    fx["HKD"] = {t: usdkrw[t] / 7.8 for t in usdkrw}     # HKD는 USD 페그(~7.8) 근사
    return fx


def _krw_returns(key: str, ccy: str, fx: dict) -> dict:
    """자산의 원화환산 일간수익률 {date: ret}. 지수레벨×환율의 변화율 → FX 포함."""
    native = {p["date"]: p["value"] for p in repo.get_series(key, _VAR_POINTS) if p.get("value")}
    if ccy == "KRW":
        krw = native                                    # 이미 원화 호가(코스피 등)
    else:
        f = fx.get(ccy) or {}
        krw = {t: native[t] * f[t] for t in native if t in f}
    dates = sorted(krw)
    out = {}
    for i in range(1, len(dates)):
        p0, p1 = krw[dates[i - 1]], krw[dates[i]]
        if p0:
            out[dates[i]] = p1 / p0 - 1.0
    return out


def _cum(rets: list[float]) -> float:
    """누적 수익률(%). (1+r) 연쇄곱 − 1."""
    acc = 1.0
    for r in rets:
        acc *= (1.0 + r)
    return (acc - 1.0) * 100.0


def compute_portfolio(holdings: dict) -> dict:
    """holdings={자산키: 원화보유액} → 포트폴리오 VaR·변동성·손익. 보유정보 미저장."""
    items = []
    for k, amt in (holdings or {}).items():
        try:
            a = float(amt)
        except (TypeError, ValueError):
            continue
        ind = INDICATOR_BY_KEY.get(k)
        if ind and ind.category in KRW_CONVERTIBLE_CATEGORIES and a > 0:
            items.append((k, ind, a))
    if not items:
        return {"ok": False, "reason": "보유 자산을 1개 이상 입력하세요."}

    total = sum(a for _, _, a in items)
    fx = _fx_krw_map(repo.get_series_batch(["usdkrw", "usdjpy", "eurusd", "usdcny"], _VAR_POINTS))

    rets = {}
    for k, ind, _a in items:
        r = _krw_returns(k, ind.ccy, fx)
        if r:
            rets[k] = r
    held = [(k, ind, a) for k, ind, a in items if k in rets]
    if not held:
        return {"ok": False, "reason": "수익률 이력이 있는 보유 자산이 없습니다."}

    # 이력 있는 자산만으로 가중치 재정규화
    wtotal = sum(a for _, _, a in held)
    w = {k: a / wtotal for k, _, a in held}

    common = set(rets[held[0][0]])
    for k, _, _ in held[1:]:
        common &= set(rets[k])
    common = sorted(common)[-_VAR_POINTS:]
    if len(common) < _MIN_OVERLAP:
        return {"ok": False, "reason": f"공통 수익률 이력이 부족합니다({len(common)}일, 최소 {_MIN_OVERLAP})."}

    port = [sum(w[k] * rets[k][t] for k, _, _ in held) for t in common]
    n = len(port)
    mean = sum(port) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in port) / n)
    vol_annual = sd * math.sqrt(252) * 100.0

    sp = sorted(port)
    def q(p):           # p분위 수익률(음수=손실)
        return sp[max(0, min(n - 1, int(p * n)))]
    var95 = max(0.0, -q(0.05) * 100.0)     # 1일 95% VaR(손실 %, 양수)
    var99 = max(0.0, -q(0.01) * 100.0)

    held_value = total       # 입력이 원화 보유액이라 합계가 곧 평가액
    breakdown = sorted(
        [{"key": k, "label": ind.label, "amount": a, "weight": round(w[k] * 100, 1)}
         for k, ind, a in held],
        key=lambda x: x["weight"], reverse=True)

    return {
        "ok": True,
        "n_holdings": len(held),
        "total_krw": held_value,
        "vol_annual_pct": round(vol_annual, 1),
        "var95_pct": round(var95, 2),
        "var95_krw": round(var95 / 100 * held_value),
        "var99_pct": round(var99, 2),
        "var99_krw": round(var99 / 100 * held_value),
        "pnl_1d_pct": round(port[-1] * 100, 2),
        "pnl_1d_krw": round(port[-1] * held_value),
        "pnl_1w_pct": round(_cum(port[-5:]), 2),
        "pnl_1w_krw": round(_cum(port[-5:]) / 100 * held_value),
        "pnl_1m_pct": round(_cum(port[-21:]), 2),
        "pnl_1m_krw": round(_cum(port[-21:]) / 100 * held_value),
        "window_days": n,
        "fx_included": True,
        "breakdown": breakdown,
    }
