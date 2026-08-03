"""决策关闭（阶段6）：契约生成、接受代价、锁定决定。"""

from sqlalchemy.orm import Session

from app.models import ClosureContract, DecisionCase, DecisionMessage
from app.models.base import utcnow
from app.services import recommendation_service
from app.services.audit import record_event
from app.services.state_machine import validate_transition
from shared_schemas import DecisionStatus, MessageRole

_GENERIC_TRADEOFF = "放弃其他选项各自的独特优势，并接受剩余的不确定性"


def commit_decision(
    db: Session, case: DecisionCase, selected_option_id: str, accepted_tradeoffs: bool
) -> ClosureContract:
    """锁定决定。必须显式接受代价；允许用户选择与推荐不同的选项。"""
    if not accepted_tradeoffs:
        raise ValueError("必须先确认接受该选择的代价")
    status = DecisionStatus(case.status)
    if status != DecisionStatus.READY_TO_COMMIT:
        raise ValueError(f"当前状态 {status} 不能提交决定")
    option = next((o for o in case.options if o.id == selected_option_id), None)
    if option is None:
        raise ValueError("选项不存在")
    if not option.is_eligible:
        raise ValueError("该选项已被硬约束淘汰，不能选择")

    rec = recommendation_service.latest_recommendation(case)
    followed = bool(rec and rec.recommended_option_id == selected_option_id)

    if rec and followed:
        tradeoffs = list(rec.accepted_tradeoffs)
        reasons = list(rec.main_reasons)
        reopen_conditions = list(rec.reopen_conditions)
        non_reopen_conditions = list(rec.non_reopen_conditions)
        next_action = rec.next_action
    else:
        # 用户不被强迫接受推荐；自选时生成通用契约并如实记录
        tradeoffs = [_GENERIC_TRADEOFF]
        if rec:
            tradeoffs.append("该选择与系统分析结果不同，放弃了分析显示的相对优势")
        reasons = ["由你本人权衡后作出的选择"]
        reopen_conditions = ["出现可靠的新事实（用途、价格或关键参数发生实质变化）"]
        non_reopen_conditions = [
            "看到他人不同的意见或评价",
            "普通的促销与价格小幅波动",
            "没有新信息、只是又开始不安或后悔",
        ]
        next_action = "在24小时内执行该决定，并记录执行情况。"

    contract = ClosureContract(
        selected_option_id=selected_option_id,
        accepted_tradeoffs=tradeoffs,
        main_reasons=reasons,
        reopen_conditions=reopen_conditions,
        non_reopen_conditions=non_reopen_conditions,
        next_action=next_action,
        followed_recommendation=followed,
        user_confirmed=True,
    )
    case.contracts.append(contract)
    case.selected_option_id = selected_option_id
    case.committed_at = utcnow()

    validate_transition(DecisionStatus(case.status), DecisionStatus.COMMITTED)
    case.status = DecisionStatus.COMMITTED
    record_event(
        db,
        case.id,
        "decision_committed",
        {
            "contract_id": contract.id,
            "selected_option_id": selected_option_id,
            "followed_recommendation": followed,
        },
        case.user_id,
    )
    case.messages.append(
        DecisionMessage(
            role=MessageRole.ASSISTANT,
            content=(
                f"已锁定：{option.name}。你已确认接受相应的代价。"
                "接下来把注意力放在执行上；除非出现契约里写明的重开条件，"
                "这个问题可以放下了。我会在24小时、7天和30天后回访。"
            ),
        )
    )
    db.flush()
    return contract


def latest_contract(case: DecisionCase) -> ClosureContract | None:
    return case.contracts[-1] if case.contracts else None
