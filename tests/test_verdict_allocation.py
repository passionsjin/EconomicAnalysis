r"""verdict·allocation — 레짐을 '타이밍'이 아니라 '흔들림 크기'로 번역하는지 검증.

5년 소급(2021-10~2026-09): 강한 위험회피 구간의 이후 60일 S&P 수익률이 전 구간 최고였다.
그 구간에서 '매수 금지'·주식 15% 를 권하면 가장 좋았던 매수 창을 막는다.
또 점수로 비중을 크게 기울여도 주식 고정비중보다 수익이 나아지지 않았다(낙폭만 소폭 개선).
"""
from __future__ import annotations

import itertools

from app.analysis import allocation, verdict


def test_모든_단계의_권고비중_합은_100():
    for stage in ("strong_on", "on", "neutral", "off", "strong_off"):
        al = allocation.recommend_allocation({"score": 50, "stage": stage})
        assert sum(b["weight"] for b in al["buckets"]) == 100, stage


def test_주식비중_틸트는_완만해야_한다():
    eq = {}
    for stage in ("strong_on", "on", "neutral", "off", "strong_off"):
        al = allocation.recommend_allocation({"score": 50, "stage": stage})
        eq[stage] = next(b["weight"] for b in al["buckets"] if b["key"] == "equity")
    assert 40 <= min(eq.values()) and max(eq.values()) <= 60
    assert eq["strong_on"] >= eq["neutral"] >= eq["strong_off"]


def test_권고비중은_원점수가_아니라_안정화된_단계를_따른다():
    # 점수 60 이지만 히스테리시스로 아직 중립 단계 → 중립 프리셋
    al = allocation.recommend_allocation({"score": 60, "stage": "neutral"})
    assert al["stage"] == "neutral"


def test_위험회피_문구는_매수를_막지_않는다():
    for stance, level in itertools.product(("good", "warn", "bad"), ("clear", "warn", "danger")):
        v = verdict.make_verdict({"score": 50, "stance_tone": stance},
                                 {"level": level, "n_warn": 1, "n_danger": 1})
        assert "매수 금지" not in v["line"] and "추격매수는 금지" not in v["line"], (stance, level)
        assert "보류" not in v["line"], (stance, level)


def test_위험회피_국면은_여전히_경고색이다():
    v = verdict.make_verdict({"score": 20, "stance_tone": "bad"}, {"level": "clear"})
    assert v["tone"] == "bad"
