from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DecisionCase, DecisionMessage, DecisionOption, User
from app.services.audit import record_event
from app.services.state_machine import validate_transition
from shared_schemas import DecisionStatus, MessageRole

DEV_USER_EMAIL = "dev@dingle.local"

_PLACEHOLDER_REPLY = (
    "我已经记录。你可以点「继续推进」让我进行下一步，或继续补充信息。"
)

_ANSWER_ACK_REPLY = "收到，已记录这条信息。点「继续推进」我会看是否还需要问什么。"


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
    record_event(
        db,
        case.id,
        "state_transition",
        payload={"from": DecisionStatus.DRAFT, "to": DecisionStatus.INTAKE},
        user_id=user.id,
    )
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


def add_option(
    db: Session, case: DecisionCase, name: str, description: str | None
) -> DecisionOption:
    option = DecisionOption(
        decision_case_id=case.id, name=name, description=description, source="user_input"
    )
    db.add(option)
    db.flush()
    return option


def append_message(
    db: Session, case: DecisionCase, content: str
) -> tuple[DecisionMessage, DecisionMessage]:
    from app.services.audit import record_event as _record
    from app.services.question_service import record_answer

    user_msg = DecisionMessage(
        decision_case_id=case.id, role=MessageRole.USER, content=content
    )
    case.messages.append(user_msg)

    # 有未回答的追问时，把这条消息记为答案（写入 facts，消解 unknowns）
    answered = record_answer(case, content)
    if answered is not None:
        _record(
            db,
            case.id,
            "question_answered",
            {"target_variable": answered["target_variable"]},
            case.user_id,
        )
        reply = _ANSWER_ACK_REPLY
    else:
        reply = _PLACEHOLDER_REPLY

    assistant_msg = DecisionMessage(
        decision_case_id=case.id, role=MessageRole.ASSISTANT, content=reply
    )
    case.messages.append(assistant_msg)
    db.flush()
    return user_msg, assistant_msg
