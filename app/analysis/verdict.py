"""신호등(레짐) + 리스크 경보 → 충돌 없는 '오늘 한 줄 결론'(행동 번역).

페르소나 비판 ②: 위는 '공격적', 바로 아래는 🔴'리스크 경보' → "사라는 거야 말라는 거야?"
분위기(공격/중립/방어)와 경보(없음/경계/위험)를 한 문장으로 화해시켜, 비전문가가
'오늘 무엇을 할지'를 즉시 읽게 한다. 숫자는 참고용이며 투자 권유가 아니다.
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
    ("good", "clear"): ("위험선호 · 매수 우호",
        "지금은 위험을 감수해도 되는 분위기입니다. 계획대로 매수·보유하되, 과열일수록 분할·손절선으로 이익을 지키세요."),
    ("good", "warn"): ("우호하나 경계 {n}건",
        "분위기는 양호하나 경계신호 {n}건이 켜졌습니다. 신규 매수는 분할로, 무리한 추격은 잠시 미루세요."),
    ("good", "danger"): ("상충 · 우호 vs 위험경보 {n}건",
        "분위기는 우호적이나 위험경보 {n}건이 발생한 상충 국면입니다. 신규 베팅은 보수적으로, 일부 차익실현·헤지를 점검하세요."),
    ("warn", "clear"): ("중립 · 관망",
        "방향이 뚜렷하지 않습니다. 큰 베팅보다 분할·관망하고 현금 비중을 유지하세요."),
    ("warn", "warn"): ("중립 + 경계 {n}건",
        "방향이 모호한데 경계신호 {n}건이 켜졌습니다. 신규 진입을 늦추고 분할·관망하세요."),
    ("warn", "danger"): ("중립이나 위험경보 {n}건",
        "방향은 모호하지만 위험경보 {n}건이 발생했습니다. 방어적으로 — 신규 위험자산 매수는 보류하세요."),
    ("bad", "clear"): ("위험회피 · 방어",
        "위험을 줄이는 분위기입니다. 신규 위험 베팅은 자제하고 현금·안전자산 비중을 점검하세요."),
    ("bad", "warn"): ("방어 + 경계 {n}건",
        "위험회피에 경계신호 {n}건. 방어를 우선하고, 반등은 비중을 줄일 기회로 보세요."),
    ("bad", "danger"): ("방어 우선 · 위험경보 {n}건",
        "위험회피에 위험경보 {n}건. 방어 최우선 — 바닥 확인 전 추격매수는 금지하고 헤지·현금을 점검하세요."),
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
