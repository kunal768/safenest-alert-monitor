from datetime import datetime

from sqlalchemy.orm import Session

from app.audit import audit
from app.config import settings
from app.models import Alert, AlertRule, Contact, House, Notification
from app.rules import in_quiet_hours, next_fallback_channel, notification_plan, to_local


class LogNotifier:
    """Records each attempt as a notifications row. NOTIFY_FAIL_CHANNELS simulates provider outages."""

    def send(self, channel: str, contact: Contact, alert: Alert) -> bool:
        failing = {c.strip() for c in settings.notify_fail_channels.split(",") if c.strip()}
        return channel not in failing


class AwsNotifier:
    """Stub behind NOTIFIER=aws — not used."""

    def send(self, channel: str, contact: Contact, alert: Alert) -> bool:
        raise NotImplementedError("AwsNotifier is a stub; ENABLE via NOTIFIER=aws is not wired for demo")


def get_notifier():
    if settings.notifier == "aws":
        return AwsNotifier()
    return LogNotifier()


def ordered_contacts(db: Session, house_id: str) -> list[Contact]:
    return (
        db.query(Contact)
        .filter(Contact.house_id == house_id)
        .order_by(Contact.priority.asc())
        .all()
    )


def all_failed(notes: list[Notification]) -> bool:
    """True when real channels were attempted and none got through (dashboard-only is not a failure)."""
    attempts = [n for n in notes if n.channel != "dashboard"]
    return bool(attempts) and not any(n.status == "sent" for n in attempts)


def _rule_channels(db: Session, alert: Alert) -> set[str] | None:
    rule = (
        db.query(AlertRule)
        .filter(AlertRule.house_id == alert.house_id, AlertRule.label == alert.type)
        .one_or_none()
    )
    if not rule or not rule.channels:
        return None
    return {c for c in rule.channels.split(",") if c}


def plan_and_notify(
    db: Session,
    alert: Alert,
    house: House,
    *,
    step: int = 0,
    force_fail: bool = False,
    now: datetime | None = None,
    escalation: bool = False,
) -> list[Notification]:
    contacts = ordered_contacts(db, house.id)
    if not contacts:
        return []
    contact = contacts[min(step, len(contacts) - 1)]
    local = to_local(now or datetime.utcnow(), house.tz)
    night = in_quiet_hours(local, house.quiet_start, house.quiet_end)
    channels = notification_plan(
        alert.severity,
        night=night,
        subscription_active=house.subscription_active,
    )
    if escalation and not channels and house.subscription_active:
        # Escalation exists to reach a human; always page the next contact.
        channels = ["push", "sms"]
    allowed = _rule_channels(db, alert)
    if allowed is not None and channels:
        channels = [c for c in channels if c in allowed] or channels[:1]

    created: list[Notification] = []
    if not channels:
        n = Notification(
            alert_id=alert.alert_id,
            contact_id=contact.id,
            channel="dashboard",
            status="dashboard",
            detail="dashboard only" + (" (subscription inactive)" if not house.subscription_active else ""),
        )
        db.add(n)
        created.append(n)
        return created

    notifier = get_notifier()
    queue = list(channels)
    attempted: set[str] = set()
    while queue:
        ch = queue.pop(0)
        if ch in attempted:
            continue
        attempted.add(ch)
        ok = False if force_fail else notifier.send(ch, contact, alert)
        n = Notification(
            alert_id=alert.alert_id,
            contact_id=contact.id,
            channel=ch,
            status="sent" if ok else "failed",
            detail=f"{ch} to {contact.name}" + (" (escalation)" if escalation else "") + ("" if ok else " — provider error, falling back"),
        )
        db.add(n)
        created.append(n)
        if not ok:
            nxt = next_fallback_channel(ch)
            if nxt and nxt not in attempted and nxt not in queue:
                queue.append(nxt)
    if all_failed(created):
        audit(
            db,
            "notify_failed",
            "alert",
            alert.alert_id,
            f"all channels failed for {contact.name}; immediate escalation",
        )
    return created
