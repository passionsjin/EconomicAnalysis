r"""risk_metrics 방어선 - 가격형이 아닌 계열에 수익률·낙폭을 계산하지 않는다.

CNN 공포·탐욕의 구성요소(52주 신고가-신저가, 안전자산 선호 등)는 0 을 넘나드는 '차이값'이라
비율수익률 seg[i]/seg[i-1]-1 의 부호가 뒤집힌다. 방어 전 실측: 연변동성 4198%, 최대낙폭 -244%
(가격 시계열에서 수학적으로 불가능한 값)가 카드에 그대로 표시됐다.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

from app.analysis import stats
from app.config import wants_risk_metrics


def _series(values: list[float]) -> list[tuple[str, float]]:
    return [(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", v) for i, v in enumerate(values)]


def test_양수_가격계열에는_리스크지표가_나온다():
    dv = _series([100 + (i % 7) - 3 for i in range(60)])
    r = stats.risk_metrics(dv)
    assert r is not None
    assert r["mdd"] is not None and -100 <= r["mdd"] <= 0


def test_0을_지나는_계열에는_리스크지표를_계산하지_않는다():
    """부호가 바뀌면 비율수익률·낙폭이 정의되지 않는다."""
    dv = _series([1.9, 0.4, -0.5, 0.99, -1.2] * 12)
    assert stats.risk_metrics(dv) is None


def test_0이_한번이라도_있으면_계산하지_않는다():
    dv = _series([5.0] * 30 + [0.0] + [5.0] * 29)
    assert stats.risk_metrics(dv) is None


def test_불가능한_낙폭이_더는_나오지_않는다():
    """방어 전에는 MDD 가 -100% 아래로 내려갔다."""
    dv = _series([-0.63, -0.75, 3.05, -2.0, 1.5] * 12)
    r = stats.risk_metrics(dv)
    assert r is None or r["mdd"] >= -100


def test_심리_카테고리는_리스크지표_대상이_아니다():
    assert wants_risk_metrics("cnn_fng") is False
    assert wants_risk_metrics("cnn_fng_putcall") is False
    assert wants_risk_metrics("sp500") is True
    assert wants_risk_metrics("없는키") is False


def test_enrich_의_risk_ok_게이트가_동작한다():
    assert "risk" in stats.enrich("sp500", 100.0, "D", risk_ok=False)
    assert stats.enrich("sp500", 100.0, "D", risk_ok=False)["risk"] is None
