r"""수집 실패 지표의 직전값 폴백 테스트.

FRED 는 간헐 타임아웃이 잦아(최근 24스냅샷 중 us_be10y 8회, us_rrp 6회) 한 시리즈가
빠지면 해당 타일이 통째로 공백이 되고, 그 지표를 쓰는 파생(실질금리·순유동성)까지
동반 결측됐다. 저장된 직전값으로 메우되, 죽은 시리즈는 결국 실패로 드러나야 한다.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

from datetime import date

import pytest

from app import pipeline as pl
from app.models import Quote


def _lookup(mapping: dict[str, list[tuple[str, float]]]):
    """key -> [(date, value), ...] 오름차순 이력을 주는 가짜 조회기(DB 불필요)."""
    return lambda key: list(mapping.get(key, []))


def test_failed_quote_falls_back_to_last_stored_value():
    quotes = {"us_rrp": Quote(key="us_rrp", ok=False, error="타임아웃")}
    lookup = _lookup({"us_rrp": [("2026-08-21", 0.0002), ("2026-08-24", 0.00038)]})

    filled = pl._apply_fallbacks(quotes, lookup, today=date(2026, 8, 25))

    assert filled == ["us_rrp"]
    q = quotes["us_rrp"]
    assert q.ok is True and q.fallback is True
    assert q.value == 0.00038
    assert q.prev_close == 0.0002
    assert q.as_of == "2026-08-24T00:00:00+00:00"
    assert q.error == "타임아웃"          # 원 실패 사유는 보존
    assert q.history[-1] == ("2026-08-24", 0.00038)


def test_daily_value_beyond_cap_stays_failed():
    """일별 지표가 10일 넘게 갱신 안 됐으면 폴백하지 않는다(죽은 시리즈 은폐 방지)."""
    quotes = {"us_rrp": Quote(key="us_rrp", ok=False, error="타임아웃")}
    lookup = _lookup({"us_rrp": [("2026-08-01", 0.0002)]})

    assert pl._apply_fallbacks(quotes, lookup, today=date(2026, 8, 25)) == []
    assert quotes["us_rrp"].ok is False
    assert quotes["us_rrp"].value is None


def test_monthly_series_tolerates_release_lag():
    """월별은 기간일자 + 발표지연이라 2개월 전 값이 정상 — 폴백해야 한다."""
    quotes = {"us_cpi_yoy": Quote(key="us_cpi_yoy", ok=False, error="타임아웃")}
    lookup = _lookup({"us_cpi_yoy": [("2026-06-01", 3.1), ("2026-07-01", 3.3)]})

    assert pl._apply_fallbacks(quotes, lookup, today=date(2026, 8, 27)) == ["us_cpi_yoy"]
    assert quotes["us_cpi_yoy"].value == 3.3


def test_successful_quote_is_untouched():
    q = Quote(key="us_rrp", value=1.0, prev_close=0.9, as_of="now", history=[("2026-08-25", 1.0)], ok=True)
    quotes = {"us_rrp": q}

    assert pl._apply_fallbacks(quotes, _lookup({"us_rrp": [("2026-08-24", 0.5)]}),
                               today=date(2026, 8, 25)) == []
    assert quotes["us_rrp"].value == 1.0
    assert quotes["us_rrp"].fallback is False


def test_no_history_stays_failed():
    quotes = {"us_rrp": Quote(key="us_rrp", ok=False, error="타임아웃")}
    assert pl._apply_fallbacks(quotes, _lookup({}), today=date(2026, 8, 25)) == []
    assert quotes["us_rrp"].ok is False


def test_derived_keys_are_not_filled_directly():
    """파생은 피연산에서 재계산되므로 직접 폴백하지 않는다(옛 값 고착 방지)."""
    quotes = {"us_real10y": Quote(key="us_real10y", ok=False, error="피연산 지표 결측")}
    lookup = _lookup({"us_real10y": [("2026-08-26", 2.34)]})

    assert pl._apply_fallbacks(quotes, lookup, today=date(2026, 8, 26)) == []


def test_derived_recovers_when_operand_falls_back():
    """실질금리 = us10y - us_be10y. be10y 만 실패해도 폴백 후 파생이 살아나야 한다."""
    quotes = {
        "us10y": Quote(key="us10y", value=4.664, prev_close=4.6,
                       history=[("2026-08-25", 4.6), ("2026-08-26", 4.664)], ok=True),
        "us_be10y": Quote(key="us_be10y", ok=False, error="타임아웃"),
    }
    lookup = _lookup({"us_be10y": [("2026-08-25", 2.3), ("2026-08-26", 2.32)]})

    pl._apply_fallbacks(quotes, lookup, today=date(2026, 8, 26))
    derived = {q.key: q for q in pl._compute_derived(quotes)}

    r = derived["us_real10y"]
    assert r.ok is True
    assert r.value == pytest.approx(2.344)
    assert r.history[-1][0] == "2026-08-26"          # 이력 교집합도 복원
    assert r.prev_close == pytest.approx(2.3)


def test_unknown_key_is_skipped():
    quotes = {"없는지표": Quote(key="없는지표", ok=False, error="타임아웃")}
    assert pl._apply_fallbacks(quotes, _lookup({"없는지표": [("2026-08-26", 1.0)]}),
                               today=date(2026, 8, 26)) == []
