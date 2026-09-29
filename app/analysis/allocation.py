"""레짐 단계 → 권고 자산비중 프리셋(결정층 '성장' 축).

위험선호 단계(regime 5단계, 히스테리시스 적용)를 위험/안전 자산의 구체 비중으로 번역한다.
점값이 아니라 '중립 대비 얼마나 공격/방어로 기울일까'(틸트)를 한눈에 보여주는 게 목적.

틸트를 일부러 완만하게(주식 45~60%) 둔다. 5년 소급(2021-10~2026-09) 결과:
- 레짐 점수는 이후 '낙폭'은 잘 예고하지만 '수익률'은 예고하지 못한다 — 강한 위험회피 구간의
  60일 후 S&P 수익률이 오히려 가장 높았다.
- 옛 프리셋(주식 15~70%)은 주식 45% 고정과 수익이 같았고(1.319 vs 1.315배) 낙폭만 조금 줄었다.
  히스테리시스를 얹으면 고정비중보다 못했다(1.284배).
- 완만한 틸트(60/55/50/45/45)는 주식 50% 고정보다 수익·낙폭이 모두 근소하게 나았다(1.362배, −12.5% vs −13.2%).
즉 비중의 핵심은 '목표비중 유지'이고, 레짐은 흔들림 대비 쿠션을 약간 조정하는 정도로만 쓴다.

숫자는 참고용 휴리스틱 — 개인 리스크허용도·투자기간·통화·세금을 반영해 조정해야 하며
투자 권유가 아니다. 프리셋은 아래 _PRESETS 한 곳에서 수정.
"""
from __future__ import annotations

from .regime import stage_of

# 표시 순서: 위험 → 안전. 세그먼트 색은 style.css 의 .al-seg.<key> 가 담당.
_BUCKETS = [
    ("equity", "주식"),
    ("bond", "채권"),
    ("alt", "금·대안"),
    ("cash", "현금"),
]

# (단계키, 라벨, tone, {버킷: 비중}, 한 줄 근거). 각 행의 비중 합 = 100. 단계키는 regime.STAGES.
_PRESETS = [
    ("strong_on", "강한 위험선호", "good",
     {"equity": 60, "bond": 20, "alt": 10, "cash": 10},
     "흔들림이 작은 구간 — 목표비중 상단까지. 오른 자산은 추격보다 목표비중으로 되돌리며 이익을 챙기세요."),
    ("on", "위험선호", "good",
     {"equity": 55, "bond": 22, "alt": 10, "cash": 13},
     "위험자산 소폭 우위. 채권·현금 쿠션은 그대로 유지."),
    ("neutral", "중립/전환", "warn",
     {"equity": 50, "bond": 25, "alt": 12, "cash": 13},
     "방향 모호 — 기본 목표비중 유지. 정기 적립·분할로 대응."),
    ("off", "위험회피", "bad",
     {"equity": 45, "bond": 27, "alt": 13, "cash": 15},
     "흔들림이 커지는 구간 — 쿠션(채권·현금)을 조금 늘리되 주식을 크게 덜어내진 않습니다. 과거엔 이 구간 이후 성과가 나쁘지 않았습니다."),
    ("strong_off", "강한 위험회피", "bad",
     {"equity": 45, "bond": 25, "alt": 15, "cash": 15},
     "낙폭이 가장 깊어지기 쉬운 구간 — 레버리지는 정리하고, 현금 쿠션으로 버티며 분할매수 여력을 남기세요. 공포 구간 투매는 역사적으로 불리했습니다."),
]

# 틸트 기준선 = 중립 단계 비중
_NEUTRAL = next(w for k, _l, _t, w, _r in _PRESETS if k == "neutral")

_CAVEAT = ("참고용 휴리스틱 — 개인 리스크허용도·투자기간·통화·세금을 반영해 조정 필요. "
           "투자 권유가 아닙니다.")


def _preset_for(stage: str):
    for key, label, tone, weights, rationale in _PRESETS:
        if key == stage:
            return key, label, tone, weights, rationale
    return None


def recommend_allocation(regime: dict | None) -> dict | None:
    """현재 레짐 단계에 대응하는 권고 비중 프리셋(중립 대비 틸트 포함). 점수 없으면 None.

    단계는 regime 이 히스테리시스로 안정화한 값(stage)을 쓴다 — 원점수로 다시 나누면
    경계 근처에서 비중이 다시 매주 뒤집힌다. stage 가 없는 입력만 원점수로 분류한다.
    """
    if not regime:
        return None
    score = regime.get("score")
    if score is None:
        return None
    found = _preset_for(regime.get("stage") or stage_of(score))
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
