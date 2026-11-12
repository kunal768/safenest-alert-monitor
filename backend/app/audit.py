from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(
    db: Session,
    action: str,
    entity: str,
    entity_id: str,
    detail: str = "",
    actor: str = "system",
):
    db.add(
        AuditLog(
            action=action,
            entity=entity,
            entity_id=entity_id,
            detail=detail,
            actor=actor,
        )
    )
