"""자금 회전지도 테스트 서버 — 기존 대시보드(8000)·현대차(8001)·신한(8002)과 공존.

스케줄러·자동수집 모두 비활성 -> SQLite 충돌 없음.

실행:  python run_flows_test.py
접속:  http://127.0.0.1:8003/flows
"""
from __future__ import annotations

import os

# pydantic-settings 가 읽기 전에 env 설정
os.environ.setdefault("COLLECT_ON_START", "false")

# ── 스케줄러·수집 전체 비활성 (기존 서버와 공존) ──────────────────────────
from app import pipeline as _pl  # noqa: E402

_pl.start_scheduler    = lambda: type("_FakeSched", (), {"running": False})()
_pl.shutdown_scheduler = lambda: None
_pl.trigger_async      = lambda **_: False

# ── 서버 기동 ─────────────────────────────────────────────────────────────
import uvicorn  # noqa: E402

PORT = 8003

print()
print("=" * 54)
print("  Capital Flow Map test server")
print(f"  -> http://127.0.0.1:{PORT}/flows")
print()
print("  - dashboard(8000)/hyundai(8001)/shinhan(8002) keep running")
print("  - scheduler/auto-collect disabled (no DB conflict)")
print("  Stop: Ctrl+C")
print("=" * 54)
print()

uvicorn.run("app.server:app", host="127.0.0.1", port=PORT, reload=False, log_level="info")
