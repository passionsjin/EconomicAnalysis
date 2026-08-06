"""TLT 이력만 단독 백필.

테스트 서버(8003)는 스케줄러가 꺼져 있어 tlt 를 수집하지 않는다.
전체 run_collection() 은 8000 서버와 DB 락 경합을 일으키므로
이 한 종목만 받아서 history 에 넣는다.

실행:  .\\.venv\\Scripts\\python.exe scripts/backfill_tlt.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import repository as repo               # noqa: E402
from app.collectors.markets import _fetch_symbol, _parse   # noqa: E402
from app.models import Quote                     # noqa: E402

SYMBOL = "TLT"
KEY = "tlt"

print(f"{SYMBOL} 5y 이력 요청 중...")
raw = _fetch_symbol(SYMBOL, "5y")
price, prev, as_of, history = _parse(SYMBOL, raw)
if not history:
    print("실패: 이력이 비어 있음")
    sys.exit(1)

print(f"수신 {len(history)}개  {history[0][0]} ~ {history[-1][0]}  최신가 {price}")
repo.upsert_history([Quote(key=KEY, value=price, prev_close=prev, as_of=as_of,
                           history=history, ok=True)])

rows = repo.get_series_batch([KEY], 2000)[KEY]
print(f"DB 저장 확인: {len(rows)}개  {rows[0]['date']} ~ {rows[-1]['date']}")
if len(rows) < 1000:
    print("경고: 5년치(약 1250개)에 못 미친다. Yahoo 응답을 확인할 것")
    sys.exit(1)
print("완료")
