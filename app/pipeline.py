"""수집 파이프라인 + 스케줄러.

run_collection(): 모든 수집기 실행 → DB 저장 → LLM 브리핑 생성 → 저장 → 정리.
중복 실행을 막는 락과, API/스케줄러가 공유하는 상태(get_status)를 제공.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import repository as repo
from .analysis import briefing as briefing_mod
from .collectors.base import ALL_COLLECTORS
from .config import settings
from .logging_setup import logger
from .models import CalendarEvent, NewsItem, Quote

KST = timezone(timedelta(hours=9))

_lock = threading.Lock()          # 수집 중복 실행 방지(권위 있는 가드)
_status_lock = threading.Lock()   # _status 공유 dict 접근 보호
_status: dict = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_skipped": None,
    "last_error": None,
    "last_snapshot_id": None,
    "last_ok": None,
    "last_fail": None,
}
_scheduler: BackgroundScheduler | None = None


def _set_status(**kw) -> None:
    with _status_lock:
        _status.update(kw)


def get_status() -> dict:
    with _status_lock:
        return dict(_status)


def _collect_body() -> dict:
    """락을 이미 보유한 상태에서 수집 본체를 실행."""
    started = datetime.now(timezone.utc)
    _set_status(running=True, last_started=started.isoformat(), last_error=None)
    t0 = time.monotonic()
    try:
        result = _do_run(started)
        if "error" not in result:
            brief = "OK" if result.get("briefing_ok") else f"실패({result.get('briefing_error')})"
            logger.info("수집 완료 - snapshot #%s | ok=%s fail=%s | 브리핑 %s | %.1fs",
                        result.get("snapshot_id"), result.get("ok"), result.get("fail"),
                        brief, time.monotonic() - t0)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("수집 실패: %s", exc)
        _set_status(last_error=f"{type(exc).__name__}: {exc}")
        return {"error": str(exc)}
    finally:
        _set_status(running=False, last_finished=datetime.now(timezone.utc).isoformat())


def run_collection() -> dict:
    """전체 수집 1회 실행(동기, 스케줄러 진입점). 락이 잡혀 있으면 스킵."""
    if not _lock.acquire(blocking=False):
        _set_status(last_skipped=datetime.now(timezone.utc).isoformat())
        logger.info("수집 스킵 — 이미 진행 중(스케줄러)")
        return {"skipped": True, "reason": "이미 수집이 진행 중"}
    try:
        return _collect_body()
    finally:
        _lock.release()


def _do_run(started: datetime) -> dict:
    ts_utc = started.isoformat()
    snapshot_id = repo.create_snapshot(ts_utc)
    logger.info("수집 시작 - snapshot #%d (수집기 %d개)", snapshot_id, len(ALL_COLLECTORS))
    try:
        return _do_run_inner(started, ts_utc, snapshot_id)
    except Exception:
        # 미완성(finished_utc=NULL) 고스트 스냅샷 + 부분 자식행 정리
        try:
            repo.delete_snapshot(snapshot_id)
        except Exception:  # noqa: BLE001
            pass
        raise


def _do_run_inner(started: datetime, ts_utc: str, snapshot_id: int) -> dict:
    # 1) 모든 수집기 병렬 실행 (각 run() 은 예외를 던지지 않음)
    with ThreadPoolExecutor(max_workers=max(2, len(ALL_COLLECTORS))) as ex:
        results = list(ex.map(lambda c: c.run(), ALL_COLLECTORS))

    quotes: dict[str, Quote] = {}
    all_quotes: list[Quote] = []
    news: list[NewsItem] = []
    events: list[CalendarEvent] = []
    healths = []
    for r in results:
        for q in r.quotes:
            quotes[q.key] = q
            all_quotes.append(q)
        news.extend(r.news)
        events.extend(r.events)
        if r.health:
            healths.append(r.health)

    ok_count = sum(1 for q in all_quotes if q.ok)
    fail_count = sum(1 for q in all_quotes if not q.ok)

    for h in sorted(healths, key=lambda x: x.source):
        logger.info("  - %-9s %-4s fetched=%d failed=%d %dms %s",
                    h.source, "OK" if h.ok else "FAIL",
                    h.fetched, h.failed, h.latency_ms, (h.message or "")[:60])

    # 2) 저장
    repo.save_observations(snapshot_id, all_quotes)
    repo.upsert_history(all_quotes)
    repo.save_health(snapshot_id, healths)
    repo.upsert_news(news, ts_utc)
    repo.upsert_calendar(events)
    repo.finish_snapshot(snapshot_id, datetime.now(timezone.utc).isoformat(), ok_count, fail_count)

    # 3) LLM 브리핑 (실패해도 무방)
    now_kst = started.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    logger.info("  - briefing 생성 중(claude -p)...")
    brief = briefing_mod.generate(quotes, news, events, now_kst)
    if brief.ok:
        logger.info("  - briefing OK: %s / %s", brief.model, brief.sentiment)
    else:
        logger.warning("  - briefing 실패: %s", brief.error)
    repo.save_briefing(snapshot_id, datetime.now(timezone.utc).isoformat(), brief)

    # 4) 정리
    try:
        repo.prune(settings.history_days)
    except Exception:  # noqa: BLE001
        pass

    _set_status(last_snapshot_id=snapshot_id, last_ok=ok_count, last_fail=fail_count,
                last_error=None if brief.ok else f"briefing: {brief.error}")
    return {
        "snapshot_id": snapshot_id,
        "ok": ok_count,
        "fail": fail_count,
        "briefing_ok": brief.ok,
        "briefing_error": brief.error,
    }


def trigger_async() -> bool:
    """API/스타트업에서 비동기 수집 시작. 실제로 시작됐으면 True, 이미 진행 중이면 False.

    요청 스레드에서 동기적으로 락을 잡아 TOCTOU 없이 정확한 시작 여부를 반환하고,
    워커 스레드가 본체 실행 후 락을 해제한다.
    """
    if not _lock.acquire(blocking=False):
        _set_status(last_skipped=datetime.now(timezone.utc).isoformat())
        logger.info("수집 요청 스킵 — 이미 진행 중")
        return False

    def _runner():
        try:
            _collect_body()
        finally:
            _lock.release()

    threading.Thread(target=_runner, name="manual-collect", daemon=True).start()
    return True


# ─────────────────────────── 스케줄러 ───────────────────────────

def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    sched = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1})
    try:
        trigger = CronTrigger(minute=settings.collect_minute, timezone="UTC")
    except (ValueError, TypeError) as exc:  # 잘못된 분 스펙이라도 기동은 보장
        _set_status(last_error=f"COLLECT_MINUTE 무효({settings.collect_minute!r}) → 매시 정각 폴백: {exc}")
        logger.warning("COLLECT_MINUTE 무효(%r) → 매시 정각(0분) 폴백", settings.collect_minute)
        trigger = CronTrigger(minute="0", timezone="UTC")
    sched.add_job(
        run_collection,
        trigger,
        id="hourly-collect",
        replace_existing=True,
    )
    sched.start()
    _scheduler = sched
    logger.info("스케줄러 시작 - 매시 %s분(UTC) 자동수집", settings.collect_minute)
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
