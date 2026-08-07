"""자금 회전지도 — RRG(Relative Rotation Graph) 좌표 계산.

두 지도를 같은 로직으로 만든다:
  1) 자산군 회전 — 벤치마크 = 크로스에셋 동일가중 바스켓
  2) 지역 회전   — 벤치마크 = 글로벌 주식 동일가중 바스켓

추가 수집 없이 history 시계열만 재가공한다(repo.get_series_batch 재활용).
좌표 정의는 스펙 4.3절. 계산 기준통화는 USD 로 통일한다(4.2절) —
원화 기준은 채권을 환율 복제본으로 만들어 기각됐다.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta

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

_VIX_KEY = "vix"
_VIX_SHOCK_PCT = 0.10     # VIX 상위 10% 급등일을 '위험 국면'으로 본다


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
    risk = _risk_scores(spec, S, dates)
    for r in rows:
        r["risk"] = risk.get(r["key"])
    rows.sort(key=lambda r: -r["x"])
    return {"as_of": dates[last], "days": len(dates), "rows": rows}


def _log_returns(vals: list[float]) -> list[float]:
    return [math.log(b / a) if a > 0 and b > 0 else 0.0 for a, b in zip(vals, vals[1:])]


def _risk_scores(spec, S, dates) -> dict[str, float]:
    """위험 성격 — VIX 급등일(상위 10%) 평균 수익률 %. 음수=공격, 양수=방어.

    임계선으로 3분류하지 않고 연속값 그대로 쓴다(스펙 4.4절):
    경계 자산이 룩백에 따라 색이 깜빡이는 것을 원천 차단한다.
    """
    vix_raw = repo.get_series_batch([_VIX_KEY], _POINTS).get(_VIX_KEY) or []
    vix = {p["date"]: p["value"] for p in vix_raw if p.get("value") is not None}
    common = [d for d in dates if d in vix]
    if len(common) < _W_RS:
        return {}
    vr = _log_returns([vix[d] for d in common])
    k = max(1, int(len(vr) * _VIX_SHOCK_PCT))
    shock = sorted(range(len(vr)), key=lambda i: -vr[i])[:k]

    price = _usd_prices(spec, S, common)
    out: dict[str, float] = {}
    for key, _, _, _, _ in spec:
        r = _log_returns(price[key])
        vals = [r[i] for i in shock if i < len(r)]
        if vals:
            out[key] = round(sum(vals) / len(vals) * 100.0, 3)
    return out


def asset_map() -> dict | None:
    """지도 1 — 자산군 회전(벤치마크 = 크로스에셋 동일가중)."""
    return _rrg(_ASSETS)


def region_map() -> dict | None:
    """지도 2 — 지역 회전(벤치마크 = 글로벌 주식 동일가중)."""
    return _rrg(_REGIONS)


# 순위표 대상 — 지도에 없는 자산까지 넓게 본다(지도는 좁게, 표는 넓게).
# (지표키, 표시라벨, 환산 환율키, 환산 연산) — 지도와 같은 USD 기준으로 맞춘다.
_RANK_KEYS: list[tuple[str, str, str, str]] = [
    ("sp500", "S&P500", "", ""), ("nasdaq", "나스닥", "", ""),
    ("kospi", "코스피", "usdkrw", "/"), ("eustoxx", "유럽", "eurusd", "*"),
    ("nikkei", "일본", "usdjpy", "/"), ("hangseng", "홍콩", "", ""),
    ("shanghai", "중국본토", "usdcny", "/"),
    ("tlt", "미 장기국채", "", ""), ("hyg", "하이일드채", "", ""), ("lqd", "투자등급채", "", ""),
    ("gold", "금", "", ""), ("silver", "은", "", ""), ("copper", "구리", "", ""),
    ("wti", "WTI", "", ""), ("btc", "비트코인", "", ""), ("eth", "이더리움", "", ""),
    ("usdkrw", "원/달러", "", ""), ("dxy", "달러인덱스", "", ""),
]
# 룩백은 반드시 달력 기준이어야 한다. 봉 개수로 세면 주말도 거래되는 자산(btc 는
# 픽스쳐에 주말 봉 370개, sp500 은 0개)이 훨씬 짧은 기간을 보게 되어 순위가 뒤집힌다.
# 실측: btc 63봉 = +5.48% vs 실제 3개월 = -19.86% (25%p 오차, 2위 -> 13위).
_RANK_LOOKBACK_DAYS = 91          # 3개월
_RANK_PREV_DAYS = 182             # 6개월(직전 3개월 구간의 기준점)


def _value_at_or_before(dates: list[str], vals: dict[str, float], target: str) -> float | None:
    """target 이하의 마지막 관측값. 휴장일·결측을 건너뛴다."""
    i = bisect.bisect_right(dates, target) - 1
    return vals[dates[i]] if i >= 0 else None


def rank_shift() -> list[dict]:
    """3개월 모멘텀 순위 — 현재와 3개월 전 시점을 비교해 ▲▼ 를 낸다.

    각 자산의 자기 이력만 쓰므로(교집합 불필요) 지도보다 대상을 넓게 잡을 수 있다.
    다만 룩백은 달력 기준이고 가격은 USD 기준이라, 지도와 같은 것을 재는 것이 보장된다.
    """
    keys = {k for k, _, _, _ in _RANK_KEYS} | {f for _, _, f, _ in _RANK_KEYS if f}
    raw = repo.get_series_batch(sorted(keys), _POINTS)
    S = {k: {p["date"]: p["value"] for p in raw.get(k, []) if p.get("value") is not None}
         for k in keys}

    now_m, prev_m = {}, {}
    for key, _, fx_key, op in _RANK_KEYS:
        s = S.get(key) or {}
        if not s:
            continue
        if fx_key:
            fx = S.get(fx_key) or {}
            dates = sorted(d for d in s if d in fx)
            vals = {d: (s[d] / fx[d] if op == "/" else s[d] * fx[d]) for d in dates}
        else:
            dates, vals = sorted(s), s
        if not dates:
            continue

        last = date.fromisoformat(dates[-1])
        t_now = (last - timedelta(days=_RANK_LOOKBACK_DAYS)).isoformat()
        t_prev = (last - timedelta(days=_RANK_PREV_DAYS)).isoformat()
        if dates[0] > t_prev:               # 6개월 전 관측이 아예 없으면 비교 불가
            continue
        base_now = _value_at_or_before(dates, vals, t_now)
        base_prev = _value_at_or_before(dates, vals, t_prev)
        mid = _value_at_or_before(dates, vals, t_now)
        if not base_now or not base_prev or not mid:
            continue
        now_m[key] = (vals[dates[-1]] / base_now - 1.0) * 100.0
        prev_m[key] = (mid / base_prev - 1.0) * 100.0
    if not now_m:
        return []

    def ranked(d: dict[str, float]) -> dict[str, int]:
        order = sorted(d, key=lambda k: -d[k])
        return {k: i + 1 for i, k in enumerate(order)}

    rn, rp = ranked(now_m), ranked(prev_m)
    label = {k: lbl for k, lbl, _, _ in _RANK_KEYS}
    rows = [{"key": k, "label": label[k], "now": rn[k], "prev": rp[k],
             "shift": rp[k] - rn[k], "m3": round(now_m[k], 2)} for k in rn]
    rows.sort(key=lambda r: r["now"])
    return rows


_SUMMARY_MAX_NAMES = 3


def _names(labels: list[str]) -> str:
    """이름이 많으면 3개까지만 쓰고 나머지는 개수로 — 문장이 8개를 나열하면 못 읽는다."""
    if len(labels) <= _SUMMARY_MAX_NAMES:
        return " · ".join(labels)
    return f"{' · '.join(labels[:_SUMMARY_MAX_NAMES])} 외 {len(labels) - _SUMMARY_MAX_NAMES}개"


def _summary(m: dict | None) -> dict | None:
    """사분면 전이를 규칙으로 문장화. LLM 미사용(브리핑 지연 없음).

    **관측한 것만 말한다.** 이 지도는 가격 상대강도이지 자금유입 측정이 아니므로
    "자금이 이동했습니다"라고 단정하지 않는다 — 같은 화면이 대리지표임을 명시하는데
    가장 큰 글씨가 그것을 부정하면 안 된다.

    이탈은 `주도`에서 벗어난 경우로 한정한다. `개선 → 지체`는 '약한 바닥에서
    회복하다 되밀린 것'이지 주도권 상실이 아니라, 이탈로 부르면 오독을 부른다.
    """
    if not m:
        return None
    into = [r["label"] for r in m["rows"]
            if r["quadrant"] == "주도" and r["quadrant_prev"] != "주도"]
    out = [r["label"] for r in m["rows"]
           if r["quadrant_prev"] == "주도" and r["quadrant"] != "주도"]
    # 자산명은 받침이 제각각이고("금" vs "나스닥" vs "S&P500") 한글이 아닌 것도 있어
    # 은/는·이/가 를 붙이면 "원/달러은(는)" 같은 문장이 나온다. 목록을 문장 끝에 두고
    # 어떤 낱말 뒤에도 붙는 '입니다'로 닫아 조사 선택 자체를 없앤다.
    if into and out:
        text = (f"지난 8주 상대강도가 앞선 자산은 {_names(into)}, "
                f"주도권에서 밀린 자산은 {_names(out)}입니다.")
    elif into:
        text = f"지난 8주 새로 주도권을 잡은 자산은 {_names(into)}입니다."
    elif out:
        text = f"지난 8주 주도권에서 밀린 자산은 {_names(out)}입니다."
    else:
        text = "지난 8주 사분면 이동이 없었습니다 — 기존 흐름이 유지되고 있습니다."
    return {"into": into, "out": out, "text": text}


def build_flows() -> dict:
    """페이지용 묶음. 개별 실패는 None/빈 리스트로 격리해 전체를 무력화하지 않는다."""
    def safe(fn, fallback):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return fallback

    asset = safe(asset_map, None)
    return {
        "asset": asset,
        "region": safe(region_map, None),
        "ranks": safe(rank_shift, []),
        "summary": _summary(asset),
    }
