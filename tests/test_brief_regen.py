r"""브리핑 재생성 판정(_should_regenerate) 테스트.

매시간 수집마다 ~150s LLM 호출로 거의 같은 헤드라인('국채금리 5년 최고권에도 증시 강세')을
몇 시간째 다시 쓰던 낭비를 막는다. 변화가 작으면 직전 브리핑을 유지한다.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

from datetime import datetime, timezone

from app import pipeline as pl
from app.models import CalendarEvent

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
PREV_TS = "2026-09-28T10:00:00+00:00"   # 2시간 전


def _prior(*deltas, regime="중립"):
    return {"prev_ts": PREV_TS, "prev_regime": regime, "deltas": list(deltas)}


def _d(key, prev, cur, unit=""):
    return {"key": key, "label": key, "unit": unit, "prev": prev, "cur": cur, "diff": cur - prev}


def _regen(prior, regime="중립", events=(), max_age=6):
    return pl._should_regenerate(prior, regime, list(events), NOW, max_age)


def test_직전_브리핑이_없으면_작성한다():
    assert _regen(None)[0] is True


def test_변화가_작으면_건너뛴다():
    ok, why = _regen(_prior(_d("sp500", 7000, 7010), _d("us10y", 4.60, 4.62, "%"),
                            _d("vix", 16.0, 16.5)))
    assert ok is False, why


def test_지수가_임계_이상_움직이면_작성한다():
    assert _regen(_prior(_d("kospi", 7000, 6900)))[0] is True


def test_금리는_퍼센트포인트로_판정한다():
    assert _regen(_prior(_d("us10y", 4.60, 4.66, "%")))[0] is True


def test_VIX_는_별도_임계로_판정한다():
    assert _regen(_prior(_d("vix", 16.0, 16.6)))[0] is False     # +3.75% — 평소 출렁임
    assert _regen(_prior(_d("vix", 16.0, 17.0)))[0] is True      # +6.25%


def test_레짐이_바뀌면_작성한다():
    assert _regen(_prior(), regime="위험 선호")[0] is True


def test_오래되면_변화가_없어도_작성한다():
    assert _regen(_prior(), max_age=2)[0] is True


def test_직전_브리핑_이후_주요발표_시각이_지나면_실제치가_없어도_작성한다():
    """ForexFactory 는 actual 을 거의 안 채운다(지난 고영향 189건 중 0건) — 실데이터 모양 그대로."""
    ev = CalendarEvent(title="FOMC Statement", impact="High", actual="",
                       date="2026-09-28T11:30:00+00:00")
    assert _regen(_prior(), events=[ev])[0] is True


def test_직전_브리핑_이전_발표_아직_안된_발표_중요도_낮은_발표는_무시한다():
    old = CalendarEvent(title="CPI", impact="High", date="2026-09-28T09:00:00+00:00")
    future = CalendarEvent(title="PPI", impact="High", date="2026-09-28T13:00:00+00:00")
    just_now = CalendarEvent(title="GDP", impact="High", date="2026-09-28T11:58:00+00:00")  # 반응 대기
    medium = CalendarEvent(title="Speech", impact="Medium", date="2026-09-28T11:00:00+00:00")
    assert _regen(_prior(), events=[old, future, just_now, medium])[0] is False


def _sd(key, prev_as_of, as_of, prev=100.0, cur=100.1):
    d = _d(key, prev, cur)
    d.update(prev_as_of=prev_as_of, as_of=as_of)
    return d


def test_새_거래_세션이_열리면_변화가_작아도_작성한다():
    """08:59 KST 에 쓴 브리핑이 09:00 코스피 개장 후에도 '어제 -2.7%' 를 말하던 실제 사례."""
    ok, why = _regen(_prior(_sd("kospi", "2026-09-28T11:05:40+00:00", "2026-09-29T00:36:50+00:00")))
    assert ok is True and "한국장" in why


def test_같은_세션이면_세션_트리거는_없다():
    d = _sd("sp500", "2026-09-28T14:00:00+00:00", "2026-09-28T15:00:00+00:00")
    assert _regen(_prior(d))[0] is False


def test_세션_트리거는_주가지수에만_적용된다():
    """환율·BTC 는 24시간 시세라 UTC 자정마다 날짜가 바뀐다 — 세션 전환으로 보면 안 된다."""
    d = _sd("btc", "2026-09-28T23:57:00+00:00", "2026-09-29T00:56:00+00:00")
    assert _regen(_prior(d))[0] is False
