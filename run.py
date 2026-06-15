"""로컬 실행 진입점:  python run.py

거시경제 수집기 + 웹 대시보드를 한 프로세스로 구동한다.
스케줄러가 매시 정각(.env 설정) 자동 수집하고, http://127.0.0.1:8000 에서 확인한다.
"""
from __future__ import annotations

import uvicorn

from app.config import settings
from app.logging_setup import LOG_FILE, setup_logging


def main() -> None:
    setup_logging()
    print("=" * 60)
    print("  거시경제 분석 대시보드")
    print(f"  → http://{settings.host}:{settings.port}")
    print(f"  수집 주기: 매시 {settings.collect_minute}분 (UTC cron)")
    print(f"  LLM 브리핑: {'ON (claude -p)' if settings.enable_llm else 'OFF'}")
    print(f"  수집 로그: {LOG_FILE} (+콘솔)")
    print("  종료: Ctrl+C")
    print("=" * 60)
    uvicorn.run(
        "app.server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
