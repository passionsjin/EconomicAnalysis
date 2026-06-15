"""저장/조회 계층. 모든 함수는 호출 시 자체 커넥션을 연다(스레드 안전)."""
from __future__ import annotations

from typing import Optional

from .config import INDICATOR_BY_KEY, settings
from .db import get_con
from .models import Briefing, CalendarEvent, NewsItem, Quote, SourceHealth


def _source_of(key: str) -> str | None:
    ind = INDICATOR_BY_KEY.get(key)
    return ind.source if ind else None


# ─────────────────────────── 쓰기 ───────────────────────────

def create_snapshot(ts_utc: str) -> int:
    with get_con() as con:
        cur = con.execute("INSERT INTO snapshots (ts_utc) VALUES (?)", (ts_utc,))
        return int(cur.lastrowid)


def finish_snapshot(snapshot_id: int, finished_utc: str, ok: int, fail: int) -> None:
    with get_con() as con:
        con.execute(
            "UPDATE snapshots SET finished_utc=?, ok_count=?, fail_count=? WHERE id=?",
            (finished_utc, ok, fail, snapshot_id),
        )


def save_observations(snapshot_id: int, quotes: list[Quote]) -> None:
    rows = [
        (
            snapshot_id, q.key, q.value, q.prev_close, q.change, q.change_pct,
            q.as_of, _source_of(q.key), 1 if q.ok else 0, q.error,
        )
        for q in quotes
    ]
    with get_con() as con:
        con.executemany(
            """INSERT OR REPLACE INTO observations
               (snapshot_id, key, value, prev_close, change, change_pct, as_of, source, ok, error)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )


def upsert_history(quotes: list[Quote]) -> None:
    rows = []
    for q in quotes:
        for date, value in q.history:
            if value is not None:
                rows.append((q.key, date, value))
    if not rows:
        return
    with get_con() as con:
        con.executemany(
            "INSERT OR REPLACE INTO history (key, date, value) VALUES (?,?,?)", rows
        )


def save_health(snapshot_id: int, items: list[SourceHealth]) -> None:
    rows = [
        (snapshot_id, h.source, 1 if h.ok else 0, h.fetched, h.failed, h.latency_ms, h.message)
        for h in items
    ]
    with get_con() as con:
        con.executemany(
            """INSERT OR REPLACE INTO source_health
               (snapshot_id, source, ok, fetched, failed, latency_ms, message)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )


def upsert_news(items: list[NewsItem], seen_ts: str) -> None:
    rows = [(it.link, it.source, it.title, it.published, it.summary, seen_ts) for it in items if it.link]
    if not rows:
        return
    with get_con() as con:
        # link 충돌 시 first_seen 은 보존(최초 등장 시각 유지)
        con.executemany(
            """INSERT INTO news (link, source, title, published, summary, first_seen)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(link) DO UPDATE SET
                 title=excluded.title, summary=excluded.summary, published=excluded.published""",
            rows,
        )


def upsert_calendar(events: list[CalendarEvent]) -> None:
    rows = []
    for e in events:
        uid = f"{e.date}|{e.country}|{e.title}"
        rows.append((uid, e.title, e.country, e.date, e.impact, e.actual, e.forecast, e.previous))
    if not rows:
        return
    with get_con() as con:
        con.executemany(
            """INSERT OR REPLACE INTO calendar
               (uid, title, country, date_utc, impact, actual, forecast, previous)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )


def save_briefing(snapshot_id: int, ts_utc: str, b: Briefing) -> int:
    with get_con() as con:
        cur = con.execute(
            """INSERT INTO briefings
               (snapshot_id, ts_utc, model, headline, summary, body_md, sentiment, ok, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, ts_utc, b.model, b.headline, b.summary, b.body_md,
             b.sentiment, 1 if b.ok else 0, b.error),
        )
        return int(cur.lastrowid)


def prune(history_days: int) -> None:
    """오래된 이력/스냅샷 정리(무한 증가 방지).

    여섯 DELETE 를 한 트랜잭션으로 묶어 원자적으로 커밋(부분 정리/고아행 방지).
    시각 비교는 datetime() 로 양변을 정규화 — 저장값은 ISO 'T'+오프셋,
    SQLite datetime('now') 는 공백구분이라 문자열 비교 시 경계가 어긋난다.
    """
    with get_con() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            # history: 키별 최근 N개만 보존(일/월/주 빈도 무관) — 월별 거시도 다년치 유지.
            # 날짜 기준으로 자르면 월별 시리즈는 3~4점만 남으므로 개수 기준으로 보존한다.
            con.execute(
                """DELETE FROM history WHERE (key, date) IN (
                     SELECT key, date FROM (
                       SELECT key, date,
                              ROW_NUMBER() OVER (PARTITION BY key ORDER BY date DESC) AS rn
                       FROM history
                     ) WHERE rn > ?
                   )""",
                (settings.history_points,),
            )
            # 스냅샷/관측/브리핑은 history_days*2 일 보관
            con.execute(
                "DELETE FROM snapshots WHERE datetime(ts_utc) < datetime('now', ?)",
                (f"-{history_days * 2} day",),
            )
            con.execute(
                "DELETE FROM observations WHERE snapshot_id NOT IN (SELECT id FROM snapshots)"
            )
            con.execute(
                "DELETE FROM source_health WHERE snapshot_id NOT IN (SELECT id FROM snapshots)"
            )
            con.execute(
                "DELETE FROM briefings WHERE snapshot_id NOT IN (SELECT id FROM snapshots)"
            )
            con.execute(
                "DELETE FROM news WHERE datetime(first_seen) < datetime('now', '-14 day')"
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def delete_snapshot(snapshot_id: int) -> None:
    """미완성/실패 스냅샷과 그 자식 행을 제거(고스트 스냅샷 정리)."""
    with get_con() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("DELETE FROM observations WHERE snapshot_id=?", (snapshot_id,))
            con.execute("DELETE FROM source_health WHERE snapshot_id=?", (snapshot_id,))
            con.execute("DELETE FROM briefings WHERE snapshot_id=?", (snapshot_id,))
            con.execute("DELETE FROM snapshots WHERE id=?", (snapshot_id,))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


# ─────────────────────────── 읽기 ───────────────────────────

def latest_snapshot() -> Optional[dict]:
    with get_con() as con:
        r = con.execute(
            "SELECT * FROM snapshots WHERE finished_utc IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(r) if r else None


def get_snapshot(snapshot_id: int) -> Optional[dict]:
    with get_con() as con:
        r = con.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        return dict(r) if r else None


def list_snapshots(limit: int = 50) -> list[dict]:
    with get_con() as con:
        rows = con.execute(
            "SELECT * FROM snapshots WHERE finished_utc IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_observations(snapshot_id: int) -> dict[str, dict]:
    with get_con() as con:
        rows = con.execute(
            "SELECT * FROM observations WHERE snapshot_id=?", (snapshot_id,)
        ).fetchall()
        return {r["key"]: dict(r) for r in rows}


def get_health(snapshot_id: int) -> list[dict]:
    with get_con() as con:
        rows = con.execute(
            "SELECT * FROM source_health WHERE snapshot_id=? ORDER BY source", (snapshot_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_series(key: str, points: int = 120) -> list[dict]:
    """차트용 시계열: 키별 최근 `points` 개를 반환(일/월/주 빈도 무관).

    날짜 윈도우가 아니라 '개수' 기준이라 월별 거시(CPI/PCE/실업률)도
    다년치가 그대로 나온다(일별 시장은 그만큼 최근 거래일).
    """
    points = max(1, min(points, 2000))
    with get_con() as con:
        rows = con.execute(
            "SELECT date, value FROM history WHERE key=? ORDER BY date DESC LIMIT ?",
            (key, points),
        ).fetchall()
    return [{"date": r["date"], "value": r["value"]} for r in reversed(rows)]


def get_briefing_for_snapshot(snapshot_id: int) -> Optional[dict]:
    with get_con() as con:
        r = con.execute(
            "SELECT * FROM briefings WHERE snapshot_id=? ORDER BY id DESC LIMIT 1",
            (snapshot_id,),
        ).fetchone()
        return dict(r) if r else None


def latest_briefing() -> Optional[dict]:
    with get_con() as con:
        r = con.execute(
            "SELECT * FROM briefings WHERE ok=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(r) if r else None


def recent_news(limit: int = 30) -> list[dict]:
    with get_con() as con:
        rows = con.execute(
            "SELECT * FROM news ORDER BY COALESCE(published, first_seen) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def news_needing_translation(limit: int = 60) -> list[dict]:
    """아직 번역 안 된(title_ko IS NULL) 최근 뉴스 — 최신 우선."""
    with get_con() as con:
        rows = con.execute(
            "SELECT link, title FROM news WHERE title_ko IS NULL "
            "ORDER BY COALESCE(published, first_seen) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def set_news_translation(link: str, title_ko: str) -> None:
    with get_con() as con:
        con.execute("UPDATE news SET title_ko=? WHERE link=?", (title_ko, link))


def upcoming_calendar(limit: int = 30) -> list[dict]:
    with get_con() as con:
        rows = con.execute(
            """SELECT * FROM calendar
               WHERE datetime(date_utc) >= datetime('now', '-12 hour')
               ORDER BY datetime(date_utc) ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_series_batch(keys: list[str], points: int = 60) -> dict[str, list[dict]]:
    """여러 키의 시계열을 한 번에(스파크라인 N개 동시요청 → 1요청)."""
    points = max(1, min(points, 2000))
    out: dict[str, list[dict]] = {}
    if not keys:
        return out
    with get_con() as con:
        for key in keys:
            rows = con.execute(
                "SELECT date, value FROM history WHERE key=? ORDER BY date DESC LIMIT ?",
                (key, points),
            ).fetchall()
            out[key] = [{"date": r["date"], "value": r["value"]} for r in reversed(rows)]
    return out


def prior_finished_snapshot(before_id: int) -> Optional[dict]:
    """주어진 스냅샷 직전의 완료 스냅샷(직전 브리핑 delta 계산용)."""
    with get_con() as con:
        r = con.execute(
            "SELECT * FROM snapshots WHERE finished_utc IS NOT NULL AND id < ? "
            "ORDER BY id DESC LIMIT 1",
            (before_id,),
        ).fetchone()
        return dict(r) if r else None


# ── 소스 장애 이벤트 ──────────────────────────────────────────

def record_source_event(ts_utc: str, source: str, kind: str, detail: str, consecutive: int) -> None:
    with get_con() as con:
        con.execute(
            "INSERT INTO source_events (ts_utc, source, kind, detail, consecutive) VALUES (?,?,?,?,?)",
            (ts_utc, source, kind, (detail or "")[:300], consecutive),
        )


def recent_source_events(limit: int = 20) -> list[dict]:
    with get_con() as con:
        rows = con.execute(
            "SELECT * FROM source_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def source_fail_streak(source: str, lookback: int = 48) -> int:
    """완료 스냅샷 기준, 가장 최근부터 이 소스가 연속 실패(ok=0)한 횟수.

    현재 스냅샷의 health 저장 '전에' 호출하면 직전까지의 연속 실패수를 준다.
    """
    with get_con() as con:
        rows = con.execute(
            """SELECT sh.ok FROM source_health sh
               JOIN snapshots s ON s.id = sh.snapshot_id
               WHERE sh.source=? AND s.finished_utc IS NOT NULL
               ORDER BY s.id DESC LIMIT ?""",
            (source, lookback),
        ).fetchall()
    streak = 0
    for r in rows:
        if r["ok"] == 0:
            streak += 1
        else:
            break
    return streak
