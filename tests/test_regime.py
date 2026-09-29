r"""regime.py 위험선호 점수의 '기준점 보정' 테스트 — 신호가 상수가 되지 않아야 한다.

배경: 초기 구현은 수준형 신호(NFCI·HY스프레드·장단기차)의 중립점을 실제 분포와
무관하게 잡아, 평상시에도 최대 우호(+1)로 포화됐다. 5년 소급 계산 결과 위험선호
점수 평균이 58.8(중립 50)로 치우쳤고, 편향의 87%가 NFCI·HY 두 신호에서 나왔다
(NFCI 는 5년간 단 하루도 비우호로 뒤집힌 적 없음). 신호가 아니라 상수였던 셈.

여기서는 DB·네트워크 없이 `_signals_at` 에 합성 시계열을 넣어 기준점만 검증한다.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

import pytest

from app.analysis import regime

_W = 20

# 보유 시계열(2021-07~2026-08)에서 실측한 각 시리즈 중앙값 — '평범한 날'의 정의.
_MEDIAN = {
    "vix": 17.82, "us_hy_spread": 3.10, "us_nfci": -0.43, "us_10y2y": 0.19,
    "us_cfnai": 0.0, "sp500": 5000.0, "dxy": 100.0, "us_real10y": 2.0,
}


def _flat(values: dict[str, float]) -> dict[str, list]:
    """모든 시리즈가 window+1 일 내내 같은 값인 정렬 데이터(=20일 변화 0)."""
    return {k: [v] * (_W + 1) for k, v in values.items()}


def _sig(values: dict[str, float]) -> dict[str, tuple]:
    return regime._signals_at(_flat(values), _W, _W)


def _contrib(values: dict[str, float], name: str) -> float:
    return _sig(values)[name][0]


# ─────────────────────── 개별 신호: 평범한 수준은 중립 근처여야 ───────────────────────

def test_NFCI_는_역사적_중앙값에서_중립_근처여야_한다():
    """NFCI 는 자체 0 이 '1971년 이후 평균'이라 최근 20년은 상시 음수(완화)다.

    0 을 중립으로 쓰면 평상시 내내 최대 우호로 포화돼 가중치 0.14 짜리 상수가 된다.
    """
    for nfci in (-0.51, -0.43, -0.35):     # 전체/축내/상위 25% 중앙값대
        c = _contrib({**_MEDIAN, "us_nfci": nfci}, "nfci")
        assert abs(c) <= 0.3, f"NFCI {nfci} 가 중립이 아님(기여 {c:+.2f})"


def test_NFCI_는_긴축_국면에서_비우호로_뒤집혀야_한다():
    assert _contrib({**_MEDIAN, "us_nfci": -0.03}, "nfci") <= -0.5   # 2022 긴축 정점
    assert _contrib({**_MEDIAN, "us_nfci": 0.60}, "nfci") == -1.0    # 위기


def test_NFCI_는_이례적_완화에서만_최대_우호가_된다():
    assert _contrib({**_MEDIAN, "us_nfci": -0.81}, "nfci") >= 0.5    # 보유 데이터 최저


def test_HY스프레드_는_보유데이터_최대치에서_명확히_비우호여야_한다():
    """기준선 4.5% 는 3.3년 데이터에서 한 번도 닿은 적 없는(최대 4.61) 수준이었다."""
    assert _contrib({**_MEDIAN, "us_hy_spread": 4.60}, "hy") <= -0.4


def test_HY스프레드_는_평범한_수준에서_최대_우호가_아니어야_한다():
    c = _contrib({**_MEDIAN, "us_hy_spread": 3.10}, "hy")
    assert 0.0 <= c <= 0.45, f"중앙값 스프레드가 과도한 우호(기여 {c:+.2f})"


def test_HY스프레드_는_역대급_타이트에서는_강한_우호를_유지해야_한다():
    """보정이 '신용이 실제로 싸다'는 정보까지 지워버리면 안 된다."""
    assert _contrib({**_MEDIAN, "us_hy_spread": 2.67}, "hy") >= 0.4


def test_장단기차_는_정상_수준에서_최대_우호가_아니어야_한다():
    """장기 규범이 +0.9%p 인데 +0.5 를 만점 처리하면 곡선이 상시 우호 상수가 된다."""
    c = _contrib({**_MEDIAN, "us_10y2y": 0.50}, "curve")
    assert c <= 0.3, f"정상 수준 곡선이 과도한 우호(기여 {c:+.2f})"


def test_장단기차_는_평탄과_역전을_구분해야_한다():
    """둘 다 -1.0 으로 포화되면 침체 경고의 강도 차이를 잃는다."""
    flat = _contrib({**_MEDIAN, "us_10y2y": 0.0}, "curve")
    inverted = _contrib({**_MEDIAN, "us_10y2y": -0.8}, "curve")
    assert inverted < flat < 0


# ─────────────────────── 종합: '평범한 날'은 중립 밴드여야 ───────────────────────

def test_모든_지표가_중앙값인_날은_중립_판정이어야_한다():
    """사용자 체감 문제의 핵심 — 평상시가 이미 '위험선호'로 표시되면 안 된다."""
    score, _ = regime._composite(_sig(_MEDIAN))
    assert 43 <= score <= 57, f"평범한 날 점수 {score} 가 중립 밴드(43~57) 밖"
    assert regime._classify(score)[1] == "중립"


def test_전면적_스트레스는_위험회피로_판정되어야_한다():
    stress = {**_MEDIAN, "vix": 32.0, "us_hy_spread": 5.5, "us_nfci": 0.5,
              "us_10y2y": -0.5, "us_cfnai": -0.8}
    score, _ = regime._composite(_sig(stress))
    assert score <= 31, f"전면 스트레스 점수 {score} 가 강한 위험회피(<31)가 아님"


def test_전면적_완화는_위험선호로_판정되어야_한다():
    calm = {**_MEDIAN, "vix": 12.5, "us_hy_spread": 2.6, "us_nfci": -0.8,
            "us_10y2y": 1.5, "us_cfnai": 0.5}
    score, _ = regime._composite(_sig(calm))
    assert score >= 70, f"전면 완화 점수 {score} 가 강한 위험선호(>=70)가 아님"


# ─────────────────────── 단계 히스테리시스: 경계 근처 잦은 뒤집힘 방지 ───────────────────────
# 5년 소급 시 원점수 단계가 1250일 동안 274번(약 4.5일마다) 바뀌어 권고 비중이 매주 흔들렸다.

def test_단계는_경계를_여유폭만큼_넘어야_바뀐다():
    # 중립(43~57)에서 시작 → 58·60 은 여유폭(5) 안이라 중립 유지, 63 에서야 위험선호
    assert regime.stable_stages([50, 58, 60, 63], band=5) == ["neutral", "neutral", "neutral", "on"]


def test_단계는_내려갈_때도_여유폭을_요구한다():
    # 위험선호(58~69)에서 55·54 는 유지, 52 에서 중립
    assert regime.stable_stages([62, 55, 54, 52], band=5) == ["on", "on", "on", "neutral"]


def test_경계를_크게_넘으면_여러_단계를_한번에_건너뛴다():
    assert regime.stable_stages([50, 20], band=5) == ["neutral", "strong_off"]


def test_경계에서_진동하는_점수는_전환을_만들지_않는다():
    wobble = [44, 42, 45, 41, 44, 42, 43, 40]
    stages = regime.stable_stages(wobble, band=5)
    assert len(set(stages)) == 1


def test_여유폭_0_은_원점수_단계와_같다():
    scores = [75, 60, 50, 35, 10]
    assert regime.stable_stages(scores, band=0) == [regime.stage_of(s) for s in scores]
