"""
중앙 설정 + 지표 레지스트리.

지표(Indicator) 정의가 수집기·저장소·프론트엔드 전체에서 공유되는 단일 출처(SSOT)다.
새 지표를 추가하려면 아래 INDICATORS 리스트에 한 줄 추가하면 끝.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (ValueError, AttributeError):
        return default


def _minute_spec(name: str, default: str = "0") -> str:
    """cron 분(minute) 스펙을 검증. 잘못된 값이면 default 로 폴백(기동 실패 방지).

    허용: 콤마구분 정수(0~59) 예) "0", "0,30"  |  "*/N"(1~59) 예) "*/15".
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    if re.fullmatch(r"\*/\d{1,2}", raw):
        n = int(raw[2:])
        return raw if 1 <= n <= 59 else default
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    try:
        vals = [int(p) for p in parts]
    except ValueError:
        return default
    if vals and all(0 <= v <= 59 for v in vals):
        return ",".join(str(v) for v in vals)
    return default


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = _i("PORT", 8000)
    collect_minute: str = _minute_spec("COLLECT_MINUTE", "0")
    collect_on_start: bool = _b("COLLECT_ON_START", True)

    enable_llm: bool = _b("ENABLE_LLM_BRIEFING", True)
    claude_bin: str = os.getenv("CLAUDE_BIN", "").strip()
    claude_model: str = os.getenv("CLAUDE_MODEL", "").strip()
    llm_timeout: int = _i("LLM_TIMEOUT", 180)

    # 뉴스 영문→한글 번역(claude -p, 신규 항목만 캐시). 실패 시 원문 표시.
    enable_news_translation: bool = _b("ENABLE_NEWS_TRANSLATION", True)
    claude_translate_model: str = os.getenv("CLAUDE_TRANSLATE_MODEL", "haiku").strip()
    translate_timeout: int = _i("TRANSLATE_TIMEOUT", 60)

    fred_api_key: str = os.getenv("FRED_API_KEY", "").strip()
    ecos_api_key: str = os.getenv("ECOS_API_KEY", "").strip()

    request_timeout: int = _i("REQUEST_TIMEOUT", 20)
    history_days: int = _i("HISTORY_DAYS", 120)        # 스냅샷/뉴스 보관 기준(일)
    history_points: int = _i("HISTORY_POINTS", 400)    # 차트 이력: 키별 최근 N개 보존(일/월 빈도 무관)

    db_path: Path = BASE_DIR / "data" / "economic.db"
    web_dir: Path = BASE_DIR / "app" / "web"

    # 경제 뉴스 RSS (키 없음). 도달 불가하면 자동 스킵.
    news_feeds: tuple = (
        ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
        ("WSJ Economy", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
        ("Investing.com 경제", "https://www.investing.com/rss/news_25.rss"),
        ("Investing.com 시장", "https://www.investing.com/rss/news_301.rss"),
        ("CNBC Economy", "https://search.cnbc.com/rss/2.0/106003/?partnerId=wrss01-18"),
        ("MarketWatch Top", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    )


settings = Settings()


# ─────────────────────────── 지표 레지스트리 ───────────────────────────

# category: 화면 그룹핑 + 한글 라벨
CATEGORIES: dict[str, str] = {
    "equity": "주가지수",
    "volatility": "변동성",
    "fx": "환율",
    "rate": "금리·채권",
    "commodity": "원자재",
    "crypto": "암호화폐",
    "us_macro": "미국 거시지표",
    "kr_macro": "한국 거시지표",
}


@dataclass(frozen=True)
class Indicator:
    key: str                       # 내부 고유 키
    label: str                     # 화면 표시명 (한글)
    category: str                  # CATEGORIES 키
    source: str                    # "yahoo" | "fred" | "ecos"
    symbol: str                    # 소스별 심볼/시리즈 ID
    unit: str = ""                 # "", "%", "P", "천명", "원" 등
    decimals: int = 2              # 표시 소수 자리
    # 값이 오르면 긍정(green)인지(주가) 단순 중립인지. 화면 색상 힌트.
    up_is_good: Optional[bool] = None
    # FRED 변환: None | "yoy"(전년동월대비 %) — 지수 시리즈를 등락률로
    transform: Optional[str] = None
    # 데이터 빈도: "D"(일)/"W"(주)/"M"(월) — 신선도 경고·변화기준 라벨(전일/전주/전월)에 사용
    freq: str = "D"
    # 파생지표 계산식(source="derived"): (lhs_key, op, rhs_key). 예) 실질금리 = 명목 - 기대인플레
    derived: Optional[tuple] = None
    # ECOS 전용 메타
    ecos_item: str = ""            # 통계항목코드(ITEM_CODE1)
    ecos_cycle: str = "M"          # 주기 D/M/Q/A
    note: str = ""                 # 보조 설명


INDICATORS: list[Indicator] = [
    # ── 주가지수 (Yahoo) ──
    Indicator("sp500",   "S&P 500",      "equity", "yahoo", "^GSPC",  decimals=2, up_is_good=True),
    Indicator("nasdaq",  "나스닥 종합",   "equity", "yahoo", "^IXIC",  decimals=2, up_is_good=True),
    Indicator("dow",     "다우존스",      "equity", "yahoo", "^DJI",   decimals=2, up_is_good=True),
    Indicator("kospi",   "코스피",        "equity", "yahoo", "^KS11",  decimals=2, up_is_good=True),
    Indicator("kosdaq",  "코스닥",        "equity", "yahoo", "^KQ11",  decimals=2, up_is_good=True),
    Indicator("nikkei",  "닛케이 225",    "equity", "yahoo", "^N225",  decimals=2, up_is_good=True),
    Indicator("hangseng","항셍",          "equity", "yahoo", "^HSI",   decimals=2, up_is_good=True),
    Indicator("eustoxx", "유로스톡스 50", "equity", "yahoo", "^STOXX50E", decimals=2, up_is_good=True),
    Indicator("shanghai","상하이 종합",   "equity", "yahoo", "000001.SS", decimals=2, up_is_good=True),

    # ── 변동성 ──
    Indicator("vix", "VIX 공포지수", "volatility", "yahoo", "^VIX", decimals=2, up_is_good=False,
              note="20 이상이면 시장 불안 고조"),

    # ── 환율 (Yahoo) ──
    Indicator("usdkrw", "원/달러",       "fx", "yahoo", "KRW=X",    decimals=2),
    Indicator("dxy",    "달러인덱스 DXY", "fx", "yahoo", "DX-Y.NYB", decimals=3,
              note="달러 종합 강세 지수"),
    Indicator("eurusd", "유로/달러",     "fx", "yahoo", "EURUSD=X", decimals=4),
    Indicator("usdjpy", "엔/달러",       "fx", "yahoo", "JPY=X",    decimals=3),
    Indicator("usdcny", "위안/달러",     "fx", "yahoo", "CNY=X",    decimals=4),

    # ── 금리·채권 (Yahoo 수익률 지수, % 단위) ──
    Indicator("us10y", "미 국채 10년", "rate", "yahoo", "^TNX", unit="%", decimals=3, up_is_good=None),
    Indicator("us30y", "미 국채 30년", "rate", "yahoo", "^TYX", unit="%", decimals=3),
    Indicator("us05y", "미 국채 5년",  "rate", "yahoo", "^FVX", unit="%", decimals=3),
    Indicator("us13w", "미 국채 13주", "rate", "yahoo", "^IRX", unit="%", decimals=3),

    # ── 원자재 (Yahoo 선물) ──
    Indicator("wti",    "WTI 유가",  "commodity", "yahoo", "CL=F", unit="$", decimals=2),
    Indicator("brent",  "브렌트유",  "commodity", "yahoo", "BZ=F", unit="$", decimals=2),
    Indicator("gold",   "금",        "commodity", "yahoo", "GC=F", unit="$", decimals=2),
    Indicator("silver", "은",        "commodity", "yahoo", "SI=F", unit="$", decimals=3),
    Indicator("copper", "구리",      "commodity", "yahoo", "HG=F", unit="$", decimals=4),
    Indicator("natgas", "천연가스",  "commodity", "yahoo", "NG=F", unit="$", decimals=3),

    # ── 암호화폐 ──
    Indicator("btc", "비트코인",   "crypto", "yahoo", "BTC-USD", unit="$", decimals=0, up_is_good=None),
    Indicator("eth", "이더리움",   "crypto", "yahoo", "ETH-USD", unit="$", decimals=2),

    # ── 미국 거시 (FRED 시리즈; 키 없으면 CSV 폴백, 도달 불가 시 자동 스킵) ──
    Indicator("us_fedfunds", "미 기준금리(실효)", "us_macro", "fred", "DFF",    unit="%", decimals=2),
    Indicator("us_cpi_yoy",  "미 CPI 전년比",     "us_macro", "fred", "CPIAUCSL", unit="%", decimals=2, transform="yoy", up_is_good=False, freq="M"),
    Indicator("us_core_pce", "미 근원 PCE 전년比","us_macro", "fred", "PCEPILFE", unit="%", decimals=2, transform="yoy", up_is_good=False, freq="M"),
    Indicator("us_unrate",   "미 실업률",         "us_macro", "fred", "UNRATE",  unit="%", decimals=1, up_is_good=False, freq="M"),
    Indicator("us_10y2y",    "미 장단기차(10Y-2Y)","us_macro","fred", "T10Y2Y",  unit="%", decimals=2,
              note="음수면 장단기금리 역전(침체 신호)"),
    Indicator("us_hy_spread","미 하이일드 스프레드","us_macro","fred", "BAMLH0A0HYM2", unit="%", decimals=2, up_is_good=False,
              note="신용위험·경기불안 척도"),
    Indicator("us_claims",   "미 신규실업수당청구","us_macro","fred", "ICSA",   unit="건", decimals=0, up_is_good=False, freq="W"),
    Indicator("us_be10y",    "미 10년 기대인플레","us_macro","fred", "T10YIE",  unit="%", decimals=2, up_is_good=False,
              note="10년 BEI(손익분기 인플레이션)"),
    # 파생: 실질 10년금리 = 명목 10년(^TNX) − 10년 기대인플레(T10YIE)
    Indicator("us_real10y",  "미 10년 실질금리",  "us_macro", "derived", "", unit="%", decimals=2,
              derived=("us10y", "-", "us_be10y"),
              note="명목 10년 − 10년 기대인플레. 실제 통화긴축 강도"),

    # ── 한국 거시 (ECOS; 키 있을 때만 활성) ──
    Indicator("kr_base_rate", "한국 기준금리", "kr_macro", "ecos", "722Y001",
              unit="%", decimals=2, ecos_item="0101000", ecos_cycle="M", freq="M"),
    Indicator("kr_cpi_yoy",   "한국 CPI 전년比", "kr_macro", "ecos", "901Y009",
              unit="%", decimals=2, ecos_item="0", ecos_cycle="M", up_is_good=False,
              transform="yoy", freq="M"),
]

INDICATOR_BY_KEY: dict[str, Indicator] = {ind.key: ind for ind in INDICATORS}


def indicators_for_source(source: str) -> list[Indicator]:
    return [ind for ind in INDICATORS if ind.source == source]


# ── 지표 우선순위(브리핑 신호/잡음 + 화면 강조) ──
# 1=핵심(레짐·정책·위험), 2=보통(기본), 3=부가(세부 원자재/만기/지역지수)
_PRIORITY_1 = {
    "sp500", "nasdaq", "vix", "us10y", "us_real10y", "dxy", "us_fedfunds",
    "us_cpi_yoy", "us_10y2y", "us_hy_spread", "kospi", "usdkrw", "gold", "btc", "wti",
}
_PRIORITY_3 = {
    "silver", "copper", "natgas", "us05y", "us13w", "us30y",
    "eurusd", "usdjpy", "usdcny", "eth", "shanghai", "eustoxx", "hangseng",
}


def priority_of(key: str) -> int:
    if key in _PRIORITY_1:
        return 1
    if key in _PRIORITY_3:
        return 3
    return 2


# ── 데이터 신뢰 구분(소스 성격) ──
_SOURCE_TIER = {"yahoo": "시장", "fred": "공식", "ecos": "공식", "derived": "파생"}


def source_tier(key: str) -> str:
    ind = INDICATOR_BY_KEY.get(key)
    return _SOURCE_TIER.get(ind.source, "—") if ind else "—"
