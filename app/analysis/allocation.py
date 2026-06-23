"""레짐 점수 → 권고 자산비중 프리셋(결정층 '성장' 축).

위험선호 점수(0~100, regime 5단계)를 위험/안전 자산의 구체 비중으로 번역한다.
점값이 아니라 '중립 대비 얼마나 공격/방어로 기울일까'(틸트)를 한눈에 보여주는 게 목적.

숫자는 참고용 휴리스틱 — 개인 리스크허용도·투자기간·통화·세금을 반영해 조정해야 하며
투자 권유가 아니다. 프리셋은 아래 _PRESETS 한 곳에서 수정(점수 임계는 regime._classify와 정렬).
"""
from __future__ import annotations

# 표시 순서: 위험 → 안전. 세그먼트 색은 style.css 의 .al-seg.<key> 가 담당.
_BUCKETS = [
    ("equity", "주식"),
    ("bond", "채권"),
    ("alt", "금·대안"),
    ("cash", "현금"),
]

# (하한 score, 단계키, 라벨, tone, {버킷: 비중}, 한 줄 근거). 각 행의 비중 합 = 100.
# 점수 임계 70/58/43/31 은 regime._classify 의 5단계와 동일.
_PRESETS = [
    (70, "strong_on", "강한 위험선호", "good",
     {"equity": 70, "bond": 12, "alt": 10, "cash": 8},
     "위험자산 적극 — 추세·유동성·신용 우호. 과열 구간일수록 트레일링 손절로 이익 보호."),
    (58, "on", "위험선호", "good",
     {"equity": 60, "bond": 18, "alt": 10, "cash": 12},
     "위험자산 비중 우위 유지, 채권·현금 안전판도 일정 보유."),
    (43, "neutral", "중립/전환", "warn",
     {"equity": 45, "bond": 25, "alt": 12, "cash": 18},
     "방향 모호 — 균형 배분. 신규 베팅보다 분할·관망."),
    (31, "off", "위험회피", "bad",
     {"equity": 30, "bond": 30, "alt": 15, "cash": 25},
     "위험 축소·듀레이션(장기채)/현금 확대. 반등은 비중 줄일 기회로."),
    (0, "strong_off", "강한 위험회피", "bad",
     {"equity": 15, "bond": 30, "alt": 20, "cash": 35},
     "방어 최우선 — 현금·안전자산 중심. 바닥 확인 전 추격 매수 금지."),
]

# 틸트 기준선 = 중립 단계 비중
_NEUTRAL = next(w for _lo, k, _l, _t, w, _r in _PRESETS if k == "neutral")

_CAVEAT = ("참고용 휴리스틱 — 개인 리스크허용도·투자기간·통화·세금을 반영해 조정 필요. "
           "투자 권유가 아닙니다.")


def _preset_for(score: float):
    for lo, key, label, tone, weights, rationale in _PRESETS:
        if score >= lo:
            return key, label, tone, weights, rationale
    return None


def recommend_allocation(regime: dict | None) -> dict | None:
    """현재 레짐 점수에 대응하는 권고 비중 프리셋(중립 대비 틸트 포함). 점수 없으면 None."""
    if not regime:
        return None
    score = regime.get("score")
    if score is None:
        return None
    found = _preset_for(score)
    if not found:
        return None
    key, label, tone, weights, rationale = found
    buckets = [{"key": bk, "label": bl, "weight": weights[bk],
                "tilt": weights[bk] - _NEUTRAL[bk]} for bk, bl in _BUCKETS]
    return {
        "stage": key, "label": label, "tone": tone, "score": score,
        "is_neutral": key == "neutral",
        "buckets": buckets,
        "rationale": rationale,
        "caveat": _CAVEAT,
    }
