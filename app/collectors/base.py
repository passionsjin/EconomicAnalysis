"""수집기 인터페이스 + 레지스트리."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..models import CollectResult, SourceHealth


class Collector(ABC):
    #: source 키 (config.Indicator.source 와 매칭) / 화면 표시명
    source: str = ""
    label: str = ""

    @abstractmethod
    def collect(self) -> CollectResult:
        """이 소스의 데이터를 수집해 CollectResult 로 반환. 예외를 던지지 말 것."""
        ...

    def run(self) -> CollectResult:
        """타이밍 + 예외 방어 래퍼. 항상 health 가 채워진 결과를 반환."""
        t0 = time.monotonic()
        try:
            result = self.collect()
        except Exception as exc:  # noqa: BLE001 — 수집기는 절대 파이프라인을 죽이면 안 됨
            return CollectResult(
                health=SourceHealth(
                    source=self.source, ok=False, fetched=0, failed=1,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    message=f"{type(exc).__name__}: {exc}"[:300],
                )
            )
        if result.health is None:
            ok_n = sum(1 for q in result.quotes if q.ok)
            fail_n = sum(1 for q in result.quotes if not q.ok)
            result.health = SourceHealth(
                source=self.source,
                ok=fail_n == 0,
                fetched=ok_n + len(result.news) + len(result.events),
                failed=fail_n,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        else:
            result.health.latency_ms = int((time.monotonic() - t0) * 1000)
        return result


# 지연 임포트로 순환참조 방지
def _build_all() -> list[Collector]:
    from .markets import MarketsCollector
    from .fred import FredCollector
    from .ecos import EcosCollector
    from .cnn_fng import CnnFngCollector
    from .news import NewsCollector
    from .calendar import CalendarCollector

    return [
        MarketsCollector(),
        FredCollector(),
        EcosCollector(),
        CnnFngCollector(),
        NewsCollector(),
        CalendarCollector(),
    ]


ALL_COLLECTORS: list[Collector] = _build_all()
