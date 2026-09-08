r"""CNN 공포·탐욕 수집기 파서 테스트 - 날짜 접기와 계열 분해를 고정.

CNN 응답에는 실측상 두 가지 함정이 있다.
 (1) 마지막 두 점이 **같은 날짜**다(자정 확정값 + 장중 갱신값). 접지 않으면 prev_close 가
     '전일'이 아니라 '같은 날 자정값'이 되어 변화율이 0으로 굳는다.
 (2) 하위 계열의 타임스탬프는 **자정이 아니다**(20:15, 22:30 등). UTC 날짜로 접어야
     종합 계열과 날짜축이 맞는다.
네트워크 없이 축소 응답으로 고정한다.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.collectors import cnn_fng
from app.config import CNN_SCORE_LABELS


def _ms(iso: str) -> float:
    """'2026-09-03T20:15:01Z' -> epoch milliseconds."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000.0


def _block(points: list[tuple[str, float | None]], score: float | None = 50.0) -> dict:
    return {"score": score, "rating": "neutral",
            "data": [{"x": _ms(t), "y": v} for t, v in points]}


def _payload(**overrides) -> dict:
    """실제 응답 축소판 - 10계열 전부 채운다."""
    base = [("2026-09-01T00:00:00Z", 30.0), ("2026-09-02T00:00:00Z", 33.0),
            ("2026-09-03T00:00:00Z", 35.0)]
    p = {
        "fear_and_greed": {"score": 35.0, "rating": "fear"},
        "fear_and_greed_historical": _block(base, 35.0),
        "market_momentum_sp500": _block(base, 42.6),
        "market_momentum_sp125": _block(base, 42.6),
        "stock_price_strength": _block(base, 15.8),
        "stock_price_breadth": _block(base, 47.8),
        "put_call_options": _block(base, 43.8),
        "market_volatility_vix": _block(base, 50.0),
        "market_volatility_vix_50": _block(base, 50.0),
        "junk_bond_demand": _block(base, 12.2),
        "safe_haven_demand": _block(base, 34.6),
    }
    p.update(overrides)
    return p


def test_열개_계열이_모두_추출되고_날짜가_오름차순이어야_한다():
    out = cnn_fng._parse(_payload())
    assert len(out) == 10, f"계열 수 {len(out)}"
    for key, series in out.items():
        dates = [d for d, _ in series]
        assert dates == sorted(dates), f"{key} 날짜 역순"
        for d in dates:
            datetime.strptime(d, "%Y-%m-%d")  # 형식 위반이면 예외


def test_같은_날짜_중복은_뒤_값으로_접혀야_한다():
    """실제 응답의 마지막 두 점(자정 확정 + 장중 갱신)이 한 점이 되어야 한다."""
    dup = _block([("2026-09-02T00:00:00Z", 33.0),
                  ("2026-09-03T00:00:00Z", 35.0),
                  ("2026-09-03T23:59:41Z", 37.0)])   # 같은 날 갱신값
    out = cnn_fng._parse(_payload(fear_and_greed_historical=dup))
    series = out["cnn_fng"]
    assert len(series) == 2, f"중복이 안 접힘: {series}"
    assert series[-1] == ("2026-09-03", 37.0), "뒤 값이 이겨야 함"
    assert series[-2][0] == "2026-09-02", "직전값은 전일이어야 함"


def test_접힌_뒤_prev_close가_같은날이_아니라_전일이어야_한다():
    dup = _block([("2026-09-02T00:00:00Z", 33.0),
                  ("2026-09-03T00:00:00Z", 35.0),
                  ("2026-09-03T23:59:41Z", 37.0)])
    out = cnn_fng._parse(_payload(fear_and_greed_historical=dup))
    q = cnn_fng._quote_from_series("cnn_fng", out["cnn_fng"])
    assert q.value == 37.0
    assert q.prev_close == 33.0, f"prev_close 가 {q.prev_close} - 같은 날 값을 집었다"
    assert q.change == 4.0


def test_자정이_아닌_타임스탬프도_올바른_UTC_날짜로_접혀야_한다():
    """하위 계열은 20:15, 22:30 등으로 온다."""
    odd = _block([("2026-09-02T20:36:05Z", 0.73), ("2026-09-03T20:15:01Z", 0.74)])
    out = cnn_fng._parse(_payload(put_call_options=odd))
    assert out["cnn_fng_putcall"] == [("2026-09-02", 0.73), ("2026-09-03", 0.74)]


def test_결측값은_버려야_한다():
    holed = _block([("2026-09-01T00:00:00Z", 30.0), ("2026-09-02T00:00:00Z", None),
                    ("2026-09-03T00:00:00Z", 35.0)])
    out = cnn_fng._parse(_payload(fear_and_greed_historical=holed))
    assert out["cnn_fng"] == [("2026-09-01", 30.0), ("2026-09-03", 35.0)]


def test_빈_데이터나_계열_누락에도_예외가_없어야_한다():
    out = cnn_fng._parse(_payload(fear_and_greed_historical={"score": None, "data": []}))
    assert out["cnn_fng"] == []
    assert cnn_fng._parse({}) == {}          # 응답 형식이 바뀌어도 죽지 않는다
    assert cnn_fng._parse({"put_call_options": None}) == {}


def test_중복_점수는_저장하지_않는다():
    """momentum_sp125·vix_50 은 짝 계열과 점수가 같아 별도 저장하지 않는다."""
    scores = cnn_fng._scores(_payload())
    assert set(scores) == {
        "cnnfg_score_total", "cnnfg_score_momentum", "cnnfg_score_strength",
        "cnnfg_score_breadth", "cnnfg_score_putcall", "cnnfg_score_vix",
        "cnnfg_score_junk", "cnnfg_score_safehaven",
    }
    assert scores["cnnfg_score_junk"] == 12.2


def test_모든_점수키에_한글라벨이_있어야_한다():
    """패널이 라벨 없이 빈 막대를 그리지 않도록."""
    keys = set(cnn_fng._scores(_payload())) - {"cnnfg_score_total"}
    assert keys == set(CNN_SCORE_LABELS), "수집기 _SERIES 와 config 라벨 목록 불일치"


def test_직전값은_확정된_마지막_날이어야_한다():
    """실측(2026-09-08) 재현 - CNN 의 previous_close 는 자기 이력과 어긋난다.

    배열: 09-03=43.91(확정) / 09-04=41.86 / 09-04T23:59:43=41.86(장중 슬롯)
    블록: score=41.86, previous_close=35.23  <- 09-03 확정값(43.91)과 8.7p 어긋남
    previous_close 를 믿으면 +18.8% 로 표시되지만 실제는 -4.7% 다.
    """
    live = _block([("2026-09-02T00:00:00Z", 32.97),
                   ("2026-09-03T00:00:00Z", 43.91),
                   ("2026-09-04T00:00:00Z", 41.86),
                   ("2026-09-04T23:59:43Z", 41.86)])
    payload = _payload(fear_and_greed_historical=live)
    payload["fear_and_greed"] = {"score": 41.86, "rating": "fear",
                                 "previous_close": 35.23,          # 신뢰하면 안 되는 값
                                 "timestamp": "2026-09-04T23:59:43+00:00"}

    series = cnn_fng._parse(payload)["cnn_fng"]
    q = cnn_fng._quote_from_series("cnn_fng", series,
                                   cnn_fng._live_date(payload["fear_and_greed"]))
    assert q.value == 41.86
    assert q.prev_close == 43.91, f"확정된 09-03 이 아니라 {q.prev_close} 를 집었다"
    assert round(q.change, 2) == -2.05


def test_장중_슬롯이_직전날까지_오염되면_변화는_0으로_둔다():
    """실측(2026-09-04 장중) 재현 - 실시간 값이 09-03·09-04 슬롯에 모두 덧씌워진 상태.

    이 구간에는 신뢰할 직전값이 없다. 큰 폭의 거짓 변화율보다 0 이 낫다.
    """
    live = _block([("2026-09-02T00:00:00Z", 32.97),
                   ("2026-09-03T00:00:00Z", 44.77),      # 실시간 값에 오염된 슬롯
                   ("2026-09-04T00:00:00Z", 44.77),
                   ("2026-09-04T11:57:46Z", 44.77)])
    payload = _payload(fear_and_greed_historical=live)
    payload["fear_and_greed"] = {"score": 44.77, "previous_close": 35.23,
                                 "timestamp": "2026-09-04T11:57:46+00:00"}
    series = cnn_fng._parse(payload)["cnn_fng"]
    q = cnn_fng._quote_from_series("cnn_fng", series,
                                   cnn_fng._live_date(payload["fear_and_greed"]))
    assert q.change == 0.0


def test_오늘_슬롯이_아직_없으면_평범하게_전일을_쓴다():
    """휴장 등으로 배열이 어제까지만 있을 때 - 마지막 두 점이 모두 확정값이다."""
    live = _block([("2026-09-02T00:00:00Z", 32.97),
                   ("2026-09-03T00:00:00Z", 43.91),
                   ("2026-09-04T00:00:00Z", 41.86)])
    payload = _payload(fear_and_greed_historical=live)
    payload["fear_and_greed"] = {"score": 41.86, "timestamp": "2026-09-08T02:00:00+00:00"}
    series = cnn_fng._parse(payload)["cnn_fng"]
    q = cnn_fng._quote_from_series("cnn_fng", series,
                                   cnn_fng._live_date(payload["fear_and_greed"]))
    assert q.value == 41.86 and q.prev_close == 43.91


def test_live_date_는_문자열과_ms_타임스탬프를_모두_읽는다():
    """종합 블록은 ISO 문자열, 하위 계열은 ms epoch 로 온다(실측)."""
    assert cnn_fng._live_date({"timestamp": "2026-09-04T23:59:43+00:00"}) == "2026-09-04"
    assert cnn_fng._live_date({"timestamp": 1788479981000.0}) == "2026-09-03"
    assert cnn_fng._live_date({}) is None
    assert cnn_fng._live_date({"timestamp": "쓰레기"}) is None


def test_live_date_가_없으면_기존대로_마지막_두_점을_쓴다():
    series = cnn_fng._parse(_payload())["cnn_fng"]
    q = cnn_fng._quote_from_series("cnn_fng", series, None)
    assert q.value == 35.0 and q.prev_close == 33.0
