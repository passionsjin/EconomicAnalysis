"""보유 데이터 가공 오버레이 — 섹터 상대강도·수익률곡선·크로스에셋 모멘텀.

새 수집 없이 history 시계열만 재가공해, 69개 카드를 일일이 안 봐도
'지금 무엇이 시장을 이기고 무엇이 지는가'를 한눈에 보여주는 종합 결정층.
모든 계산은 repo.get_series / get_series_batch(기존 적재) 재활용 — 추가 수집 0.
"""
from __future__ import annotations

from .. import repository as repo

# 모멘텀 룩백 거래일 근사(1개월 / 3개월). 인덱스 기준이라 빈도 무관.
_P1, _P3 = 21, 63

# 11개 SPDR 섹터(벤치마크 = S&P500). cyc=경기민감 태그.
_SECTORS: list[tuple[str, str]] = [
    ("xlk", "기술"), ("xlf", "금융"), ("xle", "에너지"), ("xlv", "헬스케어"),
    ("xli", "산업재"), ("xly", "임의소비재"), ("xlp", "필수소비재"),
    ("xlu", "유틸리티"), ("xlb", "소재"), ("xlre", "부동산"), ("xlc", "커뮤니케이션"),
]
_BENCH = "sp500"
_CYCLICAL = {"xlk", "xly", "xlf", "xli", "xlb", "xle"}   # 경기민감 섹터(표시 태그용)

# 수익률곡선 만기축: (지표키, 표시라벨, x축 연수)
_CURVE: list[tuple[str, str, float]] = [
    ("us13w", "3M", 0.25), ("us02y", "2Y", 2.0), ("us05y", "5Y", 5.0),
    ("us10y", "10Y", 10.0), ("us30y", "30Y", 30.0),
]

# 크로스에셋 모멘텀 대상: (지표키, 표시라벨, 자산군)
_CROSS: list[tuple[str, str, str]] = [
    ("sp500", "S&P500", "주식"), ("nasdaq", "나스닥", "주식"), ("kospi", "코스피", "주식"),
    ("gold", "금", "원자재"), ("wti", "WTI", "원자재"), ("copper", "구리", "원자재"),
    ("silver", "은", "원자재"), ("btc", "비트코인", "암호화폐"), ("eth", "이더리움", "암호화폐"),
    ("hyg", "하이일드채", "채권"), ("lqd", "투자등급채", "채권"), ("dxy", "달러", "통화"),
]


def _vd(raw: dict, key: str) -> list[tuple[str, float]]:
    """get_series(_batch) 결과 → [(date, value)] 오름차순(결측 제거)."""
    return [(p["date"], p["value"]) for p in raw.get(key, []) if p.get("value") is not None]


def _ret(vd: list[tuple[str, float]], periods: int) -> float | None:
    """최근값 대비 `periods` 거래일 전 수익률(%). 인덱스 기준 룩백."""
    if len(vd) < 2:
        return None
    cur = vd[-1][1]
    base = vd[max(0, len(vd) - 1 - periods)][1]
    return (cur / base - 1.0) * 100.0 if base else None


def _scale(rows: list[dict], field: str) -> float:
    """막대 폭 정규화용 최대 절대값(0 분모 방지)."""
    m = max((abs(r[field]) for r in rows if r.get(field) is not None), default=1.0)
    return m or 1.0


def sector_rs() -> dict | None:
    """섹터 상대강도: 각 섹터 수익률 − S&P500 수익률(%p). 1개월 상대강도로 정렬."""
    keys = [_BENCH] + [k for k, _ in _SECTORS]
    raw = repo.get_series_batch(keys, _P3 + 20)
    b = _vd(raw, _BENCH)
    b1, b3 = _ret(b, _P1), _ret(b, _P3)
    if b1 is None and b3 is None:
        return None
    rows = []
    for k, lbl in _SECTORS:
        s = _vd(raw, k)
        r1, r3 = _ret(s, _P1), _ret(s, _P3)
        rel1 = (r1 - b1) if (r1 is not None and b1 is not None) else None
        rel3 = (r3 - b3) if (r3 is not None and b3 is not None) else None
        if rel1 is None and rel3 is None:
            continue
        rows.append({"key": k, "label": lbl, "r1": r1, "r3": r3,
                     "rel1": rel1, "rel3": rel3, "cyc": k in _CYCLICAL})
    if not rows:
        return None
    rows.sort(key=lambda x: (x["rel1"] is None, -(x["rel1"] if x["rel1"] is not None else 0.0)))
    return {"bench_r1": b1, "bench_r3": b3, "rows": rows, "scale": _scale(rows, "rel1")}


def yield_curve() -> dict | None:
    """미 국채 수익률곡선(3M~30Y) + 1개월 전 곡선 + 핵심 스프레드/역전 플래그.

    5개 만기 전부 동일 % 스케일(검증 완료) — 직접 비교/스프레드 산출 가능.
    """
    pts = []
    for k, lbl, mat in _CURVE:
        s = [(p["date"], p["value"]) for p in repo.get_series(k, _P1 + 12)
             if p.get("value") is not None]
        cur = s[-1][1] if s else None
        prev = s[max(0, len(s) - 1 - _P1)][1] if len(s) > 1 else None
        pts.append({"key": k, "label": lbl, "mat": mat, "cur": cur, "prev": prev,
                    "chg": (cur - prev) if (cur is not None and prev is not None) else None})
    if not any(p["cur"] is not None for p in pts):
        return None

    def cur_of(lbl: str):
        p = next((q for q in pts if q["label"] == lbl), None)
        return p["cur"] if p else None

    y3m, y2, y10 = cur_of("3M"), cur_of("2Y"), cur_of("10Y")
    s102 = (y10 - y2) if (y10 is not None and y2 is not None) else None
    s103m = (y10 - y3m) if (y10 is not None and y3m is not None) else None
    return {"points": pts, "s_10_2": s102, "s_10_3m": s103m,
            "inv_10_2": bool(s102 is not None and s102 < 0),
            "inv_10_3m": bool(s103m is not None and s103m < 0)}


def cross_momentum() -> dict | None:
    """크로스에셋 3개월 모멘텀 랭킹(자산군 횡단 상대강도). 무엇이 위험선호/회피를 이끄나."""
    keys = [k for k, _, _ in _CROSS]
    raw = repo.get_series_batch(keys, _P3 + 20)
    rows = []
    for k, lbl, grp in _CROSS:
        s = _vd(raw, k)
        m1, m3 = _ret(s, _P1), _ret(s, _P3)
        if m1 is None and m3 is None:
            continue
        rows.append({"key": k, "label": lbl, "group": grp, "m1": m1, "m3": m3})
    if not rows:
        return None
    rows.sort(key=lambda x: (x["m3"] is None, -(x["m3"] if x["m3"] is not None else 0.0)))
    return {"rows": rows, "scale": _scale(rows, "m3")}


def build_overlays() -> dict:
    """대시보드용 3종 오버레이 묶음. 개별 실패는 None 으로 격리(전체 무력화 방지)."""
    def safe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None
    return {
        "sector_rs": safe(sector_rs),
        "yield_curve": safe(yield_curve),
        "cross_momentum": safe(cross_momentum),
    }
