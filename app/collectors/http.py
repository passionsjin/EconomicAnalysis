"""공용 HTTP 헬퍼: UA·타임아웃·재시도·호스트 로테이션."""
from __future__ import annotations

import time
from typing import Optional

import requests

from ..config import settings

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_session: Optional[requests.Session] = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "*/*"})
        _session = s
    return _session


def get(url: str, *, params: dict | None = None, timeout: int | None = None,
        retries: int = 2, headers: dict | None = None) -> requests.Response:
    """GET with 간단한 지수 백오프 재시도. 마지막 예외는 그대로 raise."""
    last_exc: Exception | None = None
    to = timeout or settings.request_timeout
    for attempt in range(retries + 1):
        try:
            resp = session().get(url, params=params, timeout=to, headers=headers)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def get_json(url: str, **kw) -> dict:
    return get(url, **kw).json()


def get_text(url: str, **kw) -> str:
    r = get(url, **kw)
    r.encoding = r.encoding or "utf-8"
    return r.text
