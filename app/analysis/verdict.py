"""신호등(레짐) + 리스크 경보 → 충돌 없는 '오늘 한 줄 결론'(행동 번역).

페르소나 비판 ②: 위는 '공격적', 바로 아래는 🔴'리스크 경보' → "사라는 거야 말라는 거야?"
분위기(공격/중립/방어)와 경보(없음/경계/위험)를 한 문장으로 화해시켜, 비전문가가
'오늘 무엇을 할지'를 즉시 읽게 한다. 숫자는 참고용이며 투자 권유가 아니다.

문구는 레짐을 '매수·매도 타이밍'이 아니라 '흔들림의 크기'로 번역한다. 5년 소급 결과
점수가 낮을수록 이후 20일 낙폭은 2~3배 깊었지만, 60일 후 수익률은 오히려 가장 높았다
(강한 위험회피 구간 S&P +4.6%, VIX≥30 이면 20일 후 +5.2%). 그래서 방어 국면의 행동은
'매수 금지'가 아니라 '레버리지를 줄이고 나눠서 산다'이다.
"""
from __future__ import annotations

from typing import Optional


def _stance_bucket(regime: dict) -> str:
    """레짐 점수 → 공격(good)/중립(warn)/방어(bad). regime._stance 와 정렬."""
    tone = regime.get("stance_tone") or regime.get("tone")
    if tone in ("good", "warn", "bad"):
        return tone
    score = regime.get("score") or 50
    return "good" if score >= 58 else "warn" if score >= 43 else "bad"


# (stance, alert_level) → (tag, 행동 문장). {n} 은 경보 건수로 치환.
_MATRIX = {
    ("good", "clear"): ("위험선호 · 흔들림 작음",
        "흔들림이 작은 구간입니다. 계획대로 보유·적립하되, 많이 오른 자산은 추격하지 말고 목표비중으로 되돌리세요."),
    ("good", "warn"): ("우호하나 경계 {n}건",
        "분위기는 양호하나 경계신호 {n}건이 켜졌습니다. 신규 매수는 나눠서, 목표비중을 넘은 자산은 조금 덜어내세요."),
    ("good", "danger"): ("상충 · 우호 vs 위험경보 {n}건",
        "분위기는 우호적이나 위험경보 {n}건이 켜진 상충 국면입니다. 레버리지·몰빵을 피하고 목표비중을 넘는 부분은 정리하세요."),
    ("warn", "clear"): ("중립 · 목표비중 유지",
        "방향이 뚜렷하지 않습니다. 목표비중을 유지하며 정기 적립·분할로 대응하세요."),
    ("warn", "warn"): ("중립 + 경계 {n}건",
        "방향이 모호한데 경계신호 {n}건이 켜졌습니다. 한 번에 크게 사지 말고 나눠서, 현금 여력을 남겨두세요."),
    ("warn", "danger"): ("중립이나 위험경보 {n}건",
        "위험경보 {n}건 — 흔들림이 커질 수 있는 구간입니다. 레버리지를 줄이고, 매수는 여러 번에 나눠 하락을 활용하세요."),
    ("bad", "clear"): ("위험회피 · 흔들림 큼",
        "단기 흔들림이 큰 구간입니다. 투매보다는 버티기 — 레버리지는 피하고, 매수는 나눠서 가격 우위를 활용하세요."),
    ("bad", "warn"): ("방어 + 경계 {n}건",
        "하락 폭이 깊어질 수 있는 구간입니다(경계 {n}건). 감당 못 할 비중·레버리지는 줄이고, 매수는 여러 번에 나눠 천천히 하세요."),
    ("bad", "danger"): ("흔들림 최대 · 위험경보 {n}건",
        "낙폭이 가장 깊어지기 쉬운 구간입니다(위험경보 {n}건). 레버리지·신용은 정리하고 현금 여력을 지키며 분할매수로만 접근하세요 — 공포 구간 투매는 역사적으로 불리했습니다."),
}


def make_verdict(regime: Optional[dict], risk_alerts: Optional[dict]) -> Optional[dict]:
    """레짐+경보 → {tag, line, tone}. 점수 없으면 None(과거 리포트·데이터 부족 시)."""
    if not regime or regime.get("score") is None:
        return None
    stance = _stance_bucket(regime)
    ra = risk_alerts or {}
    level = ra.get("level", "clear")
    if level not in ("clear", "warn", "danger"):
        level = "clear"
    n = ra.get("n_danger", 0) if level == "danger" else ra.get("n_warn", 0)

    tag, line = _MATRIX[(stance, level)]
    tag, line = tag.replace("{n}", str(n)), line.replace("{n}", str(n))

    # 톤: 분위기와 경보 중 더 신중한 쪽을 반영(빨강 경보는 항상 경고색).
    if level == "danger" or stance == "bad":
        tone = "bad"
    elif level == "warn" or stance == "warn":
        tone = "warn"
    else:
        tone = "good"
    return {"tag": tag, "line": line, "tone": tone}
