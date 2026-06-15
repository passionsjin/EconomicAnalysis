"""DB 행 → 화면/ API 용 뷰 모델 조립. HTML 라우트와 JSON API 가 공유."""
from __future__ import annotations

from typing import Optional

from . import repository as repo
from .collectors.base import ALL_COLLECTORS
from .config import CATEGORIES, INDICATORS, INDICATOR_BY_KEY, Indicator


def fmt_value(ind: Indicator, value: Optional[float]) -> str:
    if value is None:
        return "—"
    s = f"{value:,.{ind.decimals}f}"
    if ind.unit == "$":
        return f"${s}"
    if ind.unit == "%":
        return f"{s}%"
    if ind.unit:
        return f"{s}{ind.unit}"
    return s


def _tone(ind: Indicator, change: Optional[float]) -> str:
    """색상 톤: good(녹)/bad(적)/neutral(회)."""
    if change is None or change == 0:
        return "neutral"
    up = change > 0
    if ind.up_is_good is True:
        return "good" if up else "bad"
    if ind.up_is_good is False:
        return "bad" if up else "good"
    return "neutral"


def _indicator_view(ind: Indicator, obs: dict) -> dict:
    row = obs.get(ind.key) or {}
    ok = bool(row.get("ok", 0)) and row.get("value") is not None
    value = row.get("value")
    change = row.get("change")
    change_pct = row.get("change_pct")

    # 금리·비율(% 단위) 지표는 '절대 변화(%p)'가 주(主) 표시여야 자연스럽다.
    # (예: CPI 전년比 3.78%→4.17% 는 +10.25%(상대)가 아니라 +0.39%p 가 거시 관행)
    # 또 장단기차처럼 0 부근/음수를 오가는 시리즈는 상대%가 무의미하므로 %p 가 더 정확.
    is_pp = ind.unit == "%"
    if is_pp:
        delta = change
        has_change = change is not None
        change_main_fmt = f"{change:+.2f}%p" if change is not None else "—"
        change_sub_fmt = ""  # %p 단독 표시(상대% 보조 생략)
    else:
        delta = change_pct
        has_change = change_pct is not None
        change_main_fmt = f"{change_pct:+.2f}%" if change_pct is not None else "—"
        change_sub_fmt = f"{change:+,.{ind.decimals}f}" if change is not None else ""

    return {
        "key": ind.key,
        "label": ind.label,
        "category": ind.category,
        "unit": ind.unit,
        "note": ind.note,
        "ok": ok,
        "value": value,
        "value_fmt": fmt_value(ind, value) if ok else "—",
        "change": change,
        "change_pct": change_pct,
        "has_change": has_change,
        "change_main_fmt": change_main_fmt,   # 주 표시(%p 또는 상대%)
        "change_sub_fmt": change_sub_fmt,     # 보조 표시(절대 변화; % 지표는 생략)
        "sign": ("pos" if (delta or 0) > 0 else "neg" if (delta or 0) < 0 else "zero"),
        "tone": _tone(ind, delta),
        "as_of": row.get("as_of"),
        "error": row.get("error"),
    }


def build_groups(obs: dict) -> list[dict]:
    groups = []
    for cat_key, cat_label in CATEGORIES.items():
        items = [_indicator_view(ind, obs) for ind in INDICATORS if ind.category == cat_key]
        if items:
            groups.append({"key": cat_key, "label": cat_label, "items": items})
    return groups


def _source_label(source: str) -> str:
    for c in ALL_COLLECTORS:
        if c.source == source:
            return c.label
    return source


def health_view(health_rows: list[dict]) -> list[dict]:
    out = []
    for h in health_rows:
        if h.get("fetched", 0) == 0 and h.get("failed", 0) == 0:
            state = "idle"      # 비활성/데이터 없음
        elif h.get("ok"):
            state = "ok"
        elif h.get("fetched", 0) > 0:
            state = "partial"   # 일부 성공
        else:
            state = "down"
        out.append({
            "source": h["source"],
            "label": _source_label(h["source"]),
            "state": state,
            "fetched": h.get("fetched", 0),
            "failed": h.get("failed", 0),
            "latency_ms": h.get("latency_ms", 0),
            "message": h.get("message", ""),
        })
    return out


def build_dashboard(snapshot_id: Optional[int] = None) -> dict:
    snap = repo.get_snapshot(snapshot_id) if snapshot_id else repo.latest_snapshot()
    if not snap:
        return {"empty": True, "groups": [], "health": [], "news": [],
                "calendar": [], "briefing": None, "snapshot": None}

    sid = snap["id"]
    obs = repo.get_observations(sid)
    briefing = repo.get_briefing_for_snapshot(sid) or repo.latest_briefing()
    return {
        "empty": False,
        "snapshot": snap,
        "groups": build_groups(obs),
        "health": health_view(repo.get_health(sid)),
        "news": repo.recent_news(30),
        "calendar": repo.upcoming_calendar(30),
        "briefing": briefing,
    }
