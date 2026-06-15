"""수집 파이프라인에서 오가는 인메모리 데이터 구조."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Quote:
    """단일 지표의 최신 관측값 + 차트용 일별 이력."""
    key: str
    value: Optional[float] = None
    prev_close: Optional[float] = None          # 직전 종가(전일대비 계산용)
    as_of: Optional[str] = None                  # 데이터 기준시각/일자(ISO)
    history: list[tuple[str, float]] = field(default_factory=list)  # [(YYYY-MM-DD, close)]
    ok: bool = True
    error: Optional[str] = None

    @property
    def change(self) -> Optional[float]:
        if self.value is None or self.prev_close is None:
            return None
        return self.value - self.prev_close

    @property
    def change_pct(self) -> Optional[float]:
        if self.value is None or self.prev_close in (None, 0):
            return None
        return (self.value - self.prev_close) / abs(self.prev_close) * 100.0


@dataclass
class SourceHealth:
    """한 수집 소스의 상태(대시보드 배지)."""
    source: str
    ok: bool = True
    fetched: int = 0
    failed: int = 0
    latency_ms: int = 0
    message: str = ""


@dataclass
class NewsItem:
    source: str
    title: str
    link: str
    published: Optional[str] = None   # ISO
    summary: str = ""


@dataclass
class CalendarEvent:
    title: str
    country: str = ""
    date: Optional[str] = None        # ISO (UTC)
    impact: str = ""                  # High/Medium/Low
    actual: str = ""
    forecast: str = ""
    previous: str = ""


@dataclass
class CollectResult:
    """수집기 한 개의 실행 결과."""
    quotes: list[Quote] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    events: list[CalendarEvent] = field(default_factory=list)
    health: Optional[SourceHealth] = None


@dataclass
class Briefing:
    headline: str = ""
    summary: str = ""
    body_md: str = ""
    sentiment: str = ""        # risk-on / risk-off / neutral / mixed
    model: str = ""
    ok: bool = True
    error: Optional[str] = None
