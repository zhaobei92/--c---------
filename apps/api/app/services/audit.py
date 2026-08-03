from sqlalchemy.orm import Session

from app.models import AuditEvent


def record_event(
    db: Session,
    decision_case_id: str,
    event_type: str,
    payload: dict | None = None,
    user_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        decision_case_id=decision_case_id,
        user_id=user_id,
        event_type=event_type,
        payload=payload or {},
    )
    db.add(event)
    db.flush()
    return event
