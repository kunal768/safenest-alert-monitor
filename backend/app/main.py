from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from fastapi import Depends, FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.audit import audit
from app.auth import assert_house_access, get_current_user, get_optional_user, house_ids_for
from app.config import ROOT as PROJECT_ROOT, runtime, settings
from app.database import get_db, init_db
from app.errors import AppError, app_error_handler, http_error_handler, validation_handler
from app.models import AiModel, Alert, AlertRule, AuditLog, Contact, Device, Event, House, Notification, User
from app.pipeline import ESCALATION_TARGETS, alert_dict, alert_link_token, ingest_event, verify_alert_link, verify_clip_token
from app.rules import Rule, validate_rules_limits
from app.schemas import (
    CustomEventBody,
    DeployBody,
    DeviceMissedBody,
    EscalateBody,
    EventIn,
    LoginBody,
    OutageBody,
    PatchAlert,
    RegisterModelBody,
    RulesBody,
)
from app.seed import seed
from app.ws import hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
CLIPS = PROJECT_ROOT / "simulator" / "clips"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Path(PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
    init_db()
    seed()
    if not (CLIPS / "default.wav").exists():
        from simulator.make_clips import write_all

        write_all()
    if settings.model_adapter != "mock":
        from app.registry import warm_up_active

        asyncio.get_running_loop().create_task(warm_up_active())
    _rearm_escalations()
    yield


def _rearm_escalations():
    """Escalation timers live in memory; re-arm them for open alerts after a restart."""
    from app.database import SessionLocal
    from app.lifecycle import TIMER_STATUSES
    from app.pipeline import _schedule_escalation

    db = SessionLocal()
    try:
        pending = db.query(Alert).filter(Alert.escalate_after.isnot(None), Alert.status.in_(list(TIMER_STATUSES))).all()
        for a in pending:
            _schedule_escalation(a.alert_id, delay=max(0.0, (a.escalate_after - datetime.utcnow()).total_seconds()))
    finally:
        db.close()


app = FastAPI(title="SafeNest Alert Tracking", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_handler)


def _page(items, page, size, total):
    return {"items": items, "page": page, "size": size, "total": total}


def _require_staff(user: User):
    if user.role != "staff":
        raise AppError("forbidden", "staff only", 403)


def _get_alert(db: Session, user: User | None, alert_id: str, link: str | None = None) -> Alert:
    """Owner/staff by token, or the care center's one-alert link (Table 13, role "link")."""
    a = db.query(Alert).filter(Alert.alert_id == alert_id).one_or_none()
    if user is None:
        if not (a and link and verify_alert_link(alert_id, link)):
            raise AppError("unauthorized", "Missing bearer token or valid alert link", 401)
        return a
    if not a:
        raise AppError("not_found", "Alert not found", 404)
    assert_house_access(user, a.house_id)
    return a


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppError("validation_error", f"bad timestamp {value!r}", 400) from exc
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _event_result(event: Event, duplicate: bool) -> dict:
    return {
        "eventId": event.event_id,
        "status": event.status,
        "label": event.label,
        "confidence": event.confidence,
        "reason": event.reason,
        "alertId": event.alert_id,
        "modelVersion": event.model_version,
        "promptVersion": event.prompt_version,
        "latencyMs": event.latency_ms,
        "duplicate": duplicate,
    }


@app.get("/health")
async def health(db: Session = Depends(get_db)):
    """L12: dependency status and queue depth; 503 when the database is down."""
    db_ok = True
    try:
        db.query(User).count()
    except Exception:
        db_ok = False
    ollama_ok = False
    if settings.model_adapter == "mock":
        ollama_ok = not runtime["ai_outage"]
    else:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{settings.ollama_url.rstrip('/')}/api/tags")
                ollama_ok = r.status_code == 200 and not runtime["ai_outage"]
        except Exception:
            ollama_ok = False
    active = db.query(AiModel).filter(AiModel.is_active == True).one_or_none() if db_ok else None  # noqa: E712
    queue = db.query(Event).filter(Event.status == "pending").count() if db_ok else None
    body = {
        "status": "ok" if db_ok and ollama_ok else ("degraded" if db_ok else "down"),
        "db": db_ok,
        "ollama": ollama_ok,
        "deps": {"db": "up" if db_ok else "down", "models": "up" if ollama_ok else "down", "notifier": settings.notifier},
        "queueDepth": queue,
        "adapter": settings.model_adapter,
        "activeModel": active.ollama_tag if active else None,
        "aiOutage": runtime["ai_outage"],
    }
    if not db_ok:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/config/features")
def features():
    return {
        "ENABLE_CLOUD_DEPLOY": settings.enable_cloud_deploy,
        "MODEL_ADAPTER": settings.model_adapter,
        "NOTIFIER": settings.notifier,
        "escalationTimeoutSec": settings.escalation_timeout_sec,
        "promptVersion": settings.prompt_version,
        "aiOutage": runtime["ai_outage"],
        "notifyFailChannels": settings.notify_fail_channels,
    }


@app.post("/auth/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).one_or_none()
    if not user:
        raise AppError("unauthorized", "Unknown user", 401)
    return {
        "token": user.token,
        "role": user.role,
        "name": user.name,
        "email": user.email,
        "houseIds": [h for h in user.house_ids.split(",") if h],
    }


@app.get("/auth/users")
def users(db: Session = Depends(get_db)):
    return [{"email": u.email, "role": u.role, "name": u.name} for u in db.query(User).all()]


@app.get("/houses")
def houses(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    allowed = house_ids_for(user)
    q = db.query(House)
    if allowed is not None:
        q = q.filter(House.id.in_(allowed))
    return [
        {
            "id": h.id,
            "name": h.name,
            "tz": h.tz,
            "devices": [{"id": d.id, "roomId": d.room_id, "room": d.room.name, "active": d.active} for d in h.devices],
        }
        for h in q.all()
    ]


@app.post("/events")
async def post_event(
    body: EventIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_house_access(user, body.houseId)
    event, duplicate = await ingest_event(db, body.model_dump(), wait=True)
    return _event_result(event, duplicate)


@app.get("/alerts")
def list_alerts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    severity: str | None = None,
    status: str | None = None,
    type: str | None = None,
    houseId: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    q = db.query(Alert)
    if from_:
        q = q.filter(Alert.created_at >= _parse_iso(from_))
    if to:
        q = q.filter(Alert.created_at <= _parse_iso(to))
    allowed = house_ids_for(user)
    if allowed is not None:
        q = q.filter(Alert.house_id.in_(allowed))
    if houseId:
        assert_house_access(user, houseId)
        q = q.filter(Alert.house_id == houseId)
    if severity:
        q = q.filter(Alert.severity == severity)
    if status:
        q = q.filter(Alert.status == status)
    if type:
        q = q.filter(Alert.type == type)
    total = q.count()
    rows = q.order_by(Alert.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return _page([alert_dict(db, a) for a in rows], page, size, total)


@app.get("/alerts/open")
def open_alerts(
    houseIds: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """L5: open alerts for the given houses (dashboard map colors)."""
    from app.rules import OPEN_STATUSES

    wanted = [h for h in (houseIds or "").split(",") if h]
    allowed = house_ids_for(user)
    for h in wanted:
        assert_house_access(user, h)
    q = db.query(Alert).filter(Alert.status.in_(list(OPEN_STATUSES)))
    if wanted:
        q = q.filter(Alert.house_id.in_(wanted))
    elif allowed is not None:
        q = q.filter(Alert.house_id.in_(allowed))
    return {"items": [alert_dict(db, a) for a in q.order_by(Alert.created_at.desc()).all()]}


@app.get("/alerts/{alert_id}")
def get_alert(
    alert_id: str,
    link: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    a = _get_alert(db, user, alert_id, link)
    data = alert_dict(db, a)
    data["clipUrl"] = f"/alerts/{a.alert_id}/clip?token={data['clipToken']}" if data["clipToken"] else None
    data["viewer"] = "link" if user is None else user.role
    event = db.query(Event).filter(Event.event_id == a.event_id).one_or_none() if a.event_id else None
    data["event"] = None
    if event:
        data["event"] = {
            "eventId": event.event_id,
            "deviceId": event.device_id,
            "description": event.description,
            "levelDb": event.level_db,
            "durationSec": event.duration_sec,
            "ts": event.ts.isoformat() + "Z",
            "label": event.label,
            "confidence": event.confidence,
            "status": event.status,
            "error": event.error,
        }
    merged = db.query(Event).filter(Event.alert_id == a.alert_id).order_by(Event.ts).all()
    data["events"] = [
        {"eventId": e.event_id, "ts": e.ts.isoformat() + "Z", "label": e.label, "confidence": e.confidence}
        for e in merged
    ]
    history = (
        db.query(AuditLog)
        .filter(AuditLog.entity == "alert", AuditLog.entity_id == a.alert_id)
        .order_by(AuditLog.id)
        .all()
    )
    data["history"] = [
        {"ts": h.ts.isoformat() + "Z", "action": h.action, "actor": h.actor, "detail": h.detail} for h in history
    ]
    return data


@app.patch("/alerts/{alert_id}")
async def patch_alert(
    alert_id: str,
    body: PatchAlert,
    link: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """L3. A care-center link may only acknowledge (Table 13)."""
    from app.lifecycle import apply_action

    if body.action not in ("acknowledge", "resolve", "falseAlarm"):
        raise AppError("validation_error", "action must be acknowledge | resolve | falseAlarm", 422)
    a = _get_alert(db, user, alert_id, link)
    if user is None and body.action != "acknowledge":
        raise AppError("forbidden", "An alert link may only acknowledge", 403)
    actor = user.email if user else "care-center (alert link)"
    a = await apply_action(db, a, body.action, version=body.version, actor=actor, note=body.note)
    return alert_dict(db, a)


@app.post("/alerts/{alert_id}/escalate", status_code=202)
async def escalate_alert(
    alert_id: str,
    body: EscalateBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """L4: staff escalate by hand to family (next contact), careCenter, or emergency. 202 + notification ids."""
    from app.lifecycle import apply_action

    body = body or EscalateBody()
    if body.target not in ESCALATION_TARGETS:
        raise AppError("validation_error", f"target must be one of {', '.join(ESCALATION_TARGETS)}", 400)
    a = _get_alert(db, user, alert_id)
    if a.status in ("resolved", "false_alarm"):
        raise AppError("conflict", "alert is already closed", 409)
    version = body.version if body.version is not None else a.version
    a = await apply_action(db, a, "escalate", version=version, actor=user.email, note=body.note, target=body.target)
    notes = getattr(a, "last_notifications", []) or []
    return {"notificationIds": [n.id for n in notes], "target": body.target, "alert": alert_dict(db, a)}


@app.get("/alerts/{alert_id}/notifications")
def alert_notes(
    alert_id: str,
    link: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    _get_alert(db, user, alert_id, link)
    rows = db.query(Notification).filter(Notification.alert_id == alert_id).order_by(Notification.id).all()
    contacts = {c.id: c for c in db.query(Contact).all()}
    out = []
    for n in rows:
        c = contacts.get(n.contact_id)
        item = {
            "id": n.id,
            "channel": n.channel,
            "status": n.status,
            "sentAt": n.sent_at.isoformat() + "Z",
            "contact": c.name if c else n.contact_id,
            "priority": c.priority if c else None,
            "detail": n.detail,
            "link": None,
        }
        # The care center's message carries a link that opens only this alert (PDF 1.4, 4.5).
        if c and c.is_care_center and n.channel in ("sms", "email") and user is not None:
            item["link"] = f"/link/{alert_id}?t={alert_link_token(alert_id)}"
        out.append(item)
    return out


@app.get("/alerts/{alert_id}/clip")
def get_clip(alert_id: str, token: str = Query(...), db: Session = Depends(get_db)):
    a = db.query(Alert).filter(Alert.alert_id == alert_id).one_or_none()
    if not a:
        raise AppError("not_found", "Alert not found", 404)
    if not verify_clip_token(alert_id, token):
        raise AppError("unauthorized", "Invalid or expired clip token", 401)
    path = CLIPS / (a.clip_name or "default.wav")
    if not path.exists():
        path = CLIPS / "default.wav"
    if not path.exists():
        raise AppError("not_found", "No clip available (run make clips)", 404)
    return FileResponse(path, media_type="audio/wav")


def _rules_payload(db: Session, house: House) -> dict:
    rules = db.query(AlertRule).filter(AlertRule.house_id == house.id).order_by(AlertRule.id).all()
    return {
        "houseId": house.id,
        "houseName": house.name,
        "version": house.rules_version,
        "quietStart": house.quiet_start,
        "quietEnd": house.quiet_end,
        "quietHoursEnabled": house.quiet_hours_enabled,
        "tz": house.tz,
        "subscriptionActive": house.subscription_active,
        "rules": [
            {
                "label": r.label,
                "enabled": r.enabled,
                "severity": r.severity,
                "minConfidence": r.min_confidence,
                "highMark": r.high_mark,
                "channels": [c for c in r.channels.split(",") if c],
                "ackTimeoutSec": r.ack_timeout_sec,
                "firstContact": "priority 1",
            }
            for r in rules
        ],
    }


def _get_house(db: Session, user: User, house_id: str) -> House:
    assert_house_access(user, house_id)
    house = db.query(House).filter(House.id == house_id).one_or_none()
    if not house:
        raise AppError("not_found", "Unknown house", 404)
    return house


@app.get("/houses/{house_id}/alert-rules")
def get_rules(house_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _rules_payload(db, _get_house(db, user, house_id))


HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@app.put("/houses/{house_id}/alert-rules")
def put_rules(
    house_id: str,
    body: RulesBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    house = _get_house(db, user, house_id)
    if body.version != house.rules_version:
        raise AppError(
            "conflict",
            f"These rules were changed by someone else (now version {house.rules_version}). Reload and try again.",
            409,
        )
    for value in (body.quietStart, body.quietEnd):
        if value and not HHMM.match(value):
            raise AppError("validation_error", f"quiet hours must be HH:MM, got {value!r}", 422)
    try:
        parsed = [
            Rule(
                r["label"],
                bool(r.get("enabled", True)),
                r["severity"],
                float(r.get("minConfidence", 0.8)),
                float(r.get("highMark", 0.85)),
                ",".join(r["channels"]) if isinstance(r.get("channels"), list) else r.get("channels", "push,sms,email"),
                int(r.get("ackTimeoutSec", 300)),
            )
            for r in body.rules
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("validation_error", f"bad rule: {exc}", 422) from exc
    err = validate_rules_limits(parsed)
    if err:
        raise AppError("service_limit", err, 400)
    changes = []
    if body.quietStart:
        house.quiet_start = body.quietStart
    if body.quietEnd:
        house.quiet_end = body.quietEnd
    if body.quietHoursEnabled is not None and body.quietHoursEnabled != house.quiet_hours_enabled:
        house.quiet_hours_enabled = body.quietHoursEnabled
        changes.append(f"quietHours={'on' if body.quietHoursEnabled else 'off'}")
    existing = {r.label: r for r in db.query(AlertRule).filter(AlertRule.house_id == house_id).all()}
    for r in parsed:
        row = existing.get(r.label)
        if not row:
            continue
        before = (row.enabled, row.severity, row.min_confidence, row.high_mark, row.channels, row.ack_timeout_sec)
        row.enabled = r.enabled
        row.severity = r.severity
        row.min_confidence = r.min_confidence
        row.high_mark = r.high_mark
        row.channels = r.channels
        row.ack_timeout_sec = r.ack_timeout_sec
        if before != (r.enabled, r.severity, r.min_confidence, r.high_mark, r.channels, r.ack_timeout_sec):
            changes.append(f"{r.label}:{r.severity}@{r.min_confidence:.2f}")
    house.rules_version += 1
    audit(db, "rules_update", "house", house_id, f"v={house.rules_version} " + " ".join(changes), actor=user.email)
    db.commit()
    return _rules_payload(db, house)


@app.get("/houses/{house_id}/patterns")
def get_patterns(house_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.patterns import pattern_summary

    return pattern_summary(db, _get_house(db, user, house_id))


@app.post("/patterns/run")
async def patterns_run(
    houseId: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.patterns import run_patterns

    if houseId:
        assert_house_access(user, houseId)
    elif user.role != "staff":
        houseId = (house_ids_for(user) or [None])[0]
    alerts = run_patterns(db, houseId)
    for a in alerts:
        await hub.broadcast("alert", alert_dict(db, a))
    return {"created": [alert_dict(db, a) for a in alerts]}


@app.post("/devices/{device_id}/missed")
async def device_missed(
    device_id: str,
    body: DeviceMissedBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """F3.10: device.missed (eventId, lastSeen, offlineSince) from the device manager."""
    from app.patterns import mark_device_missed

    device = db.query(Device).filter(Device.id == device_id).one_or_none()
    if not device:
        raise AppError("not_found", "Unknown device", 404)
    assert_house_access(user, device.house_id)
    offline_since = _parse_iso(body.offlineSince) if body and body.offlineSince else None
    alert = mark_device_missed(
        db, device_id, fast_forward=settings.fast_forward_device and offline_since is None, offline_since=offline_since
    )
    if alert:
        await hub.broadcast("alert", alert_dict(db, alert))
        return {"alert": alert_dict(db, alert)}
    return {"alert": None, "message": "not yet over offline threshold"}


def _model_dict(m: AiModel) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "ollamaTag": m.ollama_tag,
        "promptVersion": m.prompt_version,
        "version": f"{m.ollama_tag}+prompt-{m.prompt_version}",
        "task": "sound-event classification (text description)",
        "status": m.status,
        "avgLatencyMs": m.avg_latency_ms,
        "p95LatencyMs": m.p95_latency_ms,
        "errorRate": m.error_rate,
        "accuracy": m.accuracy,
        "evalProgress": m.eval_progress,
        "isActive": m.is_active,
        "eval": json.loads(m.eval_json) if m.eval_json else None,
    }


def _get_model(db: Session, model_id: str) -> AiModel:
    m = db.query(AiModel).filter(AiModel.id == model_id).one_or_none()
    if not m:
        raise AppError("not_found", "Unknown model", 404)
    return m


@app.get("/ai/models")
def ai_models(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.registry import list_models

    return [_model_dict(m) for m in list_models(db)]


@app.post("/ai/models")
def ai_register(body: RegisterModelBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.registry import register_model

    _require_staff(user)
    return _model_dict(register_model(db, body.tag, body.name, actor=user.email))


@app.get("/ai/ollama/tags")
async def ai_ollama_tags(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.registry import ollama_tags

    known = {m.ollama_tag for m in db.query(AiModel).all()}
    return [{"tag": t, "registered": t in known} for t in await ollama_tags()]


@app.post("/ai/models/sync")
async def ai_sync(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.registry import sync_from_ollama

    _require_staff(user)
    return [_model_dict(m) for m in await sync_from_ollama(db, actor=user.email)]


@app.post("/ai/models/{model_id}/evaluate")
async def evaluate(
    model_id: str,
    wait: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.registry import evaluate_model, start_evaluation

    _require_staff(user)
    m = _get_model(db, model_id)
    if wait:
        return _model_dict(await evaluate_model(db, m, actor=user.email))
    started = start_evaluation(m.id)
    return {"id": m.id, "started": started, "message": "evaluation running" if started else "already running"}


@app.post("/ai/models/{model_id}/deploy")
async def deploy(
    model_id: str,
    body: DeployBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.registry import deploy_model, rollback_model

    _require_staff(user)
    if body.action == "rollback":
        m = await rollback_model(db, actor=user.email)
    elif body.action == "deploy":
        m = await deploy_model(db, _get_model(db, model_id), actor=user.email)
    else:
        raise AppError("validation_error", "action must be deploy | rollback", 422)
    return _model_dict(m)


@app.get("/audit")
def audit_log(
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_staff(user)
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return [
        {"ts": r.ts.isoformat() + "Z", "actor": r.actor, "action": r.action, "entity": r.entity, "entityId": r.entity_id, "detail": r.detail}
        for r in rows
    ]


# --- Simulator (stands in for the home gateway; staff only) -------------------------------

SCENARIOS = {
    "glass": {
        "description": "Sharp crash of shattering glass followed by two seconds of silence",
        "levelDb": 92,
        "deviceId": "D-210",
    },
    "fall": {
        "description": "Heavy thud then a groan as if someone fell in the bathroom",
        "levelDb": 88,
        "deviceId": "D-212",
    },
    "cough": {
        "description": "A dry cough repeated three times",
        "levelDb": 60,
        "deviceId": "D-211",
    },
    "cough_night": {
        "description": "A dry cough repeated three times",
        "levelDb": 60,
        "deviceId": "D-211",
        "localTime": "02:00",
    },
    "ambiguous": {
        "description": "someone is dragging furniture and laughing",
        "levelDb": 55,
        "deviceId": "D-210",
    },
    "fail_loud": {
        "description": "Very loud bang and crash in the kitchen",
        "levelDb": 92,
        "deviceId": "D-210",
        "outage": True,
    },
    "loud_unknown": {
        "description": "zzzz unintelligible sensor burst",
        "levelDb": 92,
        "deviceId": "D-210",
    },
}


def _sim_ts(db: Session, house_id: str, local_time: str | None) -> str:
    now = datetime.now(timezone.utc)
    if not local_time:
        return now.isoformat()
    house = db.query(House).filter(House.id == house_id).one()
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(house.tz)
    h, m = (int(x) for x in local_time.split(":"))
    local = now.astimezone(tz).replace(hour=h, minute=m, second=0, microsecond=0)
    return local.astimezone(timezone.utc).isoformat()


@app.post("/sim/scenario")
async def sim_scenario(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_staff(user)
    spec = SCENARIOS.get(name)
    if not spec:
        raise AppError("not_found", "Unknown scenario", 404)
    payload = {
        "eventId": f"E-{uuid4().hex[:8]}",
        "houseId": "H-1042",
        "deviceId": spec["deviceId"],
        "ts": _sim_ts(db, "H-1042", spec.get("localTime")),
        "description": spec["description"],
        "levelDb": spec["levelDb"],
        "durationSec": 3,
    }
    previous = runtime["ai_outage"]
    if spec.get("outage"):
        runtime["ai_outage"] = True
    try:
        event, duplicate = await ingest_event(db, payload, wait=True)
    finally:
        runtime["ai_outage"] = previous
    return _event_result(event, duplicate)


@app.post("/sim/custom")
async def sim_custom(body: CustomEventBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_staff(user)
    payload = {
        "eventId": body.eventId or f"E-{uuid4().hex[:8]}",
        "houseId": body.houseId,
        "deviceId": body.deviceId,
        "ts": datetime.now(timezone.utc).isoformat(),
        "description": body.description,
        "levelDb": body.levelDb,
        "durationSec": 3,
    }
    event, duplicate = await ingest_event(db, payload, wait=True)
    return _event_result(event, duplicate)


@app.post("/sim/duplicate")
async def sim_duplicate(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Send the same eventId twice, as a gateway retry would."""
    _require_staff(user)
    payload = {
        "eventId": f"E-dup-{uuid4().hex[:6]}",
        "houseId": "H-1042",
        "deviceId": "D-212",
        "ts": datetime.now(timezone.utc).isoformat(),
        "description": "Someone screaming loudly in the bathroom",
        "levelDb": 90,
        "durationSec": 2,
    }
    first, dup1 = await ingest_event(db, dict(payload), wait=True)
    second, dup2 = await ingest_event(db, dict(payload), wait=True)
    alert = db.query(Alert).filter(Alert.alert_id == first.alert_id).one_or_none()
    return {
        "eventId": payload["eventId"],
        "first": _event_result(first, dup1),
        "second": _event_result(second, dup2),
        "alertId": first.alert_id,
        "alertCount": alert.count if alert else None,
    }


@app.post("/sim/coughs")
async def sim_coughs(count: int = Query(10, ge=1, le=50), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_staff(user)
    last = None
    for _ in range(count):
        payload = {
            "eventId": f"E-{uuid4().hex[:8]}",
            "houseId": "H-1042",
            "deviceId": "D-211",
            "ts": datetime.now(timezone.utc).isoformat(),
            "description": "A dry cough repeated three times",
            "levelDb": 62,
            "durationSec": 1,
        }
        last, _dup = await ingest_event(db, payload, wait=True)
    alert = db.query(Alert).filter(Alert.alert_id == last.alert_id).one_or_none() if last and last.alert_id else None
    return {
        "alertId": last.alert_id if last else None,
        "lastEvent": last.event_id if last else None,
        "label": last.label if last else None,
        "count": alert.count if alert else 0,
    }


@app.post("/sim/outage")
def sim_outage(body: OutageBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Simulate Ollama being down (requests go to a closed port) to demo the fail-loud path."""
    _require_staff(user)
    runtime["ai_outage"] = body.on
    audit(db, "ai_outage", "system", "classifier", "on" if body.on else "off", actor=user.email)
    db.commit()
    return {"aiOutage": runtime["ai_outage"]}


@app.websocket("/live")
async def live(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)


web_dist = PROJECT_ROOT / "web" / "dist"
UI_ROUTES = re.compile(r"^/(alerts(/[^/]+)?|link/[^/]+|rules|models|patterns|ops)?/?$")
if web_dist.exists():

    @app.middleware("http")
    async def spa_fallback(request, call_next):
        """Browser page loads of UI routes (/alerts/A-1, /models...) get index.html, not API JSON."""
        if request.method == "GET" and "text/html" in request.headers.get("accept", "") and UI_ROUTES.match(request.url.path):
            return FileResponse(web_dist / "index.html")
        return await call_next(request)

    app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
