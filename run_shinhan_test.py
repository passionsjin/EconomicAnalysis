"""신한지주 전용 테스트 서버 — 기존 대시보드(8000)·현대차 테스트(8001)와 공존.

스케줄러·자동수집 모두 비활성 → SQLite 충돌 없음.
신한지주 데이터는 Yahoo Finance / Naver Finance 에서 직접 실시간 조회.

실행:  python run_shinhan_test.py
접속:  http://127.0.0.1:8002/shinhan
"""
from __future__ import annotations

import os

# pydantic-settings 가 읽기 전에 env 설정
os.environ.setdefault("COLLECT_ON_START", "false")

# ── 스케줄러·수집 전체 비활성 (기존 서버와 공존) ──────────────────────────
from app import pipeline as _pl  # noqa: E402

_pl.start_scheduler   = lambda: type("_FakeSched", (), {"running": False})()
_pl.shutdown_scheduler = lambda: None
_pl.trigger_async      = lambda: False

# ── 서버 기동 ─────────────────────────────────────────────────────────────
import uvicorn  # noqa: E402

PORT = 8002

print()
print("=" * 54)
print("  신한지주 테스트 서버")
print(f"  -> http://127.0.0.1:{PORT}/shinhan")
print()
print("  - 기존 대시보드(8000)/현대차(8001) 계속 동작")
print("  - 스케줄러/자동수집 비활성 (DB 충돌 없음)")
print("  - Yahoo/Naver Finance 실시간 조회 (첫 방문 수초 소요)")
print("  종료: Ctrl+C")
print("=" * 54)
print()

uvicorn.run(
    "app.server:app",
    host="127.0.0.1",
    port=PORT,
    reload=False,
    log_level="info",
)
