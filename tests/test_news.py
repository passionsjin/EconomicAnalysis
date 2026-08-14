r"""news 수집기 테스트 — 동결 피드 방어(발행일 기준 신선도 필터).

죽은 RSS 는 404 가 아니라 '옛 기사에 동결된 200' 으로 나타나므로, 수집 성공/실패만으로는
감지되지 않는다. 네트워크 없이 합성 피드로 필터 동작을 고정한다.

실행: .\.venv\Scripts\python.exe -m pytest tests -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest

from app.collectors import news as news_mod

_NOW = datetime.now(timezone.utc)


def _item(title: str, age_hours: float | None, link: str | None = None) -> str:
    pub = ("" if age_hours is None else
           f"<pubDate>{format_datetime(_NOW - timedelta(hours=age_hours))}</pubDate>")
    return (f"<item><title>{title}</title>"
            f"<link>{link or 'http://example.com/' + title.replace(' ', '-')}</link>"
            f"{pub}</item>")


def _rss(*items: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            "<title>테스트 피드</title>" + "".join(items) +
            "</channel></rss>").encode("utf-8")


@pytest.fixture
def 피드(monkeypatch):
    """{피드이름: RSS bytes} 를 등록하면 그대로 응답하는 가짜 네트워크."""
    보관: dict[str, bytes] = {}

    def 설정(**feeds: bytes):
        보관.clear()
        보관.update(feeds)
        monkeypatch.setattr(news_mod, "settings", SimpleNamespace(
            news_feeds=tuple((name, f"http://feed/{name}") for name in feeds),
            news_max_age_hours=48, request_timeout=20))
        monkeypatch.setattr(news_mod.http, "get", lambda url, **kw: SimpleNamespace(
            content=보관[url.rsplit("/", 1)[-1]]))
    return 설정


def test_동결된_피드는_실패로_처리되고_교체하라고_알린다(피드):
    피드(죽은피드=_rss(_item("6개월 전 기사", age_hours=24 * 180)))

    res = news_mod.NewsCollector().collect()

    assert res.news == []
    assert res.health.failed == 1
    assert "동결 피드 교체 필요" in res.health.message
    assert "죽은피드" in res.health.message


def test_섞인_피드에서는_신선한_기사만_남는다(피드):
    피드(정상피드=_rss(_item("어제 기사", age_hours=20),
                    _item("작년 기사", age_hours=24 * 300)))

    res = news_mod.NewsCollector().collect()

    제목 = [n.title for n in res.news]
    assert 제목 == ["어제 기사"]
    assert res.health.ok is True
    assert "동결" not in res.health.message


def test_한_피드가_죽어도_나머지_피드는_수집된다(피드):
    피드(죽은피드=_rss(_item("옛날 기사", age_hours=24 * 200)),
       산피드=_rss(_item("오늘 기사", age_hours=2)))

    res = news_mod.NewsCollector().collect()

    assert [n.title for n in res.news] == ["오늘 기사"]
    assert (res.health.ok, res.health.failed) == (True, 1)


def test_발행일이_없는_기사는_통과한다(피드):
    """현행 정책: 판단 근거가 없으면 거르지 않는다.

    (알려진 빈틈 — 날짜를 주지 않는 피드는 동결돼도 이 방어선을 통과한다.)
    """
    피드(무날짜피드=_rss(_item("날짜 없는 기사", age_hours=None)))

    res = news_mod.NewsCollector().collect()

    assert [n.title for n in res.news] == ["날짜 없는 기사"]


def test_같은_링크는_한_번만_담긴다(피드):
    같은링크 = "http://example.com/duplicate"
    피드(피드A=_rss(_item("제목 A", 1, link=같은링크)),
       피드B=_rss(_item("제목 B", 1, link=같은링크)))

    res = news_mod.NewsCollector().collect()

    assert len(res.news) == 1
    assert res.health.fetched == 1
