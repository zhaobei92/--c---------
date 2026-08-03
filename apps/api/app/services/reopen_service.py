"""Reopen Gate（阶段6）：重开判定与反刍干预。

信号全部由确定性代码计算（novelty 用 n-gram 近似，credibility/relevance/
flip_probability 用规则），LLM 不参与评分。每次请求都完整记录。
"""

import re

from sqlalchemy.orm import Session

from decision_engine.reopen.gate import ReopenSignals, evaluate_reopen
from decision_engine.reopen.novelty import (
    LOW_NOVELTY_THRESHOLD,
    is_repetitive,
    novelty,
)

from app.models import DecisionCase, DecisionMessage, ReopenRequest
from app.services.audit import record_event
from app.services.state_machine import validate_transition
from shared_schemas import DecisionStatus, MessageRole

# 连续低新颖度重开尝试达到该次数 → 反刍干预
RUMINATION_ATTEMPTS = 2

_CREDIBLE_PATTERNS = [
    r"\d",  # 具体数字（价格、参数、日期）
    r"官方|客服|卖家|承认|证实|实测|检测|报告|发票|合同",
    r"降价|涨价|停产|召回|坏了|故障|拆修|翻新",
]

# 直接动摇选择依据的负面事实特征
_ADVERSE_FACT_PATTERN = re.compile(
    r"拆修|翻新|故障|坏了|召回|停产|涨价|降价|低于标称|虚标|造假|不符"
)

_REJECT_MESSAGE = (
    "目前没有发现能够改变原决定的新事实。"
    "你现在需要处理的更像是决定后的不安，而不是重新比较选项。"
    "可以回顾一下契约里你已经接受的代价——它们没有变化。"
)

_RUMINATION_MESSAGE = (
    "这已经是短期内多次没有新信息的重开尝试了。"
    "反复比较不会带来新的确定性，只会延长不安。"
    "建议：把注意力放回执行；如果之后出现契约里写明的重开条件，随时回来。"
)


def _credibility(text: str) -> float:
    hits = sum(1 for p in _CREDIBLE_PATTERNS if re.search(p, text))
    return min(0.9, 0.3 + 0.2 * hits)


def _relevance(case: DecisionCase, text: str) -> float:
    """新信息与决策的相关度。

    词面命中选项/标准/契约重开条件各计一档；直接指向所选选项的
    负面硬事实（拆修、虚标等）即便没提到标准名也高度相关，额外计档。
    """
    targets: list[str] = [o.name for o in case.options] + [c.name for c in case.criteria]
    contract = case.contracts[-1] if case.contracts else None
    if contract:
        targets += contract.reopen_conditions
    if not targets:
        return 0.3
    hits = sum(1 for t in targets if t and any(part in text for part in _split(t)))
    selected = next((o for o in case.options if o.id == case.selected_option_id), None)
    if selected and selected.name in text and _ADVERSE_FACT_PATTERN.search(text):
        hits += 1
    return min(0.9, 0.2 + 0.2 * hits)


def _split(target: str) -> list[str]:
    parts = [p for p in re.split(r"[（）()、，,\s]", target) if len(p) >= 2]
    return parts or [target]


def _flip_probability(case: DecisionCase, text: str) -> float:
    """新信息是否可能翻转排序。

    - 触及敏感性分析发现的翻转/关键变量 → 高；
    - 直接指向所选选项的负面事实（拆修、虚标、涨价等）→ 较高；
    - 否则按原结论稳定性给底值。
    """
    run = case.runs[-1] if case.runs else None
    if run is None:
        return 0.3
    result = run.result_snapshot
    sensitive_names: set[str] = set()
    for flip in result.get("sensitivity_flips", []):
        sensitive_names.update(_split(flip.get("variable", "").split(":")[-1]))
    for var in result.get("critical_variables", []):
        sensitive_names.update(_split(var.split(":")[-1]))
    if any(name in text for name in sensitive_names if name):
        return 0.75

    selected = next(
        (o for o in case.options if o.id == case.selected_option_id), None
    )
    if (
        selected
        and selected.name in text
        and _ADVERSE_FACT_PATTERN.search(text)
    ):
        return 0.7

    stability = result.get("ranking_stability") or 1.0
    return 0.45 if stability < 0.65 else 0.2


def _previous_texts(case: DecisionCase) -> list[str]:
    texts = [r.new_information for r in case.reopen_requests]
    texts += [m.content for m in case.messages if m.role == MessageRole.USER]
    return texts


def request_reopen(
    db: Session, case: DecisionCase, new_information: str
) -> tuple[ReopenRequest, str]:
    """处理重开请求，返回 (记录, 给用户的回复文案)。"""
    status = DecisionStatus(case.status)
    if status not in (DecisionStatus.COMMITTED, DecisionStatus.FOLLOW_UP):
        raise ValueError("只有已锁定的决定才需要重开判定")

    previous = _previous_texts(case)
    n = novelty(new_information, previous)
    c = _credibility(new_information)
    r = _relevance(case, new_information)
    f = _flip_probability(case, new_information)
    v = round((n + r) / 2, 4)
    decision = evaluate_reopen(ReopenSignals(n, c, r, f, v))

    # 反刍检测：本次低新颖 + 此前已有低新颖的被拒尝试
    repetitive_now = is_repetitive(new_information, previous)
    prior_low_novelty_rejects = sum(
        1
        for req in case.reopen_requests
        if req.outcome in ("rejected", "closure_intervention")
        and req.novelty < LOW_NOVELTY_THRESHOLD
    )
    rumination = (
        decision.outcome == "rejected"
        and repetitive_now
        and prior_low_novelty_rejects + 1 >= RUMINATION_ATTEMPTS
    )

    outcome = "closure_intervention" if rumination else decision.outcome
    request = ReopenRequest(
        new_information=new_information[:4000],
        novelty=n,
        credibility=c,
        relevance=r,
        flip_probability=f,
        info_value=v,
        reopen_score=decision.score,
        outcome=outcome,
        is_rumination=rumination,
    )
    case.reopen_requests.append(request)
    db.flush()
    record_event(
        db,
        case.id,
        "reopen_requested",
        {
            "request_id": request.id,
            "score": decision.score,
            "signals": vars(decision.signals),
            "outcome": outcome,
            "is_rumination": rumination,
        },
        case.user_id,
    )

    if outcome == "full_reopen":
        # 新信息成为事实，回到澄清阶段重新分析
        case.facts = case.facts + [f"重开新信息：{new_information[:500]}"]
        validate_transition(DecisionStatus(case.status), DecisionStatus.MORE_CLARIFICATION)
        case.status = DecisionStatus.MORE_CLARIFICATION
        record_event(
            db,
            case.id,
            "state_transition",
            {"from": status, "to": DecisionStatus.MORE_CLARIFICATION},
            case.user_id,
        )
        message = (
            "这条新信息足以重新评估这个决定。"
            "我已把它记入事实，接下来会基于新情况重新分析——原来的结论不再锁定。"
        )
    elif outcome == "new_facts_only":
        case.facts = case.facts + [f"补充信息：{new_information[:500]}"]
        message = (
            "这条信息有参考价值，但还不足以推翻原决定。"
            "我已记录它；只有当它实质改变关键变量（见契约中的重开条件）时才需要完整重开。"
            "当前决定保持锁定。"
        )
    elif outcome == "closure_intervention":
        message = _RUMINATION_MESSAGE
    else:
        message = _REJECT_MESSAGE

    case.messages.append(DecisionMessage(role=MessageRole.ASSISTANT, content=message))
    db.flush()
    return request, message
