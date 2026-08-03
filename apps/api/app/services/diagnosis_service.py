"""纠结类型诊断。

- 类型标签：优先 LLM（stuck_classifier prompt），失败降级为关键词启发式；
- information_completeness / decision_readiness：永远由确定性代码计算，
  模型自报的数值一律丢弃（"不允许把模型自报置信度当成系统置信度"）。
"""

import time

from sqlalchemy.orm import Session

from app.models import DecisionCase, ModelInvocation
from app.services.intake_service import load_prompt
from model_gateway import ModelGateway, ModelRole
from model_gateway.client import ModelCallError
from model_gateway.gateway import ModelNotConfiguredError
from shared_schemas import MessageRole, StuckType, StuckTypeDiagnosis

_KEYWORDS: dict[StuckType, list[str]] = {
    StuckType.REGRET_AVERSION: ["后悔", "遗憾", "怕选错", "错过", "选错了怎么办"],
    StuckType.INFORMATION_GAP: ["不了解", "不知道", "不清楚", "没查", "不懂", "参数看不"],
    StuckType.VALUE_CONFLICT: ["一方面", "另一方面", "都想要", "鱼和熊掌", "又想", "但又"],
    StuckType.RUMINATION: ["反复", "翻来覆去", "想了很久", "好几天", "又开始", "一直在想"],
    StuckType.SOCIAL_PRESSURE: ["别人说", "朋友说", "家人", "父母", "同事说", "网上都说", "评论区"],
    StuckType.UNCERTAINTY_DISTRESS: ["万一", "不确定", "没把握", "说不准"],
    StuckType.PERFECTIONISM: ["最优", "完美", "最好的选择", "不能亏", "最划算"],
    StuckType.SUNK_COST: ["已经买", "已经花", "舍不得", "都投入"],
    StuckType.ACTION_AVOIDANCE: ["一直拖", "拖了", "不想面对", "懒得"],
    StuckType.IDENTITY_CONFLICT: ["我是不是", "适合我吗", "我这种人"],
}


def heuristic_diagnose(text: str, has_unknowns: bool) -> StuckTypeDiagnosis:
    scores: dict[StuckType, float] = {}
    for stuck_type, keywords in _KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits:
            scores[stuck_type] = min(0.9, 0.3 + 0.25 * hits)
    if not scores:
        default = (
            StuckType.INFORMATION_GAP if has_unknowns else StuckType.VALUE_CONFLICT
        )
        scores[default] = 0.5
    ordered = sorted(scores, key=scores.get, reverse=True)
    return StuckTypeDiagnosis(
        primary_type=ordered[0],
        secondary_types=ordered[1:3],
        type_scores=scores,
        information_completeness=0.0,  # 由确定性代码覆盖
        decision_readiness=0.0,
        explanation_summary="基于文本特征的基础判断（未接入模型）。",
    )


def compute_information_completeness(case: DecisionCase) -> float:
    """确定性信息完整度：与模型无关。"""
    score = 0.0
    if len(case.options) >= 2:
        score += 0.30
    if case.constraints:
        score += 0.20
    if case.facts:
        score += 0.20
    if len(case.unknowns) == 0:
        score += 0.15
    else:
        score += max(0.0, 0.15 - 0.05 * len(case.unknowns))
    user_msgs = [m for m in case.messages if m.role == MessageRole.USER]
    score += min(0.15, 0.05 * len(user_msgs))
    return round(min(1.0, score), 3)


def compute_decision_readiness(
    case: DecisionCase, comparisons_done: int = 0, evaluation_coverage: float = 0.0
) -> float:
    completeness = compute_information_completeness(case)
    readiness = (
        0.4 * completeness
        + 0.3 * min(1.0, comparisons_done / 3)
        + 0.3 * evaluation_coverage
    )
    return round(min(1.0, readiness), 3)


def diagnose(
    db: Session, case: DecisionCase, gateway: ModelGateway
) -> tuple[StuckTypeDiagnosis, str]:
    """返回 (diagnosis, source)。completeness/readiness 一律确定性覆盖。"""
    user_text = "\n".join(
        m.content for m in case.messages if m.role == MessageRole.USER
    )
    start = time.monotonic()
    try:
        diagnosis = gateway.structured(
            ModelRole.FAST,
            system_prompt=load_prompt("diagnosis/stuck_classifier.md"),
            user_content=user_text,
            schema=StuckTypeDiagnosis,
        )
        source = "llm"
        success, error = True, None
    except (ModelNotConfiguredError, ModelCallError) as exc:
        diagnosis = heuristic_diagnose(user_text, has_unknowns=bool(case.unknowns))
        source = "heuristic"
        success, error = False, str(exc)[:2000]
    db.add(
        ModelInvocation(
            decision_case_id=case.id,
            task_kind="stuck_diagnosis",
            model_role=ModelRole.FAST,
            model_name=gateway.settings.MODEL_FAST,
            success=success,
            error=error,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    )

    # 系统分数只来自确定性计算
    diagnosis = diagnosis.model_copy(
        update={
            "information_completeness": compute_information_completeness(case),
            "decision_readiness": compute_decision_readiness(case),
        }
    )
    return diagnosis, source
