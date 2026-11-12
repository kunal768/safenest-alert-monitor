from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from ai.classifier import ClassifyResult, get_adapter  # noqa: E402

from app.audit import audit
from app.config import settings
from app.errors import AppError
from app.models import ActivityProfile, AiModel, Alert, AlertRule, Device, Event, House, Notification, Room
from app.notify import all_failed, ordered_contacts, plan_and_notify
from app.rules import OPEN_STATUSES, Rule, decide_severity, should_merge, to_local
from app.ws import hub

log = logging.getLogger("safenest.pipeline")

CLIP_BY_LABEL = {
    "glass_break": "glass.wav",
    "fall": "fall.wav",
    "cough": "cough.wav",
    "scream": "scream.wav",
    "door_open": "door.wav",
    "call_for_help": "help.wav",
}

# Severities that page a person and therefore start an escalation timer.
ESCALATING_SEVERITIES = {"emergency", "security"}


def house_rules(db: Session, house_id: str) -> list[Rule]:
    rows = db.query(AlertRule).filter(AlertRule.house_id == house_id).all()
    return [
        Rule(r.label, r.enabled, r.severity, r.min_confidence, r.high_mark, r.channels, r.ack_timeout_sec) for r in rows
    ]


def active_model_tag(db: Session) -> str:
    m = db.query(AiModel).filter(AiModel.is_active == True).one_or_none()  # noqa: E712
    if m:
        return m.ollama_tag
    return "llama3.2:3b"


def _parse_ts(ts) -> datetime:
    if isinstance(ts, str):
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            from datetime import timezone

            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return ts or datetime.utcnow()


async def ingest_event(db: Session, payload: dict, *, wait: bool = True) -> tuple[Event, bool]:
    """F3.1: validate, dedupe on eventId, store, classify. Returns (event, duplicate)."""
    existing = db.query(Event).filter(Event.event_id == payload["eventId"]).one_or_none()
    if existing:
        audit(db, "duplicate", "event", existing.event_id, "duplicate eventId ignored")
        db.commit()
        return existing, True

    house = db.query(House).filter(House.id == payload["houseId"]).one_or_none()
    if not house:
        raise AppError("not_found", "Unknown house", 404)
    device = db.query(Device).filter(Device.id == payload["deviceId"]).one_or_none()
    if not device:
        raise AppError("not_found", "Unknown device", 404)
    if device.house_id != house.id:
        raise AppError("validation_error", "device does not belong to house", 422)

    event = Event(
        event_id=payload["eventId"],
        house_id=house.id,
        device_id=device.id,
        ts=_parse_ts(payload.get("ts")),
        description=payload["description"],
        level_db=float(payload.get("levelDb", 0)),
        duration_sec=float(payload.get("durationSec", 0)),
        status="pending",
    )
    db.add(event)
    device.last_seen = datetime.utcnow()
    device.missed_since = None
    device.active = True
    try:
        db.commit()
    except IntegrityError:
        # Concurrent duplicate: the unique primary key on event_id is the real guard.
        db.rollback()
        return db.query(Event).filter(Event.event_id == payload["eventId"]).one(), True
    _record_activity(db, house, event.ts)

    if wait:
        await classify_and_decide(db, event.event_id)
        db.refresh(event)
    else:
        asyncio.create_task(_bg_classify(event.event_id))
    return event, False


def _record_activity(db: Session, house: House, ts: datetime):
    """F3.8 input: every event adds to the house-local hourly activity profile."""
    local = to_local(ts, house.tz)
    day = local.date().isoformat()
    row = (
        db.query(ActivityProfile)
        .filter(ActivityProfile.house_id == house.id, ActivityProfile.day == day, ActivityProfile.hour == local.hour)
        .one_or_none()
    )
    if row:
        row.count += 1
    else:
        db.add(ActivityProfile(house_id=house.id, day=day, hour=local.hour, count=1, is_baseline=False))
    db.commit()


async def _bg_classify(event_id: str):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await classify_and_decide(db, event_id)
    finally:
        db.close()


async def classify_and_decide(db: Session, event_id: str) -> Event:
    event = db.query(Event).filter(Event.event_id == event_id).one()
    house = db.query(House).filter(House.id == event.house_id).one()
    device = db.query(Device).filter(Device.id == event.device_id).one()
    room = db.query(Room).filter(Room.id == device.room_id).one()
    adapter = get_adapter()
    model = active_model_tag(db)
    context = {
        "Room": room.name,
        "Level": f"{event.level_db:.0f} dB",
        "Duration": f"{event.duration_sec:g} s" if event.duration_sec else None,
    }
    t0 = time.perf_counter()
    result: ClassifyResult = await adapter.classify(event.description, model, context)
    event.latency_ms = result.latency_ms or int((time.perf_counter() - t0) * 1000)
    event.model_version = result.model_version or model
    event.prompt_version = result.prompt_version
    if result.unclassified:
        event.status = "unclassified"
        event.label = None
        event.confidence = None
        event.reason = result.error or "unclassified"
        event.error = result.error
        audit(db, "unclassified", "event", event.event_id, (result.error or "")[:200])
        db.commit()
        if event.level_db > 85:
            await _fail_loud(db, event, house, device)
        await hub.broadcast("event", _event_dict(event))
        return event

    event.label = result.label
    event.confidence = result.confidence
    event.reason = result.reason
    event.status = "classified"
    db.commit()

    decision = decide_severity(
        event.label or "unknown",
        event.confidence or 0,
        house_rules(db, house.id),
        now=to_local(event.ts, house.tz),
        quiet_start=house.quiet_start,
        quiet_end=house.quiet_end,
        quiet_enabled=house.quiet_hours_enabled,
    )
    if decision.action in ("ignore", "suppress"):
        audit(
            db,
            "ignored" if decision.action == "ignore" else "suppressed",
            "event",
            event.event_id,
            f"{event.label} {event.confidence:.2f}: {decision.reason}",
        )
        db.commit()
        await hub.broadcast("event", {**_event_dict(event), "decision": decision.reason})
        return event

    await _create_or_merge_alert(db, event, house, device.room_id, decision.severity or "info")
    return event


async def _fail_loud(db: Session, event: Event, house: House, device: Device):
    """LLM failed but the sound was loud (> 85 dB): raise a warning anyway."""
    event.reason = f"AI unavailable ({event.error or 'unclassified'}); fail-loud warning because level {event.level_db:.0f} dB > 85"
    await _create_or_merge_alert(db, event, house, device.room_id, "safety", force_type="unclassified")


async def _create_or_merge_alert(
    db: Session,
    event: Event,
    house: House,
    room_id: str,
    severity: str,
    force_type: str | None = None,
) -> Alert:
    alert_type = force_type or event.label or "unknown"
    open_alerts = (
        db.query(Alert)
        .filter(
            Alert.house_id == house.id,
            Alert.room_id == room_id,
            Alert.type == alert_type,
            Alert.status.in_(list(OPEN_STATUSES)),
        )
        .order_by(Alert.created_at.desc())
        .all()
    )
    now = datetime.utcnow()
    for a in open_alerts:
        if should_merge(
            existing_type=a.type,
            existing_room=a.room_id,
            existing_status=a.status,
            existing_created=a.created_at,
            new_type=alert_type,
            new_room=room_id,
            now=now,
            window_min=settings.merge_window_min,
        ):
            # increment once per eventId
            if event.alert_id == a.alert_id:
                return a
            a.count += 1
            a.confidence = max(a.confidence, event.confidence or 0)
            a.version += 1
            a.updated_at = now
            event.alert_id = a.alert_id
            audit(db, "merge", "alert", a.alert_id, f"count={a.count} event={event.event_id}")
            db.commit()
            await hub.broadcast("alert", alert_dict(db, a))
            return a

    alert = create_alert(
        db,
        house,
        room_id=room_id,
        alert_type=alert_type,
        severity=severity,
        confidence=event.confidence or 0,
        reason=event.reason or "",
        event=event,
    )
    await hub.broadcast("alert", alert_dict(db, alert))
    if alert.escalate_after:
        _schedule_escalation(alert.alert_id)
    return alert


def create_alert(
    db: Session,
    house: House,
    *,
    room_id: str,
    alert_type: str,
    severity: str,
    confidence: float,
    reason: str,
    event: Event | None = None,
) -> Alert:
    """Create, notify (F3.4) and commit an alert. The caller broadcasts and schedules escalation."""
    now = datetime.utcnow()
    alert = Alert(
        alert_id=f"A-{uuid.uuid4().hex[:8]}",
        house_id=house.id,
        room_id=room_id,
        type=alert_type,
        severity=severity,
        status="new",
        count=1,
        confidence=confidence,
        reason=reason,
        event_id=event.event_id if event else None,
        clip_name=CLIP_BY_LABEL.get(alert_type, "default.wav") if event else None,
        model_version=event.model_version if event else None,
        prompt_version=event.prompt_version if event else None,
        latency_ms=event.latency_ms if event else None,
        created_at=now,
        updated_at=now,
        escalate_step=0,
    )
    db.add(alert)
    db.flush()
    if event:
        event.alert_id = alert.alert_id
    audit(db, "create", "alert", alert.alert_id, f"{alert.type} {alert.severity}")
    notes = plan_and_notify(db, alert, house, step=0, now=event.ts if event else now)
    if any(n.status == "sent" for n in notes):
        alert.status = "notified"
        audit(db, "notified", "alert", alert.alert_id, ", ".join(sorted({n.channel for n in notes if n.status == "sent"})))
    if severity in ESCALATING_SEVERITIES:
        alert.escalate_after = now + timedelta(seconds=ack_timeout(db, alert))
    if all_failed(notes):
        escalate_now(db, alert, house, actor="system", reason="all channels failed")
    db.commit()
    return alert


def ack_timeout(db: Session, alert: Alert) -> int:
    """F3.6: the house rule's acknowledge timeout, capped by ESCALATION_TIMEOUT_SEC (short in the demo)."""
    rule = (
        db.query(AlertRule)
        .filter(AlertRule.house_id == alert.house_id, AlertRule.label == alert.type)
        .one_or_none()
    )
    house_value = rule.ack_timeout_sec if rule and rule.ack_timeout_sec else settings.escalation_timeout_sec
    return min(house_value, settings.escalation_timeout_sec)


ESCALATION_TARGETS = ("family", "careCenter", "emergency")


def escalate_now(
    db: Session,
    alert: Alert,
    house: House,
    *,
    actor: str,
    reason: str = "timeout",
    target: str = "family",
) -> list[Notification]:
    """F3.6 / L4: page the next contact, or jump to the care center. Returns the notifications sent."""
    contacts = ordered_contacts(db, house.id)
    last = max(len(contacts) - 1, 0)
    alert.status = "escalated"
    created: list[Notification] = []
    if target in ("careCenter", "emergency"):
        alert.escalate_step = next((i for i, c in enumerate(contacts) if c.is_care_center), last)
        created += plan_and_notify(db, alert, house, step=alert.escalate_step, escalation=True)
        if target == "emergency":
            care = contacts[alert.escalate_step]
            n = Notification(
                alert_id=alert.alert_id,
                contact_id=care.id,
                channel="call",
                status="sent",
                detail=f"{care.name} asked to dispatch emergency services (simulated)",
            )
            db.add(n)
            created.append(n)
        audit(db, "escalate", "alert", alert.alert_id, f"target={target} to {contacts[alert.escalate_step].name} ({reason})", actor=actor)
        alert.escalate_after = None
        db.flush()
        return created
    if alert.escalate_step >= last:
        alert.escalate_after = None
        audit(db, "escalate", "alert", alert.alert_id, "already at last contact", actor=actor)
        return created
    while True:
        alert.escalate_step += 1
        notes = plan_and_notify(db, alert, house, step=alert.escalate_step, escalation=True)
        created += notes
        name = contacts[alert.escalate_step].name
        audit(db, "escalate", "alert", alert.alert_id, f"step={alert.escalate_step} to {name} ({reason})", actor=actor)
        if not all_failed(notes) or alert.escalate_step >= last:
            break
        reason = "all channels failed"
    if alert.escalate_step < last:
        alert.escalate_after = datetime.utcnow() + timedelta(seconds=ack_timeout(db, alert))
    else:
        alert.escalate_after = None
    db.flush()
    return created


def _schedule_escalation(alert_id: str, delay: float | None = None):
    async def _run():
        await asyncio.sleep(settings.escalation_timeout_sec if delay is None else delay)
        from app.database import SessionLocal
        from app.lifecycle import auto_escalate

        db = SessionLocal()
        try:
            await auto_escalate(db, alert_id)
        except Exception:  # never kill the loop over one timer
            log.exception("escalation timer failed for %s", alert_id)
        finally:
            db.close()

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass


def clip_token(alert_id: str, exp: int | None = None) -> str:
    exp = exp or int(time.time()) + 300
    msg = f"{alert_id}:{exp}".encode()
    sig = hmac.new(settings.clip_secret.encode(), msg, hashlib.sha256).hexdigest()[:16]
    return f"{exp}.{sig}"


def alert_link_token(alert_id: str, exp: int | None = None) -> str:
    """One-alert link for the care center (PDF 4.5): opens only this alert, valid 24 h."""
    return clip_token(f"link:{alert_id}", exp or int(time.time()) + 24 * 3600)


def verify_alert_link(alert_id: str, token: str) -> bool:
    return verify_clip_token(f"link:{alert_id}", token)


def verify_clip_token(alert_id: str, token: str) -> bool:
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
        if exp < time.time():
            return False
        expected = clip_token(alert_id, exp)
        return hmac.compare_digest(expected, f"{exp}.{sig}")
    except Exception:
        return False


def _event_dict(event: Event) -> dict:
    return {
        "eventId": event.event_id,
        "houseId": event.house_id,
        "label": event.label,
        "status": event.status,
        "confidence": event.confidence,
        "reason": event.reason,
        "alertId": event.alert_id,
        "modelVersion": event.model_version,
    }


def alert_dict(db: Session, a: Alert) -> dict:
    house = db.query(House).filter(House.id == a.house_id).one()
    room = db.query(Room).filter(Room.id == a.room_id).one()
    remaining = None
    if a.escalate_after and a.status in ("new", "notified", "escalated"):
        remaining = max(0, int((a.escalate_after - datetime.utcnow()).total_seconds()))
    contacts = ordered_contacts(db, a.house_id)
    next_contact = None
    if remaining is not None and a.escalate_step + 1 < len(contacts):
        next_contact = contacts[a.escalate_step + 1].name
    return {
        "alertId": a.alert_id,
        "houseId": a.house_id,
        "houseName": house.name,
        "roomId": a.room_id,
        "roomName": room.name,
        "type": a.type,
        "severity": a.severity,
        "status": a.status,
        "count": a.count,
        "confidence": a.confidence,
        "reason": a.reason,
        "ackBy": a.ack_by,
        "version": a.version,
        "createdAt": a.created_at.isoformat() + "Z",
        "updatedAt": (a.updated_at or a.created_at).isoformat() + "Z",
        "eventId": a.event_id,
        "modelVersion": a.model_version,
        "promptVersion": a.prompt_version,
        "latencyMs": a.latency_ms,
        "escalateStep": a.escalate_step,
        "escalateRemainingSec": remaining,
        "nextContact": next_contact,
        "clipToken": clip_token(a.alert_id) if a.clip_name else None,
    }
