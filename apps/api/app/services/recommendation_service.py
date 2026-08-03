"""推荐解释生成（阶段5）。

- 解释内容首先由确定性代码从算法结果构建（保证收益与代价都在）；
- LLM（decision_coach prompt）只做措辞润色；
- 模型若擅自更改 recommended_option_id，其输出整体作废并记入审计；
- Challenger 结果只附加展示，永远不覆盖推荐。
"""

import json
import time

from sqlalchemy.orm import Session

from app.models import DecisionCase, DecisionRun, ModelInvocation, Recommendation
from app.services import challenger_service
from app.services.audit import record_event
from app.services.intake_service import load_prompt
from app.services.state_machine import validate_transition
from model_gateway import ModelGateway, ModelRole
from model_gateway.client import ModelCallError
from model_gateway.gateway import ModelNotConfiguredError
from shared_schemas import DecisionStatus, RecommendationExplanation

_NON_REOPEN_DEFAULTS = [
    "看到他人不同的意见或评价",
    "普通的促销与价格小幅波动",
    "没有新信息、只是又开始不安或后悔",
]


def stability_label(stability: float | None) -> str:
    s = stability or 0.0
    if s >= 0.8:
        return "高"
    if s >= 0.65:
        return "中高"
    if s >= 0.5:
        return "中"
    return "低（结论不稳定，请谨慎对待）"


def _humanize_flip(flip: dict) -> str:
    variable = flip.get("variable", "")
    change = flip.get("change", "")
    new_winner = flip.get("new_winner", "")
    if variable.startswith("weight:"):
        name = variable.removeprefix("weight:")
        return f"如果「{name}」的重要性发生明显变化（{change}），结论会变为 {new_winner}"
    if variable.startswith("score:"):
        _, option, criterion = (variable.split(":", 2) + ["", ""])[:3]
        return f"如果 {option} 在「{criterion}」上的实际表现与预期不符（{change}），结论会变为 {new_winner}"
    return f"{variable} {change} 时结论会变为 {new_winner}"


def build_deterministic_explanation(
    case: DecisionCase, run: DecisionRun
) -> RecommendationExplanation:
    result = run.result_snapshot
    winner_id = result.get("winner")
    option_name = {o.id: o.name for o in case.options}
    winner_name = option_name.get(winner_id, "该选项")
    ranking = result.get("ranking", [])
    runner_up_id = ranking[1] if len(ranking) > 1 else None
    runner_up_name = option_name.get(runner_up_id, "其他选项")

    # 用评估矩阵比较赢家与第二名的长短板
    eval_map = {(e.option_id, e.criterion_id): e.expected_value for e in case.evaluations}
    weights = {
        c.id: (c.learned_weight if c.learned_weight is not None else c.initial_weight)
        for c in case.criteria
    }
    advantages: list[tuple[float, str]] = []
    tradeoffs: list[tuple[float, str]] = []
    for c in sorted(case.criteria, key=lambda c: -weights[c.id]):
        w = eval_map.get((winner_id, c.id))
        r = eval_map.get((runner_up_id, c.id)) if runner_up_id else None
        if w is None or r is None:
            continue
        gap = w - r
        if gap > 0.05:
            advantages.append(
                (weights[c.id] * gap, f"在你更看重的「{c.name}」上，{winner_name} 表现更好")
            )
        elif gap < -0.05:
            tradeoffs.append(
                (-gap, f"在「{c.name}」上 {winner_name} 不如 {runner_up_name}，这是需要接受的代价")
            )

    main_reasons = [text for _, text in sorted(advantages, reverse=True)[:3]]
    if not main_reasons:
        main_reasons = [f"综合当前偏好权重，{winner_name} 的总体效用最高"]
    prob = result.get("winner_probability", {}).get(winner_id)
    if prob is not None:
        main_reasons.append(
            f"在当前偏好和信息的不确定范围内，{winner_name} 在 {round(prob * 100)}% 的模拟条件下排名第一"
        )
    accepted_tradeoffs = [text for _, text in sorted(tradeoffs, reverse=True)[:2]]
    if not accepted_tradeoffs:
        accepted_tradeoffs = ["两个方向差距不大，接受的代价是放弃另一选项的独特优势"]

    critical_unknowns = list(case.unknowns)[:3]
    for e in case.evaluations:
        if e.option_id == winner_id and e.uncertainty >= 0.25:
            crit = next((c.name for c in case.criteria if c.id == e.criterion_id), None)
            if crit:
                critical_unknowns.append(f"{winner_name} 在「{crit}」上的实际表现仍不确定")
    critical_unknowns = critical_unknowns[:4]

    reopen_conditions = [
        _humanize_flip(f) for f in result.get("sensitivity_flips", [])[:3]
    ]
    reopen_conditions.append("出现可靠的新事实（用途结构、价格差异或关键参数发生实质变化）")

    stability = stability_label(run.ranking_stability)
    summary = (
        f"综合你的偏好与当前信息，建议选择 {winner_name}。推荐稳定性：{stability}。"
    )
    if result.get("minimax_regret_option") and result["minimax_regret_option"] != winner_id:
        robust_name = option_name.get(result["minimax_regret_option"], "")
        summary += (
            f"提示：若更看重最坏情况下损失最小，{robust_name} 更稳健——"
            "两个结论不一致说明你的纠结有客观依据。"
        )

    return RecommendationExplanation(
        recommended_option_id=winner_id,
        summary=summary,
        main_reasons=main_reasons,
        accepted_tradeoffs=accepted_tradeoffs,
        critical_unknowns=critical_unknowns,
        reopen_conditions=reopen_conditions,
        non_reopen_conditions=list(_NON_REOPEN_DEFAULTS),
        next_action="确认接受以上代价后执行该选择；如关键未知项让你不安，先补齐它们再决定。",
    )


def _polish_with_llm(
    db: Session,
    case: DecisionCase,
    run: DecisionRun,
    deterministic: RecommendationExplanation,
    gateway: ModelGateway,
) -> tuple[RecommendationExplanation, str]:
    """LLM 润色；改动 recommended_option_id 即整体作废。"""
    start = time.monotonic()
    try:
        polished = gateway.structured(
            ModelRole.DEFAULT,
            system_prompt=load_prompt("coaching/decision_coach.md"),
            user_content=json.dumps(
                {
                    "deterministic_explanation": deterministic.model_dump(),
                    "options": [
                        {"id": o.id, "name": o.name} for o in case.options
                    ],
                    "algorithm_result": run.result_snapshot,
                },
                ensure_ascii=False,
            ),
            schema=RecommendationExplanation,
        )
        success, error = True, None
    except (ModelNotConfiguredError, ModelCallError) as exc:
        polished = None
        success, error = False, str(exc)[:2000]
    db.add(
        ModelInvocation(
            decision_case_id=case.id,
            task_kind="recommendation_polish",
            model_role=ModelRole.DEFAULT,
            model_name=gateway.settings.MODEL_DEFAULT,
            success=success,
            error=error,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    )
    if polished is None:
        return deterministic, "deterministic"
    if polished.recommended_option_id != deterministic.recommended_option_id:
        record_event(
            db,
            case.id,
            "llm_output_discarded",
            {
                "reason": "model attempted to change recommended_option_id",
                "model_option": polished.recommended_option_id,
                "algorithm_option": deterministic.recommended_option_id,
            },
            case.user_id,
        )
        return deterministic, "deterministic"
    # 代价与重开条件不允许被模型删空
    if not polished.accepted_tradeoffs or not polished.reopen_conditions:
        return deterministic, "deterministic"
    return polished, "llm_polished"


def _transition(db: Session, case: DecisionCase, target: DecisionStatus) -> None:
    current = DecisionStatus(case.status)
    validate_transition(current, target)
    case.status = target
    record_event(
        db, case.id, "state_transition", {"from": current, "to": target}, case.user_id
    )
    db.flush()


def generate_recommendation(
    db: Session, case: DecisionCase, run: DecisionRun, gateway: ModelGateway
) -> Recommendation:
    """生成推荐：确定性解释 → 可选 LLM 润色 → 可选 Challenger → 状态推进。"""
    if run.winning_option_id is None:
        raise ValueError("该决策运行没有产生可推荐的选项")

    deterministic = build_deterministic_explanation(case, run)
    explanation, source = _polish_with_llm(db, case, run, deterministic, gateway)

    challenger_output = None
    if challenger_service.should_challenge(run):
        challenge = challenger_service.run_challenger(db, case, run, gateway)
        if challenge is not None:
            challenger_output = challenge.model_dump()

    recommendation = Recommendation(
        decision_run_id=run.id,
        recommended_option_id=deterministic.recommended_option_id,  # 永远取算法结果
        summary=explanation.summary,
        main_reasons=explanation.main_reasons,
        accepted_tradeoffs=explanation.accepted_tradeoffs,
        critical_unknowns=explanation.critical_unknowns,
        reopen_conditions=explanation.reopen_conditions,
        non_reopen_conditions=explanation.non_reopen_conditions,
        next_action=explanation.next_action,
        challenger_output=challenger_output,
        source=source,
    )
    case.recommendations.append(recommendation)
    db.flush()
    record_event(
        db,
        case.id,
        "recommendation_generated",
        {
            "recommendation_id": recommendation.id,
            "run_id": run.id,
            "source": source,
            "challenged": challenger_output is not None,
        },
        case.user_id,
    )

    if DecisionStatus(case.status) == DecisionStatus.SENSITIVITY_ANALYSIS:
        _transition(db, case, DecisionStatus.CHALLENGE)
    if DecisionStatus(case.status) == DecisionStatus.CHALLENGE:
        _transition(db, case, DecisionStatus.READY_TO_COMMIT)
    return recommendation


def latest_recommendation(case: DecisionCase) -> Recommendation | None:
    return case.recommendations[-1] if case.recommendations else None
