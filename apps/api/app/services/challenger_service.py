"""Challenger（阶段5）：第二模型对不稳定推荐做批判性审核。

- 只在需要时调用（稳定性低 / 差距小）；
- 只能提出问题与风险，永远不能改变算法推荐；
- LLM 不可用时跳过（返回 None），不阻塞流程。
"""

import json
import time

from sqlalchemy.orm import Session

from app.models import DecisionCase, DecisionRun, ModelInvocation
from app.services.intake_service import load_prompt
from model_gateway import ModelGateway, ModelRole
from model_gateway.client import ModelCallError
from model_gateway.gateway import ModelNotConfiguredError
from shared_schemas import ChallengeResult

STABILITY_THRESHOLD = 0.55
UTILITY_GAP_THRESHOLD = 0.05


def should_challenge(run: DecisionRun) -> bool:
    result = run.result_snapshot
    stability = result.get("ranking_stability") or 0.0
    gap = result.get("expected_utility_gap")
    if stability < STABILITY_THRESHOLD:
        return True
    if gap is not None and gap < UTILITY_GAP_THRESHOLD:
        return True
    return False


def _challenge_input(case: DecisionCase, run: DecisionRun) -> str:
    return json.dumps(
        {
            "title": case.title,
            "options": [
                {"id": o.id, "name": o.name, "is_eligible": o.is_eligible}
                for o in case.options
            ],
            "criteria_weights": {
                c.name: c.learned_weight if c.learned_weight is not None else c.initial_weight
                for c in case.criteria
            },
            "constraints": [c.description for c in case.constraints],
            "facts": case.facts,
            "unknowns": case.unknowns,
            "algorithm_result": run.result_snapshot,
        },
        ensure_ascii=False,
    )


def run_challenger(
    db: Session, case: DecisionCase, run: DecisionRun, gateway: ModelGateway
) -> ChallengeResult | None:
    """执行 Challenger 审核。失败或未配置时返回 None（不阻塞流程）。"""
    start = time.monotonic()
    try:
        result = gateway.structured(
            ModelRole.CHALLENGER,
            system_prompt=load_prompt("challenger/challenger.md"),
            user_content=_challenge_input(case, run),
            schema=ChallengeResult,
        )
        success, error = True, None
    except (ModelNotConfiguredError, ModelCallError) as exc:
        result = None
        success, error = False, str(exc)[:2000]
    db.add(
        ModelInvocation(
            decision_case_id=case.id,
            task_kind="challenger_review",
            model_role=ModelRole.CHALLENGER,
            model_name=gateway.settings.MODEL_CHALLENGER,
            success=success,
            error=error,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    )
    return result
