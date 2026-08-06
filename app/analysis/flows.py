"""자금 회전지도 — RRG(Relative Rotation Graph) 좌표 계산.

두 지도를 같은 로직으로 만든다:
  1) 자산군 회전 — 벤치마크 = 크로스에셋 동일가중 바스켓
  2) 지역 회전   — 벤치마크 = 글로벌 주식 동일가중 바스켓

추가 수집 없이 history 시계열만 재가공한다(repo.get_series_batch 재활용).
좌표 정의는 스펙 4.3절. 계산 기준통화는 USD 로 통일한다(4.2절) —
원화 기준은 채권을 환율 복제본으로 만들어 기각됐다.
"""
from __future__ import annotations

import math

from .. import repository as repo

_W_RS = 63        # 상대강도 정규화 창(3개월)
_W_MOM = 21       # 상대모멘텀 정규화 창(1개월)
_TAIL_STEP = 5    # 꼬리 샘플 간격(거래일 ≈ 1주)
_TAIL_N = 8       # 꼬리 점 개수(8주)
_POINTS = 1300    # DB 에서 끌어올 이력 길이
_MIN_DAYS = 130   # RRG 최소 요건(63 + 21 + 꼬리 여유)

# (지표키, 표시라벨, 자산군, 환산 환율키, 환산 연산) — 환산은 USD 기준 통일용
_ASSETS: list[tuple[str, str, str, str, str]] = [
    ("sp500",  "S&P500",   "equity", "", ""),
    ("nasdaq", "나스닥",     "equity", "", ""),
    ("kospi",  "코스피",     "equity", "usdkrw", "/"),
    ("tlt",    "미 장기국채",  "bond",   "", ""),
    ("hyg",    "하이일드채",  "bond",   "", ""),
    ("gold",   "금",        "real",   "", ""),
    ("copper", "구리",       "real",   "", ""),
    ("wti",    "WTI",      "real",   "", ""),
    ("btc",    "비트코인",    "crypto", "", ""),
    ("usdkrw", "원/달러",    "fx",     "", ""),
]

# 지역 지도. 홍콩(HKD)은 달러 페그라 상수 배율 → t0 정규화에서 약분되므로 환산 불필요.
_REGIONS: list[tuple[str, str, str, str, str]] = [
    ("sp500",    "미국",     "region", "", ""),
    ("kospi",    "한국",     "region", "usdkrw", "/"),
    ("eustoxx",  "유럽",     "region", "eurusd", "*"),
    ("shanghai", "중국본토",  "region", "usdcny", "/"),
    ("hangseng", "홍콩",     "region", "", ""),
    ("nikkei",   "일본",     "region", "usdjpy", "/"),
]

GROUP_LABEL = {"equity": "주식", "bond": "채권", "real": "실물",
               "crypto": "코인", "fx": "통화", "region": "지역"}

_QUADRANTS = ("주도", "약화", "개선", "지체")


def _quadrant(x: float, y: float) -> str:
    """RRG 사분면. 자금은 통상 시계방향으로 순환: 개선→주도→약화→지체."""
    if x >= 100.0:
        return "주도" if y >= 100.0 else "약화"
    return "개선" if y >= 100.0 else "지체"


def _norm(vals: list[float], win: int) -> list[float | None]:
    """롤링 z-정규화: 100 + (x − 평균) / 표준편차(모집단). 창이 안 차면 None."""
    out: list[float | None] = [None] * len(vals)
    for i in range(win - 1, len(vals)):
        w = vals[i - win + 1:i + 1]
        mean = sum(w) / win
        sd = math.sqrt(sum((v - mean) ** 2 for v in w) / win)
        out[i] = 100.0 + (vals[i] - mean) / sd if sd else 100.0
    return out


def _usd_prices(spec, S, dates) -> dict[str, list[float]]:
    """USD 기준 가격열. 환율키가 있으면 지정 연산으로 환산."""
    out: dict[str, list[float]] = {}
    for key, _, _, fx_key, op in spec:
        if not fx_key:
            out[key] = [S[key][d] for d in dates]
        elif op == "/":
            out[key] = [S[key][d] / S[fx_key][d] for d in dates]
        else:
            out[key] = [S[key][d] * S[fx_key][d] for d in dates]
    return out


def _rrg(spec) -> dict | None:
    """자산 목록 → RRG 좌표 묶음. 데이터 부족·결측 시 None."""
    keys = {k for k, _, _, _, _ in spec}
    keys |= {f for _, _, _, f, _ in spec if f}
    raw = repo.get_series_batch(sorted(keys), _POINTS)
    S = {k: {p["date"]: p["value"] for p in raw.get(k, []) if p.get("value") is not None}
         for k in keys}
    if any(not S[k] for k in keys):
        return None

    # 공통 거래일 교집합에서만 계산(휴장일·주말 불일치 제거)
    dates = [d for d in sorted(S[spec[0][0]]) if all(S[k].get(d) is not None for k in keys)]
    if len(dates) < _MIN_DAYS:
        return None

    price = _usd_prices(spec, S, dates)
    order = [k for k, _, _, _, _ in spec]
    # t0 = 교집합 첫 날을 100 으로 정규화(전 자산 동일 기준일)
    N = {k: [100.0 * v / price[k][0] for v in price[k]] for k in order}
    B = [sum(N[k][i] for k in order) / len(order) for i in range(len(dates))]

    X: dict[str, list[float | None]] = {}
    Y: dict[str, list[float | None]] = {}
    for k in order:
        rs = [100.0 * N[k][i] / B[i] for i in range(len(dates))]
        r = _norm(rs, _W_RS)
        # 모멘텀은 RS-Ratio 위에 다시 정규화한다. 미확정 구간을 100 으로 채운 뒤
        # 마스킹하는 이 순서 그대로 기준 좌표가 산출됐다 — 순서를 바꾸면 값이 달라진다.
        m = _norm([v if v is not None else 100.0 for v in r], _W_MOM)
        X[k] = r
        Y[k] = [None if r[i] is None else m[i] for i in range(len(r))]

    last = len(dates) - 1
    rows = []
    for key, label, group, _, _ in spec:
        tail = []
        for j in range(_TAIL_N - 1, -1, -1):
            i = last - _TAIL_STEP * j
            if i < 0 or X[key][i] is None or Y[key][i] is None:
                continue
            tail.append({"x": round(X[key][i], 3), "y": round(Y[key][i], 3), "date": dates[i]})
        if not tail:
            continue
        cur = tail[-1]
        rows.append({
            "key": key, "label": label, "group": group,
            "group_label": GROUP_LABEL.get(group, group),
            "x": cur["x"], "y": cur["y"],
            "quadrant": _quadrant(cur["x"], cur["y"]),
            "quadrant_prev": _quadrant(tail[0]["x"], tail[0]["y"]),
            "tail": tail,
        })
    if not rows:
        return None
    rows.sort(key=lambda r: -r["x"])
    return {"as_of": dates[last], "days": len(dates), "rows": rows}


def asset_map() -> dict | None:
    """지도 1 — 자산군 회전(벤치마크 = 크로스에셋 동일가중)."""
    return _rrg(_ASSETS)


def region_map() -> dict | None:
    """지도 2 — 지역 회전(벤치마크 = 글로벌 주식 동일가중)."""
    return _rrg(_REGIONS)
