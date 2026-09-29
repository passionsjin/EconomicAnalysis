r"""calibration.py — '이 단계에서 과거엔 어땠나' 통계의 순수 계산 테스트(DB 없음).

배경: 레짐 점수는 이후 '낙폭'은 잘 예고하지만(점수↓ → 20일 내 낙폭 2~3배) '수익률' 방향은
예고하지 못한다(강한 위험회피 구간의 60일 후 수익률이 오히려 가장 높았다). 화면이 점수를
매수·매도 타이밍처럼 읽히지 않도록, 단계별 과거 성과를 확률로 보여준다.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

from app.analysis import calibration as cal


def _dates(n: int) -> list[str]:
    # 날짜 문자열은 정렬 순서만 맞으면 된다
    return [f"d{i:04d}" for i in range(n)]


def test_선행_성과는_기간_끝의_수익률과_기간_중_최대낙폭을_잰다():
    pd = _dates(5)
    pv = [100.0, 90.0, 95.0, 110.0, 120.0]
    ret, dd = cal.forward(pd, pv, "d0000", ret_n=4, dd_n=2)
    assert abs(ret - 0.20) < 1e-9
    assert abs(dd - (-0.10)) < 1e-9


def test_미래_데이터가_모자라면_None():
    pd = _dates(5)
    pv = [100.0] * 5
    assert cal.forward(pd, pv, "d0003", ret_n=4, dd_n=2) is None


def test_가격_축에_없는_날짜는_다음_거래일부터_잰다():
    pd = ["a", "c", "d"]
    pv = [100.0, 50.0, 100.0]
    ret, dd = cal.forward(pd, pv, "b", ret_n=1, dd_n=1)
    assert abs(ret - 1.0) < 1e-9          # c(50) → d(100)
    assert dd == 0.0


def test_단계별_통계는_라벨별로_묶고_표본이_적으면_뺀다():
    n = 200
    dates = _dates(n)
    prices = [100.0 + i for i in range(n)]            # 꾸준히 오르는 시장 → 낙폭 0
    labels = ["off" if i < 100 else "on" for i in range(n)]
    labels[150] = "rare"                               # 표본 1개 → 제외
    stats = cal.bucket_stats(dates, labels, dates, prices, ret_n=20, dd_n=10, min_n=30)
    assert set(stats) == {"off", "on", "all"}
    assert stats["off"]["n"] == 100
    assert stats["off"]["hit"] == 1.0
    assert stats["off"]["dd_mean"] == 0.0
    assert stats["all"]["n"] == n - 20                 # 끝 20일은 미래가 없어 빠진다


def test_해석은_흔들림과_성과를_각각_평소와_비교한다():
    base = {"dd_mean": -0.027, "ret_mean": 0.025}
    assert cal.reading({"dd_mean": -0.035, "ret_mean": 0.046}, base) ==         "흔들림이 평소보다 컸고 기다린 결과는 평소보다 좋았던 구간"
    assert cal.reading({"dd_mean": -0.014, "ret_mean": 0.010}, base) ==         "흔들림이 평소보다 작았고 기다린 결과는 평소보다 약했던 구간"


def test_차이가_작으면_평소_수준이라고_말한다():
    """실측 회귀: 중립 단계 낙폭 -3.0% vs 평소 -2.7% 를 '흔들림이 작았다'고 표시했다."""
    base = {"dd_mean": -0.027, "ret_mean": 0.028}
    assert cal.reading({"dd_mean": -0.030, "ret_mean": 0.030}, base) ==         "흔들림은 평소 수준이었고 기다린 결과도 평소 수준이었던 구간"
