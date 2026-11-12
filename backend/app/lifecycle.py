from datetime import datetime

from sqlalchemy.orm import Session

from app.audit import audit
from app.errors import AppError
from app.models import Alert, House
from app.pipeline import _schedule_escalation, alert_dict, escalate_now
from app.rules import CLOSED_STATUSES, apply_transition, can_transition
from app.ws import hub

# Statuses in which the escalation timer keeps running (Table 45: acknowledged stops it).
TIMER_STATUSES = {"new", "notified", "escalated"}


async def apply_action(
    db: Session,
    alert: Alert,
    action: str,
    *,
    version: int,
    actor: str,
    note: str | None = None,
    target: str = "family",
) -> Alert:
    if alert.version != version:
        raise AppError("conflict", "This alert was changed by someone else. Reload and try again.", 409)
    if alert.status in CLOSED_STATUSES:
        raise AppError("conflict", "closed alerts are read-only", 409)
    if not can_transition(alert.status, action):
        raise AppError("conflict", f"illegal transition {alert.status} -> {action}", 409)
    previous = alert.status
    alert.status = apply_transition(alert.status, action)
    alert.version += 1
    alert.updated_at = datetime.utcnow()
    if action == "acknowledge":
        alert.ack_by = actor
        alert.escalate_after = None
    elif action in ("resolve", "falseAlarm"):
        alert.escalate_after = None
    elif action == "escalate":
        house = db.query(House).filter(House.id == alert.house_id).one()
        alert.last_notifications = escalate_now(db, alert, house, actor=actor, reason="manual", target=target)
    detail = f"{previous} -> {alert.status}" + (f" · note: {note.strip()}" if note and note.strip() else "")
    audit(db, action, "alert", alert.alert_id, detail, actor=actor)
    db.commit()
    await hub.broadcast("alert", alert_dict(db, alert))
    if action == "escalate" and alert.escalate_after:
        _schedule_escalation(alert.alert_id)
    return alert


async def auto_escalate(db: Session, alert_id: str):
    """Timer callback: nobody acknowledged in time, so page the next contact."""
    alert = db.query(Alert).filter(Alert.alert_id == alert_id).one_or_none()
    if not alert or alert.status not in TIMER_STATUSES or not alert.escalate_after:
        return
    remaining = (alert.escalate_after - datetime.utcnow()).total_seconds()
    if remaining > 0:
        # Deadline moved (e.g. a manual escalation reset it); check again later.
        _schedule_escalation(alert.alert_id, delay=remaining)
        return
    house = db.query(House).filter(House.id == alert.house_id).one()
    previous = alert.status
    escalate_now(db, alert, house, actor="system", reason="no acknowledgement")
    alert.version += 1
    alert.updated_at = datetime.utcnow()
    audit(db, "auto_escalate", "alert", alert.alert_id, f"{previous} -> {alert.status}")
    db.commit()
    await hub.broadcast("alert", alert_dict(db, alert))
    if alert.escalate_after:
        _schedule_escalation(alert.alert_id)
