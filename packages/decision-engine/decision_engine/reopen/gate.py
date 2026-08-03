"""Reopen Gate（方案 6.10）：决定是否允许重开已锁定的决定。

ReopenScore = 0.20·N + 0.20·C + 0.25·R + 0.25·F + 0.10·V

- N 新颖性、C 可信度、R 决策相关度、F 排序翻转概率、V 新增信息价值
- ≥ 0.70：允许完整重开
- 0.45–0.69：只分析新事实影响
- < 0.45：不重新跑完整决策

完全确定性，禁止 LLM 参与评分。
"""

from dataclasses import dataclass

WEIGHTS = {
    "novelty": 0.20,
    "credibility": 0.20,
    "relevance": 0.25,
    "flip_probability": 0.25,
    "value": 0.10,
}

FULL_REOPEN_THRESHOLD = 0.70
PARTIAL_ANALYSIS_THRESHOLD = 0.45


@dataclass(frozen=True)
class ReopenSignals:
    novelty: float
    credibility: float
    relevance: float
    flip_probability: float
    value: float

    def clipped(self) -> "ReopenSignals":
        c = lambda x: min(1.0, max(0.0, x))  # noqa: E731
        return ReopenSignals(
            c(self.novelty),
            c(self.credibility),
            c(self.relevance),
            c(self.flip_probability),
            c(self.value),
        )


@dataclass(frozen=True)
class ReopenDecision:
    score: float
    outcome: str  # full_reopen | new_facts_only | rejected
    signals: ReopenSignals


def evaluate_reopen(signals: ReopenSignals) -> ReopenDecision:
    s = signals.clipped()
    score = (
        WEIGHTS["novelty"] * s.novelty
        + WEIGHTS["credibility"] * s.credibility
        + WEIGHTS["relevance"] * s.relevance
        + WEIGHTS["flip_probability"] * s.flip_probability
        + WEIGHTS["value"] * s.value
    )
    score = round(score, 4)
    if score >= FULL_REOPEN_THRESHOLD:
        outcome = "full_reopen"
    elif score >= PARTIAL_ANALYSIS_THRESHOLD:
        outcome = "new_facts_only"
    else:
        outcome = "rejected"
    return ReopenDecision(score=score, outcome=outcome, signals=s)
