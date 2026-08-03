from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DecisionCase, DecisionMessage, User
from app.services.state_machine import validate_transition
from shared_schemas import DecisionStatus, MessageRole

DEV_USER_EMAIL = "dev@dingle.local"

# 阶段1没有 LLM 接入，助手回复用确定性占位文案，
# 阶段2由 Intake Parser + Model Gateway 替换。
_PLACEHOLDER_REPLY = (
    "我已经记录你的纠结。当前版本还在搭建结构化解析能力，"
    "下一步会自动提取选项、约束和担忧，并每轮只问你一个最关键的问题。"
)


def get_or_create_dev_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        user = User(email=DEV_USER_EMAIL, display_name="Dev User", is_dev_placeholder=True)
        db.add(user)
        db.flush()
    return user


def _derive_title(text: str) -> str:
    first_line = text.strip().splitlines()[0]
    return first_line[:60] or "未命名决策"


def create_decision(db: Session, user: User, input_text: str) -> DecisionCase:
    case = DecisionCase(
        user_id=user.id,
        title=_derive_title(input_text),
        status=DecisionStatus.DRAFT,
    )
    db.add(case)
    db.flush()

    db.add(
        DecisionMessage(
            decision_case_id=case.id, role=MessageRole.USER, content=input_text
        )
    )
    # DRAFT -> INTAKE：录入完成即进入结构化解析阶段
    validate_transition(DecisionStatus(case.status), DecisionStatus.INTAKE)
    case.status = DecisionStatus.INTAKE
    db.flush()
    return case


def get_decision(db: Session, decision_id: str, user: User) -> DecisionCase | None:
    return db.scalar(
        select(DecisionCase).where(
            DecisionCase.id == decision_id, DecisionCase.user_id == user.id
        )
    )


def list_decisions(db: Session, user: User) -> list[DecisionCase]:
    return list(
        db.scalars(
            select(DecisionCase)
            .where(DecisionCase.user_id == user.id)
            .order_by(DecisionCase.updated_at.desc())
        )
    )


def append_message(
    db: Session, case: DecisionCase, content: str
) -> tuple[DecisionMessage, DecisionMessage]:
    user_msg = DecisionMessage(
        decision_case_id=case.id, role=MessageRole.USER, content=content
    )
    assistant_msg = DecisionMessage(
        decision_case_id=case.id, role=MessageRole.ASSISTANT, content=_PLACEHOLDER_REPLY
    )
    db.add_all([user_msg, assistant_msg])
    db.flush()
    return user_msg, assistant_msg
