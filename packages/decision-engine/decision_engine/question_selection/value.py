"""追问价值评分（确定性）。

QuestionValue = impact * uncertainty - burden_weight * burden
每轮只选分数最高且超过阈值的一个问题；低于阈值即停止追问。
"""

from dataclasses import dataclass

BURDEN_WEIGHT = 0.2
DEFAULT_VALUE_THRESHOLD = 0.15


@dataclass(frozen=True)
class QuestionCandidate:
    target_variable: str
    impact: float  # 0-1：该变量对排序的影响程度
    uncertainty: float  # 0-1：当前对该变量的不确定程度
    burden: float  # 0-1：用户回答成本


def question_value(candidate: QuestionCandidate) -> float:
    return candidate.impact * candidate.uncertainty - BURDEN_WEIGHT * candidate.burden


def select_best_question(
    candidates: list[QuestionCandidate],
    asked_targets: set[str] | None = None,
    threshold: float = DEFAULT_VALUE_THRESHOLD,
) -> QuestionCandidate | None:
    """返回价值最高且未问过的候选；全部低于阈值时返回 None（停止追问）。"""
    asked = asked_targets or set()
    eligible = [c for c in candidates if c.target_variable not in asked]
    if not eligible:
        return None
    best = max(eligible, key=question_value)
    if question_value(best) < threshold:
        return None
    return best
