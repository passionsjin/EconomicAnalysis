"""경제 뉴스 헤드라인 수집기 — RSS (키 불필요).

config.settings.news_feeds 의 각 피드를 가져와 정규화. 일부 피드 실패는 무시.
"""
from __future__ import annotations

import calendar as _cal
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape

import feedparser

from ..config import settings
from ..models import CollectResult, NewsItem, SourceHealth
from . import http
from .base import Collector

_TAG = re.compile(r"<[^>]+>")
_PER_FEED = 8


def _safe(fn, arg):
    try:
        return fn(arg)
    except Exception:  # noqa: BLE001
        return None


def _clean(text: str) -> str:
    return unescape(_TAG.sub("", text or "")).strip()


def _published_iso(entry) -> str | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            return datetime.fromtimestamp(_cal.timegm(t), tz=timezone.utc).isoformat()
    return None


class NewsCollector(Collector):
    source = "news"
    label = "뉴스(RSS)"

    def collect(self) -> CollectResult:
        now = datetime.now(timezone.utc)
        max_age = max(1, settings.news_max_age_hours)
        stale: list[str] = []

        def _one(feed) -> list[NewsItem]:
            name, url = feed
            raw = http.get(url, retries=1, timeout=min(settings.request_timeout, 12)).content
            parsed = feedparser.parse(raw)
            out = []
            newest_age = None
            for e in parsed.entries[:_PER_FEED]:
                link = getattr(e, "link", "")
                title = _clean(getattr(e, "title", ""))
                if not link or not title:
                    continue
                pub = _published_iso(e)
                if pub:
                    # 죽은 피드는 200 을 주면서 옛 기사를 계속 반환한다 — 날짜로 거른다.
                    age_h = (now - datetime.fromisoformat(pub)).total_seconds() / 3600.0
                    if newest_age is None or age_h < newest_age:
                        newest_age = age_h
                    if age_h > max_age:
                        continue
                out.append(NewsItem(
                    source=name, title=title, link=link, published=pub,
                    summary=_clean(getattr(e, "summary", ""))[:400],
                ))
            if not out:
                if newest_age is not None and newest_age > max_age:
                    stale.append(f"{name}(최신 {newest_age / 24:.0f}일 전)")
                    raise ValueError("동결 피드")
                raise ValueError("빈 피드")
            return out

        items: list[NewsItem] = []
        ok_feeds = 0
        fail_feeds = 0
        feeds = list(settings.news_feeds)
        with ThreadPoolExecutor(max_workers=min(8, len(feeds) or 1)) as ex:
            for res in ex.map(lambda f: _safe(_one, f), feeds):
                if res is None:
                    fail_feeds += 1
                else:
                    ok_feeds += 1
                    items.extend(res)

        # 중복 링크 제거(순서 유지)
        seen = set()
        deduped = []
        for it in items:
            if it.link in seen:
                continue
            seen.add(it.link)
            deduped.append(it)

        total = ok_feeds + fail_feeds
        msg = f"{ok_feeds}/{total} 피드 정상, 기사 {len(deduped)}건"
        if stale:
            msg += f" | 동결 피드 교체 필요: {', '.join(stale)}"
        health = SourceHealth(
            source=self.source, ok=ok_feeds > 0, fetched=len(deduped), failed=fail_feeds,
            message=msg,
        )
        return CollectResult(news=deduped, health=health)
