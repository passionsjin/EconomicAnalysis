"""CNN 공포·탐욕 이력 백필 (일회성).

상시 수집은 기본 URL(최근 1년, 251건)만 받는다. 백분위·z-score 룩백을 5년치로 채우려면
이 스크립트를 한 번 돌려 2021-01-04 이후 전량(약 1423건 x 10계열)을 history 에 넣는다.
CNN 은 2021-01-04 이전 시작일에 500 을 반환한다(그 이전 데이터가 없음).

주의
  - run.py 가 떠 있으면 실행하지 말 것 (SQLite 락 경합으로 매우 느려진다).
  - 로그는 ASCII 구두점만 쓴다 (한글 Windows 콘솔 cp949 에서 특수문자가 크래시).

실행: .\\.venv\\Scripts\\python.exe backfill_cnn_fng.py
"""
from __future__ import annotations

import sys
import time

# 한글 Windows 콘솔은 cp949 라 한글/특수문자가 깨진다(CLAUDE.md). utf-8 로 고정.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from app import repository as repo
from app.collectors import cnn_fng
from app.config import indicators_for_source
from app.models import Quote

START = "2021-01-01"      # CNN 데이터 시작(2021-01-04)보다 앞선 아무 날


def main() -> int:
    keys = [ind.key for ind in indicators_for_source("cnn")]
    print(f"[1/3] CNN graphdata/{START} 요청 ({len(keys)} keys)...")
    t0 = time.monotonic()
    try:
        payload = cnn_fng.fetch(START)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        print("  (CNN 이 UA + Referer 를 모두 요구한다. 418 이면 헤더 정책 변경 의심)")
        return 1
    print(f"  OK {int((time.monotonic() - t0) * 1000)}ms")

    print("[2/3] 파싱...")
    parsed = cnn_fng._parse(payload)
    quotes: list[Quote] = []
    total = 0
    for key in keys:
        series = parsed.get(key) or []
        if not series:
            print(f"  WARN {key}: 데이터 없음 - 건너뜀")
            continue
        total += len(series)
        print(f"  {key:24s} {len(series):5d} rows  {series[0][0]} ~ {series[-1][0]}")
        # 값/직전값은 상시 수집이 채운다. 여기서는 이력만 넣으므로 history 만 담는다.
        quotes.append(Quote(key=key, history=series, ok=True))

    if not quotes:
        print("  저장할 이력이 없다 - 응답 형식 변경 의심")
        return 1

    print(f"[3/3] history 저장 {total} rows (단일 트랜잭션)...")
    t1 = time.monotonic()
    repo.upsert_history(quotes)
    print(f"  OK {int((time.monotonic() - t1) * 1000)}ms")
    print("완료. run.py 를 재시작하면 신규 지표가 스케줄 수집에 포함된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
