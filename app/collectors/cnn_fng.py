"""CNN 공포·탐욕 지수(Fear & Greed) 수집기.

CNN 대시보드가 실제로 쓰는 비공식 JSON 엔드포인트를 그대로 호출한다. 키는 필요 없지만
**브라우저 UA + Referer 를 모두 보내야 한다**(UA 만으로는 418 Teapot 으로 막힌다).
호출 1회에 종합점수 + 하위 9계열이 함께 오므로 지표별 병렬 호출이 필요 없다.

값의 성격이 계열마다 다르다:
  - fear_and_greed_historical → 0~100 종합 점수 시계열
  - 나머지 9계열의 data      → **원시값**(풋/콜 비율 0.74, VIX 14.32, S&P500 7747 …)
각 구성요소의 0~100 환산점수는 최상위 `score` 필드에 **현재값 하나만** 있고 시계열이 없다.
그래서 점수는 INDICATORS 에 등록하지 않고 `cnnfg_score_*` 키의 Quote 로만 내보낸다 —
화면 카드(INDICATORS 순회)에는 안 뜨고 history/observations 에는 쌓여 패널이 읽어 쓴다.

**이력 꼬리는 확정값이 아니다.** CNN 은 장중 실시간 값을 마지막 1~2개 *날짜 슬롯에 덧씌우고*
장 마감 후 확정값으로 되돌린다(실측: 09-04 장중에 09-03 슬롯이 35.26 -> 44.77 로 튀었다가
나중에 43.91 로 확정). 그래서 블록의 `timestamp` 날짜(=아직 확정 전인 슬롯) 이전의 마지막
점을 직전값으로 삼는다 - `_live_date()` + `_quote_from_series(live_date=...)`.

**`previous_close` 는 쓰지 않는다.** 자기 이력과 어긋난다 - 실측(2026-09-08): 블록이
previous_close=35.23 을 주는데 같은 응답의 09-03 확정값은 43.91 이었다. 이걸 믿으면
41.86 대비 +18.8% 로 표시되지만 실제는 -4.7% 다.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from ..config import indicators_for_source, settings
from ..models import CollectResult, Quote, SourceHealth
from . import http
from .base import Collector

_BASE = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

# UA 는 http.session() 기본값(브라우저 UA)을 쓰고 Referer 만 얹는다. 둘 중 하나라도 빠지면 418.
_HEADERS = {"Referer": "https://edition.cnn.com/", "Accept": "application/json, text/plain, */*"}

_TIMEOUT = min(settings.request_timeout, 15)

# CNN 응답 키 → 이 프로젝트 지표 key. 값은 (지표key, 점수key).
# 점수key 가 None 이면 그 계열의 환산점수는 다른 계열과 중복이라 저장하지 않는다
# (momentum_sp500/sp125 가 같은 점수, vix/vix_50 이 같은 점수 — 실측 확인).
_SERIES: dict[str, tuple[str, Optional[str]]] = {
    "fear_and_greed_historical": ("cnn_fng", "cnnfg_score_total"),
    "market_momentum_sp500":     ("cnn_fng_momentum", "cnnfg_score_momentum"),
    "market_momentum_sp125":     ("cnn_fng_momentum_ma", None),
    "stock_price_strength":      ("cnn_fng_strength", "cnnfg_score_strength"),
    "stock_price_breadth":       ("cnn_fng_breadth", "cnnfg_score_breadth"),
    "put_call_options":          ("cnn_fng_putcall", "cnnfg_score_putcall"),
    "market_volatility_vix":     ("cnn_fng_vix", "cnnfg_score_vix"),
    "market_volatility_vix_50":  ("cnn_fng_vix_ma", None),
    "junk_bond_demand":          ("cnn_fng_junk", "cnnfg_score_junk"),
    "safe_haven_demand":         ("cnn_fng_safehaven", "cnnfg_score_safehaven"),
}

# 지표 key -> CNN 응답 key 역인덱스(계열별 timestamp 를 찾기 위해)
_CNN_KEY_BY_IND: dict[str, str] = {ind: cnn for cnn, (ind, _s) in _SERIES.items()}


def _num(v) -> Optional[float]:
    """비수치·NaN·무한대를 None 으로."""
    if v is None:
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _to_date(ms) -> Optional[str]:
    """ms epoch → UTC 'YYYY-MM-DD'. 하위계열은 자정이 아닌 시각으로 오므로 날짜로 접는다."""
    f = _num(ms)
    if f is None:
        return None
    try:
        return datetime.fromtimestamp(f / 1000.0, timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return None


def _parse(payload: dict) -> dict[str, list[tuple[str, float]]]:
    """CNN 응답 → {지표key: [(YYYY-MM-DD, value), ...] 오름차순}.

    같은 날짜가 두 번 오는 경우(자정 확정값 + 장중 갱신값)는 **뒤 값이 이긴다**.
    접지 않으면 마지막 두 점이 같은 날이라 prev_close 가 '전일'이 아니라
    '같은 날 자정값'이 되어 변화율이 항상 0으로 굳는다.
    """
    out: dict[str, list[tuple[str, float]]] = {}
    for cnn_key, (ind_key, _score_key) in _SERIES.items():
        block = payload.get(cnn_key)
        if not isinstance(block, dict):
            continue
        folded: dict[str, float] = {}
        for point in block.get("data") or []:
            if not isinstance(point, dict):
                continue
            date = _to_date(point.get("x"))
            value = _num(point.get("y"))
            if date is None or value is None:
                continue
            folded[date] = value          # 뒤 값 우선
        out[ind_key] = sorted(folded.items())
    return out


def _scores(payload: dict) -> dict[str, float]:
    """CNN 응답 → {점수key: 0~100 현재 점수}. 시계열은 없고 오늘 값 하나뿐."""
    out: dict[str, float] = {}
    for cnn_key, (_ind_key, score_key) in _SERIES.items():
        if score_key is None:
            continue
        block = payload.get(cnn_key)
        if not isinstance(block, dict):
            continue
        score = _num(block.get("score"))
        if score is not None:
            out[score_key] = score
    return out


def _live_date(block: dict | None) -> Optional[str]:
    """블록 `timestamp`(마지막 갱신 시각)의 UTC 날짜 = **아직 확정 전인 슬롯의 날짜**.

    종합 블록은 ISO 문자열("2026-09-04T23:59:43+00:00"), 하위 계열은 ms epoch 로 준다.
    """
    ts = (block or {}).get("timestamp")
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return _to_date(ts)


def _quote_from_series(key: str, series: list[tuple[str, float]],
                       live_date: Optional[str] = None) -> Quote:
    """이력 마지막이 현재값, 그 앞이 직전값. fred/ecos 와 같은 규약.

    단 마지막 점이 `live_date`(장중 갱신 중인 슬롯)면 그 날짜 **이전의 확정된 마지막 점**을
    직전값으로 쓴다. 확정 구간이 통째로 오염된 드문 경우엔 value==prev 가 되어 변화 0 이
    되는데, 큰 폭의 거짓 변화율보다 그쪽이 낫다.
    """
    series = series[-settings.history_points:]
    value_date, value = series[-1]
    if live_date is not None and value_date >= live_date:
        settled = [v for d, v in series if d < live_date]
        prev = settled[-1] if settled else None
    else:
        prev = series[-2][1] if len(series) >= 2 else None
    as_of = value_date
    try:
        as_of = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    return Quote(key=key, value=value, prev_close=prev, as_of=as_of, history=series, ok=True)


def fetch(start: str | None = None) -> dict:
    """CNN 원본 JSON. start='YYYY-MM-DD' 를 주면 그 날부터(백필용, 2021-01-04 이전은 500)."""
    url = f"{_BASE}/{start}" if start else _BASE
    return http.get_json(url, headers=_HEADERS, timeout=_TIMEOUT, retries=1)


class CnnFngCollector(Collector):
    source = "cnn"
    label = "CNN 공포·탐욕"

    def collect(self) -> CollectResult:
        inds = indicators_for_source("cnn")
        quotes: list[Quote] = []

        try:
            payload = fetch()
        except Exception as exc:  # noqa: BLE001 — collect() 는 예외를 던지지 않는다
            msg = f"전체 실패 — CNN dataviz 도달 불가. {type(exc).__name__}: {exc}"[:200]
            for ind in inds:
                quotes.append(Quote(key=ind.key, ok=False, error=str(exc)[:200]))
            return CollectResult(quotes=quotes, health=SourceHealth(
                self.source, ok=False, fetched=0, failed=len(inds), message=msg))

        series_by_key = _parse(payload)
        failed = 0
        first_error = ""
        for ind in inds:
            series = series_by_key.get(ind.key) or []
            if not series:
                failed += 1
                first_error = first_error or f"{ind.key}: 데이터 없음"
                quotes.append(Quote(key=ind.key, ok=False, error="데이터 없음"))
                continue
            cnn_key = _CNN_KEY_BY_IND[ind.key]
            quotes.append(_quote_from_series(
                ind.key, series, _live_date(payload.get(cnn_key))))

        # 구성요소 환산점수(0~100) — INDICATORS 미등록 키라 카드로는 안 뜨고 이력만 쌓인다.
        # 시계열이 없으므로 오늘 한 점만 기록 → 수집이 반복되며 점수 시계열이 만들어진다.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for score_key, score in _scores(payload).items():
            quotes.append(Quote(key=score_key, value=score, prev_close=None,
                                as_of=f"{today}T00:00:00+00:00",
                                history=[(today, score)], ok=True))

        if failed == 0:
            msg = f"정상 ({len(inds)}계열)"
        elif failed == len(inds):
            msg = f"전체 실패 — 응답 형식 변경 의심. {first_error}"[:200]
        else:
            msg = f"{failed}/{len(inds)} 실패. {first_error}"[:200]
        health = SourceHealth(self.source, ok=failed == 0, fetched=len(inds) - failed,
                              failed=failed, message=msg)
        return CollectResult(quotes=quotes, health=health)
