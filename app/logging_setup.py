"""앱 로깅 설정 — 콘솔 + 회전 파일(logs/app.log).

수집 진행상황(시작·소스별 결과·브리핑·완료)을 남긴다.
시각은 시스템 로컬시간(사용자 PC = KST)으로 표기. 파일은 2MB×5 회전.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import BASE_DIR

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"

# 모듈 어디서든 `from .logging_setup import logger` 로 같은 싱글톤을 쓴다.
logger = logging.getLogger("econ")
_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """앱 로거를 콘솔+파일 핸들러로 1회 구성(idempotent)."""
    global _configured
    if _configured:
        return logger

    # 콘솔이 cp949(한글 윈도우 기본)라도 표현불가 문자로 죽지 않도록 안전망.
    # 한글 자체는 cp949 로 정상 출력되고, 드문 비표현 문자만 escape 처리된다.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(errors="backslashreplace")
        except Exception:  # noqa: BLE001 — 일부 환경엔 reconfigure 없음
            pass

    logger.setLevel(level)
    logger.propagate = False  # uvicorn/root 로 중복 전파 방지

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    try:  # 파일 핸들러 실패(권한 등)해도 콘솔 로깅은 유지
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:  # noqa: BLE001
        logger.warning("로그 파일 핸들러 설정 실패 — 콘솔 로깅만 사용")

    _configured = True
    return logger
