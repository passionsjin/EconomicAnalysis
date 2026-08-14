"""신한금융지주(055550.KS) 전용 분석 모듈.

Yahoo Finance chart API(주가·이력) + quoteSummary/Naver Finance(밸류에이션·손익)로
주가·밸류에이션·분기실적·수익성을 종합한다. 은행 지주 특성상 매출/마진 대신
PBR·배당수익률·ROE를 중심에 두고, API로 잡히지 않는 은행 고유 지표
(NIM·BIS·CET1·NPL 등)와 전략·주주환원·비은행 이행현황은 공개 IR 기반 정적 데이터로 보강한다.
LLM 종합 분석(은행/금융지주 전담 프롬프트)도 여기서 생성·캐시한다.

구조·수집·캐시 골격은 현대차 모듈(hyundai.py)과 동일 패턴 — presenter/config/DB를 우회하고
모듈 전역 메모리 캐시만 사용한다.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote as urlquote

from ..collectors import http
from ..logging_setup import logger
from . import llm

_SYMBOL = "055550.KS"
_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
_CACHE_TTL = 3600        # 데이터 캐시 유효시간(초) — 매시 파이프라인에서 갱신
_ANALYSIS_TTL = 3 * 3600  # LLM 분석 캐시 — 3시간

_lock = threading.Lock()
_cache: dict = {"data": None, "ts": 0.0}
_analysis_lock = threading.Lock()
_analysis_cache: dict = {"data": None, "ts": 0.0}
_analysis_generating = threading.Event()  # 생성 중 플래그


# ─────────────────────────── 핵심 은행 지표 (정적·공개 IR 기반) ───────────────────────────
# 주의: NIM·BIS·CET1·NPL 등은 Yahoo/Naver API로 잡히지 않아 공개 IR/실적발표 기반 하드코딩.
# 기준: 2025 회계연도 실적발표(2026년 초 공시) 근사치 — 최신 분기 공시로 검증 후 갱신할 것.

_BANK_KPI_ASOF = "2025 회계연도 기준 (공개 IR 근사·검증 필요)"

# tone: good | warn | bad | neutral / higher_better: 값이 클수록 좋은 지표인지
_BANK_KPI: list[dict] = [
    {
        "group": "수익성",
        "items": [
            {"label": "그룹 NIM", "hint": "순이자마진",
             "value": 2.00, "unit": "%", "fmt": "약 2.00%",
             "tone": "neutral", "note": "은행 단독 NIM 약 1.60% — 금리 하락기 마진 압박"},
            {"label": "ROE", "hint": "자기자본이익률",
             "value": 9.5, "unit": "%", "fmt": "약 9.5%",
             "tone": "warn", "note": "밸류업 목표 10%+ 근접 — 주주환원 확대의 근거"},
            {"label": "ROA", "hint": "총자산이익률",
             "value": 0.65, "unit": "%", "fmt": "약 0.65%",
             "tone": "neutral", "note": "국내 은행 지주 평균 수준"},
        ],
    },
    {
        "group": "자본 건전성",
        "items": [
            {"label": "CET1 비율", "hint": "보통주자본비율",
             "value": 13.1, "unit": "%", "fmt": "약 13.1%",
             "tone": "good", "note": "밸류업 기준선(13%) 상회 — 초과자본이 환원 재원"},
            {"label": "BIS 총자본비율", "hint": "국제결제은행 기준",
             "value": 15.9, "unit": "%", "fmt": "약 15.9%",
             "tone": "good", "note": "규제 최소치(10.5%) 대비 충분한 완충"},
        ],
    },
    {
        "group": "자산 건전성",
        "items": [
            {"label": "고정이하여신비율", "hint": "NPL 비율",
             "value": 0.62, "unit": "%", "fmt": "약 0.62%",
             "tone": "neutral", "note": "부동산 PF·연체 증가로 소폭 상승 추세 — 모니터링 필요"},
            {"label": "연체율", "hint": "1개월 이상",
             "value": 0.36, "unit": "%", "fmt": "약 0.36%",
             "tone": "neutral", "note": "가계·중소기업 연체 완만한 상승"},
            {"label": "NPL 커버리지", "hint": "대손충당금적립률",
             "value": 155, "unit": "%", "fmt": "약 155%",
             "tone": "good", "note": "부실 대비 충당금 여력 충분"},
        ],
    },
]


# ─────────────────────────── 전략 플랜 (정적·공개 IR 기반, 2026년 7월 현황) ───────────────────────────

# status: achieved | in_progress | planned | delayed
_PLANS: list[dict] = [
    {
        "horizon": "short",
        "title": "단기 플랜 (2024~2026)",
        "icon": "📌",
        "items": [
            {
                "label": "밸류업(기업가치 제고) 프로그램 이행",
                "detail": "CET1 13% 기반 자본정책 발표 — 총주주환원율 단계적 상향, 자사주 매입·소각 정례화",
                "target_date": "2024 발표 / 2027 목표",
                "progress_pct": 60,
                "milestone": "밸류업 계획 공시 완료 / 분기 자사주 매입·소각 진행 중",
                "status": "in_progress",
                "kpi": {
                    "actual": "총주주환원율 약 42% (2024)",
                    "target": "50% (2027)",
                    "actual_val": 42, "target_val": 50,
                    "unit": "% 총주주환원율",
                    "on_track": True,
                    "note": "국내 금융지주 중 선도적 환원 정책 — CET1 여력이 관건",
                },
            },
            {
                "label": "디지털 플랫폼 '뉴 쏠(SOL)' 고도화",
                "detail": "그룹 슈퍼앱 통합 — 은행·카드·증권·보험 원앱 경험, AI 기반 개인화 자산관리",
                "target_date": "2025~2026",
                "progress_pct": 70,
                "milestone": "뉴 쏠 리뉴얼 출시 / MAU 1천만명대 확대 중",
                "status": "in_progress",
                "kpi": {
                    "actual": "MAU 약 1,000만명",
                    "target": "1,200만명+",
                    "actual_val": 1000, "target_val": 1200,
                    "unit": "만 MAU",
                    "on_track": True,
                    "note": "비대면 채널 수익화·교차판매 확대가 핵심",
                },
            },
            {
                "label": "부동산 PF·연체 건전성 관리",
                "detail": "부동산 PF 사업장 정리·충당금 선제 적립으로 자산건전성 방어",
                "target_date": "2025~2026 지속",
                "progress_pct": 55,
                "milestone": "충당금 선제 적립 / NPL 비율 0.6%대 관리",
                "status": "in_progress",
                "kpi": {
                    "actual": "NPL 비율 약 0.62%",
                    "target": "0.6% 이하 안정화",
                    "actual_val": 62, "target_val": 60,
                    "unit": "bp (고정이하여신)",
                    "on_track": None,
                    "note": "부동산 경기·PF 익스포저가 하방 변수",
                },
            },
        ],
    },
    {
        "horizon": "mid",
        "title": "중기 플랜 (2025~2030)",
        "icon": "🎯",
        "items": [
            {
                "label": "ROE 10%+ 지속 달성",
                "detail": "자본 효율 중심 경영 — 저수익 자산 축소, 비은행·수수료 이익 확대로 자본생산성 제고",
                "target_date": "2027년~ 지속",
                "progress_pct": 50,
                "milestone": "ROE 9%대 → 10%+ 목표",
                "status": "in_progress",
                "kpi": {
                    "actual": "ROE 약 9.5%",
                    "target": "10%+ 지속",
                    "actual_val": 95, "target_val": 100,
                    "unit": "‰ (ROE ×10)",
                    "on_track": True,
                    "note": "금리 하락기 NIM 압박 속 비이자이익 확대가 관건",
                },
            },
            {
                "label": "비은행 이익 비중 40%+ 확대",
                "detail": "신한카드(업계 1위)·신한투자증권·신한라이프 등 비은행 계열 이익 기여 확대",
                "target_date": "2030년",
                "progress_pct": 60,
                "milestone": "비은행 이익 비중 30%대 후반 → 40%+ 목표",
                "status": "in_progress",
                "kpi": {
                    "actual": "약 35% (비은행 이익 비중)",
                    "target": "40%+",
                    "actual_val": 35, "target_val": 40,
                    "unit": "% 이익 비중",
                    "on_track": True,
                    "note": "은행 편중 완화 — 이익 안정성·밸류에이션 재평가 요인",
                },
            },
            {
                "label": "글로벌 부문 이익 기여 확대 (베트남 중심)",
                "detail": "신한베트남은행(현지 최대 외국계) 성장 + 일본 SBJ은행·동남아 리테일 확장",
                "target_date": "2030년",
                "progress_pct": 45,
                "milestone": "글로벌 이익 비중 10%대 → 15%+ 목표",
                "status": "in_progress",
                "kpi": {
                    "actual": "글로벌 이익 비중 약 12%",
                    "target": "15%+",
                    "actual_val": 12, "target_val": 15,
                    "unit": "% 이익 비중",
                    "on_track": True,
                    "note": "베트남 성장 지속 — 신흥국 리스크 분산 필요",
                },
            },
        ],
    },
    {
        "horizon": "long",
        "title": "장기 플랜 (2030~)",
        "icon": "🚀",
        "items": [
            {
                "label": "총주주환원율 50%+ 정착",
                "detail": "이익 성장과 CET1 관리 병행하며 배당·자사주 소각 통한 주주환원 지속 상향·유지",
                "target_date": "2027년 도달 / 이후 유지",
                "progress_pct": 40,
                "milestone": "42%(2024) → 50%(2027) → 이후 유지",
                "status": "planned",
                "kpi": {
                    "actual": "약 42% (2024)",
                    "target": "50%+ 정착",
                    "actual_val": 42, "target_val": 50,
                    "unit": "% 총주주환원율",
                    "on_track": True,
                    "note": "선진 금융지주 수준 환원율 지향 — PBR 재평가의 핵심 동력",
                },
            },
            {
                "label": "AI 기반 금융 플랫폼 전환",
                "detail": "생성형 AI 상담·신용평가·자산관리 내재화로 비용효율(CIR) 개선 및 신수익 창출",
                "target_date": "2030년대",
                "progress_pct": 25,
                "milestone": "AI 상담·내부업무 자동화 파일럿 / 전면 적용 준비",
                "status": "planned",
                "kpi": {
                    "actual": "파일럿·부분 적용 단계",
                    "target": "전 채널 AI 내재화",
                    "actual_val": 25, "target_val": 100,
                    "unit": "% 전환 진척",
                    "on_track": None,
                    "note": "규제·데이터 거버넌스가 속도 변수",
                },
            },
            {
                "label": "비은행·글로벌 이익 균형 포트폴리오",
                "detail": "은행·비은행·글로벌 3축 이익 균형으로 경기 방어력 강화 — 이익 변동성 축소",
                "target_date": "2030년대",
                "progress_pct": 35,
                "milestone": "은행 편중 완화 진행 중",
                "status": "planned",
                "kpi": {
                    "actual": "비은행 35% · 글로벌 12%",
                    "target": "비은행 40%+ · 글로벌 15%+",
                    "actual_val": 35, "target_val": 40,
                    "unit": "% (비은행 이익)",
                    "on_track": True,
                    "note": "포트폴리오 다각화가 장기 밸류에이션 리레이팅 근거",
                },
            },
        ],
    },
]

_STATUS_META: dict[str, dict] = {
    "achieved":    {"label": "✅ 달성",    "tone": "good"},
    "in_progress": {"label": "🟡 진행 중",  "tone": "warn"},
    "planned":     {"label": "📋 예정",    "tone": "neutral"},
    "delayed":     {"label": "⚠ 지연",    "tone": "bad"},
}

_TRACK_META: dict = {
    True:  {"label": "정상 궤도", "tone": "good"},
    False: {"label": "주의 필요", "tone": "warn"},
    None:  {"label": "변수 존재", "tone": "neutral"},
}

# 은행 섹터 P/E 비교 (국내 4대 금융지주 + 글로벌 은행, 2025~2026 밸류업 랠리 후 근사치·검증 필요)
_SECTOR_PE: dict[str, float] = {
    "KB금융":        8.0,
    "하나금융":      6.5,
    "우리금융":      5.5,
    "JPMorgan":      13.0,
    "MUFG":          12.0,
    "섹터 중앙값":   7.5,   # 국내 지주 중앙값 기준 (vs 평가에 사용)
}
# 은행은 PBR이 핵심 지표 — 밸류업 랠리로 재평가됐으나 대부분 여전히 장부가 이하
_SECTOR_PBR: dict[str, float] = {
    "KB금융":        0.72,
    "하나금융":      0.62,
    "우리금융":      0.52,
    "JPMorgan":      2.10,
    "MUFG":          1.10,
    "섹터 중앙값":   0.70,   # 국내 지주 중앙값 기준 (vs 평가에 사용)
}


# ─────────────────────────── 주주환원·밸류업 (정적·공개 IR 기반) ───────────────────────────

_SHAREHOLDER_ASOF = "2024 밸류업 계획 + 2025 실적 기준 (공개 IR 근사·검증 필요)"

# 헤드라인 지표 카드
_SHAREHOLDER_CARDS: list[dict] = [
    {"title": "총주주환원율", "val": "약 42%", "sub": "2024 기준 · 2027년 50% 목표", "tone": "good"},
    {"title": "배당성향", "val": "약 35%", "sub": "분기 균등배당 정착", "tone": "neutral"},
    {"title": "자사주 매입·소각", "val": "연 5천억+ ", "sub": "정례 매입 후 소각 — 주식수 감축", "tone": "good"},
    {"title": "주식수 감축 목표", "val": "4.5억주 이하", "sub": "5천만주+ 소각 통한 EPS 제고", "tone": "good"},
]

# 밸류업 이행 세부 (robot 섹션과 동일 구조: 카테고리 → 항목)
_SHAREHOLDER_PLANS: list[dict] = [
    {
        "category": "자본 환원 정책",
        "icon": "💰",
        "items": [
            {
                "label": "총주주환원율 50% 상향",
                "detail": "배당 + 자사주 소각 합산 환원율을 2027년까지 50%로 단계적 상향",
                "target_date": "2027년",
                "target": "50%",
                "actual": "약 42% (2024)",
                "actual_val": 42, "target_val": 50,
                "unit": "% 총주주환원율",
                "progress_pct": 84,
                "on_track": True,
                "note": "국내 금융지주 최상위권 환원율 — CET1 13% 초과분이 재원",
                "status": "in_progress",
            },
            {
                "label": "자사주 매입 후 소각 정례화",
                "detail": "분기·연간 자사주 매입 후 전량 소각으로 주식수 축소·EPS 제고",
                "target_date": "매년 지속",
                "target": "누적 소각 확대",
                "actual": "연 5천억원+ 매입·소각",
                "actual_val": 60, "target_val": 100,
                "unit": "% 프로그램 진척",
                "progress_pct": 60,
                "on_track": True,
                "note": "발행주식수 감소 → 주당 지표 개선의 직접 동력",
                "status": "in_progress",
            },
        ],
    },
    {
        "category": "자본비율 관리",
        "icon": "🛡️",
        "items": [
            {
                "label": "CET1 13% 기반 자본정책",
                "detail": "보통주자본비율 13% 관리선 유지 — 초과 자본은 주주환원 재원으로 활용",
                "target_date": "상시",
                "target": "CET1 13% 유지",
                "actual": "약 13.1% (2025)",
                "actual_val": 131, "target_val": 130,
                "unit": "‰ (CET1 ×10)",
                "progress_pct": 90,
                "on_track": True,
                "note": "위험가중자산 관리·이익 유보 균형이 환원 지속성의 관건",
                "status": "in_progress",
            },
        ],
    },
]


# ─────────────────────────── 비은행·글로벌 포트폴리오 (정적·공개 IR 기반) ───────────────────────────

_NONBANK_ASOF = "2025 실적 기준 (공개 IR 근사·검증 필요)"

# 비은행 이익 비중 구성 (근사·검증 필요)
_NONBANK_MIX: list[dict] = [
    {"name": "신한카드", "note": "국내 카드 업계 1위", "pct": 14},
    {"name": "신한투자증권", "note": "증권·IB", "pct": 8},
    {"name": "신한라이프", "note": "생명보험", "pct": 8},
    {"name": "기타 (캐피탈·자산운용 등)", "note": "비은행 기타", "pct": 5},
]
_NONBANK_RATIO = {"actual": 35, "target": 40}  # 비은행 이익 비중 % (검증 필요)

_NONBANK_PLANS: list[dict] = [
    {
        "category": "비은행 계열 강화",
        "icon": "🏢",
        "items": [
            {
                "label": "신한카드 업계 1위 유지·수익 다변화",
                "detail": "결제·할부·데이터 사업 확장, 신한 플랫폼 연계 교차판매",
                "target_date": "지속",
                "target": "카드 1위 유지",
                "actual": "취급액·순이익 업계 1위",
                "actual_val": 90, "target_val": 100,
                "unit": "% 목표 대비",
                "progress_pct": 90,
                "on_track": True,
                "note": "가맹점 수수료 규제·조달금리가 수익성 변수",
                "status": "in_progress",
            },
            {
                "label": "비은행 이익 비중 40%+ 확대",
                "detail": "증권·보험·카드 이익 기여 제고로 은행 편중 완화",
                "target_date": "2030년",
                "target": "40%+",
                "actual": "약 35%",
                "actual_val": 35, "target_val": 40,
                "unit": "% 이익 비중",
                "progress_pct": 65,
                "on_track": True,
                "note": "이익 안정성 개선 → 밸류에이션 리레이팅 요인",
                "status": "in_progress",
            },
        ],
    },
    {
        "category": "글로벌 확장",
        "icon": "🌏",
        "items": [
            {
                "label": "신한베트남은행 성장 지속",
                "detail": "베트남 현지 최대 외국계 은행 — 리테일·기업금융 확대",
                "target_date": "지속",
                "target": "베트남 이익 성장",
                "actual": "현지 외국계 1위 · 순이익 성장",
                "actual_val": 70, "target_val": 100,
                "unit": "% 목표 대비",
                "progress_pct": 70,
                "on_track": True,
                "note": "신흥국 성장 수혜 — 환율·현지 규제 변수",
                "status": "in_progress",
            },
            {
                "label": "글로벌 이익 비중 15% 확대",
                "detail": "베트남 + 일본 SBJ + 동남아 리테일로 해외 이익 기여 제고",
                "target_date": "2030년",
                "target": "15%+",
                "actual": "약 12%",
                "actual_val": 12, "target_val": 15,
                "unit": "% 이익 비중",
                "progress_pct": 55,
                "on_track": True,
                "note": "국내 저성장 보완 — 지역 분산으로 리스크 완화",
                "status": "in_progress",
            },
        ],
    },
]

# 국내 금융지주 피어 (Yahoo Finance ticker) — 1년 수익률 비교용
_BANK_PEERS: dict[str, str] = {
    "KB금융 (105560)":   "105560.KS",
    "하나금융 (086790)": "086790.KS",
    "우리금융 (316140)": "316140.KS",
}


def _fetch_bank_peer_returns() -> list[dict]:
    """국내 금융지주 피어 1년 수익률 조회 (Yahoo Finance chart API)."""
    results = []
    for name, ticker in _BANK_PEERS.items():
        try:
            path = f"/v8/finance/chart/{ticker}"
            params = {"range": "1y", "interval": "1mo", "includePrePost": "false"}
            for host in _HOSTS:
                try:
                    raw = http.get_json(host + path, params=params, retries=1)
                    chart = raw.get("chart", {}).get("result") or []
                    if not chart:
                        continue
                    meta = chart[0].get("meta") or {}
                    closes = (chart[0].get("indicators", {})
                              .get("quote", [{}])[0].get("close") or [])
                    closes = [c for c in closes if c is not None]
                    if len(closes) < 2:
                        continue
                    ret = round((closes[-1] / closes[0] - 1) * 100, 1)
                    cur = meta.get("regularMarketPrice") or closes[-1]
                    results.append({
                        "name": name, "ticker": ticker,
                        "ret_1y": ret,
                        "current": cur,
                        "currency": meta.get("currency", "KRW"),
                        "fmt_ret": f"+{ret:.1f}%" if ret > 0 else f"{ret:.1f}%",
                        "tone": "good" if ret > 0 else "bad",
                    })
                    break
                except Exception:
                    continue
        except Exception:
            pass
    results.sort(key=lambda x: x["ret_1y"], reverse=True)
    return results


# ─────────────────────────── 포맷 헬퍼 ───────────────────────────

def _fmt_krw(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e12:
        return f"{sign}{a / 1e12:,.{decimals}f}조원"
    if a >= 1e8:
        return f"{sign}{a / 1e8:,.{decimals}f}억원"
    if a >= 1e4:
        return f"{sign}{a / 1e4:,.0f}만원"
    return f"₩{v:,.0f}"


def _fmt_price(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"₩{int(v):,}"


def _tone(chg: Optional[float]) -> str:
    if chg is None or chg == 0:
        return "neutral"
    return "good" if chg > 0 else "bad"


def _pct(v: Optional[float]) -> Optional[float]:
    return round(v * 100, 1) if v is not None else None


# ─────────────────────────── Yahoo Finance 조회 ───────────────────────────

def _fetch_chart(symbol: str, rng: str = "1y") -> dict:
    path = f"/v8/finance/chart/{urlquote(symbol, safe='')}"
    params = {"range": rng, "interval": "1d", "includePrePost": "false"}
    last_exc: Exception | None = None
    for host in _HOSTS:
        try:
            data = http.get_json(host + path, params=params, retries=1)
            result = (data.get("chart") or {}).get("result") or []
            if not result:
                err = (data.get("chart") or {}).get("error")
                raise ValueError(f"빈 응답 {err}")
            return result[0]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise last_exc  # type: ignore[misc]


def _fetch_quote_summary(symbol: str) -> Optional[dict]:
    modules = ",".join([
        "financialData",
        "summaryDetail",
        "defaultKeyStatistics",
        "incomeStatementHistoryQuarterly",
        "incomeStatementHistory",
    ])
    param_variants = [
        {"modules": modules, "lang": "ko-KR", "region": "KR", "formatted": "true"},
        {"modules": modules, "lang": "en-US", "region": "US", "formatted": "true"},
    ]
    for params in param_variants:
        for host in _HOSTS:
            for api_ver in ("v10", "v11"):
                path = f"/{api_ver}/finance/quoteSummary/{urlquote(symbol, safe='')}"
                try:
                    data = http.get_json(host + path, params=params, retries=1)
                    result = (data.get("quoteSummary") or {}).get("result") or []
                    if result:
                        logger.info("신한지주 quoteSummary OK (%s %s %s)", api_ver, host, params.get("region"))
                        return result[0]
                except Exception as exc:  # noqa: BLE001
                    logger.debug("신한지주 quoteSummary 시도 실패 (%s %s): %s", api_ver, host, str(exc)[:80])
    logger.warning("신한지주 quoteSummary 모든 시도 실패 — 차트 데이터만 사용")
    return None


# ─────────────────────────── 파싱 헬퍼 ───────────────────────────

def _rv(obj: dict, key: str) -> Optional[float]:
    """raw 숫자 추출 — formatted dict({raw:X}) 또는 직접 숫자 모두 처리."""
    v = obj.get(key)
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("raw") if v.get("raw") is not None else v.get("fmt")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _rv_str(obj: dict, key: str) -> Optional[str]:
    v = obj.get(key)
    if v is None:
        return None
    if isinstance(v, dict):
        return str(v.get("raw") or v.get("fmt") or "")
    return str(v)


# ─────────────────────────── Naver Finance 데이터 소스 ───────────────────────────
# Yahoo quoteSummary 가 한국 주식에 막혀 있을 때 대체 소스로 사용.

_NAVER_HOST = "https://m.stock.naver.com"


def _naver_num(s: str) -> Optional[float]:
    """'12,345', '12.31배', '2.51%', '10,000원' → float. 변환 불가 시 None."""
    if not s or s in ("-", "—", ""):
        return None
    cleaned = s.replace(",", "").replace("배", "").replace("%", "").replace("원", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _naver_krw(s: str) -> Optional[float]:
    """'81조 7,495억' → raw KRW float."""
    import re as _re
    if not s:
        return None
    s2 = s.replace(",", "").replace(" ", "")
    total = 0.0
    m = _re.search(r"(\d+)조", s2)
    if m:
        total += int(m.group(1)) * 1_000_000_000_000
    m = _re.search(r"(\d+(?:\.\d+)?)억", s2)
    if m:
        total += float(m.group(1)) * 100_000_000
    return total if total else None


def _fetch_naver_integration(code: str) -> Optional[dict]:
    try:
        return http.get_json(_NAVER_HOST + f"/api/stock/{code}/integration", retries=1)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Naver integration 실패: %s", str(exc)[:80])
        return None


def _fetch_naver_finance(code: str, period_type: str) -> Optional[dict]:
    """period_type: 'quarter' | 'annual'"""
    try:
        return http.get_json(_NAVER_HOST + f"/api/stock/{code}/finance/{period_type}", retries=1)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Naver finance/%s 실패: %s", period_type, str(exc)[:80])
        return None


def _nfind(infos: list, code: str) -> str:
    for item in infos:
        if item.get("code") == code:
            return item.get("value", "")
    return ""


def _parse_naver_valuation(nav_int: Optional[dict]) -> dict:
    """Naver integration API → valuation dict (Yahoo quoteSummary 대체)."""
    infos = (nav_int or {}).get("totalInfos") or []

    per_t  = _naver_num(_nfind(infos, "per"))
    per_f  = _naver_num(_nfind(infos, "cnsPer"))
    pbr    = _naver_num(_nfind(infos, "pbr"))
    bps    = _naver_num(_nfind(infos, "bps"))
    div_y  = _naver_num(_nfind(infos, "dividendYieldRatio"))
    div_r  = _naver_num(_nfind(infos, "dividend"))
    mc     = _naver_krw(_nfind(infos, "marketValue"))

    def _vs(val: Optional[float], med: float) -> Optional[str]:
        if val is None:
            return None
        return "할인" if val < med * 0.82 else "프리미엄" if val > med * 1.20 else "적정"

    per_med = _SECTOR_PE.get("섹터 중앙값", 6.0)
    pbr_med = _SECTOR_PBR.get("섹터 중앙값", 0.50)

    return {
        "per_trailing":  round(per_t, 1) if per_t is not None else None,
        "per_forward":   round(per_f, 1) if per_f is not None else None,
        "pbr":           round(pbr, 2) if pbr is not None else None,
        "ev_ebitda":     None, "psr": None, "ev": None, "ev_rev": None,
        "div_yield":     round(div_y, 2) if div_y is not None else None,
        "div_rate":      round(div_r) if div_r is not None else None,
        "beta":          None, "peg":  None,
        "book_value":    bps,
        "_market_cap":   mc,        # price dict 보완에 사용
        "target_mean": None, "target_high": None, "target_low": None,
        "n_analysts":  None, "recommendation_key": None,
        "vs_per": _vs(per_t, per_med), "vs_pbr": _vs(pbr, pbr_med),
        "sector_pe": _SECTOR_PE, "sector_pbr": _SECTOR_PBR,
        "fmt": {
            "per_trailing": f"{per_t:.1f}x" if per_t is not None else "—",
            "per_forward":  f"{per_f:.1f}x" if per_f is not None else "—",
            "pbr":          f"{pbr:.2f}x"   if pbr  is not None else "—",
            "ev_ebitda":    "—", "psr": "—",
            "div_yield":    f"{div_y:.2f}%" if div_y is not None else "—",
            "ev":           "—",
            "target_mean":  "—", "target_high": "—", "target_low": "—",
            "n_analysts":   "—",
            "book_value":   f"₩{int(bps):,}" if bps is not None else "—",
        },
    }


def _parse_naver_periods(fin_data: Optional[dict]) -> list[dict]:
    """Naver finance/quarter 또는 finance/annual → period list."""
    fi = (fin_data or {}).get("financeInfo") or {}
    period_defs = fi.get("trTitleList") or []
    row_list    = fi.get("rowList") or []

    row_map: dict[str, dict] = {}
    for row in row_list:
        row_map[row.get("title", "")] = row.get("columns", {})

    def _cell(title: str, key: str) -> Optional[float]:
        cell = (row_map.get(title) or {}).get(key)
        val_str = (cell.get("value") if isinstance(cell, dict) else cell) or ""
        return _naver_num(val_str)

    results = []
    for p in period_defs:
        if p.get("isConsensus") == "Y":
            continue  # 컨센서스 추정치 제외
        key   = p.get("key", "")
        title = p.get("title", "")   # "2025.03."
        period_label = title
        try:
            y, m = int(title[:4]), int(title[5:7])
            period_label = f"{y}Q{(m-1)//3+1}"
        except (ValueError, IndexError):
            pass

        # 은행 지주: '매출액' 대신 '영업수익'이 잡히는 경우가 있어 폴백 처리
        rev  = _cell("영업수익", key) or _cell("매출액", key)
        op   = _cell("영업이익", key)
        ni   = _cell("당기순이익", key)
        op_m = _cell("영업이익률", key)
        net_m = _cell("순이익률", key)
        roe  = _cell("ROE", key)
        de   = _cell("부채비율", key)
        qr   = _cell("당좌비율", key)
        eps  = _cell("EPS", key)
        bps  = _cell("BPS", key)

        def to_won(v: Optional[float]) -> Optional[float]:
            return v * 1e8 if v is not None else None

        results.append({
            "period":           period_label,
            "end_date":         title,
            "revenue":          to_won(rev),
            "gross_profit":     None,
            "operating_profit": to_won(op),
            "net_income":       to_won(ni),
            "op_margin":        op_m,
            "net_margin":       net_m,
            "roe":              roe,
            "debt_ratio":       de,
            "quick_ratio":      qr,
            "eps":              eps,
            "bps":              bps,
            "fmt": {
                "revenue":   _fmt_krw(to_won(rev)),
                "gross":     "—",
                "op_profit": _fmt_krw(to_won(op)),
                "net":       _fmt_krw(to_won(ni)),
                "op_margin": f"{op_m:.1f}%" if op_m is not None else "—",
                "net_margin": f"{net_m:.1f}%" if net_m is not None else "—",
                "roe":       f"{roe:.1f}%" if roe is not None else "—",
            },
        })
    return results


def _parse_naver_financials_ttm(fin_data: Optional[dict]) -> dict:
    """분기 데이터 최근 4Q 합산 → TTM 재무 지표."""
    periods = _parse_naver_periods(fin_data)
    actual  = [p for p in periods if p.get("net_income") is not None]
    last4   = actual[-4:] if len(actual) >= 4 else actual

    def _sum(field: str) -> Optional[float]:
        vals = [p.get(field) for p in last4 if p.get(field) is not None]
        return sum(vals) if vals else None

    def _latest(field: str) -> Optional[float]:
        for p in reversed(last4):
            if p.get(field) is not None:
                return p[field]
        return None

    rev = _sum("revenue")
    op  = _sum("operating_profit")
    ni  = _sum("net_income")

    op_m  = round(op / rev * 100, 1) if (op and rev) else None
    net_m = round(ni / rev * 100, 1) if (ni and rev) else None

    roe       = _latest("roe")
    debt_r    = _latest("debt_ratio")
    quick_r   = _latest("quick_ratio")

    return {
        "revenue":          rev,
        "revenue_growth":   None,
        "gross_profit":     None,
        "ebitda":           None,
        "fcf":              None,
        "ocf":              None,
        "gross_margin":     None,
        "operating_margin": op_m,
        "net_margin":       net_m,
        "ebitda_margin":    None,
        "roe":              roe,
        "roa":              None,
        "debt_to_equity":   debt_r,
        "current_ratio":    None,
        "quick_ratio":      quick_r,
        "net_income":       ni,
        "operating_profit": op,
        "fmt": {
            "revenue":    _fmt_krw(rev),
            "gross":      "—",
            "ebitda":     "—",
            "fcf":        "—",
            "ocf":        "—",
            "rev_growth": "—",
            "net_income": _fmt_krw(ni),
            "operating_profit": _fmt_krw(op),
        },
    }


# ─────────────────────────── 주가 파싱 ───────────────────────────

def _parse_price(chart_raw: dict) -> tuple[dict, list[dict]]:
    meta = chart_raw.get("meta") or {}
    price = meta.get("regularMarketPrice")
    ts = meta.get("regularMarketTime")
    as_of = None
    if ts:
        try:
            as_of = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            pass

    hi52 = meta.get("fiftyTwoWeekHigh")
    lo52 = meta.get("fiftyTwoWeekLow")
    dist_high = round((price / hi52 - 1) * 100, 1) if (price and hi52) else None
    dist_low = round((price / lo52 - 1) * 100, 1) if (price and lo52) else None

    mc = meta.get("marketCap")
    vol = meta.get("regularMarketVolume")
    avg_vol = meta.get("averageDailyVolume10Day") or meta.get("averageDailyVolume3Month")

    timestamps = chart_raw.get("timestamp") or []
    closes = ((chart_raw.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    history: list[dict] = []
    for ts_val, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(int(ts_val), tz=timezone.utc).strftime("%Y-%m-%d")
        history.append({"date": d, "value": round(float(close))})
    history = history[-260:]

    prev_meta = meta.get("previousClose")
    if prev_meta:
        prev = prev_meta
    elif len(history) >= 2:
        prev = history[-2]["value"]
    else:
        prev = None

    change = (price - prev) if (price is not None and prev is not None) else None
    change_pct = ((price / prev - 1) * 100) if (price and prev) else None

    yr_ret = None
    yr_ret_fmt = "—"
    if len(history) >= 2:
        yr_ret = round((history[-1]["value"] / history[0]["value"] - 1) * 100, 1)
        yr_ret_fmt = f"+{yr_ret:.1f}%" if yr_ret > 0 else f"{yr_ret:.1f}%"

    price_view = {
        "current": price,
        "prev": prev,
        "change": change,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "yr_ret": yr_ret,
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "open": meta.get("regularMarketOpen"),
        "volume": vol,
        "avg_volume": avg_vol,
        "week52_high": hi52,
        "week52_low": lo52,
        "dist_high_pct": dist_high,
        "dist_low_pct": dist_low,
        "market_cap": mc,
        "as_of": as_of,
        "trailing_pe_meta": meta.get("trailingPE"),
        "fmt": {
            "current": _fmt_price(price),
            "prev": _fmt_price(prev),
            "change_abs": (f"+{_fmt_price(abs(change))}" if (change and change > 0)
                           else f"-{_fmt_price(abs(change))}" if (change and change < 0)
                           else "변동 없음"),
            "change_pct": (f"+{change_pct:.2f}%" if (change_pct and change_pct > 0)
                           else f"{change_pct:.2f}%" if change_pct is not None else "—"),
            "yr_ret": yr_ret_fmt,
            "market_cap": _fmt_krw(mc),
            "week52_high": _fmt_price(hi52),
            "week52_low": _fmt_price(lo52),
            "volume": f"{vol:,.0f}" if vol else "—",
            "avg_volume": f"{avg_vol:,.0f}" if avg_vol else "—",
        },
        "tone": _tone(change),
        "yr_tone": ("good" if (yr_ret and yr_ret > 0) else "bad" if (yr_ret and yr_ret < 0) else "neutral"),
    }
    return price_view, history


# ─────────────────────────── 밸류에이션 파싱 ───────────────────────────

def _parse_valuation(qs: Optional[dict], price_meta: dict) -> dict:
    sd = (qs or {}).get("summaryDetail") or {}
    ks = (qs or {}).get("defaultKeyStatistics") or {}
    fd = (qs or {}).get("financialData") or {}

    per_t = _rv(sd, "trailingPE") or price_meta.get("trailing_pe_meta")
    per_f = _rv(sd, "forwardPE")
    pbr   = _rv(ks, "priceToBook")
    ev_eb = _rv(ks, "enterpriseToEbitda")
    psr   = _rv(sd, "priceToSalesTrailing12Months")
    ev    = _rv(ks, "enterpriseValue")
    ev_rev = _rv(ks, "enterpriseToRevenue")

    div_y = _rv(sd, "dividendYield")
    if div_y is not None and div_y < 0.1:   # 비율 → %
        div_y *= 100
    div_rate = _rv(sd, "dividendRate")
    beta = _rv(sd, "beta")
    peg = _rv(ks, "pegRatio")
    book_val = _rv(ks, "bookValue")

    target_mean = _rv(fd, "targetMeanPrice")
    target_high = _rv(fd, "targetHighPrice")
    target_low  = _rv(fd, "targetLowPrice")
    n_analysts  = _rv(fd, "numberOfAnalystOpinions")
    rec_key     = _rv_str(fd, "recommendationKey")

    def _vs(val: Optional[float], sector_med: float) -> Optional[str]:
        if val is None:
            return None
        if val < sector_med * 0.82:
            return "할인"
        if val > sector_med * 1.20:
            return "프리미엄"
        return "적정"

    per_sector = _SECTOR_PE.get("섹터 중앙값", 6.0)
    pbr_sector = _SECTOR_PBR.get("섹터 중앙값", 0.50)

    return {
        "per_trailing": round(per_t, 1) if per_t is not None else None,
        "per_forward":  round(per_f, 1) if per_f is not None else None,
        "pbr":          round(pbr, 2) if pbr is not None else None,
        "ev_ebitda":    round(ev_eb, 1) if ev_eb is not None else None,
        "psr":          round(psr, 2) if psr is not None else None,
        "ev":           ev,
        "ev_rev":       round(ev_rev, 2) if ev_rev is not None else None,
        "div_yield":    round(div_y, 2) if div_y is not None else None,
        "div_rate":     round(div_rate) if div_rate is not None else None,
        "beta":         round(beta, 2) if beta is not None else None,
        "peg":          round(peg, 2) if peg is not None else None,
        "book_value":   book_val,
        "target_mean":  target_mean,
        "target_high":  target_high,
        "target_low":   target_low,
        "n_analysts":   int(n_analysts) if n_analysts is not None else None,
        "recommendation_key": rec_key,
        "vs_per":       _vs(per_t, per_sector),
        "vs_pbr":       _vs(pbr, pbr_sector),
        "sector_pe":    _SECTOR_PE,
        "sector_pbr":   _SECTOR_PBR,
        "fmt": {
            "per_trailing": f"{per_t:.1f}x" if per_t is not None else "—",
            "per_forward":  f"{per_f:.1f}x" if per_f is not None else "—",
            "pbr":          f"{pbr:.2f}x" if pbr is not None else "—",
            "ev_ebitda":    f"{ev_eb:.1f}x" if ev_eb is not None else "—",
            "psr":          f"{psr:.2f}x" if psr is not None else "—",
            "div_yield":    f"{div_y:.2f}%" if div_y is not None else "—",
            "ev":           _fmt_krw(ev),
            "target_mean":  _fmt_price(target_mean),
            "target_high":  _fmt_price(target_high),
            "target_low":   _fmt_price(target_low),
            "n_analysts":   f"{int(n_analysts)}명" if n_analysts is not None else "—",
            "book_value":   f"₩{int(book_val):,}" if book_val is not None else "—",
        },
    }


# ─────────────────────────── 재무 파싱 (Yahoo) ───────────────────────────

def _parse_financials(qs: Optional[dict]) -> dict:
    fd = (qs or {}).get("financialData") or {}

    revenue    = _rv(fd, "totalRevenue")
    rev_growth = _rv(fd, "revenueGrowth")
    gross      = _rv(fd, "grossProfits")
    ebitda     = _rv(fd, "ebitda")
    fcf        = _rv(fd, "freeCashflow")
    ocf        = _rv(fd, "operatingCashflow")
    gross_m    = _rv(fd, "grossMargins")
    op_m       = _rv(fd, "operatingMargins")
    net_m      = _rv(fd, "profitMargins")
    ebitda_m   = _rv(fd, "ebitdaMargins")
    roe        = _rv(fd, "returnOnEquity")
    roa        = _rv(fd, "returnOnAssets")
    d2e        = _rv(fd, "debtToEquity")
    cur_r      = _rv(fd, "currentRatio")
    quick_r    = _rv(fd, "quickRatio")
    net_income = _rv(fd, "netIncomeToCommon")

    return {
        "revenue":          revenue,
        "revenue_growth":   _pct(rev_growth),
        "gross_profit":     gross,
        "ebitda":           ebitda,
        "fcf":              fcf,
        "ocf":              ocf,
        "gross_margin":     _pct(gross_m),
        "operating_margin": _pct(op_m),
        "net_margin":       _pct(net_m),
        "ebitda_margin":    _pct(ebitda_m),
        "roe":              _pct(roe),
        "roa":              _pct(roa),
        "debt_to_equity":   round(d2e, 1) if d2e is not None else None,
        "current_ratio":    round(cur_r, 2) if cur_r is not None else None,
        "quick_ratio":      round(quick_r, 2) if quick_r is not None else None,
        "net_income":       net_income,
        "operating_profit": None,
        "fmt": {
            "revenue":    _fmt_krw(revenue),
            "gross":      _fmt_krw(gross),
            "ebitda":     _fmt_krw(ebitda),
            "fcf":        _fmt_krw(fcf),
            "ocf":        _fmt_krw(ocf),
            "rev_growth": (f"+{rev_growth * 100:.1f}%" if (rev_growth and rev_growth > 0)
                           else f"{rev_growth * 100:.1f}%" if rev_growth is not None else "—"),
            "net_income": _fmt_krw(net_income),
            "operating_profit": "—",
        },
    }


# ─────────────────────────── 분기/연간 손익 파싱 (Yahoo) ───────────────────────────

def _parse_period_list(raw_list: list) -> list[dict]:
    rows = []
    for q in raw_list:
        if not isinstance(q, dict):
            continue
        end_raw = q.get("endDate")
        if isinstance(end_raw, dict):
            ts_val = end_raw.get("raw")
            fmt_date = end_raw.get("fmt", "")
        elif isinstance(end_raw, (int, float)):
            ts_val, fmt_date = end_raw, ""
        else:
            ts_val, fmt_date = None, str(end_raw or "")

        period = fmt_date
        if ts_val:
            try:
                dt = datetime.fromtimestamp(int(ts_val), tz=timezone.utc)
                fmt_date = dt.strftime("%Y-%m-%d")
                period = f"{dt.year}Q{(dt.month - 1) // 3 + 1}"
                if dt.month == 12:
                    period = str(dt.year)
            except (ValueError, OSError):
                pass

        rev = _rv(q, "totalRevenue")
        gp  = _rv(q, "grossProfit")
        op  = _rv(q, "ebit") or _rv(q, "operatingIncome")
        ni  = _rv(q, "netIncome")
        op_margin = round(op / rev * 100, 1) if (op and rev) else None

        rows.append({
            "period": period,
            "end_date": fmt_date,
            "revenue": rev,
            "gross_profit": gp,
            "operating_profit": op,
            "net_income": ni,
            "op_margin": op_margin,
            "fmt": {
                "revenue":   _fmt_krw(rev),
                "gross":     _fmt_krw(gp),
                "op_profit": _fmt_krw(op),
                "net":       _fmt_krw(ni),
                "op_margin": f"{op_margin:.1f}%" if op_margin is not None else "—",
            },
        })
    return rows


def _parse_quarterly(qs: Optional[dict]) -> list[dict]:
    raw = ((qs or {}).get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory") or []
    rows = _parse_period_list(raw)
    rows.reverse()
    quarterly = [r for r in rows if not (len(r["period"]) == 4 and r["period"].isdigit())]
    return quarterly[-8:]


def _parse_annual(qs: Optional[dict]) -> list[dict]:
    raw = ((qs or {}).get("incomeStatementHistory") or {}).get("incomeStatementHistory") or []
    rows = _parse_period_list(raw)
    rows.reverse()
    for r in rows:
        if r["end_date"] and len(r["end_date"]) >= 4:
            r["period"] = r["end_date"][:4]
    return rows[-5:]


# ─────────────────────────── 종합 빌드 ───────────────────────────

def _recommendation_ko(key: Optional[str]) -> str:
    mapping = {
        "strongbuy": "강력 매수", "buy": "매수", "hold": "보유",
        "underperform": "시장하회", "sell": "매도",
    }
    return mapping.get((key or "").lower(), key or "—")


def _quick_view(val: dict, fin: dict, quarterly: list[dict]) -> dict:
    """주가 수준(PBR)·실적 모멘텀(순이익)·전략 이행 종합 한 줄 평가 (은행형)."""
    # 주가 수준: 은행은 PBR 기준
    pbr = val.get("pbr")
    sector_med = _SECTOR_PBR.get("섹터 중앙값", 0.50)
    if pbr is None:
        price_level, price_tone = "데이터 부족", "neutral"
    elif pbr < sector_med * 0.85:
        price_level, price_tone = f"저평가 (PBR {pbr:.2f}x · 장부가 대비 대폭 할인)", "good"
    elif pbr < 1.0:
        price_level, price_tone = f"할인 (PBR {pbr:.2f}x · 장부가 이하)", "neutral"
    else:
        price_level, price_tone = f"장부가 상회 (PBR {pbr:.2f}x)", "warn"

    # 실적 모멘텀: 순이익 YoY (분기 데이터 최근 vs 4분기 전)
    mom_label, mom_tone = "데이터 부족", "neutral"
    ni_series = [q.get("net_income") for q in quarterly if q.get("net_income") is not None]
    if len(ni_series) >= 5:
        recent, year_ago = ni_series[-1], ni_series[-5]
        if year_ago:
            g = (recent / year_ago - 1) * 100
            if g > 8:
                mom_label, mom_tone = f"순이익 강한 성장 (분기 YoY +{g:.1f}%)", "good"
            elif g > 0:
                mom_label, mom_tone = f"순이익 완만한 성장 (분기 YoY +{g:.1f}%)", "neutral"
            else:
                mom_label, mom_tone = f"순이익 감소 (분기 YoY {g:.1f}%)", "bad"
    elif fin.get("roe") is not None:
        roe = fin["roe"]
        mom_label = f"ROE {roe:.1f}% 수준"
        mom_tone = "good" if roe > 10 else "warn" if roe > 7 else "bad"

    # 전략 이행: achieved/in_progress 비율
    total, done = 0, 0
    for ph in _PLANS:
        for it in ph["items"]:
            total += 1
            if it.get("status") in ("achieved", "in_progress"):
                done += 1
    plan_pct = round(done / total * 100) if total else 0
    if plan_pct >= 80:
        plan_label, plan_tone = f"순조 ({plan_pct}% 이행 중)", "good"
    elif plan_pct >= 50:
        plan_label, plan_tone = f"혼조 ({plan_pct}% 이행 중)", "warn"
    else:
        plan_label, plan_tone = f"부진 ({plan_pct}% 이행 중)", "bad"

    return {
        "price_level": price_level, "price_tone": price_tone,
        "momentum": mom_label, "momentum_tone": mom_tone,
        "plan_status": plan_label, "plan_tone": plan_tone,
    }


def _build_exec_plans(plan_defs: list[dict], good_th: int = 60, warn_th: int = 30) -> tuple[list[dict], dict]:
    """robot 섹션형 이행 계획(카테고리→flat item)을 status/track 메타 적용 + 점수 집계."""
    built = []
    score_sum, score_cnt = 0, 0
    for cat in plan_defs:
        items = []
        for it in cat["items"]:
            sm = _STATUS_META.get(it.get("status", "in_progress"), _STATUS_META["in_progress"])
            tm = _TRACK_META.get(it.get("on_track"), _TRACK_META[None])
            pp = it.get("progress_pct")
            if pp is not None:
                score_sum += pp; score_cnt += 1
            items.append({
                **it,
                "status_label": sm["label"], "status_tone": sm["tone"],
                "track_label": tm["label"],  "track_tone":  tm["tone"],
            })
        built.append({**cat, "items": items})
    score = round(score_sum / score_cnt) if score_cnt else 0
    summary = {
        "score": score,
        "score_tone": "good" if score >= good_th else "warn" if score >= warn_th else "bad",
        "total": score_cnt,
    }
    return built, summary


def _build(chart_raw: dict, qs: Optional[dict],
           nav_int: Optional[dict] = None,
           nav_q:   Optional[dict] = None,
           nav_a:   Optional[dict] = None) -> dict:
    price, history = _parse_price(chart_raw)

    if qs:
        val       = _parse_valuation(qs, price)
        fin       = _parse_financials(qs)
        quarterly = _parse_quarterly(qs)
        annual    = _parse_annual(qs)
    else:
        val       = _parse_naver_valuation(nav_int)
        fin       = _parse_naver_financials_ttm(nav_q) if nav_q else _parse_financials(None)
        quarterly = _parse_naver_periods(nav_q)
        annual    = _parse_naver_periods(nav_a)

    if price.get("market_cap") is None:
        mc = val.pop("_market_cap", None)
        if mc:
            price["market_cap"] = mc
            price["fmt"]["market_cap"] = _fmt_krw(mc)
    else:
        val.pop("_market_cap", None)

    qview = _quick_view(val, fin, quarterly)

    # 애널리스트
    cur_p = price.get("current")
    tgt   = val.get("target_mean")
    upside = round((tgt / cur_p - 1) * 100, 1) if (tgt and cur_p) else None
    rec_key = val.get("recommendation_key")
    analyst = {
        "target_mean":        tgt,
        "target_high":        val.get("target_high"),
        "target_low":         val.get("target_low"),
        "n_analysts":         val.get("n_analysts"),
        "recommendation":     _recommendation_ko(rec_key),
        "recommendation_key": rec_key,
        "upside_pct":         upside,
        "fmt": {
            **val["fmt"],
            "upside": (f"+{upside:.1f}%" if (upside and upside > 0) else
                       f"{upside:.1f}%" if upside is not None else "—"),
        },
        "tone": ("good" if (upside and upside > 10) else
                 "bad"  if (upside and upside < -5) else "neutral"),
    }

    # 전략 플랜 — status 메타 + KPI on_track 레이블
    plans = []
    stat_count = {"achieved": 0, "in_progress": 0, "planned": 0, "delayed": 0}
    score_sum, score_cnt = 0, 0
    for ph in _PLANS:
        items = []
        for it in ph["items"]:
            sm = _STATUS_META.get(it.get("status", "planned"), _STATUS_META["planned"])
            kpi = it.get("kpi")
            kpi_meta = None
            if kpi is not None:
                tm = _TRACK_META.get(kpi.get("on_track"), _TRACK_META[None])
                kpi_meta = {**kpi, "track_label": tm["label"], "track_tone": tm["tone"]}
            items.append({**it, "status_label": sm["label"], "status_tone": sm["tone"],
                          "kpi": kpi_meta})
            st = it.get("status", "planned")
            stat_count[st] = stat_count.get(st, 0) + 1
            pp = it.get("progress_pct")
            if pp is not None:
                score_sum += pp; score_cnt += 1
        plans.append({**ph, "items": items})

    overall_score = round(score_sum / score_cnt) if score_cnt else 0
    plan_summary = {
        **stat_count,
        "total": sum(stat_count.values()),
        "score": overall_score,
        "score_tone": "good" if overall_score >= 70 else "warn" if overall_score >= 40 else "bad",
    }

    # 섹터 PBR 기준 적정주가 테이블 (은행 핵심 지표)
    cur_price = price.get("current")
    per_t = val.get("per_trailing")
    bps_v = val.get("book_value")
    pbr_cur = val.get("pbr")

    implied_pbr: list[dict] = []
    if bps_v:
        scenarios = [
            ("보수적 (0.50x)", 0.50),
            ("국내 지주 중앙값 (0.70x)", 0.70),
        ]
        if pbr_cur:
            scenarios.append((f"현재 수준 ({pbr_cur:.2f}x)", round(pbr_cur, 2)))
        scenarios += [
            ("상단 재평가 (0.90x)", 0.90),
            ("장부가치 (1.0x)", 1.0),
        ]
        seen = set()
        for label, target_pbr in scenarios:
            if round(target_pbr, 2) in seen:
                continue
            seen.add(round(target_pbr, 2))
            implied = bps_v * target_pbr
            gap = (implied / cur_price - 1) * 100 if cur_price else None
            implied_pbr.append({
                "label": label,
                "pbr": target_pbr,
                "implied": round(implied),
                "gap": round(gap, 1) if gap is not None else None,
                "tone": "good" if (gap and gap > 10) else "bad" if (gap and gap < -10) else "neutral",
                "fmt_implied": _fmt_price(implied),
                "fmt_gap": (f"+{gap:.1f}%" if gap > 0 else f"{gap:.1f}%") if gap is not None else "—",
                "is_current": abs(target_pbr - (pbr_cur or 0)) < 0.03,
            })

    # 섹터 PER 기준 적정주가 (보조)
    implied_pe: list[dict] = []
    if cur_price and per_t:
        eps = cur_price / per_t
        for name, pe in _SECTOR_PE.items():
            implied = eps * pe
            gap = (implied / cur_price - 1) * 100
            implied_pe.append({
                "name": name,
                "pe": pe,
                "implied": round(implied),
                "gap": round(gap, 1),
                "tone": "good" if gap > 10 else "bad" if gap < -10 else "neutral",
                "fmt_implied": _fmt_price(implied),
                "fmt_gap": f"+{gap:.1f}%" if gap > 0 else f"{gap:.1f}%",
            })

    # 국내 금융지주 피어 1년 수익률 (신한 삽입)
    peers_raw = _fetch_bank_peer_returns()
    shb_yr = price.get("yr_ret")
    shb_entry = {
        "name": "신한지주 (055550)", "ticker": "055550.KS",
        "ret_1y": shb_yr or 0,
        "current": cur_price, "currency": "KRW",
        "fmt_ret": price["fmt"]["yr_ret"],
        "tone": price.get("yr_tone", "neutral"),
        "is_self": True,
    }
    bank_peers_full = sorted(
        [shb_entry] + [{**p, "is_self": False} for p in peers_raw],
        key=lambda x: x["ret_1y"], reverse=True,
    )

    # 주주환원·밸류업 섹션
    sh_plans, sh_summary = _build_exec_plans(_SHAREHOLDER_PLANS, good_th=60, warn_th=30)
    shareholder = {
        "asof": _SHAREHOLDER_ASOF,
        "cards": _SHAREHOLDER_CARDS,
        "plans": sh_plans,
        "plan_summary": sh_summary,
    }

    # 비은행·글로벌 섹션
    nb_plans, nb_summary = _build_exec_plans(_NONBANK_PLANS, good_th=60, warn_th=30)
    nonbank_total = sum(m["pct"] for m in _NONBANK_MIX)
    nonbank = {
        "asof": _NONBANK_ASOF,
        "mix": _NONBANK_MIX,
        "mix_total": nonbank_total,
        "ratio": _NONBANK_RATIO,
        "plans": nb_plans,
        "plan_summary": nb_summary,
    }

    return {
        "ok":        True,
        "symbol":    _SYMBOL,
        "as_of":     price.get("as_of"),
        "price":     price,
        "history":   history,
        "valuation": val,
        "financials": fin,
        "quarterly": quarterly,
        "annual":    annual,
        "analyst":   analyst,
        "quick_view": qview,
        "plans":        plans,
        "plan_summary": plan_summary,
        "sector_pe":  _SECTOR_PE,
        "sector_pbr": _SECTOR_PBR,
        "implied_pe":  implied_pe,
        "implied_pbr": implied_pbr,
        "bank_kpi":    _BANK_KPI,
        "bank_kpi_asof": _BANK_KPI_ASOF,
        "shareholder": shareholder,
        "nonbank":     nonbank,
        "bank_peers":  bank_peers_full,
    }


# ─────────────────────────── 캐시 관리 ───────────────────────────

def refresh() -> bool:
    try:
        logger.info("신한지주 데이터 갱신 시작...")
        chart_raw = _fetch_chart(_SYMBOL, "1y")
        qs        = _fetch_quote_summary(_SYMBOL)
        nav_code  = _SYMBOL.split(".")[0]   # "055550"
        nav_int   = _fetch_naver_integration(nav_code)
        nav_q     = _fetch_naver_finance(nav_code, "quarter")
        nav_a     = _fetch_naver_finance(nav_code, "annual")
        logger.info("신한지주 소스 상태 — quoteSummary:%s Naver_int:%s Naver_q:%s",
                    "OK" if qs else "없음",
                    "OK" if nav_int else "없음",
                    "OK" if nav_q else "없음")
        data = _build(chart_raw, qs, nav_int, nav_q, nav_a)
        with _lock:
            _cache["data"] = data
            _cache["ts"] = time.monotonic()
        logger.info("신한지주 데이터 갱신 완료 (quoteSummary: %s)", "OK" if qs else "없음(차트만)")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("신한지주 데이터 갱신 실패: %s", exc)
        with _lock:
            if _cache["data"]:
                _cache["data"]["stale"] = True
        return False


def get_data() -> dict:
    with _lock:
        d = _cache["data"]
        age = time.monotonic() - _cache["ts"]
        if d and age < _CACHE_TTL:
            return d
    refresh()
    with _lock:
        if _cache["data"]:
            return _cache["data"]
    return {"ok": False, "error": "신한지주 데이터 로드 실패 — Yahoo Finance API를 확인하세요."}


# ─────────────────────────── LLM 분석 브리핑 ───────────────────────────

_SYSTEM_PROMPT = """너는 신한금융지주(055550.KS) 전담 은행/금융지주 주식 분석가다.
아래 재무·주가·밸류에이션·건전성 데이터를 바탕으로 한국어 분석 브리핑을 작성하라.

규칙:
- 제공된 수치에만 근거. 없는 수치는 인용 금지.
- 투자 권유 금지. 사실 기반 분석·평가.
- 은행 지주는 매출/영업이익률이 아니라 PBR·배당수익률·ROE·순이자마진(NIM)·건전성(BIS·NPL)이 핵심임을 반영.
- 주가 수준: 특히 PBR을 국내 4대 금융지주와 비교해 평가 필수.
- 거시환경(기준금리 방향·NIM·부동산 PF·대손비용)이 실적에 미치는 영향 언급.
- 주주환원·밸류업(총주주환원율·자사주 소각·CET1)이 밸류에이션에 주는 함의 언급.
- 출력은 아래 JSON 하나만. 코드펜스 없이.

JSON 스키마:
{
  "headline": "한 줄 핵심 평가 (40자 이내, 한국어)",
  "summary": "4~5문장 종합 판단. 현 주가가 싼지 비싼지, 가장 큰 기회·리스크 포함. 일반 투자자가 바로 이해하는 평이한 표현.",
  "body_md": "마크다운 본문. ## 밸류에이션 평가(PBR·배당), ## 실적·수익성(순이익·NIM·ROE), ## 자산·자본 건전성(BIS·NPL·CET1), ## 주주환원·밸류업, ## 핵심 리스크 5개 섹션."
}"""


def _build_analysis_prompt(data: dict) -> str:
    p  = data.get("price") or {}
    v  = data.get("valuation") or {}
    f  = data.get("financials") or {}
    a  = data.get("analyst") or {}
    ql = data.get("quarterly") or []
    vf = v.get("fmt") or {}
    ff = f.get("fmt") or {}

    lines = [_SYSTEM_PROMPT, "", "<DATA>",
             f"=== 신한금융지주(055550.KS) 분석 데이터 ({(data.get('as_of') or '')[:10]}) ===", ""]

    pf = p.get("fmt") or {}
    lines += [
        "## 주가 현황",
        f"  현재가: {pf.get('current', '—')} ({pf.get('change_abs', '—')} · {pf.get('change_pct', '—')})",
        f"  시가총액: {pf.get('market_cap', '—')}",
        f"  52주 고점: {pf.get('week52_high', '—')} (현재가 대비 {p.get('dist_high_pct', '—')}%)",
        f"  52주 저점: {pf.get('week52_low', '—')} (현재가 대비 {p.get('dist_low_pct', '—')}%)",
        "",
    ]

    lines += ["## 밸류에이션"]
    lines.append(f"  PBR: {vf.get('pbr', '—')}"
                 f" (국내 지주 중앙값 {_SECTOR_PBR.get('섹터 중앙값', '—'):.2f}x)"
                 f" → {v.get('vs_pbr', '—')}")
    lines.append(f"  PER(Trailing): {vf.get('per_trailing', '—')}"
                 f" (국내 지주 중앙값 {_SECTOR_PE.get('섹터 중앙값', '—'):.1f}x)"
                 f" → {v.get('vs_per', '—')}")
    lines.append(f"  배당수익률: {vf.get('div_yield', '—')}")
    lines.append(f"  BPS(주당순자산): {vf.get('book_value', '—')}")
    if a.get("target_mean"):
        lines.append(f"  애널리스트 목표가: {a['fmt'].get('target_mean', '—')}"
                     f" (현재 대비 {a['fmt'].get('upside', '—')})"
                     f" / 컨센서스: {a.get('recommendation', '—')}"
                     f" / {a['fmt'].get('n_analysts', '—')} 커버")
    lines.append("")

    lines += ["## 재무 지표 (TTM/최근)"]
    lines.append(f"  순이익(TTM): {ff.get('net_income', '—')}")
    if f.get("net_margin") is not None:
        lines.append(f"  순이익률: {f['net_margin']:.1f}%")
    if f.get("roe") is not None:
        lines.append(f"  ROE: {f['roe']:.1f}%")
    lines.append("")

    lines += ["## 핵심 은행 지표 (공개 IR 기준·근사)"]
    for grp in data.get("bank_kpi") or []:
        for it in grp["items"]:
            note = f" — {it['note']}" if it.get("note") else ""
            lines.append(f"  {it['label']}({it.get('hint','')}): {it['fmt']}{note}")
    lines.append("")

    if ql:
        lines.append("## 최근 분기 실적")
        for q in ql[-4:]:
            qf = q.get("fmt") or {}
            lines.append(f"  {q.get('period', '—')}: 순이익 {qf.get('net', '—')}"
                         f" / 영업이익 {qf.get('op_profit', '—')}")
        lines.append("")

    sh = data.get("shareholder") or {}
    lines.append("## 주주환원·밸류업")
    for c in sh.get("cards") or []:
        lines.append(f"  {c['title']}: {c['val']} ({c['sub']})")
    lines.append("")

    lines.append("## 전략 이행 현황")
    for ph in _PLANS:
        lines.append(f"  [{ph['title']}]")
        for it in ph["items"]:
            sm = _STATUS_META.get(it.get("status", "planned"), _STATUS_META["planned"])
            lines.append(f"    - {it['label']} → {sm['label']}")
    lines.append("")

    lines.append("## 국내 금융지주 PBR 비교")
    for name, pbr in _SECTOR_PBR.items():
        lines.append(f"  {name}: {pbr:.2f}x")
    lines.append("")
    lines.append("</DATA>")
    lines.append("\n위 데이터로 JSON 분석 브리핑을 작성하라.")
    return "\n".join(lines)


def get_cached_analysis() -> Optional[dict]:
    """캐시에 있는 분석만 반환 — LLM 호출 없음. 없으면 None."""
    with _analysis_lock:
        cached = _analysis_cache.get("data")
        age = time.monotonic() - _analysis_cache.get("ts", 0.0)
        if cached and age < _ANALYSIS_TTL:
            return cached
    return None


def trigger_analysis_async(data: dict) -> bool:
    """백그라운드 스레드에서 LLM 분석 생성. 이미 생성 중이거나 캐시 유효하면 False."""
    if not (data or {}).get("ok"):
        return False
    if get_cached_analysis():
        return False
    if _analysis_generating.is_set():
        return False

    def _gen():
        try:
            get_analysis(data)
        finally:
            _analysis_generating.clear()

    _analysis_generating.set()
    threading.Thread(target=_gen, name="shinhan-analysis", daemon=True).start()
    return True


def get_analysis(data: dict) -> Optional[dict]:
    """LLM 신한지주 전용 분석 브리핑 (3시간 캐시). data가 ok=False면 None 반환."""
    if not (data or {}).get("ok"):
        return None

    with _analysis_lock:
        cached = _analysis_cache.get("data")
        age    = time.monotonic() - _analysis_cache.get("ts", 0.0)
        if cached and age < _ANALYSIS_TTL:
            return cached

    err = llm.availability_error()
    if err:
        return {"ok": False, "error": err}

    try:
        prompt = _build_analysis_prompt(data)
        res = llm.complete(prompt, timeout=120, label="신한지주 분석")
        if not res.ok:
            return {"ok": False, "error": res.error}

        from .briefing import _extract_json_obj  # noqa: PLC0415
        obj = _extract_json_obj(res.text)
        if not obj:
            return {"ok": False, "error": "JSON 파싱 실패"}

        result = {
            "ok":       True,
            "headline": str(obj.get("headline", ""))[:120],
            "summary":  str(obj.get("summary", "")),
            "body_md":  str(obj.get("body_md", "")),
            "ts_utc":   datetime.now(timezone.utc).isoformat(),
            "model":    res.model,
        }
        with _analysis_lock:
            _analysis_cache["data"] = result
            _analysis_cache["ts"]   = time.monotonic()
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("신한지주 LLM 분석 실패: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
