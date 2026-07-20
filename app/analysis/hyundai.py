"""현대자동차(005380.KS) 전용 분석 모듈.

Yahoo Finance chart API(주가·이력) + quoteSummary(재무제표·밸류에이션)로
주가·밸류에이션·분기실적·수익성·재무건전성을 한 페이지에 종합한다.
LLM 종합 분석(현대차 전용 프롬프트)도 여기서 생성·캐시한다.
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

_SYMBOL = "005380.KS"
_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
_CACHE_TTL = 3600        # 데이터 캐시 유효시간(초) — 매시 파이프라인에서 갱신
_ANALYSIS_TTL = 3 * 3600  # LLM 분석 캐시 — 3시간

_lock = threading.Lock()
_cache: dict = {"data": None, "ts": 0.0}
_analysis_lock = threading.Lock()
_analysis_cache: dict = {"data": None, "ts": 0.0}
_analysis_generating = threading.Event()  # 생성 중 플래그


# ─────────────────────────── 전략 플랜 (정적·공개 IR 기반, 2026년 7월 현황) ───────────────────────────

# status: achieved | in_progress | planned | delayed
_PLANS: list[dict] = [
    {
        "horizon": "short",
        "title": "단기 플랜 (2024~2026)",
        "icon": "📌",
        "items": [
            {
                "label": "글로벌 판매 416~430만대(현대+제네시스)",
                "detail": "2024 연간 421만대 달성. 2025년 글로벌 수요 둔화 속 416만대 이상 유지 목표",
                "target_date": "2025 연간",
                "progress_pct": 100,
                "milestone": "421만대 달성(2024) / 목표 416~430만대",
                "status": "achieved",
                "kpi": {
                    "actual": "421만대 (2024)",
                    "target": "416~430만대",
                    "actual_val": 421, "target_val": 423,
                    "unit": "만대",
                    "on_track": True,
                    "note": "2024년 목표 달성. 2025년 관세·수요 환경 변수",
                },
            },
            {
                "label": "아이오닉 9 · EV9 GT 양산 출시",
                "detail": "아이오닉 9: 2025년 1분기 양산 개시 / EV9 GT: 2025년 4분기 출시",
                "target_date": "2025 Q1 / 2025 Q4",
                "progress_pct": 100,
                "milestone": "아이오닉 9 양산 완료 / EV9 GT 출시 완료",
                "status": "achieved",
                "kpi": {
                    "actual": "양산·출시 완료",
                    "target": "2025년 내 출시",
                    "actual_val": 2, "target_val": 2,
                    "unit": "개 모델",
                    "on_track": True,
                    "note": "예정대로 완료",
                },
            },
            {
                "label": "미국 HMGMA(조지아 메타플랜트) 가동",
                "detail": "연 30만대 생산 능력 — 아이오닉 5·9 현지 생산으로 IRA $7,500 세액공제 수혜",
                "target_date": "2025 Q3 (완료)",
                "progress_pct": 90,
                "milestone": "1단계 가동 개시 완료 / 2단계 풀가동 전환 중",
                "status": "in_progress",
                "kpi": {
                    "actual": "1단계 가동 중",
                    "target": "30만대/년 풀가동",
                    "actual_val": 70, "target_val": 100,
                    "unit": "% 풀가동 대비",
                    "on_track": True,
                    "note": "가동 개시 완료. 증산 궤도 진행 중",
                },
            },
            {
                "label": "IRA 대응: EV 세액공제 수혜 확보",
                "detail": "HMGMA 현지 생산분부터 소비자 $7,500 세액공제 적용. 미-한 관세 협상 변수 지속 모니터링",
                "target_date": "2025 H2~ 지속",
                "progress_pct": 70,
                "milestone": "HMGMA 생산분 IRA 적용 확인 / 관세 영향 협상 중",
                "status": "in_progress",
                "kpi": {
                    "actual": "HMGMA분 적용 중",
                    "target": "전 EV 라인 세액공제",
                    "actual_val": 70, "target_val": 100,
                    "unit": "% 커버리지",
                    "on_track": None,
                    "note": "트럼프 관세·IRA 정책 변동 변수 상존",
                },
            },
            {
                "label": "수소트럭(XCIENT) 유럽 확대 + 넥쏘 2세대",
                "detail": "XCIENT 누적 700대+ 유럽 상용 운영 중 / 넥쏘 2세대 2024년 공개·2025년 양산",
                "target_date": "2025 연간 (넥쏘 2세대 양산)",
                "progress_pct": 70,
                "milestone": "넥쏘 2세대 양산 개시 / XCIENT 유럽 누적 700대+",
                "status": "in_progress",
                "kpi": {
                    "actual": "XCIENT 700대+, 넥쏘2 양산",
                    "target": "유럽 수소 상용 확대",
                    "actual_val": 700, "target_val": 1000,
                    "unit": "대 (유럽 누적)",
                    "on_track": True,
                    "note": "넥쏘 2세대 양산 완료. XCIENT 보급 확대 중",
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
                "label": "2030년 EV 200만대 판매 (현대 브랜드 단독)",
                "detail": "그룹 전체 목표 364만대. 2024 그룹 EV 약 51만대 → 연평균 28%+ 성장 필요. 관세·수요 둔화가 핵심 리스크",
                "target_date": "2030년",
                "progress_pct": 26,
                "milestone": "2024 그룹 EV 51만대 / 목표 364만대 (현대 단독 200만대)",
                "status": "in_progress",
                "kpi": {
                    "actual": "51만대/년 (2024 그룹)",
                    "target": "364만대/년 (2030 그룹)",
                    "actual_val": 51, "target_val": 364,
                    "unit": "만대",
                    "on_track": False,
                    "note": "연 28%+ 복합 성장 필요 — 현재 성장세 대비 목표 높음",
                },
            },
            {
                "label": "전동화 차종 31개 라인업 구축",
                "detail": "현대·기아·제네시스 합산 2030년까지 31개 EV 모델 출시 — 현재 아이오닉 5·6·9·EV6·EV9 등 11개 내외",
                "target_date": "2030년",
                "progress_pct": 35,
                "milestone": "현재 약 11개 모델 / 목표 31개 모델",
                "status": "in_progress",
                "kpi": {
                    "actual": "약 11개 (그룹 전체)",
                    "target": "31개 (2030)",
                    "actual_val": 11, "target_val": 31,
                    "unit": "개 EV 모델",
                    "on_track": True,
                    "note": "연 2~3개 신모델 출시 페이스로 달성 가능",
                },
            },
            {
                "label": "SDV(소프트웨어 정의 차량) 플랫폼 전환",
                "detail": "ccOS 차량 OS · OTA 원격 업데이트 · AI 인포테인먼트 · 데이터 수익화. 2026년부터 신차 전 적용",
                "target_date": "2026년~ (신차 전 적용)",
                "progress_pct": 50,
                "milestone": "ccOS 개발 완료 / 2026년 양산차 적용 예정",
                "status": "in_progress",
                "kpi": {
                    "actual": "ccOS 개발 완료",
                    "target": "전 신차 OTA 탑재",
                    "actual_val": 50, "target_val": 100,
                    "unit": "% 완성도",
                    "on_track": True,
                    "note": "2026년 양산 적용 예정 — 일정 내 진행 중",
                },
            },
            {
                "label": "자율주행 Level 4 상용화 (모셔널 JV)",
                "detail": "앱티브 JV 모셔널 — 미국 주요 도시 로보택시 상용 서비스. 규제 불확실성·자금조달이 변수",
                "target_date": "2026~2028년 (주요 도시)",
                "progress_pct": 30,
                "milestone": "라스베이거스·오스틴 시범 운행 중 / 상용 확대 준비",
                "status": "in_progress",
                "kpi": {
                    "actual": "시범 운행 2개 도시",
                    "target": "상용 서비스 다수 도시",
                    "actual_val": 2, "target_val": 10,
                    "unit": "개 도시",
                    "on_track": False,
                    "note": "모셔널 자금 조달·규제 불확실성으로 일정 지연 가능성",
                },
            },
            {
                "label": "글로벌 자동차 그룹 Top 3 점유율 유지",
                "detail": "도요타·폭스바겐에 이어 현대차그룹 글로벌 3위 — 2024년 그룹 판매 720만대로 3위 유지",
                "target_date": "매년 유지",
                "progress_pct": 85,
                "milestone": "2024 그룹 판매 720만대 / 3위 유지 중",
                "status": "in_progress",
                "kpi": {
                    "actual": "720만대 (2024, 글로벌 3위)",
                    "target": "Top 3 지속 유지",
                    "actual_val": 720, "target_val": 700,
                    "unit": "만대",
                    "on_track": True,
                    "note": "도요타·폭스바겐 이어 3위 유지. BYD 추격이 변수",
                },
            },
        ],
    },
    {
        "horizon": "long",
        "title": "장기 플랜 (2030~2045)",
        "icon": "🚀",
        "items": [
            {
                "label": "2045년 탄소중립 달성",
                "detail": "스코프 1+2+3 전주기 탄소중립 — 제품·공급망·운영 포함. 2030 중간 목표: 탄소 25% 감축",
                "target_date": "2030 중간목표 / 2045 최종",
                "progress_pct": 15,
                "milestone": "RE100 가입 완료 / 탄소중립 로드맵 발표",
                "status": "planned",
                "kpi": {
                    "actual": "로드맵 수립·RE100 가입",
                    "target": "탄소 25% 감축 (2030)",
                    "actual_val": 15, "target_val": 100,
                    "unit": "% 달성",
                    "on_track": None,
                    "note": "초기 단계 — 공급망 전환이 핵심 과제",
                },
            },
            {
                "label": "AAM 도심항공모빌리티 상용화 (Supernal)",
                "detail": "Supernal SA-2(6인승 eVTOL) — FAA 형식인증 취득 후 미국 상용화 목표. 2028년 인증 신청 예정",
                "target_date": "2028년 FAA 인증 / 2030년대 초 상용화",
                "progress_pct": 25,
                "milestone": "SA-2 설계 완료 / 2028년 형식인증 신청 예정",
                "status": "in_progress",
                "kpi": {
                    "actual": "설계 완료, 시제기 제작",
                    "target": "2028 FAA 인증 신청",
                    "actual_val": 25, "target_val": 100,
                    "unit": "% 인증 절차",
                    "on_track": True,
                    "note": "FAA 인증 기간 3~5년 소요 — 2030년대 상용화 현실적",
                },
            },
            {
                "label": "로보틱스 사업화 (Boston Dynamics)",
                "detail": "Atlas·Spot·Stretch 산업 로봇 상용 배치 확대. Spot 기업 구독 수익 성장 중 / Atlas 공장 파일럿 완료",
                "target_date": "2025~2030년 단계적",
                "progress_pct": 45,
                "milestone": "Spot 1,000개 이상 기업 배치 / Atlas 파일럿 가동",
                "status": "in_progress",
                "kpi": {
                    "actual": "Spot 1,000대+ 구독 배치",
                    "target": "산업 로봇 수익화",
                    "actual_val": 1000, "target_val": 5000,
                    "unit": "대 (누적 배치)",
                    "on_track": True,
                    "note": "Spot 구독 매출 성장 중. Atlas 제조업 적용 확대 중",
                },
            },
            {
                "label": "수소 에너지 생태계 구축 (HTWO 브랜드)",
                "detail": "수소 생산·저장·운송·활용 전주기 사업화 — 상용차·발전·항만 확장. 2030년 수소 매출 10조원 목표",
                "target_date": "2030년 (매출 목표)",
                "progress_pct": 20,
                "milestone": "HTWO 브랜드 출범 / 유럽·중국 수소 프로젝트 진행 중",
                "status": "planned",
                "kpi": {
                    "actual": "HTWO 출범, 프로젝트 진행",
                    "target": "수소 매출 10조원 (2030)",
                    "actual_val": 20, "target_val": 100,
                    "unit": "% 진척",
                    "on_track": None,
                    "note": "수소 인프라 구축 속도가 핵심 변수",
                },
            },
            {
                "label": "차세대 배터리(전고체) 내재화",
                "detail": "전고체 배터리(SSB) 자체 개발·탑재 — 2030년 소규모 양산, 2033~2035년 대량 탑재 목표",
                "target_date": "2030년 소규모 / 2033~2035년 대량",
                "progress_pct": 30,
                "milestone": "파일럿 셀 개발 완료 / 2030년 소량 양산 목표",
                "status": "planned",
                "kpi": {
                    "actual": "파일럿 셀 개발 완료",
                    "target": "소규모 양산 (2030)",
                    "actual_val": 30, "target_val": 100,
                    "unit": "% 개발 단계",
                    "on_track": True,
                    "note": "Toyota·삼성SDI 대비 소폭 뒤처짐. 2030년 목표는 유효",
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

# 글로벌 자동차 섹터 P/E 비교 (2025~2026 시장 추정치)
_SECTOR_PE: dict[str, float] = {
    "Toyota (TM)":  9.5,
    "Volkswagen":   4.5,
    "Stellantis":   4.0,
    "GM":           5.5,
    "Ford":         6.0,
    "섹터 중앙값":  6.5,
}
_SECTOR_PBR: dict[str, float] = {
    "Toyota (TM)":  1.1,
    "Volkswagen":   0.4,
    "Stellantis":   0.5,
    "GM":           0.8,
    "Ford":         1.0,
    "섹터 중앙값":  0.8,
}


# ─────────────────────────── 로봇 섹터 ───────────────────────────

# 로봇 피어 비교 (Yahoo Finance ticker)
_ROBOT_PEERS: dict[str, str] = {
    "ROBO ETF":        "ROBO",    # ROBO Global Robotics Index ETF
    "Fanuc":           "6954.T",  # 산업용 로봇 1위 (일본)
    "Yaskawa":         "6506.T",  # 산업/서비스 로봇 (일본)
    "ABB":             "ABB",     # 자동화·로봇 (스위스, NYSE)
    "Tesla (Optimus)": "TSLA",    # 휴머노이드 Optimus 내러티브
}

# Boston Dynamics 사업 컨텍스트 (정적, 공개 정보 기반)
_BD_CONTEXT: dict = {
    "stake_pct":     80,
    "acq_year":      2021,
    "acq_krw":       1_100_000_000_000,   # ~1.1조원 (HMG 총 투자 추정)
    "val_low_usd":   3_000_000_000,       # $3B (보수적 추정)
    "val_high_usd":  6_000_000_000,       # $6B (낙관적 추정)
    "rev_est":       "3,000억원 내외",    # 추정, 미공개
    "products":      ["Spot (4족 보행)", "Atlas (휴머노이드)", "Stretch (물류)"],
    "clients":       "1,000개 이상 기업 구독 (Spot 기준)",
    "stage":         "성장 투자 단계 — 매출 성장 중, 영업 적자 운영",
}
_USDKRW_APPROX = 1_380.0   # 근사 환율 (실시간 미반영)

# 로봇 사업 세부 이행 계획 (공개 IR·발표 기반, 2026년 7월 현황)
_ROBOT_PLANS: list[dict] = [
    {
        "category": "Boston Dynamics",
        "icon": "🦾",
        "items": [
            {
                "label": "Spot 기업 구독 배포",
                "detail": "제조·건설·에너지·안전점검 등 산업 현장 채택 확대",
                "target_date": "2027년",
                "target": "5,000대+",
                "actual": "1,000대+ (2026 현재)",
                "actual_val": 1000, "target_val": 5000,
                "unit": "대 (누적 배치)",
                "progress_pct": 20,
                "on_track": True,
                "note": "연간 구독 계약 기반 — 반복 매출 확대 중",
                "status": "in_progress",
            },
            {
                "label": "Atlas 휴머노이드 공장 파일럿",
                "detail": "현대차 울산·앨라배마 공장 라인 적용, 도장·용접·부품 이송 자동화",
                "target_date": "2026년",
                "target": "5개 이상 공장",
                "actual": "현대차 울산 파일럿 1개 가동",
                "actual_val": 1, "target_val": 5,
                "unit": "개 공장",
                "progress_pct": 20,
                "on_track": True,
                "note": "자체 공장 검증 후 외부 산업체 공급 확장 예정",
                "status": "in_progress",
            },
            {
                "label": "Stretch 물류 로봇 수주 확대",
                "detail": "물류센터 박스 하역 자동화 — 아마존 파트너십 외 다수 물류사 확대",
                "target_date": "2027년",
                "target": "50개 이상 물류센터",
                "actual": "15개 내외 파트너 (추정)",
                "actual_val": 15, "target_val": 50,
                "unit": "개 물류센터",
                "progress_pct": 30,
                "on_track": True,
                "note": "이커머스 물류 자동화 수요 증가로 수주 확대 중",
                "status": "in_progress",
            },
            {
                "label": "Boston Dynamics 매출·수익성",
                "detail": "연간 매출 성장 지속 중이나 R&D 투자로 적자 운영. 흑자 전환이 로봇주 재평가 트리거",
                "target_date": "2027~2028년 (흑자 전환 목표)",
                "target": "영업 흑자 전환",
                "actual": "~3,000억원 매출 (추정) / 적자",
                "actual_val": 30, "target_val": 100,
                "unit": "% 흑자 전환 진척",
                "progress_pct": 30,
                "on_track": None,
                "note": "매출 성장 궤도이나 R&D 비용 부담으로 흑자 시점 불확실",
                "status": "in_progress",
            },
        ],
    },
    {
        "category": "Supernal (도심항공)",
        "icon": "✈️",
        "items": [
            {
                "label": "SA-2 eVTOL 설계 및 시제기",
                "detail": "6인승 전동 수직이착륙기. 설계 완료 → 시제기 제작 → FAA 형식인증 신청",
                "target_date": "2028년 FAA 인증 신청",
                "target": "FAA 형식인증 신청",
                "actual": "설계 완료, 시제기 제작 중",
                "actual_val": 25, "target_val": 100,
                "unit": "% 인증 절차",
                "progress_pct": 25,
                "on_track": True,
                "note": "FAA 심사 3~5년 소요 → 2030년대 초 상용화 현실적",
                "status": "in_progress",
            },
            {
                "label": "버티포트 인프라 파트너십",
                "detail": "이착륙 인프라 선제 구축 — 미국 주요 도시 공항·빌딩 옥상 협약",
                "target_date": "2027년",
                "target": "5개 이상 주요 도시",
                "actual": "2~3개 도시 MOU 단계",
                "actual_val": 2, "target_val": 5,
                "unit": "개 도시 협약",
                "progress_pct": 40,
                "on_track": True,
                "note": "인프라 협약 선점이 경쟁사 대비 진입장벽",
                "status": "in_progress",
            },
        ],
    },
    {
        "category": "SDV (소프트웨어 정의 차량)",
        "icon": "💻",
        "items": [
            {
                "label": "ccOS 차량 OS 신차 전면 적용",
                "detail": "현대차 독자 차량 OS — OTA 원격 업데이트·AI 인포테인먼트·데이터 수익화 기반",
                "target_date": "2026년~ 신차 전체",
                "target": "신차 100% 탑재 (2026~)",
                "actual": "ccOS 개발 완료, 2026년 적용 시작",
                "actual_val": 50, "target_val": 100,
                "unit": "% 신차 커버리지",
                "progress_pct": 50,
                "on_track": True,
                "note": "2026년 양산 적용 → 연간 400만대 연결 차량 확보 목표",
                "status": "in_progress",
            },
            {
                "label": "커넥티드 차량 구독·데이터 수익화",
                "detail": "OTA 업데이트·원격 진단·AI 기능 구독 모델. 차량당 연 $50~200 수익 목표",
                "target_date": "2027년",
                "target": "500만 대 연결 / 수익화",
                "actual": "초기 신차 중심 도입 단계",
                "actual_val": 10, "target_val": 100,
                "unit": "% 목표 대비",
                "progress_pct": 10,
                "on_track": None,
                "note": "수익 모델 완성도가 SDV 재평가 핵심 — 아직 초기 단계",
                "status": "in_progress",
            },
        ],
    },
]


def _fetch_robot_peer_returns() -> list[dict]:
    """로봇 피어 1년 수익률 조회 (Yahoo Finance chart API)."""
    results = []
    for name, ticker in _ROBOT_PEERS.items():
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
                        "currency": meta.get("currency", "USD"),
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
    # v10 시도 (KR 지역 우선 → US 폴백)
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
                        logger.info("현대차 quoteSummary OK (%s %s %s)", api_ver, host, params.get("region"))
                        return result[0]
                except Exception as exc:  # noqa: BLE001
                    logger.debug("현대차 quoteSummary 시도 실패 (%s %s): %s", api_ver, host, str(exc)[:80])
    logger.warning("현대차 quoteSummary 모든 시도 실패 — 차트 데이터만 사용")
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
# 밸류에이션(PER·PBR·배당)은 integration API, 분기·연간 실적은 finance API.

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

    per_med = _SECTOR_PE.get("섹터 중앙값", 6.5)
    pbr_med = _SECTOR_PBR.get("섹터 중앙값", 0.8)

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
        # "2025.03." → "2025Q1"
        period_label = title
        try:
            y, m = int(title[:4]), int(title[5:7])
            period_label = f"{y}Q{(m-1)//3+1}"
        except (ValueError, IndexError):
            pass

        rev  = _cell("매출액", key)
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
    actual  = [p for p in periods if p.get("revenue") is not None]
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

    # 비율 지표: TTM 합산이 아닌 최신 분기 값
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
        "fmt": {
            "revenue":    _fmt_krw(rev),
            "gross":      "—",
            "ebitda":     "—",
            "fcf":        "—",
            "ocf":        "—",
            "rev_growth": "—",
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

    # 이력 (최근 252 거래일 ≈ 1년) — 먼저 구성해서 prev 계산에 활용
    timestamps = chart_raw.get("timestamp") or []
    closes = ((chart_raw.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    history: list[dict] = []
    for ts_val, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(int(ts_val), tz=timezone.utc).strftime("%Y-%m-%d")
        history.append({"date": d, "value": round(float(close))})
    history = history[-260:]

    # 전일 종가: Yahoo meta의 previousClose 우선, 없으면 history 직전값
    # chartPreviousClose는 차트 시작일 이전 종가(≈1년 전)이므로 사용 안 함
    prev_meta = meta.get("previousClose")
    if prev_meta:
        prev = prev_meta
    elif len(history) >= 2:
        prev = history[-2]["value"]
    else:
        prev = None

    change = (price - prev) if (price is not None and prev is not None) else None
    change_pct = ((price / prev - 1) * 100) if (price and prev) else None

    # 1년 수익률
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

    # 애널리스트 컨센서스
    target_mean = _rv(fd, "targetMeanPrice")
    target_high = _rv(fd, "targetHighPrice")
    target_low  = _rv(fd, "targetLowPrice")
    n_analysts  = _rv(fd, "numberOfAnalystOpinions")
    rec_key     = _rv_str(fd, "recommendationKey")

    # vs 섹터 평가
    def _vs(val: Optional[float], sector_med: float) -> Optional[str]:
        if val is None:
            return None
        if val < sector_med * 0.82:
            return "할인"
        if val > sector_med * 1.20:
            return "프리미엄"
        return "적정"

    per_sector = _SECTOR_PE.get("섹터 중앙값", 6.5)
    pbr_sector = _SECTOR_PBR.get("섹터 중앙값", 0.8)

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


# ─────────────────────────── 재무 파싱 ───────────────────────────

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
        "fmt": {
            "revenue":    _fmt_krw(revenue),
            "gross":      _fmt_krw(gross),
            "ebitda":     _fmt_krw(ebitda),
            "fcf":        _fmt_krw(fcf),
            "ocf":        _fmt_krw(ocf),
            "rev_growth": (f"+{rev_growth * 100:.1f}%" if (rev_growth and rev_growth > 0)
                           else f"{rev_growth * 100:.1f}%" if rev_growth is not None else "—"),
        },
    }


# ─────────────────────────── 분기/연간 손익 파싱 ───────────────────────────

def _parse_period_list(raw_list: list) -> list[dict]:
    rows = []
    for q in raw_list:
        if not isinstance(q, dict):
            continue
        # endDate 가 {"raw":timestamp, "fmt":"YYYY-MM-DD"} 또는 timestamp 직접
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
                # 분기 판별: 3/6/9/12월 기준
                period = f"{dt.year}Q{(dt.month - 1) // 3 + 1}"
                if dt.month == 12:
                    period = str(dt.year)   # 연간 항목은 연도만
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
    rows.reverse()      # 오래된 순으로 정렬
    # 연간 항목 제거(period가 순수 연도 4자리이면 연간), 최근 8분기만
    quarterly = [r for r in rows if not (len(r["period"]) == 4 and r["period"].isdigit())]
    return quarterly[-8:]


def _parse_annual(qs: Optional[dict]) -> list[dict]:
    raw = ((qs or {}).get("incomeStatementHistory") or {}).get("incomeStatementHistory") or []
    rows = _parse_period_list(raw)
    rows.reverse()
    # period를 연도로 통일
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


def _quick_view(val: dict, fin: dict, price: dict) -> dict:
    """주가 수준·실적 모멘텀·전략 이행 종합 한 줄 평가."""
    # 주가 수준: PER 기반
    per = val.get("per_trailing")
    sector_med = _SECTOR_PE.get("섹터 중앙값", 6.5)
    if per is None:
        price_level, price_tone = "데이터 부족", "neutral"
    elif per < sector_med * 0.75:
        price_level, price_tone = f"저평가 (PER {per:.1f}x · 섹터 대비 할인)", "good"
    elif per < sector_med * 1.10:
        price_level, price_tone = f"적정 (PER {per:.1f}x · 섹터 수준)", "neutral"
    else:
        price_level, price_tone = f"고평가 (PER {per:.1f}x · 섹터 대비 프리미엄)", "warn"

    # 실적 모멘텀: 매출 성장률
    rg = fin.get("revenue_growth")
    om = fin.get("operating_margin")
    if rg is None:
        mom_label, mom_tone = "데이터 부족", "neutral"
    elif rg > 8:
        mom_label, mom_tone = f"강한 성장 (매출 +{rg:.1f}% YoY)", "good"
    elif rg > 0:
        mom_label, mom_tone = f"완만한 성장 (매출 +{rg:.1f}% YoY)", "neutral"
    else:
        mom_label, mom_tone = f"역성장 (매출 {rg:.1f}% YoY)", "bad"

    if om is not None:
        mom_label += f" · 영업이익률 {om:.1f}%"

    # 전략 이행: 모든 플랜 항목에서 achieved/in_progress 비율
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
        # Yahoo quoteSummary 없을 때 Naver Finance 대체
        val       = _parse_naver_valuation(nav_int)
        fin       = _parse_naver_financials_ttm(nav_q) if nav_q else _parse_financials(None)
        quarterly = _parse_naver_periods(nav_q)
        annual    = _parse_naver_periods(nav_a)

    # Naver에서 얻은 시총으로 Yahoo 누락분 보완
    if price.get("market_cap") is None:
        mc = val.pop("_market_cap", None)
        if mc:
            price["market_cap"] = mc
            price["fmt"]["market_cap"] = _fmt_krw(mc)
    else:
        val.pop("_market_cap", None)
    qview     = _quick_view(val, fin, price)

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

    # 전략 플랜 — status 메타 적용 + KPI on_track 레이블
    _TRACK_META = {
        True:  {"label": "정상 궤도", "tone": "good"},
        False: {"label": "주의 필요", "tone": "warn"},
        None:  {"label": "변수 존재", "tone": "neutral"},
    }
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

    # 섹터 PER 기준 적정주가 테이블
    cur_price = price.get("current")
    per_t = val.get("per_trailing")
    bps_v = val.get("book_value")
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

    # PBR 기준 적정주가 범위
    implied_pbr: list[dict] = []
    if bps_v:
        for label, target_pbr in [("보수적 (0.6x)", 0.6), ("섹터 중앙값 (0.8x)", 0.8),
                                   ("현재 수준 (0.87x)", 0.87), ("장부가치 (1.0x)", 1.0),
                                   ("Toyota 수준 (1.1x)", 1.1)]:
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
                "is_current": abs(target_pbr - (val.get("pbr") or 0)) < 0.05,
            })

    # 로봇 섹터 데이터
    robot_peers = _fetch_robot_peer_returns()
    # 현대차 1년 수익률 피어 리스트에 삽입 (비교용)
    hmc_yr = price.get("yr_ret")
    hmc_peer_entry = {
        "name": "현대차 (005380)", "ticker": "005380.KS",
        "ret_1y": hmc_yr or 0,
        "current": cur_price, "currency": "KRW",
        "fmt_ret": price["fmt"]["yr_ret"],
        "tone": price.get("yr_tone", "neutral"),
        "is_hmc": True,
    }
    robot_peers_full = sorted(
        [hmc_peer_entry] + [{**p, "is_hmc": False} for p in robot_peers],
        key=lambda x: x["ret_1y"], reverse=True,
    )

    # Boston Dynamics 옵션가치 추정 (현대차 단독 귀속분)
    bd_val_low_krw  = round(_BD_CONTEXT["val_low_usd"]  * _USDKRW_APPROX * 0.8 / 1e12, 1)
    bd_val_high_krw = round(_BD_CONTEXT["val_high_usd"] * _USDKRW_APPROX * 0.8 / 1e12, 1)
    mkt_cap_t = (price.get("market_cap") or 0) / 1e12  # 조원
    # 자동차 사업 적정가치: EPS × 섹터 중앙값 PER × 발행주수
    mc_auto = None
    if cur_price and per_t:
        eps = cur_price / per_t
        sector_pe_mid = _SECTOR_PE.get("섹터 중앙값", 6.5)
        shares = (price.get("market_cap") or 0) / cur_price if cur_price else 0
        mc_auto = round(eps * sector_pe_mid * shares / 1e12, 1)  # 조원
    option_val = round(mkt_cap_t - mc_auto, 1) if mc_auto else None  # 로봇·SDV 등 옵션가치

    # 로봇 이행 계획 — status 메타 + on_track 메타 적용
    _rtrack = {
        True:  {"label": "정상 궤도", "tone": "good"},
        False: {"label": "주의 필요", "tone": "warn"},
        None:  {"label": "변수 존재", "tone": "neutral"},
    }
    robot_plans_built = []
    rp_score_sum, rp_score_cnt = 0, 0
    for cat in _ROBOT_PLANS:
        items = []
        for it in cat["items"]:
            sm   = _STATUS_META.get(it.get("status", "in_progress"), _STATUS_META["in_progress"])
            tm   = _rtrack.get(it.get("on_track"), _rtrack[None])
            pp   = it.get("progress_pct")
            if pp is not None:
                rp_score_sum += pp; rp_score_cnt += 1
            items.append({
                **it,
                "status_label": sm["label"], "status_tone": sm["tone"],
                "track_label": tm["label"],  "track_tone":  tm["tone"],
            })
        robot_plans_built.append({**cat, "items": items})

    rp_score = round(rp_score_sum / rp_score_cnt) if rp_score_cnt else 0
    robot_plan_summary = {
        "score": rp_score,
        "score_tone": "good" if rp_score >= 60 else "warn" if rp_score >= 30 else "bad",
        "total": rp_score_cnt,
    }

    robot_section = {
        "bd": {
            **_BD_CONTEXT,
            "val_low_krw":  bd_val_low_krw,
            "val_high_krw": bd_val_high_krw,
            "fmt_acq":      _fmt_krw(_BD_CONTEXT["acq_krw"]),
            "fmt_val_low":  f"{bd_val_low_krw:.1f}조원",
            "fmt_val_high": f"{bd_val_high_krw:.1f}조원",
        },
        "peers": robot_peers_full,
        "option_val": option_val,
        "option_val_fmt": f"{option_val:.1f}조원" if option_val else "—",
        "mc_auto_fmt": f"{mc_auto:.1f}조원" if mc_auto else "—",
        "mc_total_fmt": f"{mkt_cap_t:.1f}조원",
        "plans": robot_plans_built,
        "plan_summary": robot_plan_summary,
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
        "robot":       robot_section,
    }


# ─────────────────────────── 캐시 관리 ───────────────────────────

def refresh() -> bool:
    try:
        logger.info("현대차 데이터 갱신 시작...")
        chart_raw = _fetch_chart(_SYMBOL, "1y")
        qs        = _fetch_quote_summary(_SYMBOL)
        nav_code  = _SYMBOL.split(".")[0]   # "005380"
        nav_int   = _fetch_naver_integration(nav_code)
        nav_q     = _fetch_naver_finance(nav_code, "quarter")
        nav_a     = _fetch_naver_finance(nav_code, "annual")
        logger.info("현대차 소스 상태 — quoteSummary:%s Naver_int:%s Naver_q:%s",
                    "OK" if qs else "없음",
                    "OK" if nav_int else "없음",
                    "OK" if nav_q else "없음")
        data = _build(chart_raw, qs, nav_int, nav_q, nav_a)
        with _lock:
            _cache["data"] = data
            _cache["ts"] = time.monotonic()
        logger.info("현대차 데이터 갱신 완료 (quoteSummary: %s)", "OK" if qs else "없음(차트만)")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("현대차 데이터 갱신 실패: %s", exc)
        with _lock:
            if _cache["data"]:
                _cache["data"]["stale"] = True   # 캐시 있으면 stale 표시 후 계속 사용
        return False


def get_data() -> dict:
    with _lock:
        d = _cache["data"]
        age = time.monotonic() - _cache["ts"]
        if d and age < _CACHE_TTL:
            return d
    # 캐시 미스 또는 만료 → 즉시 갱신
    refresh()
    with _lock:
        if _cache["data"]:
            return _cache["data"]
    return {"ok": False, "error": "현대차 데이터 로드 실패 — Yahoo Finance API를 확인하세요."}


# ─────────────────────────── LLM 분석 브리핑 ───────────────────────────

_SYSTEM_PROMPT = """너는 현대자동차(005380.KS) 전담 주식 분석가다.
아래 재무·주가·밸류에이션 데이터를 바탕으로 한국어 분석 브리핑을 작성하라.

규칙:
- 제공된 수치에만 근거. 없는 수치는 인용 금지.
- 투자 권유 금지. 사실 기반 분석·평가.
- 밸류에이션·성장성·리스크를 균형 있게 평가.
- 주가 수준: PER·PBR뿐 아니라 글로벌 자동차 섹터 비교 필수.
- 거시환경(원/달러 환율·유가·EV 경쟁)이 실적에 미치는 영향 언급.
- 출력은 아래 JSON 하나만. 코드펜스 없이.

JSON 스키마:
{
  "headline": "한 줄 핵심 평가 (40자 이내, 한국어)",
  "summary": "4~5문장 종합 판단. 현 주가가 싼지 비싼지, 가장 큰 기회·리스크 포함. 일반 투자자가 바로 이해하는 평이한 표현.",
  "body_md": "마크다운 본문. ## 밸류에이션 평가, ## 실적 모멘텀, ## 전략 이행 현황, ## 핵심 리스크, ## 투자 포인트 요약 5개 섹션."
}"""


def _build_analysis_prompt(data: dict) -> str:
    p  = data.get("price") or {}
    v  = data.get("valuation") or {}
    f  = data.get("financials") or {}
    a  = data.get("analyst") or {}
    ql = data.get("quarterly") or []
    al = data.get("annual") or []
    vf = v.get("fmt") or {}
    ff = f.get("fmt") or {}

    lines = [_SYSTEM_PROMPT, "", "<DATA>",
             f"=== 현대자동차(005380.KS) 분석 데이터 ({(data.get('as_of') or '')[:10]}) ===", ""]

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
    per_t = v.get("per_trailing")
    lines.append(f"  PER(Trailing): {vf.get('per_trailing', '—')}"
                 f" (글로벌 자동차 섹터 중앙값 {_SECTOR_PE.get('섹터 중앙값', '—'):.1f}x)"
                 f" → {v.get('vs_per', '—')}")
    lines.append(f"  PER(Forward): {vf.get('per_forward', '—')}")
    lines.append(f"  PBR: {vf.get('pbr', '—')}"
                 f" (섹터 중앙값 {_SECTOR_PBR.get('섹터 중앙값', '—'):.1f}x)"
                 f" → {v.get('vs_pbr', '—')}")
    lines.append(f"  EV/EBITDA: {vf.get('ev_ebitda', '—')}")
    lines.append(f"  P/S: {vf.get('psr', '—')}")
    lines.append(f"  배당수익률: {vf.get('div_yield', '—')}")
    if a.get("target_mean"):
        lines.append(f"  애널리스트 목표가: {a['fmt'].get('target_mean', '—')}"
                     f" (현재 대비 {a['fmt'].get('upside', '—')})"
                     f" / 컨센서스: {a.get('recommendation', '—')}"
                     f" / {a['fmt'].get('n_analysts', '—')} 커버")
    lines.append("")

    lines += ["## 재무 지표 (TTM)"]
    lines.append(f"  매출: {ff.get('revenue', '—')} (YoY {ff.get('rev_growth', '—')})")
    lines.append(f"  EBITDA: {ff.get('ebitda', '—')}")
    lines.append(f"  잉여현금흐름(FCF): {ff.get('fcf', '—')}")
    if f.get("operating_margin") is not None:
        lines.append(f"  영업이익률: {f['operating_margin']:.1f}%")
    if f.get("net_margin") is not None:
        lines.append(f"  순이익률: {f['net_margin']:.1f}%")
    if f.get("roe") is not None:
        lines.append(f"  ROE: {f['roe']:.1f}%")
    if f.get("debt_to_equity") is not None:
        lines.append(f"  부채/자본: {f['debt_to_equity']:.1f}%")
    lines.append("")

    if ql:
        lines.append("## 최근 분기 실적")
        for q in ql[-4:]:
            qf = q.get("fmt") or {}
            lines.append(f"  {q.get('period', '—')}: 매출 {qf.get('revenue', '—')}"
                         f" / 영업이익 {qf.get('op_profit', '—')}"
                         f" / 영업이익률 {qf.get('op_margin', '—')}")
        lines.append("")

    lines.append("## 전략 이행 현황")
    for ph in _PLANS:
        lines.append(f"  [{ph['title']}]")
        for it in ph["items"]:
            sm = _STATUS_META.get(it.get("status", "planned"), _STATUS_META["planned"])
            lines.append(f"    - {it['label']} → {sm['label']}")
    lines.append("")

    lines.append("## 섹터 PER 비교")
    for name, pe in _SECTOR_PE.items():
        lines.append(f"  {name}: {pe:.1f}x")
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
        return False  # 이미 캐시 유효
    if _analysis_generating.is_set():
        return False  # 이미 생성 중

    def _gen():
        try:
            get_analysis(data)
        finally:
            _analysis_generating.clear()

    _analysis_generating.set()
    threading.Thread(target=_gen, name="hyundai-analysis", daemon=True).start()
    return True


def get_analysis(data: dict) -> Optional[dict]:
    """LLM 현대차 전용 분석 브리핑 (3시간 캐시). data가 ok=False면 None 반환."""
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
        res = llm.complete(prompt, timeout=120, label="현대차 분석")
        if not res.ok:
            return {"ok": False, "error": res.error}

        # briefing._extract_json_obj 재사용
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
        logger.warning("현대차 LLM 분석 실패: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
