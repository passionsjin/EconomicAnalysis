"""경제지표 발표 캘린더 수집기 — ForexFactory 주간 JSON (키 불필요).

소스: https://nfs.faireconomy.media/ff_calendar_thisweek.json
도달 불가하거나 형식이 바뀌면 '비활성'으로 graceful degrade.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import CalendarEvent, CollectResult, SourceHealth
from . import http
from .base import Collector

_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def _to_utc_iso(raw_date: str) -> str | None:
    if not raw_date:
        return None
    s = raw_date.strip().replace("Z", "+00:00")  # 'Z' 변형 피드 대비 정규화
    # 예: "2026-06-12T08:30:00-04:00" (ForexFactory 는 항상 오프셋 포함)
    try:
        dt = datetime.fromisoformat(s)
        # 오프셋이 없으면 UTC 로 간주(현 피드는 오프셋 포함이라 통상 도달 안 함)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


class CalendarCollector(Collector):
    source = "calendar"
    label = "경제 캘린더"

    def collect(self) -> CollectResult:
        try:
            data = http.get_json(_URL, retries=1)
        except Exception as exc:  # noqa: BLE001
            return CollectResult(events=[], health=SourceHealth(
                source=self.source, ok=False, fetched=0, failed=1,
                message=f"캘린더 소스 도달 불가: {str(exc)[:120]}",
            ))

        if not isinstance(data, list):
            return CollectResult(events=[], health=SourceHealth(
                source=self.source, ok=False, fetched=0, failed=1,
                message="예상치 못한 응답 형식",
            ))

        events: list[CalendarEvent] = []
        for it in data:
            try:
                impact = str(it.get("impact", "")).strip()
                # High/Medium 중심으로 노이즈 축소
                if impact.lower() == "low" or impact.lower() == "holiday":
                    continue
                events.append(CalendarEvent(
                    title=str(it.get("title", "")).strip(),
                    country=str(it.get("country", "")).strip(),
                    date=_to_utc_iso(str(it.get("date", ""))),
                    impact=impact,
                    actual=str(it.get("actual", "") or ""),
                    forecast=str(it.get("forecast", "") or ""),
                    previous=str(it.get("previous", "") or ""),
                ))
            except Exception:  # noqa: BLE001
                continue

        return CollectResult(events=events, health=SourceHealth(
            source=self.source, ok=True, fetched=len(events), failed=0,
            message=f"이벤트 {len(events)}건(High/Medium)",
        ))
