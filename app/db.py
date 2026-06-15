"""SQLite 스키마 초기화 + 커넥션 헬퍼.

스레드(스케줄러 워커)와 요청 핸들러가 각각 자기 커넥션을 열도록 설계.
WAL 모드로 동시 읽기/쓰기 안정화.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc      TEXT NOT NULL,            -- 수집 시작 시각 (UTC ISO8601)
    finished_utc TEXT,
    ok_count    INTEGER DEFAULT 0,
    fail_count  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observations (
    snapshot_id INTEGER NOT NULL,
    key         TEXT NOT NULL,
    value       REAL,
    prev_close  REAL,
    change      REAL,
    change_pct  REAL,
    as_of       TEXT,
    source      TEXT,
    ok          INTEGER DEFAULT 1,
    error       TEXT,
    PRIMARY KEY (snapshot_id, key)
);
CREATE INDEX IF NOT EXISTS idx_obs_key ON observations(key);

-- 차트용 일별 이력 (provider 제공 종가; (key,date) 유니크 업서트)
CREATE TABLE IF NOT EXISTS history (
    key   TEXT NOT NULL,
    date  TEXT NOT NULL,          -- YYYY-MM-DD
    value REAL,
    PRIMARY KEY (key, date)
);

CREATE TABLE IF NOT EXISTS source_health (
    snapshot_id INTEGER NOT NULL,
    source      TEXT NOT NULL,
    ok          INTEGER DEFAULT 1,
    fetched     INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    latency_ms  INTEGER DEFAULT 0,
    message     TEXT,
    PRIMARY KEY (snapshot_id, source)
);

CREATE TABLE IF NOT EXISTS news (
    link        TEXT PRIMARY KEY,
    source      TEXT,
    title       TEXT,
    published   TEXT,
    summary     TEXT,
    first_seen  TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_seen ON news(first_seen DESC);

CREATE TABLE IF NOT EXISTS calendar (
    uid       TEXT PRIMARY KEY,
    title     TEXT,
    country   TEXT,
    date_utc  TEXT,
    impact    TEXT,
    actual    TEXT,
    forecast  TEXT,
    previous  TEXT
);
CREATE INDEX IF NOT EXISTS idx_cal_date ON calendar(date_utc);

CREATE TABLE IF NOT EXISTS briefings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    ts_utc      TEXT NOT NULL,
    model       TEXT,
    headline    TEXT,
    summary     TEXT,
    body_md     TEXT,
    sentiment   TEXT,
    ok          INTEGER DEFAULT 1,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_brief_snap ON briefings(snapshot_id);

-- 소스 장애/복구 이벤트 로그 (연속 실패 에스컬레이션·알림용)
CREATE TABLE IF NOT EXISTS source_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc      TEXT NOT NULL,
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,        -- 'down' | 'recovered'
    detail      TEXT,
    consecutive INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_srcevt_ts ON source_events(ts_utc DESC);

-- 차트 조회 가속: (key, date) 복합 인덱스
CREATE INDEX IF NOT EXISTS idx_hist_key_date ON history(key, date DESC);
"""


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        con.commit()


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(
        settings.db_path,
        timeout=30,
        check_same_thread=False,
        isolation_level=None,  # autocommit; 명시적 트랜잭션은 호출부에서
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA busy_timeout=30000;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con


@contextmanager
def get_con() -> Iterator[sqlite3.Connection]:
    con = connect()
    try:
        yield con
    finally:
        con.close()
