"""最高价值追问（确定性近似算法，符合方案 6.8 的 V1 要求）。

候选问题由缺失信息生成，价值 = 影响 × 不确定性 - 回答成本；
每轮只问一个；已问过的目标不重复；低于阈值即停止。
"""

from decision_engine.question_selection.value import (
    QuestionCandidate,
    question_value,
    select_best_question,
)

from app.models import DecisionCase
from shared_schemas import NextQuestion, StuckType

MAX_CLARIFICATION_ROUNDS = 5


def _candidates(case: DecisionCase) -> dict[str, tuple[QuestionCandidate, NextQuestion]]:
    out: dict[str, tuple[QuestionCandidate, NextQuestion]] = {}

    if len(case.options) < 2:
        target = "options"
        out[target] = (
            QuestionCandidate(target, impact=1.0, uncertainty=1.0, burden=0.3),
            NextQuestion(
                question="你具体在哪些选项之间犹豫？请把候选都列出来。",
                target_variable=target,
                expected_value=0.9,
                answer_type="free_text",
            ),
        )

    if not case.constraints:
        target = "constraints"
        out[target] = (
            QuestionCandidate(target, impact=0.7, uncertainty=0.8, burden=0.3),
            NextQuestion(
                question="这个决定有什么硬性限制吗？比如预算上限、必须有的功能或时间要求。没有的话直接说没有。",
                target_variable=target,
                expected_value=0.6,
                answer_type="free_text",
            ),
        )

    if (
        case.primary_stuck_type == StuckType.REGRET_AVERSION
        and len(case.options) >= 2
    ):
        target = "regret_comparison"
        a, b = case.options[0].name, case.options[1].name
        out[target] = (
            QuestionCandidate(target, impact=0.8, uncertainty=0.7, burden=0.2),
            NextQuestion(
                question=(
                    f"假设其他条件都一样，你更难接受哪种遗憾："
                    f"选了{a}之后怀念{b}的好处，还是选了{b}之后怀念{a}的好处？"
                ),
                target_variable=target,
                expected_value=0.7,
                answer_type="single_choice",
                choices=[f"更怕失去{b}的好处", f"更怕失去{a}的好处", "两种都还好"],
            ),
        )

    for unknown in list(case.unknowns)[:3]:
        target = f"unknown:{unknown}"
        out[target] = (
            QuestionCandidate(target, impact=0.6, uncertainty=0.9, burden=0.4),
            NextQuestion(
                question=f"关于「{unknown}」，你能补充一下实际情况吗？大概说说就行。",
                target_variable=target,
                expected_value=0.5,
                answer_type="free_text",
            ),
        )

    return out


def next_question(case: DecisionCase) -> NextQuestion | None:
    """返回本轮应问的唯一问题；无值得问的问题或轮数用尽时返回 None。"""
    if case.clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
        return None
    candidates = _candidates(case)
    asked = {q["target_variable"] for q in case.asked_questions}
    best = select_best_question(
        [cand for cand, _ in candidates.values()], asked_targets=asked
    )
    if best is None:
        return None
    question = candidates[best.target_variable][1]
    return question.model_copy(
        update={"expected_value": round(question_value(best), 3)}
    )


def record_asked(case: DecisionCase, question: NextQuestion) -> None:
    case.asked_questions = case.asked_questions + [
        {
            "target_variable": question.target_variable,
            "question": question.question,
            "answered": False,
        }
    ]
    case.clarification_rounds = case.clarification_rounds + 1


def pending_question(case: DecisionCase) -> dict | None:
    for q in reversed(case.asked_questions):
        if not q.get("answered"):
            return q
    return None


def record_answer(case: DecisionCase, answer_text: str) -> dict | None:
    """把用户消息记为最近一个未回答问题的答案；返回该问题记录。"""
    pending = pending_question(case)
    if pending is None:
        return None
    updated = []
    for q in case.asked_questions:
        if q["target_variable"] == pending["target_variable"] and not q.get("answered"):
            q = {**q, "answered": True, "answer": answer_text[:2000]}
        updated.append(q)
    case.asked_questions = updated

    target = pending["target_variable"]
    case.facts = case.facts + [f"补充（{target}）：{answer_text[:500]}"]
    if target.startswith("unknown:"):
        resolved = target.removeprefix("unknown:")
        case.unknowns = [u for u in case.unknowns if u != resolved]
    return pending
