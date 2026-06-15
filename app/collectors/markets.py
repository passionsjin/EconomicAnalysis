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


def _fetch_symbol(symbol: str, days: int) -> dict:
    """한 종목의 chart JSON 을 가져와 정규화. 실패 시 예외."""
    rng = "1y" if days > 180 else "6mo"
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


def _parse(symbol: str, raw: dict) -> tuple[float | None, float | None, str | None, list[tuple[str, float]]]:
    meta = raw.get("meta") or {}
    price = meta.get("regularMarketPrice")

    ts_market = meta.get("regularMarketTime")
    as_of = None
    mkt_date = None
    if ts_market:
        dt = datetime.fromtimestamp(int(ts_market), tz=timezone.utc)
        as_of = dt.isoformat()
        mkt_date = dt.strftime("%Y-%m-%d")

    history: list[tuple[str, float]] = []
    timestamps = raw.get("timestamp") or []
    quote_block = ((raw.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_block.get("close") or []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        history.append((d, float(close)))

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

        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_fetch_symbol, ind.symbol, settings.history_days): ind for ind in inds}
            for fut in as_completed(futures):
                ind = futures[fut]
                try:
                    raw = fut.result()
                    price, prev, as_of, hist = _parse(ind.symbol, raw)
                    if price is None:
                        raise ValueError("가격 없음")
                    quotes.append(Quote(
                        key=ind.key, value=price, prev_close=prev, as_of=as_of,
                        history=hist[-settings.history_days:], ok=True,
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
