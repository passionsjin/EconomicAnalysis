"""LLM provider 추상화 — 호출부(briefing·translate)로부터 백엔드를 감춘다.

- claude: 사용자가 로그인한 Claude Code 구독 인증 기반 `claude -p` 헤드리스(별도 키 불필요).
- gemini: GEMINI_API_KEY 로 Google generativelanguage REST(generateContent) 직접 호출.

`LLM_PROVIDER` 로 선택(기본 claude). 어느 provider 든 `complete()` 는 '어시스턴트 본문
텍스트'를 그대로 돌려준다(claude 엔벨로프 해제·gemini candidate 추출은 여기서 처리).
JSON 파싱·재시도·인용 점검은 호출부 몫이라 provider 교체에 영향받지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from ..config import BASE_DIR, settings
from ..logging_setup import logger


@dataclass
class LLMResult:
    ok: bool
    text: str = ""      # 어시스턴트 본문(엔벨로프/candidate 해제 후)
    model: str = ""     # 실제 사용 모델명(화면 배지용)
    error: str = ""


# ─────────────────────────── provider/모델 해석 ───────────────────────────

def provider() -> str:
    p = (settings.llm_provider or "claude").strip().lower()
    return p if p in ("claude", "gemini") else "claude"


def engine_label() -> str:
    """로그·화면용 현재 엔진 표기 (예: 'claude -p', 'gemini API')."""
    return "gemini API" if provider() == "gemini" else "claude -p"


def model_fallback_label() -> str:
    """배지에서 실제 모델명을 못 받았을 때 쓸 provider 기본 표기."""
    return "Gemini" if provider() == "gemini" else "Claude Code"


def briefing_model() -> Optional[str]:
    if provider() == "gemini":
        return settings.gemini_model or None
    return settings.claude_model or None


def translate_model() -> Optional[str]:
    if provider() == "gemini":
        return settings.gemini_translate_model or settings.gemini_model or None
    return settings.claude_translate_model or settings.claude_model or None


def availability_error() -> Optional[str]:
    """provider 가 호출 가능한 상태가 아니면 사람이 읽을 오류 문자열, 가능하면 None."""
    if provider() == "gemini":
        if not settings.gemini_api_key:
            return "GEMINI_API_KEY 가 설정되지 않음 (.env 확인)"
        return None
    if not resolve_claude():
        return "claude CLI 를 찾을 수 없음 (CLAUDE_BIN 설정 또는 PATH 확인)"
    return None


# ─────────────────────────── 공용 진입점 ───────────────────────────

def complete(prompt: str, *, model: Optional[str] = None, timeout: int,
             label: str = "브리핑", json_out: bool = True) -> LLMResult:
    """현재 provider 로 1회 호출. 전송 성공이면 ok=True+text, 실패면 ok=False+error.

    model 미지정 시 provider 의 브리핑 기본 모델 사용. 타임아웃·네트워크 예외는
    내부에서 잡아 ok=False 로 변환(호출부 재시도 로직을 단순하게 유지).
    json_out 은 gemini 의 응답 형식을 JSON 으로 강제할 때만 의미(claude 는 프롬프트로 지시).
    """
    use_model = model if model is not None else briefing_model()
    if provider() == "gemini":
        return _gemini_complete(prompt, use_model, timeout, label, json_out)
    return _claude_complete(prompt, use_model, timeout, label)


# 긴 생성 동안 '멈춤'처럼 보이지 않게 30초마다 진행 로그를 남기는 하트비트.

def _start_heartbeat(label: str, timeout: int) -> threading.Event:
    stop = threading.Event()

    def _beat() -> None:
        s = 0
        while not stop.wait(30):
            s += 30
            logger.info("    - %s 생성 중... (%ds 경과 / 최대 %ds)", label, s, timeout)

    threading.Thread(target=_beat, name="llm-heartbeat", daemon=True).start()
    return stop


# ─────────────────────────── Claude (CLI 헤드리스) ───────────────────────────

def resolve_claude() -> Optional[str]:
    """claude 실행 파일 경로. 없으면 None."""
    cand = settings.claude_bin or "claude"
    found = shutil.which(cand)
    if found:
        return found
    if settings.claude_bin and os.path.exists(settings.claude_bin):
        return settings.claude_bin
    return None


def _claude_cmd(exe: str, model: Optional[str]) -> list[str]:
    args = ["-p", "--output-format", "json"]
    if model:
        args += ["--model", model]
    # Windows 의 .cmd/.bat 셔임은 cmd /c 로 실행해야 함
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe, *args]
    return [exe, *args]


def _kill_tree(proc: subprocess.Popen) -> None:
    """타임아웃 시 프로세스 트리 전체 종료. Windows 의 cmd /c 셔임 자식 고아화 방지."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                proc.kill()
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _run_cli(cmd: list[str], prompt: str, timeout: int, label: str) -> tuple[int, str, str]:
    """claude CLI 호출 → (returncode, stdout, stderr). 타임아웃 시 자식 트리 종료."""
    kw = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
              cwd=str(BASE_DIR), env={**os.environ, "PYTHONUTF8": "1"})
    if os.name != "nt":
        kw["start_new_session"] = True  # killpg 대상 프로세스 그룹 분리
    proc = subprocess.Popen(cmd, **kw)
    stop = _start_heartbeat(label, timeout)
    try:
        out, err = proc.communicate(input=prompt.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        stop.set()
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _pick_claude_model(envelope: dict, fallback: str, requested: Optional[str]) -> str:
    """엔벨로프에서 '주 작업' 모델명 선택.

    Claude Code 는 보조작업에 haiku 를 함께 써 modelUsage 에 여러 모델이 섞인다.
    우선순위: 명시 요청 모델 → top-level model → 비용 최대 모델 → fallback.
    """
    mu = envelope.get("modelUsage") or {}
    if requested:
        for k in mu:
            if requested == k or requested in k:
                return k
        return requested
    if envelope.get("model"):
        return envelope["model"]
    if mu:
        return max(
            mu.items(),
            key=lambda kv: (kv[1] or {}).get("costUSD", 0) if isinstance(kv[1], dict) else 0,
        )[0]
    return fallback


def _claude_complete(prompt: str, model: Optional[str], timeout: int, label: str) -> LLMResult:
    exe = resolve_claude()
    if not exe:
        return LLMResult(False, error="claude CLI 를 찾을 수 없음 (CLAUDE_BIN 설정 또는 PATH 확인)")
    cmd = _claude_cmd(exe, model)
    try:
        rc, stdout, stderr = _run_cli(cmd, prompt, timeout, label)
    except subprocess.TimeoutExpired:
        return LLMResult(False, error=f"타임아웃({timeout}s)")
    except Exception as exc:  # noqa: BLE001
        return LLMResult(False, error=f"실행 실패: {type(exc).__name__}: {exc}")
    if rc != 0:
        return LLMResult(False, error=f"종료코드 {rc}: {(stderr or stdout or '')[-300:]}")
    raw = (stdout or "").strip()
    if not raw:
        return LLMResult(False, error="빈 응답")

    text = raw
    model_used = model or "Claude Code"
    try:
        env = json.loads(raw)
        if isinstance(env, dict):
            if env.get("is_error"):
                return LLMResult(False, error=f"claude 응답 오류: {str(env.get('result'))[:300]}")
            rt = env.get("result", raw)
            text = rt if isinstance(rt, str) else json.dumps(rt, ensure_ascii=False)
            model_used = _pick_claude_model(env, model_used, model)
    except json.JSONDecodeError:
        pass  # text 모드 폴백
    return LLMResult(True, text=text, model=model_used)


# ─────────────────────────── Gemini (REST generateContent) ───────────────────────────

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"

# 과부하("high demand")·일시 서버 오류·레이트리밋은 재시도로 넘어가는 경우가 많다.
_GEMINI_RETRY_STATUS = {429, 500, 502, 503, 504}
_GEMINI_BACKOFFS = (3, 8, 20)  # 재시도 전 대기(초). 최대 4회 시도.


def _gemini_retry_after(resp: "requests.Response", default: int) -> int:
    """Retry-After 헤더가 초 단위 숫자면 그 값(상한 60초), 아니면 default."""
    ra = resp.headers.get("Retry-After", "").strip()
    if ra.isdigit():
        return min(int(ra), 60)
    return default


def _gemini_complete(prompt: str, model: Optional[str], timeout: int,
                     label: str, json_out: bool) -> LLMResult:
    key = settings.gemini_api_key
    if not key:
        return LLMResult(False, error="GEMINI_API_KEY 가 설정되지 않음 (.env 확인)")
    mdl = model or _GEMINI_DEFAULT_MODEL
    gen_cfg: dict = {"temperature": 0.4, "maxOutputTokens": 16384}
    if json_out:
        gen_cfg["responseMimeType"] = "application/json"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen_cfg,
    }
    url = f"{_GEMINI_BASE}/{mdl}:generateContent"
    stop = _start_heartbeat(label, timeout)
    resp = None
    try:
        last_err = ""
        # 시도 = 1 + len(백오프). 과부하/일시오류·연결오류만 재시도(전체 타임아웃은 즉시 실패).
        for attempt in range(len(_GEMINI_BACKOFFS) + 1):
            try:
                resp = requests.post(
                    url, params={"key": key}, json=payload, timeout=timeout,
                    headers={"Content-Type": "application/json"},
                )
            except requests.Timeout:
                return LLMResult(False, error=f"타임아웃({timeout}s)")
            except requests.RequestException as exc:
                last_err = f"요청 실패: {type(exc).__name__}: {exc}"
                resp = None
            else:
                if resp.status_code == 200:
                    break
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code not in _GEMINI_RETRY_STATUS:
                    return LLMResult(False, error=last_err)
            # 여기 도달 = 재시도 대상. 남은 시도 있으면 백오프 후 다시.
            if attempt >= len(_GEMINI_BACKOFFS):
                return LLMResult(False, error=f"재시도 {attempt + 1}회 초과 — {last_err}")
            wait = _gemini_retry_after(resp, _GEMINI_BACKOFFS[attempt]) if resp is not None \
                else _GEMINI_BACKOFFS[attempt]
            logger.info("    - %s Gemini 일시오류(%s), %ds 후 재시도(%d/%d)...",
                        label, last_err[:60], wait, attempt + 1, len(_GEMINI_BACKOFFS))
            time.sleep(wait)
    finally:
        stop.set()

    try:
        data = resp.json()
    except ValueError:
        return LLMResult(False, error="JSON 응답 파싱 실패")

    pf = data.get("promptFeedback") or {}
    if pf.get("blockReason"):
        return LLMResult(False, error=f"안전 필터 차단: {pf.get('blockReason')}")
    cands = data.get("candidates") or []
    if not cands:
        return LLMResult(False, error="빈 응답(candidates 없음)")
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        return LLMResult(False, error=f"빈 텍스트(finishReason={cands[0].get('finishReason')})")
    return LLMResult(True, text=text, model=mdl)
