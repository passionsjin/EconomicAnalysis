"""DB 행 → 화면/ API 용 뷰 모델 조립. HTML 라우트와 JSON API 가 공유."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import repository as repo
from .analysis import alerts as alerts_mod
from .analysis import allocation as allocation_mod
from .analysis import calendar_util
from .analysis import overlays as overlays_mod
from .analysis import portfolio as portfolio_mod
from .analysis import regime as regime_mod
from .analysis import stats as stats_mod
from .analysis import verdict as verdict_mod
from .collectors.base import ALL_COLLECTORS
from .config import (CATEGORIES, INDICATORS, INDICATOR_BY_KEY, Indicator,
                     priority_of, source_tier, is_krw_convertible, so_what)

# 빈도별 신선도 임계(시간) — 초과 시 'stale' 경고. 월별은 발표주기 고려해 넉넉히.
_STALE_HOURS = {"D": 24 * 3, "W": 24 * 10, "M": 24 * 55}
# 변화 기준 라벨(직전 관측이 며칠/주/월 전인지)
_BASIS_LABEL = {"D": "전일", "W": "전주", "M": "전월"}


def _staleness(as_of: Optional[str], freq: str) -> Optional[dict]:
    if not as_of:
        return None
    try:
        dt = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    return {"stale": age_h > _STALE_HOURS.get(freq, 72),
            "age_days": int(age_h // 24), "age_hours": round(age_h, 1)}


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


def _fmt_krw(v: Optional[float]) -> str:
    """원화 환산 금액을 만/억 단위로 가독화(예: 7,450,000 → ₩745만, 1.3e8 → ₩1.30억)."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e8:
        return f"₩{v / 1e8:,.2f}억"
    if a >= 1e4:
        return f"₩{v / 1e4:,.0f}만"
    return f"₩{v:,.0f}"


def _pct_label(pct: Optional[float]) -> tuple[Optional[str], Optional[str]]:
    """백분위(0~100) → (직관 라벨, 색상 레벨코드). 자기 역사 범위 내 위치를 평이하게."""
    if pct is None:
        return None, None
    if pct >= 90:
        return "매우 높음", "vh"
    if pct >= 70:
        return "높음", "h"
    if pct > 30:
        return "보통", "m"
    if pct > 10:
        return "낮음", "l"
    return "매우 낮음", "vl"


def _pct_plain(pct: Optional[float], span: Optional[str]) -> str:
    """백분위 → 평이한 위치 표현(예: '최근 약 5년 중 상위 17%'). %ile 외계어 대체용."""
    if pct is None:
        return ""
    base = f"최근 {span} 중 " if span else ""
    p = round(pct)
    top = 100 - p
    if top <= 0:
        return f"{base}최고 수준 (역대 상단)"
    if p <= 0:
        return f"{base}최저 수준 (역대 하단)"
    tag = " (높은 편)" if p >= 70 else " (낮은 편)" if p <= 30 else ""
    if p >= 50:
        return f"{base}상위 {top}%{tag}"
    return f"{base}하위 {p}%{tag}"


def _easy_impact(ind: Indicator) -> Optional[dict]:
    """'쉬운 설명' 모드용 — 오를수록 주식에 호재/부담인지 평이 신호(up_is_good 기반).

    주가지수·섹터 자체는 '오를수록 호재'가 자명하므로 생략(노이즈 방지). 방향이
    모호한 지표(금리·환율·비율 등 up_is_good=None)는 태그 없이 so_what 설명에 맡긴다.
    """
    if ind.category in ("equity", "sector"):
        return None
    if ind.up_is_good is True:
        return {"cls": "good", "txt": "오를수록 주식에 호재"}
    if ind.up_is_good is False:
        return {"cls": "bad", "txt": "오를수록 주식에 부담"}
    return None


def _krw_rates(obs: dict) -> dict:
    """관측값에서 호가통화→원화 환율(현재, 직전)을 구성. USD/JPY/EUR/CNY 지원(HKD 등 제외)."""
    def vp(k):
        r = obs.get(k) or {}
        v, c = r.get("value"), r.get("change")
        return v, ((v - c) if (v is not None and c is not None) else None)

    usdkrw, usdkrw_p = vp("usdkrw")
    usdjpy, usdjpy_p = vp("usdjpy")
    eurusd, eurusd_p = vp("eurusd")
    usdcny, usdcny_p = vp("usdcny")
    rates: dict = {"KRW": (1.0, 1.0)}
    if usdkrw:
        rates["USD"] = (usdkrw, usdkrw_p)
        if usdjpy:
            rates["JPY"] = (usdkrw / usdjpy,
                            (usdkrw_p / usdjpy_p) if (usdkrw_p and usdjpy_p) else None)
        if eurusd:
            rates["EUR"] = (eurusd * usdkrw,
                            (eurusd_p * usdkrw_p) if (eurusd_p and usdkrw_p) else None)
        if usdcny:
            rates["CNY"] = (usdkrw / usdcny,
                            (usdkrw_p / usdcny_p) if (usdkrw_p and usdcny_p) else None)
    return rates


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


def _indicator_view(ind: Indicator, obs: dict, rates: Optional[dict] = None) -> dict:
    row = obs.get(ind.key) or {}
    ok = bool(row.get("ok", 0)) and row.get("value") is not None
    value = row.get("value")
    change = row.get("change")
    change_pct = row.get("change_pct")

    # 금리·비율(% 단위) 지표는 '절대 변화(%p)'가 주된 표시여야 자연스럽다.
    # (예: CPI 전년비 3.78%→4.17% 는 +10.25%(상대)가 아니라 +0.39%p 가 거시 관행)
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

    st = _staleness(row.get("as_of"), ind.freq) if ok else None
    enr = (stats_mod.enrich(ind.key, value, ind.freq, unit=ind.unit) if ok
           else {"ctx": None, "momentum": None, "risk": None})
    ctx, mom, risk = enr["ctx"], enr["momentum"], enr.get("risk")

    # 백분위 → 비전문가용 직관 라벨/레벨(매우 낮음~매우 높음)
    pct_label, pct_level = _pct_label(ctx["percentile"] if ctx else None)

    # 원화 환산(보유 가능한 가격자산 + 환율 보유 시): 값·등락 모두 FX 포함
    krw = False
    krw_value_fmt = krw_value_exact = krw_change_main_fmt = krw_tone = ""
    if ok and rates and is_krw_convertible(ind.key) and ind.ccy in rates:
        rate, rate_prev = rates[ind.ccy]
        if rate:
            krw = True
            kv = value * rate
            krw_value_fmt = _fmt_krw(kv)
            krw_value_exact = f"₩{kv:,.0f}"
            asset_prev = (value - change) if change is not None else None
            if asset_prev and rate_prev:
                kpct = (kv / (asset_prev * rate_prev) - 1) * 100
                krw_change_main_fmt = f"{kpct:+.2f}%"
                krw_tone = _tone(ind, kpct)

    return {
        "key": ind.key,
        "label": ind.label,
        "category": ind.category,
        "unit": ind.unit,
        "note": ind.note,
        "priority": priority_of(ind.key),
        "tier": source_tier(ind.key),          # 시장/공식/파생 (데이터 신뢰 구분)
        "ok": ok,
        "value": value,
        "value_fmt": fmt_value(ind, value) if ok else "—",
        "change": change,
        "change_pct": change_pct,
        "has_change": has_change,
        "change_main_fmt": change_main_fmt,   # 주 표시(%p 또는 상대%)
        "change_sub_fmt": change_sub_fmt,     # 보조 표시(절대 변화; % 지표는 생략)
        "basis": _BASIS_LABEL.get(ind.freq, "전일"),  # 변화 기준(전일/전주/전월)
        "freq": ind.freq,                             # D/W/M — 갱신주기 안내(쉬운 설명)
        "sign": ("pos" if (delta or 0) > 0 else "neg" if (delta or 0) < 0 else "zero"),
        "tone": _tone(ind, delta),
        "as_of": row.get("as_of"),
        "stale": bool(st and st["stale"]),
        "age_days": st["age_days"] if st else None,
        "percentile": ctx["percentile"] if ctx else None,   # 기간내 백분위(0~100)
        "pct_n": ctx["n"] if ctx else None,                  # 백분위 표본수
        "pct_span": ctx["span"] if ctx else None,            # 백분위 실제 기간(예: '약 5년')
        "pct_label": pct_label,                              # 직관 라벨(매우 낮음~매우 높음)
        "pct_level": pct_level,                              # 색상 레벨(vl/l/m/h/vh)
        "pct_plain": _pct_plain(ctx["percentile"] if ctx else None,
                                ctx["span"] if ctx else None),  # 평이 위치(쉬운 설명)
        "zscore": ctx["zscore"] if ctx else None,
        "anomaly": bool(ctx and ctx["anomaly"]),             # |z|≥3 통계적 이상치
        "momentum": mom,                                     # {w1,m1,m3,ytd} (일별만)
        # 리스크 지표(일별 가격형만): 실현변동성·최대낙폭·52주 고저거리
        "rvol": risk.get("rvol") if risk else None,
        "mdd": risk.get("mdd") if risk else None,
        "dist_high": risk.get("dist_high") if risk else None,
        "dist_low": risk.get("dist_low") if risk else None,
        "hi52": risk.get("hi") if risk else None,
        "lo52": risk.get("lo") if risk else None,
        "krw": krw,                                          # 원화 환산 가능 여부
        "krw_value_fmt": krw_value_fmt,                      # ₩만/억 가독화
        "krw_value_exact": krw_value_exact,                  # ₩원 단위(툴팁)
        "krw_change_main_fmt": krw_change_main_fmt,          # 원화기준 등락%(FX 포함)
        "krw_tone": krw_tone,
        "so_what": so_what(ind.key),                         # 핵심지표 한 줄 함의
        "easy_impact": _easy_impact(ind),                    # 쉬운 설명: 오를수록 호재/부담
        "error": row.get("error"),
    }


def build_groups(obs: dict, rates: Optional[dict] = None) -> list[dict]:
    groups = []
    for cat_key, cat_label in CATEGORIES.items():
        items = [_indicator_view(ind, obs, rates) for ind in INDICATORS if ind.category == cat_key]
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


_SURP_LABEL = {"beat": "예상상회 ▲", "miss": "예상하회 ▼", "inline": "부합"}


def _calendar_view(rows: list[dict]) -> list[dict]:
    """캘린더 행에 서프라이즈(실제 vs 예상) 표기 추가."""
    out = []
    for r in rows:
        e = dict(r)
        s = (calendar_util.surprise(e.get("actual"), e.get("forecast"))
             if (e.get("actual") and e.get("forecast")) else None)
        e["surprise"] = {"dir": s["dir"], "label": _SURP_LABEL[s["dir"]]} if s else None
        out.append(e)
    return out


def _recent_alerts(limit: int = 6) -> list[dict]:
    out = []
    for e in repo.recent_source_events(limit):
        out.append({
            "source": e["source"], "label": _source_label(e["source"]),
            "kind": e["kind"], "detail": e.get("detail", ""),
            "consecutive": e.get("consecutive", 0), "ts_utc": e.get("ts_utc"),
        })
    return out


def build_dashboard(snapshot_id: Optional[int] = None) -> dict:
    snap = repo.get_snapshot(snapshot_id) if snapshot_id else repo.latest_snapshot()
    if not snap:
        return {"empty": True, "groups": [], "health": [], "news": [],
                "calendar": [], "briefing": None, "briefing_cached": False,
                "snapshot": None, "regime": None, "alerts": [], "overlays": None,
                "risk_alerts": None, "allocation": None, "holdable": [], "verdict": None}

    sid = snap["id"]
    obs = repo.get_observations(sid)

    # 브리핑 캐시 폴백: 현재 스냅샷 브리핑이 없거나 실패면 최근 성공 브리핑 표시
    cur_brief = repo.get_briefing_for_snapshot(sid)
    briefing_cached = False
    if cur_brief and cur_brief.get("ok"):
        briefing = cur_brief
    else:
        fb = repo.latest_briefing()
        if fb and (not cur_brief or fb.get("id") != cur_brief.get("id")):
            briefing, briefing_cached = fb, True
        else:
            briefing = cur_brief

    # 레짐: 과거 스냅샷 조회면 '그 시점' 기록값, 최신이면 현재 재계산
    if snapshot_id is not None and snap.get("regime_score") is not None:
        regime = regime_mod.stored_regime_view(snap)
    else:
        try:
            regime = regime_mod.detect_regime(20)
        except Exception:  # noqa: BLE001
            regime = None

    # 리스크 경보·권고비중은 '현재' 위험상태 도구 → 라이브에서만(history 의존)
    risk_alerts = alerts_mod.evaluate_alerts(obs, regime) if snapshot_id is None else None

    return {
        "empty": False,
        "snapshot": snap,
        "groups": build_groups(obs, _krw_rates(obs)),
        "health": health_view(repo.get_health(sid)),
        "news": repo.recent_news(30),
        "calendar": _calendar_view(repo.upcoming_calendar(30)),
        "briefing": briefing,
        "briefing_cached": briefing_cached,
        "regime": regime,
        "alerts": _recent_alerts(),
        # 오버레이는 현재 history 기준 → 과거 스냅샷 리포트엔 부적합(시점 불일치)하므로 라이브에서만
        "overlays": overlays_mod.build_overlays() if snapshot_id is None else None,
        "risk_alerts": risk_alerts,
        # 권고 자산비중 = 현재 레짐 점수의 행동 번역 → 라이브에서만(과거 리포트엔 부적합)
        "allocation": allocation_mod.recommend_allocation(regime) if snapshot_id is None else None,
        # 신호등+경보를 화해시킨 '오늘 한 줄 결론'(라이브에서만; 경보 의존)
        "verdict": verdict_mod.make_verdict(regime, risk_alerts) if snapshot_id is None else None,
        # 포트폴리오 입력기용 보유가능 자산(정적 목록; VaR 계산은 /api/portfolio)
        "holdable": portfolio_mod.holdable_assets(),
    }
