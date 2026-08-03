"""Intake 流水线：风险分级 → 结构化提取 → 应用结果 → 状态推进。

LLM 不可用或调用失败时降级到确定性启发式解析（source 标记 heuristic），
流程照常走完 —— 模型失败永远不能破坏决策状态。
"""

import os
import re
import time
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DecisionCase, DecisionMessage, DecisionOption, HardConstraint, ModelInvocation
from app.services import risk_triage
from app.services.audit import record_event
from app.services.state_machine import validate_transition
from model_gateway import ModelGateway, ModelRole
from model_gateway.client import ModelCallError
from model_gateway.gateway import ModelNotConfiguredError
from shared_schemas import (
    DecisionDomain,
    DecisionStatus,
    ExtractedConstraint,
    ExtractedOption,
    IntakeExtraction,
    MessageRole,
    RiskLevel,
)

_PROMPTS_DIR = Path(
    os.environ.get("PROMPTS_DIR", Path(__file__).resolve().parents[4] / "prompts")
)


@lru_cache
def load_prompt(relative_path: str) -> str:
    return (_PROMPTS_DIR / relative_path).read_text(encoding="utf-8")


# ---------- 启发式降级解析 ----------

_BETWEEN_PATTERN = re.compile(r"在(.{1,30}?)[和跟与](.{1,30}?)之间")
_OR_PATTERN = re.compile(r"(?:是买|是选|选|买)?(.{2,20}?)还是(.{2,20}?)[，。？！,?!\s]")
_BUDGET_PATTERN = re.compile(r"(预算[^，。；,;]{0,20}|[0-9]+千?百?万?元以内|不超过[0-9]+[千百万]?元?)")

_DOMAIN_KEYWORDS: list[tuple[DecisionDomain, list[str]]] = [
    (DecisionDomain.PRODUCT, ["买", "显示器", "手机", "电脑", "显卡", "家电", "耳机", "相机", "车"]),
    (DecisionDomain.WORK_PRIORITY, ["先做", "优先级", "项目", "任务", "排期"]),
    (DecisionDomain.LEARNING, ["课程", "报班", "学什么", "考证", "考研"]),
    (DecisionDomain.CAREER, ["offer", "岗位", "跳槽", "职业"]),
    (DecisionDomain.RELATIONSHIP, ["朋友", "同事", "家人", "沟通", "关系"]),
]


def _guess_domain(text: str) -> DecisionDomain:
    for domain, keywords in _DOMAIN_KEYWORDS:
        if any(kw in text for kw in keywords):
            return domain
    return DecisionDomain.OTHER


def heuristic_extract(text: str) -> IntakeExtraction:
    """无 LLM 时的确定性解析：只提取有把握的信息，绝不编造。"""
    options: list[ExtractedOption] = []
    m = _BETWEEN_PATTERN.search(text)
    if m:
        options = [ExtractedOption(name=m.group(1).strip()), ExtractedOption(name=m.group(2).strip())]
    else:
        m = _OR_PATTERN.search(text + "。")
        if m:
            options = [ExtractedOption(name=m.group(1).strip()), ExtractedOption(name=m.group(2).strip())]

    constraints = [
        ExtractedConstraint(description=b.strip(), is_hard=True)
        for b in _BUDGET_PATTERN.findall(text)
    ]

    title = text.strip().splitlines()[0][:30] or "未命名决策"
    return IntakeExtraction(
        title=title,
        domain=_guess_domain(text),
        options=options,
        facts=[],
        constraints=constraints,
        concerns=[],
        unknowns=["关键偏好与使用场景待澄清"],
        clarification_required=len(options) < 2,
    )


# ---------- LLM 提取（带调用记录与降级） ----------

def extract_with_fallback(
    db: Session, case: DecisionCase, text: str, gateway: ModelGateway
) -> tuple[IntakeExtraction, str]:
    """返回 (extraction, source)，source 为 'llm' 或 'heuristic'。"""
    start = time.monotonic()
    try:
        extraction = gateway.structured(
            ModelRole.FAST,
            system_prompt=load_prompt("intake/intake_extractor.md"),
            user_content=text,
            schema=IntakeExtraction,
        )
        db.add(
            ModelInvocation(
                decision_case_id=case.id,
                task_kind="intake_extraction",
                model_role=ModelRole.FAST,
                model_name=gateway.settings.MODEL_FAST,
                success=True,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        )
        return extraction, "llm"
    except (ModelNotConfiguredError, ModelCallError) as exc:
        db.add(
            ModelInvocation(
                decision_case_id=case.id,
                task_kind="intake_extraction",
                model_role=ModelRole.FAST,
                model_name=gateway.settings.MODEL_FAST,
                success=False,
                error=str(exc)[:2000],
                latency_ms=(time.monotonic() - start) * 1000,
            )
        )
        return heuristic_extract(text), "heuristic"


def _apply_extraction(
    db: Session, case: DecisionCase, extraction: IntakeExtraction, source: str
) -> None:
    case.title = extraction.title[:200]
    case.domain = extraction.domain
    case.facts = list(extraction.facts)
    case.concerns = list(extraction.concerns)
    case.unknowns = list(extraction.unknowns)
    option_source = "ai_extracted" if source == "llm" else "heuristic"
    for opt in extraction.options:
        db.add(
            DecisionOption(
                decision_case_id=case.id,
                name=opt.name[:200],
                description=opt.description,
                source=option_source,
            )
        )
    for con in extraction.constraints:
        db.add(
            HardConstraint(
                decision_case_id=case.id,
                description=con.description,
                is_hard=con.is_hard,
                source=option_source,
            )
        )
    db.flush()
    record_event(
        db,
        case.id,
        "intake_extraction_applied",
        payload={"source": source, "extraction": extraction.model_dump()},
        user_id=case.user_id,
    )


def _transition(db: Session, case: DecisionCase, target: DecisionStatus) -> None:
    current = DecisionStatus(case.status)
    validate_transition(current, target)
    case.status = target
    record_event(
        db,
        case.id,
        "state_transition",
        payload={"from": current, "to": target},
        user_id=case.user_id,
    )
    db.flush()


_GUIDED_ONLY_MESSAGE = (
    "这个决定属于高影响或受限类别。我不会替你直接拍板，"
    "但可以帮你梳理事实、澄清你在意的价值、找出缺失的信息，"
    "并整理一份和专业人士或重要相关人讨论的清单。"
)

_RESTRICTED_EXTRA = (
    "如果你现在处于强烈痛苦或有伤害自己/他人的想法，请立即联系当地的专业求助渠道"
    "或信任的人。这个工具不能替代专业帮助。"
)


def _summary_message(extraction: IntakeExtraction, source: str) -> str:
    parts = []
    if extraction.options:
        names = "、".join(o.name for o in extraction.options)
        parts.append(f"我识别到 {len(extraction.options)} 个选项：{names}。")
    else:
        parts.append("我还没有识别出明确的候选选项。")
    if extraction.constraints:
        parts.append(f"约束：{'；'.join(c.description for c in extraction.constraints)}。")
    if source == "heuristic":
        parts.append("（当前为基础解析模式，提取可能不完整。）")
    parts.append("以上信息可能有误，你可以在结构化面板中直接修改、补充或删除。")
    if extraction.clarification_required:
        parts.append("请补充：你具体在哪些选项之间犹豫？各自吸引你或让你担心的是什么？")
    else:
        parts.append("确认无误后，下一步我会诊断你主要的纠结机制，并每次只问你一个关键问题。")
    return "".join(parts)


def run_intake_pipeline(
    db: Session, case: DecisionCase, gateway: ModelGateway
) -> Iterator[tuple[str, dict]]:
    """执行 Intake 流水线，产出 SSE 事件流。幂等：状态不在 INTAKE 时只回放快照。"""
    if case.status != DecisionStatus.INTAKE:
        yield "state", {"status": case.status, "risk_level": case.risk_level}
        yield "done", {"reason": "already_processed"}
        return

    first_user_text = next(
        (m.content for m in case.messages if m.role == MessageRole.USER), ""
    )

    # 1. 风险分级
    _transition(db, case, DecisionStatus.RISK_TRIAGE)
    yield "state", {"status": case.status}

    risk, hits = risk_triage.triage_text(first_user_text)
    case.risk_level = risk
    record_event(
        db, case.id, "risk_triage", payload={"risk_level": risk, "keyword_hits": hits},
        user_id=case.user_id,
    )
    yield "risk", {"risk_level": risk}

    # 2. 结构化提取（受限/高风险也提取，但走 GUIDED_ONLY）
    extraction, source = extract_with_fallback(db, case, first_user_text, gateway)
    _apply_extraction(db, case, extraction, source)
    yield "extraction", {"source": source, "extraction": extraction.model_dump()}

    # 3. 状态推进 + 助手消息
    if risk in (RiskLevel.HIGH, RiskLevel.RESTRICTED):
        _transition(db, case, DecisionStatus.GUIDED_ONLY)
        content = _GUIDED_ONLY_MESSAGE
        if risk == RiskLevel.RESTRICTED:
            content = _RESTRICTED_EXTRA + "\n\n" + content
    else:
        _transition(db, case, DecisionStatus.PROBLEM_NORMALIZATION)
        content = _summary_message(extraction, source)

    # 通过关系追加，保证已加载的 case.messages 集合同步更新
    case.messages.append(DecisionMessage(role=MessageRole.ASSISTANT, content=content))
    db.flush()
    yield "assistant", {"content": content}
    yield "state", {"status": case.status, "risk_level": case.risk_level}
    yield "done", {"reason": "completed"}
