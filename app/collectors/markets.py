"""시장 데이터 수집기 — Yahoo Finance chart API (키 불필요).

지수·환율·금리·원자재·암호화폐를 병렬로 가져온다.
query1/query2 호스트 로테이션 + UA 로 안티봇 회피, 실패 종목만 ok=False 처리.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from ..config import indicators_for_source, settings
from ..models import CollectResult, Quote, SourceHealth
from . import http
from .base import Collector

_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]


def _range_for(points: int) -> str:
    """저장 목표 포인트 수 → Yahoo chart range(일봉 기준 근사).

    백분위·z-score 룩백을 넉넉히 확보하려고 기본은 5년치를 받는다.
    (1y≈250 거래일, 2y≈500, 5y≈1250)
    """
    if points > 1200:
        return "5y"
    if points > 480:
        return "2y"
    if points > 240:
        return "1y"
    return "6mo"


def _fetch_symbol(symbol: str, rng: str) -> dict:
    """한 종목의 chart JSON 을 가져와 정규화. 실패 시 예외."""
    from urllib.parse import quote

    path = f"/v8/finance/chart/{quote(symbol, safe='')}"
    last_exc: Exception | None = None
    for host in _HOSTS:
        try:
            data = http.get_json(
                host + path,
                params={"range": rng, "interval": "1d", "includePrePost": "false"},
                retries=1,
            )
            result = (data.get("chart") or {}).get("result") or []
            if not result:
                err = (data.get("chart") or {}).get("error")
                raise ValueError(f"빈 응답 {err}")
            return result[0]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise last_exc  # type: ignore[misc]


def _local_date(ts: int, gmtoffset: int) -> str:
    """epoch -> 거래소 로컬 기준 날짜.

    Yahoo 일봉 타임스탬프는 '거래소 로컬 자정 또는 장중 시각'이라 UTC 날짜로 찍으면 어긋난다.
    특히 `=X`(환율)는 거래소가 Europe/London 이라 서머타임(gmtoffset=3600) 동안 일봉이
    '전날 23:00 UTC' 로 오고, 그대로 저장하면 이력 전체가 하루씩 밀린다(일요일 행 발생).
    meta.gmtoffset 을 더해 로컬 날짜로 되돌린다(오프셋 0 인 소스는 종전과 동일).
    """
    return datetime.fromtimestamp(ts + gmtoffset, tz=timezone.utc).strftime("%Y-%m-%d")


def _parse(symbol: str, raw: dict) -> tuple[float | None, float | None, str | None, list[tuple[str, float]]]:
    meta = raw.get("meta") or {}
    price = meta.get("regularMarketPrice")
    try:
        gmtoffset = int(meta.get("gmtoffset") or 0)
    except (TypeError, ValueError):
        gmtoffset = 0

    ts_market = meta.get("regularMarketTime")
    as_of = None
    mkt_date = None
    if ts_market:
        # as_of 는 시점이라 UTC 그대로, 날짜 비교용 mkt_date 만 로컬로 환산한다.
        as_of = datetime.fromtimestamp(int(ts_market), tz=timezone.utc).isoformat()
        mkt_date = _local_date(int(ts_market), gmtoffset)

    history: list[tuple[str, float]] = []
    timestamps = raw.get("timestamp") or []
    quote_block = ((raw.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_block.get("close") or []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        history.append((_local_date(int(ts), gmtoffset), float(close)))

    if price is None and history:
        price = history[-1][1]

    # 직전 거래일 종가 결정.
    # range=1y 의 meta.chartPreviousClose 는 1년 전 값이라 쓰지 않는다.
    # 일별 이력의 마지막 항목이 '오늘' 봉이면 직전 종가는 그 전날(history[-2]),
    # 오늘 봉이 아직 없으면 마지막 항목이 곧 직전 종가다.
    prev = None
    if history:
        last_date = history[-1][0]
        if mkt_date and last_date < mkt_date:
            prev = history[-1][1]
        elif len(history) >= 2:
            prev = history[-2][1]
    # meta.previousClose 가 명시돼 있으면 우선 신뢰
    if meta.get("previousClose") is not None:
        prev = meta.get("previousClose")

    return price, prev, as_of, history


class MarketsCollector(Collector):
    source = "yahoo"
    label = "시장(Yahoo)"

    def collect(self) -> CollectResult:
        inds = indicators_for_source("yahoo")
        quotes: list[Quote] = []
        failed = 0

        rng = _range_for(settings.history_points)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_fetch_symbol, ind.symbol, rng): ind for ind in inds}
            for fut in as_completed(futures):
                ind = futures[fut]
                try:
                    raw = fut.result()
                    price, prev, as_of, hist = _parse(ind.symbol, raw)
                    if price is None:
                        raise ValueError("가격 없음")
                    quotes.append(Quote(
                        key=ind.key, value=price, prev_close=prev, as_of=as_of,
                        history=hist[-settings.history_points:], ok=True,
                    ))
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    quotes.append(Quote(key=ind.key, ok=False, error=str(exc)[:200]))

        quotes.sort(key=lambda q: q.key)
        health = SourceHealth(
            source=self.source, ok=failed == 0, fetched=len(inds) - failed, failed=failed,
            message="정상" if failed == 0 else f"{failed}/{len(inds)} 종목 실패",
        )
        return CollectResult(quotes=quotes, health=health)
