r"""briefing.py 순수 함수 테스트 — LLM 프롬프트에 들어가는 숫자·문구의 표기 규약.

이 모듈의 함수들은 네트워크·DB·LLM 없이 입출력이 결정적이라 그대로 테스트한다.
지표 메타데이터는 실제 레지스트리(config.INDICATOR_BY_KEY)를 쓴다 — 레지스트리가
바뀌어 표기 규약이 깨지면 여기서 잡히게 하려는 의도.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analysis import briefing
from app.config import INDICATOR_BY_KEY
from app.models import CalendarEvent, NewsItem, Quote

_END = "2026-08-14"


def _hist(n: int, fn) -> list[tuple[str, float]]:
    """마지막 날짜가 _END 인 일별 이력 n개."""
    last = datetime.fromisoformat(_END)
    return [((last - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d"), fn(i))
            for i in range(n)]


def _q(key: str, value: float, prev: float, hist: list | None = None, n: int = 90) -> Quote:
    h = hist if hist is not None else _hist(n, lambda i: value)
    return Quote(key=key, value=value, prev_close=prev, history=h, ok=True)


def _chg(key: str, value: float, prev: float, hist: list | None = None) -> str:
    return briefing._change_str(INDICATOR_BY_KEY[key], _q(key, value, prev, hist))


# ─────────────────────── _momentum_block: 레지스트리 드리프트 내성 ───────────────────────

def test_모멘텀블록은_레지스트리에_없는_키를_건너뛴다(monkeypatch):
    """_MTF_KEYS 에 config 에서 사라진 키가 남아도 브리핑 전체가 죽으면 안 된다.

    _momentum_of 가 ind.unit 을 읽기 시작하면서, 레지스트리에 없는 키의 ind=None 이
    그대로 넘어가 AttributeError 로 브리핑 생성이 통째로 실패할 수 있게 됐다.
    """
    monkeypatch.setattr(briefing, "_MTF_KEYS", ["sp500", "__삭제된키__"])
    quotes = {
        "sp500": _q("sp500", 5000.0, 4950.0, _hist(90, lambda i: 4000.0 + 10 * i)),
        "__삭제된키__": _q("__삭제된키__", 100.0, 99.0),
    }

    out = briefing._momentum_block(quotes)

    assert "S&P 500" in out
    assert "__삭제된키__" not in out


# ─────────────────────── _change_str: 변화 표기 단위·기준 ───────────────────────

def test_금리는_퍼센트포인트로_표기된다():
    """us10y 4.18 -> 4.60 은 '+10%' 가 아니라 '+0.42%p'. 값 자체가 %인 지표의 규약."""
    out = _chg("us10y", 4.60, 4.18)

    assert out == "+0.42%p"


def test_월별지표는_변화옆에_전월이_붙는다():
    """월별 CPI 의 전월비를 LLM 이 '오늘의 변화'로 읽지 않게 기준을 명시한다."""
    out = _chg("us_cpi_yoy", 3.10, 2.90)

    assert out == "+0.20%p 전월"


def test_가격지표는_비율로_표기된다():
    out = _chg("sp500", 5000.0, 4950.0, _hist(90, lambda i: 4000.0 + 10 * i))

    assert out == "+1.01%"


def test_음수를_오가는_지수는_절대변화로_표기된다():
    """CFNAI -0.10 -> -0.05 를 '+50%' 로 주면 LLM 이 큰 사건으로 읽는다."""
    hist = _hist(40, lambda i: -0.10 + 0.001 * i)

    out = _chg("us_cfnai", -0.05, -0.10, hist)

    assert "50" not in out          # 비율 표기가 아님
    assert out.startswith("+0.05")
    assert out.endswith("전월")      # 월별 기준 라벨


def test_0으로_수렴한_지표는_절대변화로_표기된다():
    """사실상 소멸한 ON RRP(과거 2.5T$ -> 현재 0.001T$)의 -18% 류 표기 방지."""
    hist = _hist(90, lambda i: 2.5 - 0.0277 * i)   # 2.5T$ 에서 0 근처까지 축소

    out = _chg("us_rrp", 0.0010, 0.0012, hist)

    assert "%" not in out
    assert out.startswith("-0.0002")


def test_변화값이_없으면_대시로_표기된다():
    q = Quote(key="sp500", value=5000.0, prev_close=None, history=[], ok=True)

    assert briefing._change_str(INDICATOR_BY_KEY["sp500"], q) == "—"


# ─────────────────────── _momentum_of: 모멘텀 단위 분기 ───────────────────────

def test_금리_모멘텀은_퍼센트포인트_절대변화다():
    hist = _hist(90, lambda i: 4.00 + 0.02 * i)     # 하루 +0.02%p
    q = _q("us10y", hist[-1][1], 5.76, hist)

    m, unit, dp = briefing._momentum_of(INDICATOR_BY_KEY["us10y"], q)

    assert (unit, dp) == ("%p", 2)
    assert m["m1"] == pytest.approx(0.42)           # 21영업일 x 0.02
    assert m["w1"] == pytest.approx(0.10)


def test_가격_모멘텀은_비율이다():
    hist = _hist(90, lambda i: 4000.0 + 10 * i)
    q = _q("sp500", hist[-1][1], 4880.0, hist)

    m, unit, dp = briefing._momentum_of(INDICATOR_BY_KEY["sp500"], q)

    assert (unit, dp) == ("%", 1)
    assert m["m1"] == pytest.approx(4.487, abs=0.01)   # (4890/4680-1)*100


# ─────────────────────── 뉴스 키워드 매칭: 영문 단어경계 ───────────────────────

@pytest.mark.parametrize("title", [
    "european shares slide on growth worries",   # euro
    "goldman sachs names new partners",          # gold
    "markets edge down after the bell",          # dow
])
def test_영문_키워드는_부분일치로_오탐하지_않는다(title):
    assert not briefing._M_MARKET(title)


def test_잡음어_my_는_economy_를_오탐하지_않는다():
    assert not briefing._M_NOISE("us economy adds jobs in july")


def test_한국어_키워드는_조사가_붙어도_매칭된다():
    """한국어는 교착어라 '기준금리인하'처럼 붙어 나온다 — 부분일치가 맞다."""
    assert briefing._M_POLICY("한은 기준금리인하 가능성 시사")


def test_뉴스_점수는_정책_시장_잡음_순이다():
    정책 = briefing._news_score(NewsItem("s", "Fed signals rate cut as inflation cools", "l"))
    시장 = briefing._news_score(NewsItem("s", "Oil prices climb", "l"))
    잡음 = briefing._news_score(NewsItem("s", "Why is Nvidia stock jumps today", "l"))

    assert 정책 > 시장 > 0 > 잡음


# ─────────────────────── _news_block: 선별 ───────────────────────

def _news(source: str, title: str, day: int) -> NewsItem:
    ts = datetime(2026, 8, day, 12, 0, tzinfo=timezone.utc).isoformat()
    return NewsItem(source=source, title=title, link=f"http://x/{source}/{title}", published=ts)


def test_한_소스가_전체칸을_먹지_못한다():
    """피드 나열 순서로 자르면 앞쪽 피드 하나가 12칸을 다 먹던 문제."""
    많은피드 = [_news("A", f"Fed policy update {i}", 10) for i in range(10)]
    다른피드 = [_news("B", f"Inflation data {i}", 10) for i in range(4)]

    out = briefing._news_block(많은피드 + 다른피드, limit=12, per_source=4)

    assert out.count("[A]") == 4
    assert out.count("[B]") == 4


def test_쓸만한_기사가_충분하면_잡음은_버려진다():
    유효 = [_news("A", f"Fed rate decision {i}", 10) for i in range(4)]
    잡음 = [_news("B", f"Here's how I saved on prime day {i}", 10) for i in range(4)]

    out = briefing._news_block(유효 + 잡음, limit=6, per_source=4)

    assert "prime day" not in out


def test_같은_점수면_최신_기사가_먼저다():
    옛것 = _news("A", "Fed holds rates steady", 1)
    최신 = _news("B", "Fed holds rates steady again", 12)

    out = briefing._news_block([옛것, 최신], limit=2)

    assert "steady again" in out.splitlines()[0]   #최신이 첫 줄


# ─────────────────────── _calendar_block: 지난 발표 처리 ───────────────────────

_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _ev(title: str, hours: float, actual: str = "", forecast: str = "") -> CalendarEvent:
    return CalendarEvent(title=title, country="US", impact="High",
                         date=(_NOW + timedelta(hours=hours)).isoformat(),
                         actual=actual, forecast=forecast)


def test_결과없이_지나간_발표는_예정으로_나가지_않는다():
    """ForexFactory 가 actual 을 안 채우는 경우가 잦아, 그대로 두면 없는 이벤트를 예고한다."""
    out = briefing._calendar_block([_ev("지난 고용지표", -8.0)], now_utc=_NOW)

    assert "지난 고용지표" not in out


def test_결과가_있는_지난_발표는_발표완료로_남는다():
    out = briefing._calendar_block([_ev("CPI", -8.0, actual="3.1%", forecast="3.0%")],
                                   now_utc=_NOW)

    assert "[발표 완료 (지난 12h)]" in out
    assert "실제 3.1%" in out
    assert "→ 예상 상회" in out


def test_임박한_발표는_별표로_강조된다():
    out = briefing._calendar_block([_ev("FOMC 성명", 6.0)], now_utc=_NOW)

    assert "[임박/방금 발표 (-3h~+12h) — 우선 주목]" in out
    assert "** " in out


def test_주요발표가_없으면_명시적으로_알린다():
    assert briefing._calendar_block([_ev("결과없는 과거", -20.0)], now_utc=_NOW) \
        == "(예정된 주요 발표 없음)"


# ─────────────────────── _prior_block: 직전 대비 변화 ───────────────────────

def _delta(label: str, cur: float, prev: float, unit: str = "") -> dict:
    return {"label": label, "unit": unit, "cur": cur, "prev": prev, "diff": cur - prev}


def test_움직이지_않은_지표는_직전대비_목록에서_빠진다():
    """미국장 마감 시간대엔 절반이 +0.00% 라 그대로 넣으면 변화 없음을 변화로 읽힌다."""
    prior = {"prev_ts": "2026-08-14T11:00:00", "prev_sentiment": "neutral",
             "prev_headline": "관망세 지속",
             "deltas": [_delta("S&P 500", 5000.0, 5000.0), _delta("금", 3400.0, 3380.0)]}

    out = briefing._prior_block(prior)

    assert "S&P 500" not in out
    assert "금" in out


def test_모두_제자리면_변동_목록_자체가_생략된다():
    prior = {"prev_ts": "2026-08-14T11:00:00", "prev_sentiment": "neutral",
             "prev_headline": "관망세 지속",
             "deltas": [_delta("S&P 500", 5000.0, 5000.0)]}

    out = briefing._prior_block(prior)

    assert "직전 대비 변동" not in out
    assert "관망세 지속" in out          # 직전 심리 자체는 유지


def test_파싱실패_폴백_헤드라인은_인용하지_않는다():
    """'시황 브리핑'은 JSON 파싱 실패 시의 자리표시자라 직전 판단이 아니다."""
    prior = {"prev_ts": "2026-08-14T11:00:00", "prev_sentiment": "neutral",
             "prev_headline": briefing._FALLBACK_HEADLINE, "deltas": []}

    out = briefing._prior_block(prior)

    assert briefing._FALLBACK_HEADLINE not in out
    assert "neutral" in out


# ─────────────────────── 인용 수치 점검(_citation_audit) ───────────────────────

def test_더_긴_라벨의_인용은_짧은_라벨_값으로_오인하지_않는다():
    """'코스피 PER이 23.1배' 의 23.1 을 코스피 지수값(6,556)으로 읽으면 안 된다.

    문체 지침 도입 후 LLM 이 조사('PER이')를 붙여 쓰면서 실제로 오발화한 사례.
    """
    quotes = {"kospi": _q("kospi", 6556.35, 6870.0)}
    body = "- 코스피 PER이 23.1배로 밸류에이션 부담이 높다."
    assert briefing._citation_audit(body, quotes) == []


def test_실제로_틀린_인용은_잡아낸다():
    quotes = {"kospi": _q("kospi", 6556.35, 6870.0)}
    body = "- 코스피 3,120.5 로 마감했다."
    issues = briefing._citation_audit(body, quotes)
    assert len(issues) == 1 and "코스피" in issues[0]


def test_정상_인용은_통과한다():
    quotes = {"kospi": _q("kospi", 6556.35, 6870.0)}
    body = "- 코스피 6,556.35 로 마감했다."
    assert briefing._citation_audit(body, quotes) == []


# 아래는 저장된 브리핑 954건 중 34%에 경고를 붙였던 실제 오탐 문장들이다.
@pytest.mark.parametrize("key,value,body", [
    ("usdkrw", 1362.26, "- 관전 지표: 원/달러, 한미 10년 금리차, 코스피 수급."),   # 나열
    ("btc", 84187.9, "비트코인이 금·비트코인 상관(0.64)대로 동반 조정됐다."),      # 괄호 부가정보
    ("btc", 84187.9, "금·비트코인 상관 +0.73은 두 자산이 함께 움직인다는 뜻이다."), # 부호 붙은 상관계수
    ("gold", 4187.9, "- 금 1M -3.2%, 은 -4.3%로 약세다."),                         # 모멘텀 태그
    ("gold", 4174.9, "- 구리/금 비율 1.521(23%ile)은 경기 둔화 신호다."),            # 비율 이름의 일부
    ("vix", 16.66, "- 트리거: VIX 20 재돌파 여부, HY 스프레드 확대."),              # 가정·임계 수준
    ("kospi", 6880.97, "- 코스피 3중 악재: PER 32.3배."),                         # 수사(3중)
    ("kospi", 6023.66, "- 코스피200 선물 야간 동향을 본다."),                      # 다른 상품 이름
    ("sp500", 7753.11, "S&P 500 5년 내 100분위 고점권을 유지했다."),               # 기간 뒤 순위
    ("btc", 84468.0, "비트코인은 8만4천 달러대에서 등락했다."),                     # 한국어 수 단위
])
def test_부가정보_문맥의_숫자는_값_인용으로_보지_않는다(key, value, body):
    quotes = {key: _q(key, value, value)}
    assert briefing._citation_audit(body, quotes) == []


def test_조사가_붙은_정상_인용도_점검된다():
    """'원/달러는 1,355.95원' 이 가장 흔한 인용 형태인데, 예전엔 조사 때문에 검사 자체를 건너뛰었다."""
    quotes = {"usdkrw": _q("usdkrw", 1355.95, 1350.0)}
    assert briefing._citation_audit("- 원/달러는 1,355.95원으로 올랐다.", quotes) == []
    issues = briefing._citation_audit("- 원/달러는 1,555.95원으로 올랐다.", quotes)
    assert len(issues) == 1 and "원/달러" in issues[0]


def test_VIX_는_짧은_호칭으로_인용해도_점검된다():
    quotes = {"vix": _q("vix", 16.5, 16.0)}
    assert briefing._citation_audit("- VIX 16.5로 안정적이다.", quotes) == []
    assert len(briefing._citation_audit("- VIX 24.5로 뛰었다.", quotes)) == 1


def test_조사가_아닌_한글이_붙으면_다른_낱말이다():
    """'금리' 의 '금' 은 금 가격이 아니다."""
    quotes = {"gold": _q("gold", 4187.9, 4180.0)}
    assert briefing._citation_audit("- 금리 5.18%로 올랐다.", quotes) == []


# ─────────────────────── 수집 실패 직전값(fallback) 표기 ───────────────────────

def _fb(key: str, value: float, prev: float) -> Quote:
    q = _q(key, value, prev)
    q.fallback, q.as_of = True, "2026-08-11T00:00:00+00:00"
    return q


def test_직전값_지표는_기준일을_달고_변화를_숨긴다():
    out = briefing._data_block({"us10y": _fb("us10y", 4.2, 4.0)})
    row = next(l for l in out.splitlines() if INDICATOR_BY_KEY["us10y"].label in l)
    assert "직전값·2026-08-11 기준" in row
    assert "%p" not in row            # 옛 변화(+0.20%p)를 오늘 변화로 내보내지 않는다


def test_직전값_지표는_수치점검에서_제외된다():
    quotes = {"kospi": _fb("kospi", 6556.35, 6870.0)}
    assert briefing._citation_audit("- 코스피 3,120.5 로 마감했다.", quotes) == []


def test_CNN_하위_원시계열은_프롬프트에서_빠진다():
    quotes = {"cnn_fng": _q("cnn_fng", 43, 40), "cnn_fng_vix": _q("cnn_fng_vix", 14.2, 14.0)}
    out = briefing._data_block(quotes)
    assert INDICATOR_BY_KEY["cnn_fng"].label in out
    assert INDICATOR_BY_KEY["cnn_fng_vix"].label not in out


def test_레짐_블록에_CNN_교차검증이_붙는다():
    regime = {"regime": {"label": "중립/전환", "score": 51, "drivers": []}, "window": 30}
    out = briefing._regime_block(regime, _q("cnn_fng", 43, 40))
    assert "CNN 공포·탐욕 43/100 (공포)" in out


def test_뉴스에_발행_후_경과시간이_붙는다():
    now = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
    news = [NewsItem(source="R", title="Fed signals rate cut", link="x",
                     published="2026-08-14T07:00:00+00:00")]
    assert "(5h 전)" in briefing._news_block(news, now_utc=now)


# ─────────────────────── 응답 파싱 ───────────────────────

def test_JSON_파싱_실패는_실패로_돌려_재시도_폴백을_탄다():
    b = briefing._briefing_from_text("죄송합니다, 형식을 지키지 못했습니다.", "m")
    assert b.ok is False and "파싱" in b.error
