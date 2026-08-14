"""한국 거시지표 수집기 — 한국은행 ECOS API.

ECOS_API_KEY 가 설정된 경우에만 활성. 미설정 시 '비활성' 상태로 표시(오류 아님).
transform="yoy" 지수는 전년동월대비 %로 변환.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import indicators_for_source, settings
from ..models import CollectResult, Quote, SourceHealth
from . import http
from .base import Collector

_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"


def _time_to_date(time_str: str, cycle: str) -> str:
    s = str(time_str)
    try:
        if cycle == "D" and len(s) == 8:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        if cycle in ("M",) and len(s) == 6:
            return f"{s[:4]}-{s[4:6]}-01"
        if cycle == "Q" and len(s) == 5:
            q = int(s[4]); month = (q - 1) * 3 + 1
            return f"{s[:4]}-{month:02d}-01"
        if cycle == "A" and len(s) >= 4:
            return f"{s[:4]}-01-01"
    except (ValueError, IndexError):
        pass
    return f"{s}-01-01"[:10]


def _yoy_monthly(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    by_month = {d[:7]: (d, v) for d, v in series}
    out = []
    for ym, (d, v) in sorted(by_month.items()):
        y, m = int(ym[:4]), int(ym[5:7])
        prev = f"{y - 1:04d}-{m:02d}"
        prev_v = by_month.get(prev, (None, None))[1]
        if prev_v is not None and prev_v != 0:
            out.append((d, (v / prev_v - 1.0) * 100.0))
    return out


class EcosCollector(Collector):
    source = "ecos"
    label = "한국 거시(ECOS)"

    def collect(self) -> CollectResult:
        inds = indicators_for_source("ecos")
        if not settings.ecos_api_key:
            return CollectResult(quotes=[], health=SourceHealth(
                source=self.source, ok=True, fetched=0, failed=0,
                message="ECOS_API_KEY 미설정 — 비활성 (.env 에 키 입력 시 활성화)",
            ))

        key = settings.ecos_api_key
        now = datetime.now(timezone.utc)
        quotes: list[Quote] = []
        failed = 0
        first_error = ""

        for ind in inds:
            try:
                if ind.ecos_cycle == "D":
                    # 일별은 5년치(≈1250 영업일) — 백분위 룩백 stat_window("D")=1260 을 채우기 위함.
                    # 행수가 1000을 넘으므로 페이지 크기도 함께 키운다.
                    start_t, end_t = f"{now.year - 5}0101", now.strftime("%Y%m%d")
                    rows_max = 5000
                else:  # 월별 기본 (3년치 → YoY 가능)
                    start_t, end_t = f"{now.year - 3}01", now.strftime("%Y%m")
                    rows_max = 1000

                url = (f"{_BASE}/{key}/json/kr/1/{rows_max}/"
                       f"{ind.symbol}/{ind.ecos_cycle}/{start_t}/{end_t}/{ind.ecos_item}")
                data = http.get_json(url)

                if "RESULT" in data:  # ECOS 오류 응답
                    raise ValueError(data["RESULT"].get("MESSAGE", "ECOS 오류"))
                rows = (data.get("StatisticSearch") or {}).get("row") or []
                series: list[tuple[str, float]] = []
                for r in rows:
                    v = r.get("DATA_VALUE")
                    if v in (None, "", "-"):
                        continue
                    try:
                        series.append((_time_to_date(r.get("TIME", ""), ind.ecos_cycle), float(v)))
                    except ValueError:
                        continue
                series.sort()
                if ind.transform == "yoy":
                    series = _yoy_monthly(series)
                if not series:
                    raise ValueError("데이터 없음")

                value = series[-1][1]
                prev = series[-2][1] if len(series) >= 2 else None
                quotes.append(Quote(
                    key=ind.key, value=value, prev_close=prev,
                    as_of=series[-1][0] + "T00:00:00+00:00",
                    history=series[-settings.history_points:], ok=True,
                ))
            except Exception as exc:  # noqa: BLE001
                failed += 1
                first_error = first_error or f"{ind.symbol}: {exc}"
                quotes.append(Quote(key=ind.key, ok=False, error=str(exc)[:200]))

        msg = "정상" if failed == 0 else f"{failed}/{len(inds)} 실패 — {first_error}"[:200]
        return CollectResult(quotes=quotes, health=SourceHealth(
            source=self.source, ok=failed == 0, fetched=len(inds) - failed,
            failed=failed, message=msg,
        ))
