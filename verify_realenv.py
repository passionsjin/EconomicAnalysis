"""실환경(실제 네트워크) 검증 스크립트.

각 수집기를 실제 네트워크로 1회 실행 → 소스별 상태/표본/현실성(일간 등락률) 점검.
결과를 콘솔 + verify_report.json 로 출력. DB 는 건드리지 않음(수집기 run()만 호출).
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone

# UTF-8 콘솔
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.collectors.base import ALL_COLLECTORS
from app.config import INDICATOR_BY_KEY, settings


def daily_returns(history):
    """history=[(date,val)] → 인접일 등락률(%) 리스트."""
    out = []
    for (d0, v0), (d1, v1) in zip(history, history[1:]):
        if v0 not in (None, 0) and v1 is not None:
            out.append((v1 / v0 - 1.0) * 100.0)
    return out


def main():
    print("=" * 70)
    print("  실환경 검증 — 실제 네트워크로 모든 수집기 실행")
    print(f"  FRED_API_KEY: {'설정됨' if settings.fred_api_key else '없음(CSV 폴백)'}")
    print(f"  ECOS_API_KEY: {'설정됨' if settings.ecos_api_key else '없음(비활성)'}")
    print("=" * 70)

    report = {"generated": datetime.now(timezone.utc).isoformat(), "sources": []}

    for collector in ALL_COLLECTORS:
        t0 = time.monotonic()
        result = collector.run()
        elapsed = time.monotonic() - t0
        h = result.health
        src = {
            "source": collector.source,
            "label": collector.label,
            "ok": h.ok,
            "fetched": h.fetched,
            "failed": h.failed,
            "latency_ms": h.latency_ms,
            "elapsed_s": round(elapsed, 1),
            "message": h.message,
            "quotes": [],
            "n_news": len(result.news),
            "n_events": len(result.events),
        }
        print(f"\n[{collector.source}] {collector.label}")
        print(f"  상태: {'OK' if h.ok else 'FAIL'}  fetched={h.fetched} failed={h.failed} "
              f"{elapsed:.1f}s")
        print(f"  메시지: {h.message}")

        # 지표 표본
        for q in result.quotes:
            ind = INDICATOR_BY_KEY.get(q.key)
            label = ind.label if ind else q.key
            rets = daily_returns(q.history) if q.history else []
            qrow = {
                "key": q.key, "label": label, "ok": q.ok,
                "value": q.value, "prev_close": q.prev_close,
                "change_pct": round(q.change_pct, 3) if q.change_pct is not None else None,
                "as_of": q.as_of, "n_hist": len(q.history),
                "error": q.error,
            }
            if rets:
                qrow["ret_mean_abs"] = round(statistics.fmean(abs(r) for r in rets), 3)
                qrow["ret_max_abs"] = round(max(abs(r) for r in rets), 3)
                qrow["ret_stdev"] = round(statistics.pstdev(rets), 3) if len(rets) > 1 else 0.0
            src["quotes"].append(qrow)
            if q.ok:
                flag = ""
                # 현실성 플래그: 일간 |등락| 최대가 너무 크면 의심
                if rets and max(abs(r) for r in rets) > 6 and ind and ind.category == "equity":
                    flag = "  ⚠ 일간변동 과대(의심)"
                chg = f"{q.change_pct:+.2f}%" if q.change_pct is not None else "—"
                mx = f" maxΔ={max(abs(r) for r in rets):.1f}%" if rets else ""
                print(f"    ✓ {label:14s} {q.value!s:>14}  ({chg}) hist={len(q.history)}{mx}{flag}")
            else:
                print(f"    ✗ {label:14s} ERROR: {q.error}")

        if result.news:
            print(f"  뉴스 {len(result.news)}건 (예: {result.news[0].title[:50]!r})")
        if result.events:
            hi = [e for e in result.events if (e.impact or '').lower() == 'high']
            print(f"  캘린더 {len(result.events)}건 (High {len(hi)}건)")

        report["sources"].append(src)

    # 요약
    print("\n" + "=" * 70)
    print("  요약")
    print("=" * 70)
    total_ok = total_fail = 0
    for s in report["sources"]:
        total_ok += s["fetched"]
        total_fail += s["failed"]
        badge = "🟢" if s["ok"] else ("🟡" if s["fetched"] > 0 else "🔴")
        extra = ""
        if s["n_news"]:
            extra = f" 뉴스{s['n_news']}"
        if s["n_events"]:
            extra += f" 캘린더{s['n_events']}"
        print(f"  {badge} {s['label']:18s} fetched={s['fetched']:3d} failed={s['failed']:3d} "
              f"{s['elapsed_s']:5.1f}s{extra}")
    print(f"\n  총 지표 OK={total_ok} FAIL={total_fail}")

    with open("verify_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("  → verify_report.json 저장")


if __name__ == "__main__":
    main()
