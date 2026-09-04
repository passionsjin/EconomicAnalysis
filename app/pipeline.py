"""수집 파이프라인 + 스케줄러.

run_collection(): 모든 수집기 실행 → DB 저장 → LLM 브리핑 생성 → 저장 → 정리.
중복 실행을 막는 락과, API/스케줄러가 공유하는 상태(get_status)를 제공.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import repository as repo
from .analysis import briefing as briefing_mod
from .analysis import hyundai as hyundai_mod
from .analysis import shinhan as shinhan_mod
from .analysis import llm as llm_mod
from .analysis import regime as regime_mod
from .analysis import translate as translate_mod
from .collectors.base import ALL_COLLECTORS
from .config import settings, INDICATORS, INDICATOR_BY_KEY
from .logging_setup import logger
from .models import CalendarEvent, NewsItem, Quote

KST = timezone(timedelta(hours=9))

# 장애 에스컬레이션: 연속 실패가 이 횟수에 도달할 때만 이벤트 기록(스팸 방지)
_ESCALATE_AT = {1, 3, 6, 12, 24}
# 직전 브리핑 대비 변화를 LLM 에 전달할 핵심 지표
_DELTA_KEYS = ["sp500", "nasdaq", "kospi", "vix", "us10y", "us_real10y",
               "dxy", "usdkrw", "gold", "wti", "btc", "us_hy_spread"]


# 수집 실패 시 DB 직전값으로 메울 수 있는 최대 경과일(빈도별).
# 간헐 장애(FRED 타임아웃)는 덮되, 죽은 시리즈가 옛 값으로 영원히 살아있는 것처럼
# 보이지 않게 상한을 둔다. 월별은 '기간일자 + 발표지연'이라 넉넉히 잡는다.
_FALLBACK_MAX_AGE_DAYS = {"D": 10, "W": 30, "M": 120}


def _apply_fallbacks(quotes: dict[str, Quote], lookup, today=None) -> list[str]:
    """수집 실패한 지표를 저장된 직전값으로 대체(제자리 수정). 대체한 키 목록 반환.

    lookup(key) -> [(YYYY-MM-DD, value), ...] 오름차순 이력.
    파생지표는 대상에서 제외한다 — 피연산이 복구되면 _compute_derived 가 다시 계산하므로,
    직접 메우면 옛 값이 고착된다. 원 실패 사유(error)는 남겨 두고 fallback=True 로 표시.
    """
    today = today or datetime.now(timezone.utc).date()
    filled: list[str] = []
    for key, q in quotes.items():
        if q.ok:
            continue
        ind = INDICATOR_BY_KEY.get(key)
        if ind is None or ind.source == "derived":
            continue
        hist = [(d, v) for d, v in lookup(key) if v is not None]
        if not hist:
            continue
        last_date, last_value = hist[-1]
        try:
            age = (today - datetime.strptime(last_date, "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if age > _FALLBACK_MAX_AGE_DAYS.get(ind.freq, 10):
            continue
        q.value = last_value
        q.prev_close = hist[-2][1] if len(hist) >= 2 else None
        q.as_of = f"{last_date}T00:00:00+00:00"
        q.history = hist
        q.ok = True
        q.fallback = True
        filled.append(key)
    return filled


def _apply_op(acc, op: str, x):
    """누적값에 연산 적용. '/' 는 0 나눗셈 시 None(계산 불가) 반환."""
    if acc is None or x is None:
        return None
    if op == "/":
        return acc / x if x != 0 else None
    if op == "*":
        return acc * x
    if op == "-":
        return acc - x
    return acc + x


def _compute_derived(quotes: dict[str, Quote]) -> list[Quote]:
    """source='derived' 지표를 피연산 지표로부터 계산.

    derived 는 (기준키, op, 키, op, 키, ...) 평탄 시퀀스 — N항 가감을 지원한다.
    예) 실질금리=("us10y","-","us_be10y")  |  순유동성=("us_walcl","-","us_rrp","-","us_tga").
    값/이력 모두 공통 날짜에서만 결합(피연산 빈도가 다르면 교집합 날짜로).
    """
    out: list[Quote] = []
    for ind in INDICATORS:
        if ind.source != "derived" or not ind.derived:
            continue
        terms = ind.derived
        base_key = terms[0]
        ops = [(terms[i], terms[i + 1]) for i in range(1, len(terms), 2)]  # [(op, key), ...]
        bq = quotes.get(base_key)
        operands = {k: quotes.get(k) for _op, k in ops}
        if (bq is None or not bq.ok or bq.value is None
                or any(q is None or not q.ok or q.value is None for q in operands.values())):
            out.append(Quote(key=ind.key, ok=False, error="피연산 지표 결측"))
            continue

        value = bq.value
        for op, k in ops:
            value = _apply_op(value, op, operands[k].value)
        if value is None:                       # 0 나눗셈 등 → 계산 불가
            out.append(Quote(key=ind.key, ok=False, error="파생 계산 불가(0 나눗셈)"))
            continue
        scale = ind.scale                       # 비율 가독화(예: 구리/금 ×1000)
        value *= scale

        maps = {k: {d: v for d, v in operands[k].history} for _op, k in ops}
        hist: list[tuple[str, float]] = []
        for d, v in bq.history:
            if all(d in maps[k] for _op, k in ops):
                acc = v
                for op, k in ops:
                    acc = _apply_op(acc, op, maps[k][d])
                if acc is not None:
                    hist.append((d, acc * scale))
        prev = hist[-2][1] if len(hist) >= 2 else None
        out.append(Quote(key=ind.key, value=value, prev_close=prev,
                         as_of=bq.as_of, history=hist, ok=True))
    return out


def _translate_news() -> None:
    """미번역 뉴스(영문)만 일괄 번역해 저장. 한글 제목은 LLM 없이 그대로 캐시."""
    rows = repo.news_needing_translation(60)
    if not rows:
        return
    to_translate = []
    for r in rows:
        if translate_mod.needs_translation(r["title"]):
            to_translate.append(r)
        else:
            repo.set_news_translation(r["link"], r["title"])  # 이미 한국어 → 캐시
    if not to_translate:
        return
    logger.info("    - 뉴스 번역 중 (%d건, %s)...", len(to_translate), llm_mod.engine_label())
    kos = translate_mod.translate_titles([r["title"] for r in to_translate])
    n = 0
    for r, ko in zip(to_translate, kos):
        if ko and ko != r["title"]:
            repo.set_news_translation(r["link"], ko)
            n += 1
    logger.info("    - 뉴스 번역 완료 %d/%d건", n, len(to_translate))


def _escalate_failures(healths, ts_utc: str) -> None:
    """직전까지의 연속 실패수 기준으로 장애/복구 이벤트를 기록(save_health 호출 전)."""
    for h in healths:
        prior = repo.source_fail_streak(h.source)  # 현재 저장 전이므로 직전까지의 연속실패
        if not h.ok:
            streak = prior + 1
            if streak in _ESCALATE_AT:
                repo.record_source_event(ts_utc, h.source, "down", h.message, streak)
                logger.warning("소스 장애 - %s 연속 %d회 실패: %s",
                               h.source, streak, (h.message or "")[:80])
        elif prior >= 1:  # 직전까지 실패였는데 이번에 정상 → 복구
            repo.record_source_event(ts_utc, h.source, "recovered", h.message, 0)
            logger.info("소스 복구 - %s (직전 %d회 실패 후 정상)", h.source, prior)


def _prior_delta(snapshot_id: int, quotes: dict[str, Quote]) -> dict | None:
    """직전 완료 스냅샷 대비 핵심 지표 변화 + 직전 브리핑 심리/헤드라인."""
    prior = repo.prior_finished_snapshot(snapshot_id)
    if not prior:
        return None
    prev_obs = repo.get_observations(prior["id"])
    prev_brief = repo.get_briefing_for_snapshot(prior["id"]) or {}
    deltas = []
    for k in _DELTA_KEYS:
        cur, po = quotes.get(k), prev_obs.get(k)
        if not cur or not cur.ok or cur.value is None or not po or po.get("value") is None:
            continue
        ind = INDICATOR_BY_KEY.get(k)
        deltas.append({"label": ind.label if ind else k, "unit": ind.unit if ind else "",
                       "cur": cur.value, "prev": po["value"], "diff": cur.value - po["value"]})
    return {"prev_ts": prior["finished_utc"],
            "prev_sentiment": prev_brief.get("sentiment"),
            "prev_headline": prev_brief.get("headline"),
            "deltas": deltas}

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


def scheduler_health() -> dict:
    """스케줄러 생존 + 마지막 수집 경과시간으로 misfire(조용한 정지) 감지."""
    snap = repo.latest_snapshot()
    mins = None
    if snap and snap.get("finished_utc"):
        try:
            dt = datetime.fromisoformat(snap["finished_utc"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
        except ValueError:
            pass
    alive = bool(_scheduler and _scheduler.running)
    # 매시 수집 가정 → 90분 넘게 완료 수집이 없으면 misfire 의심
    misfire = mins is not None and mins > 90
    return {
        "alive": alive,
        "minutes_since_collection": round(mins, 1) if mins is not None else None,
        "misfire_suspected": bool(misfire or not alive),
    }


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
    # 1) 모든 수집기 병렬 실행 - 끝나는 대로 소스별 결과를 로그(라이브 진행 표시)
    n_src = len(ALL_COLLECTORS)
    logger.info("  [1/5] 소스 수집 - %d개 병렬 시작...", n_src)
    t_stage = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=max(2, n_src)) as ex:
        futures = {ex.submit(c.run): c for c in ALL_COLLECTORS}
        for fut in as_completed(futures):
            c = futures[fut]
            r = fut.result()
            results.append(r)
            h = r.health
            logger.info("    - (%d/%d) %-9s %s fetched=%d failed=%d %dms",
                        len(results), n_src, c.source, "OK" if (h and h.ok) else "FAIL",
                        h.fetched if h else 0, h.failed if h else 0, h.latency_ms if h else 0)

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

    # 1-b) 수집 실패 지표는 저장된 직전값으로 대체 — 간헐 장애(FRED 타임아웃)로
    #      타일이 통째로 비고 파생까지 동반 결측되는 것을 막는다. 파생 계산보다 먼저.
    filled = _apply_fallbacks(
        quotes,
        lambda k: [(r["date"], r["value"]) for r in repo.get_series(k, settings.history_points)],
    )
    if filled:
        logger.info("    - 직전값 폴백 %d개: %s", len(filled), ", ".join(filled))

    # 2) 파생지표(실질금리·비율 등) — 수집된 피연산 지표로 계산
    logger.info("  [2/5] 파생지표 계산...")
    for dq in _compute_derived(quotes):
        quotes[dq.key] = dq
        all_quotes.append(dq)

    ok_count = sum(1 for q in all_quotes if q.ok)
    fail_count = sum(1 for q in all_quotes if not q.ok)
    fb_count = sum(1 for q in all_quotes if q.fallback)

    # 3) 저장 (+ 장애 에스컬레이션은 직전 상태 기준이라 save_health 전에)
    logger.info("  [3/5] 저장 - 지표 %d(ok %d/fail %d/폴백 %d) | 뉴스 %d | 캘린더 %d...",
                len(all_quotes), ok_count, fail_count, fb_count, len(news), len(events))
    repo.save_observations(snapshot_id, all_quotes)
    repo.upsert_history(all_quotes)
    _escalate_failures(healths, ts_utc)
    repo.save_health(snapshot_id, healths)
    repo.upsert_news(news, ts_utc)
    try:
        _translate_news()
    except Exception:  # noqa: BLE001 — 번역 실패가 수집을 막지 않게
        logger.warning("뉴스 번역 단계 실패(무시) — 원문 표시")
    repo.upsert_calendar(events)
    repo.finish_snapshot(snapshot_id, datetime.now(timezone.utc).isoformat(), ok_count, fail_count)

    # 3) LLM 브리핑 (실패해도 무방) — 직전 대비 변화 + 레짐/상관 맥락 추가
    now_kst = started.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    prior_ctx = _prior_delta(snapshot_id, quotes)
    try:
        regime_snap = regime_mod.snapshot(30)
    except Exception:  # noqa: BLE001 — 레짐 계산 실패해도 브리핑은 진행
        regime_snap = None
    if regime_snap:
        r = regime_snap.get("regime") or {}
        if r.get("score") is not None:
            try:
                repo.save_regime_score(snapshot_id, r["score"], r.get("tone", ""), r.get("short", ""))
            except Exception:  # noqa: BLE001 — 점수 기록 실패가 수집을 막지 않게
                pass
    logger.info("  [4/5] 데이터 저장 완료(%.1fs) - 브리핑 생성 중 (%s, 최대 %ds)...",
                time.monotonic() - t_stage, llm_mod.engine_label(), settings.llm_timeout)
    t_brief = time.monotonic()
    brief = briefing_mod.generate(quotes, news, events, now_kst,
                                  prior=prior_ctx, regime=regime_snap, now_utc=started)
    if brief.ok:
        logger.info("  [5/5] 브리핑 OK - %s / %s (%.1fs)",
                    brief.model, brief.sentiment, time.monotonic() - t_brief)
    else:
        logger.warning("  [5/5] 브리핑 실패 - %s (%.1fs)", brief.error, time.monotonic() - t_brief)
    repo.save_briefing(snapshot_id, datetime.now(timezone.utc).isoformat(), brief)

    # 현대차 실시간 데이터 갱신 (실패해도 수집을 막지 않게)
    try:
        hyundai_mod.refresh()
    except Exception:  # noqa: BLE001
        logger.warning("현대차 데이터 갱신 실패(무시)")

    # 신한지주 실시간 데이터 갱신 (실패해도 수집을 막지 않게)
    try:
        shinhan_mod.refresh()
    except Exception:  # noqa: BLE001
        logger.warning("신한지주 데이터 갱신 실패(무시)")

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
