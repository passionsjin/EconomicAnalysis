"""미국 거시지표 수집기 — FRED.

우선순위: (1) FRED_API_KEY 가 있으면 공식 JSON API → (2) 없으면 키 없는 CSV 엔드포인트.
둘 다 도달 불가하면 각 지표를 ok=False 로 두고 소스를 비정상으로 표시(앱은 계속 동작).
transform="yoy" 인 지수 시리즈는 전년동월대비 등락률(%)로 변환.
"""
from __future__ import annotations

import csv
import io
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from ..config import indicators_for_source, settings
from ..models import CollectResult, Quote, SourceHealth
from . import http
from .base import Collector

_API = "https://api.stlouisfed.org/fred/series/observations"
_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# FRED 가 네트워크에서 막히면 수집 전체가 지연되지 않도록 짧은 타임아웃.
# API 경로는 1회 재시도: 정각 혼잡 때 21개 중 2~9개가 타임아웃으로 떨어지는데
# 몇 분 뒤 재수집은 1~2초에 전량 성공한다(일시 혼잡). CSV 경로는 '차단'이 흔해 무재시도.
_TIMEOUT = min(settings.request_timeout, 12)
_API_RETRIES = 1


def _num(v) -> Optional[float]:
    """결측('.', '', NaN)·비수치를 None 으로. JSON/CSV 경로 동작 일치."""
    if v in (None, ".", "", "NaN", "nan"):
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _fetch_series(series_id: str) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, value)] 오름차순. 결측은 제외."""
    if settings.fred_api_key:
        data = http.get_json(_API, timeout=_TIMEOUT, retries=_API_RETRIES, params={
            "series_id": series_id,
            "api_key": settings.fred_api_key,
            "file_type": "json",
        })
        out = []
        for obs in data.get("observations", []):
            f = _num(obs.get("value"))
            if f is not None:
                out.append((obs["date"], f))
        return out

    # 키 없는 CSV 폴백
    text = http.get_text(_CSV, params={"id": series_id}, timeout=_TIMEOUT, retries=0)
    out = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # 헤더 ["observation_date" or "DATE", series_id]
    for row in reader:
        if len(row) < 2:
            continue
        f = _num(row[1])
        if f is not None:
            out.append((row[0], f))
    return out


def _yoy(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """월별 지수 → 전년동월대비 %. date(YYYY-MM) 기준 12개월 전과 비교."""
    by_month = {d[:7]: (d, v) for d, v in series}
    out: list[tuple[str, float]] = []
    for ym, (d, v) in sorted(by_month.items()):
        y, m = int(ym[:4]), int(ym[5:7])
        prev_ym = f"{y - 1:04d}-{m:02d}"
        prev_v = by_month.get(prev_ym, (None, None))[1]
        if prev_v is not None and prev_v != 0:  # 0 기준선(yoy 미정의)·결측만 제외
            out.append((d, (v / prev_v - 1.0) * 100.0))
    return out


class FredCollector(Collector):
    source = "fred"
    label = "미국 거시(FRED)"

    def collect(self) -> CollectResult:
        inds = indicators_for_source("fred")
        quotes: list[Quote] = []
        failed = 0
        first_error = ""

        def _one(ind):
            series = _fetch_series(ind.symbol)
            if ind.transform == "yoy":
                series = _yoy(series)
            if not series:
                raise ValueError("데이터 없음")
            series = series[-settings.history_points:]  # 다년 보관(일별≈5년/월별 다년) — 백분위 룩백 확보
            if ind.scale != 1.0:                         # 단위 환산(예: 백만$→조$)
                series = [(d, v * ind.scale) for d, v in series]
            value = series[-1][1]
            prev = series[-2][1] if len(series) >= 2 else None
            as_of = series[-1][0]
            try:
                as_of = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
            return Quote(key=ind.key, value=value, prev_close=prev, as_of=as_of,
                         history=series, ok=True)

        # 시리즈를 병렬로 — 차단 시에도 전체 지연을 한 타임아웃 창으로 제한
        with ThreadPoolExecutor(max_workers=min(6, len(inds) or 1)) as ex:
            futures = {ex.submit(_one, ind): ind for ind in inds}
            for fut, ind in futures.items():
                try:
                    quotes.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    first_error = first_error or f"{ind.symbol}: {exc}"
                    quotes.append(Quote(key=ind.key, ok=False, error=str(exc)[:200]))

        mode = "API키" if settings.fred_api_key else "키없는 CSV"
        if failed == 0:
            msg = f"정상 ({mode})"
        elif failed == len(inds):
            if not settings.fred_api_key:
                # fred.stlouisfed.org(CSV) 가 일부 망에서 차단되는 사례. API 호스트
                # (api.stlouisfed.org)는 보통 도달되므로 무료 키 설정이 확실한 해결책.
                msg = (f"전체 실패 — fred.stlouisfed.org(CSV) 도달 불가. "
                       f"무료 FRED_API_KEY 설정 시 api.stlouisfed.org 경유로 해결. {first_error}")[:200]
            else:
                msg = f"전체 실패 — FRED API 도달 불가(API키). {first_error}"[:200]
        else:
            msg = f"{failed}/{len(inds)} 실패 ({mode})"
        health = SourceHealth(self.source, ok=failed == 0, fetched=len(inds) - failed,
                              failed=failed, message=msg)
        return CollectResult(quotes=quotes, health=health)
