r"""markets 수집기 테스트 - 일봉 날짜를 거래소 로컬 기준으로 찍는지 고정.

Yahoo 는 `=X`(환율) 일봉을 거래소(Europe/London) 자정 기준으로 준다. 서머타임(BST,
gmtoffset=3600) 구간에는 그 시각이 UTC 로 '전날 23:00' 이라, UTC 날짜로 찍으면 이력 전체가
하루씩 밀린다(일요일 환율 행이 생기고 금요일 행이 사라짐). 네트워크 없이 합성 응답으로 고정한다.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.collectors import markets as mk


def _ts(iso: str) -> int:
    """'2026-08-23T23:00:00Z' -> epoch seconds."""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _chart(gmtoffset: int | None, bars: list[tuple[str, float]], *, market_time: str | None = None) -> dict:
    meta: dict = {"regularMarketPrice": bars[-1][1]}
    if gmtoffset is not None:
        meta["gmtoffset"] = gmtoffset
    if market_time:
        meta["regularMarketTime"] = _ts(market_time)
    return {
        "meta": meta,
        "timestamp": [_ts(t) for t, _ in bars],
        "indicators": {"quote": [{"close": [v for _, v in bars]}]},
    }


def test_fx_summer_time_bars_use_exchange_local_date():
    """BST 구간 환율 일봉(전날 23:00 UTC)은 런던 기준 당일 날짜로 저장돼야 한다."""
    raw = _chart(3600, [
        ("2026-08-20T23:00:00Z", 1380.0),  # 8/21(금)
        ("2026-08-23T23:00:00Z", 1384.0),  # 8/24(월)
        ("2026-08-24T23:00:00Z", 1385.0),  # 8/25(화)
    ])
    _, _, _, hist = mk._parse("KRW=X", raw)
    assert [d for d, _ in hist] == ["2026-08-21", "2026-08-24", "2026-08-25"]


def test_fx_winter_time_bars_unchanged():
    """GMT 구간(gmtoffset=0)은 이미 00:00 UTC 라 날짜가 그대로여야 한다."""
    raw = _chart(0, [("2026-01-05T00:00:00Z", 1400.0), ("2026-01-06T00:00:00Z", 1402.0)])
    _, _, _, hist = mk._parse("KRW=X", raw)
    assert [d for d, _ in hist] == ["2026-01-05", "2026-01-06"]


def test_us_equity_bars_keep_trading_day():
    """미국 장중(13:30 UTC) 봉은 로컬 변환(-4h) 후에도 같은 거래일이어야 한다."""
    raw = _chart(-14400, [("2026-08-24T13:30:00Z", 6800.0), ("2026-08-25T13:30:00Z", 6810.0)])
    _, _, _, hist = mk._parse("^GSPC", raw)
    assert [d for d, _ in hist] == ["2026-08-24", "2026-08-25"]


def test_missing_gmtoffset_falls_back_to_utc():
    """meta 에 gmtoffset 이 없으면 종전대로 UTC 날짜."""
    raw = _chart(None, [("2026-08-25T13:30:00Z", 6810.0)])
    _, _, _, hist = mk._parse("^GSPC", raw)
    assert [d for d, _ in hist] == ["2026-08-25"]


def test_prev_close_uses_exchange_local_market_date():
    """오늘 봉이 이미 있으면 직전 종가는 그 전날 봉이어야 한다(로컬 날짜 비교)."""
    raw = _chart(
        -14400,
        [("2026-08-24T13:30:00Z", 6800.0), ("2026-08-25T13:30:00Z", 6810.0)],
        market_time="2026-08-26T00:30:00Z",  # NY 로는 8/25 20:30 (장 마감 후)
    )
    _, prev, _, _ = mk._parse("^GSPC", raw)
    assert prev == 6800.0
