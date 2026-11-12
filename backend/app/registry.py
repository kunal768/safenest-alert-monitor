from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.classifier import get_adapter, normalize_tag  # noqa: E402

from app.audit import audit
from app.config import settings
from app.errors import AppError
from app.models import AiModel, Event
from app.rules import next_model_status
from app.ws import hub

log = logging.getLogger("safenest.registry")

EVAL_SET = ROOT / "ai" / "eval_set.json"
_running_evals: set[str] = set()


def list_models(db: Session) -> list[AiModel]:
    _refresh_live_metrics(db)
    return db.query(AiModel).order_by(AiModel.id).all()


def live_metrics(db: Session, tag: str, window: int = 200) -> dict | None:
    """Rolling avg/p95 latency and error rate over the most recent production events for a tag."""
    events = (
        db.query(Event)
        .filter(Event.model_version == tag)
        .order_by(Event.ts.desc())
        .limit(window)
        .all()
    )
    if not events:
        return None
    lats = sorted(e.latency_ms or 0 for e in events)
    return {
        "events": len(events),
        "avgLatencyMs": sum(lats) / len(lats),
        "p95LatencyMs": lats[min(len(lats) - 1, int(len(lats) * 0.95))],
        "errorRate": sum(1 for e in events if e.status == "unclassified") / len(events),
    }


def _refresh_live_metrics(db: Session):
    for m in db.query(AiModel).all():
        stats = live_metrics(db, m.ollama_tag)
        if not stats:
            continue
        m.avg_latency_ms = stats["avgLatencyMs"]
        m.p95_latency_ms = stats["p95LatencyMs"]
        m.error_rate = stats["errorRate"]
    db.commit()


def register_model(db: Session, tag: str, name: str | None = None, actor: str = "system") -> AiModel:
    tag = normalize_tag(tag.strip())
    if not tag or tag == ":latest":
        raise AppError("validation_error", "tag is required", 422)
    existing = db.query(AiModel).filter(AiModel.ollama_tag == tag, AiModel.prompt_version == settings.prompt_version).one_or_none()
    if existing:
        raise AppError("conflict", f"{tag} with prompt {settings.prompt_version} is already registered", 409)
    m = AiModel(
        id=f"M-{uuid.uuid4().hex[:6].upper()}",
        name=name or tag,
        ollama_tag=tag,
        prompt_version=settings.prompt_version,
        status="Registered",
        is_active=False,
    )
    db.add(m)
    audit(db, "register", "ai_model", m.id, f"tag={tag}", actor=actor)
    db.commit()
    return m


async def ollama_tags() -> list[str]:
    try:
        return await get_adapter().list_tags()
    except Exception as exc:
        raise AppError("ollama_unavailable", f"Cannot reach Ollama: {exc}", 503) from exc


async def sync_from_ollama(db: Session, actor: str = "system") -> list[AiModel]:
    """Register every local Ollama tag that is not in the registry yet."""
    known = {m.ollama_tag for m in db.query(AiModel).all()}
    return [register_model(db, t, actor=actor) for t in await ollama_tags() if t not in known]


def start_evaluation(model_id: str) -> bool:
    """Run the evaluation in the background; progress is pushed over the WebSocket."""
    if model_id in _running_evals:
        return False
    _running_evals.add(model_id)

    async def _run():
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            m = db.query(AiModel).filter(AiModel.id == model_id).one()
            await evaluate_model(db, m)
        except Exception:
            log.exception("evaluation failed for %s", model_id)
        finally:
            _running_evals.discard(model_id)
            db.close()

    asyncio.get_running_loop().create_task(_run())
    return True


async def evaluate_model(db: Session, model: AiModel, actor: str = "system") -> AiModel:
    adapter = get_adapter()
    items = json.loads(EVAL_SET.read_text())
    labels = sorted({i["label"] for i in items})
    cols = labels + ["unclassified"]
    matrix = {a: {b: 0 for b in cols} for a in labels}
    correct = 0
    errors = 0
    latencies = []
    mistakes = []
    model.eval_progress = 0
    db.commit()
    await hub.broadcast("eval", {"modelId": model.id, "progress": 0})
    for i, item in enumerate(items):
        t0 = time.perf_counter()
        result = await adapter.classify(item["description"], model.ollama_tag)
        latencies.append(result.latency_ms or int((time.perf_counter() - t0) * 1000))
        pred = "unclassified" if result.unclassified else result.label
        errors += result.unclassified
        actual = item["label"]
        if pred == actual:
            correct += 1
        elif len(mistakes) < 8:
            mistakes.append({"description": item["description"], "expected": actual, "got": pred})
        if actual in matrix:
            matrix[actual][pred if pred in cols else "unclassified"] += 1
        model.eval_progress = int((i + 1) / len(items) * 100)
        db.commit()
        await hub.broadcast("eval", {"modelId": model.id, "progress": model.eval_progress})
    n = len(items) or 1
    per_label = {}
    for lab in labels:
        tp = matrix[lab][lab]
        fp = sum(matrix[o][lab] for o in labels if o != lab)
        fn = sum(v for k, v in matrix[lab].items() if k != lab)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        per_label[lab] = {"precision": round(prec, 3), "recall": round(rec, 3)}
    model.accuracy = correct / n
    model.eval_json = json.dumps(
        {
            "labels": labels,
            "columns": cols,
            "confusion": matrix,
            "perLabel": per_label,
            "avgLatencyMs": round(sum(latencies) / len(latencies)) if latencies else 0,
            "errorRate": errors / n,
            "items": len(items),
            "mistakes": mistakes,
        }
    )
    model.eval_progress = 100
    audit(db, "evaluate", "ai_model", model.id, f"accuracy={model.accuracy:.2f} errors={errors}", actor=actor)
    db.commit()
    await hub.broadcast("model", {"id": model.id, "accuracy": model.accuracy})
    return model


def active_model(db: Session) -> AiModel | None:
    return db.query(AiModel).filter(AiModel.is_active == True).one_or_none()  # noqa: E712


async def deploy_model(db: Session, model: AiModel, *, actor: str = "system", action: str = "deploy") -> AiModel:
    """Registered/Retired/Failed -> Deploying -> Active (old Active -> Retired), or Deploying -> Failed.

    Traffic switches only after the health check passes; on failure the current model keeps serving.
    """
    if model.is_active:
        raise AppError("conflict", f"{model.name} is already Active", 409)
    try:
        deploying = next_model_status(model.status, "deploy")
    except ValueError as exc:
        raise AppError("conflict", str(exc), 409) from exc
    current = active_model(db)
    model.status = deploying
    db.commit()
    audit(db, f"{action}_start", "ai_model", model.id, f"tag={model.ollama_tag}", actor=actor)
    await hub.broadcast("model", {"id": model.id, "status": model.status})
    await asyncio.sleep(settings.deploy_step_delay_sec)

    ok, detail = await health_check(model.ollama_tag)
    if not ok:
        model.status = next_model_status(model.status, "fail")
        audit(
            db,
            f"{action}_failed",
            "ai_model",
            model.id,
            f"tag={model.ollama_tag}: {detail}; {current.name if current else 'none'} keeps serving",
            actor=actor,
        )
        db.commit()
        await hub.broadcast("model", {"id": model.id, "status": model.status})
        return model

    if current:
        current.status = next_model_status(current.status, "retire")
        current.is_active = False
    model.status = next_model_status(model.status, "success")
    model.is_active = True
    model.previous_active_id = current.id if current else None
    audit(db, action, "ai_model", model.id, f"tag={model.ollama_tag} replaces {current.ollama_tag if current else 'none'}", actor=actor)
    db.commit()
    await hub.broadcast("model", {"id": model.id, "status": model.status, "active": True})
    return model


async def rollback_model(db: Session, actor: str = "system") -> AiModel:
    """Reactivate the model that was Active before the current one, with the same health check."""
    active = active_model(db)
    prev = None
    if active and active.previous_active_id:
        prev = db.query(AiModel).filter(AiModel.id == active.previous_active_id).one_or_none()
    if prev is None or prev.status != "Retired":
        prev = db.query(AiModel).filter(AiModel.status == "Retired").first()
    if prev is None:
        raise AppError("not_found", "No retired model to roll back to", 404)
    return await deploy_model(db, prev, actor=actor, action="rollback")


async def health_check(tag: str) -> tuple[bool, str]:
    """(1) tag exists in Ollama, (2) warm-up prompt returns valid JSON."""
    adapter = get_adapter()
    try:
        tags = {normalize_tag(t) for t in await adapter.list_tags()}
    except Exception as exc:
        return False, f"Ollama unreachable ({type(exc).__name__})"
    if normalize_tag(tag) not in tags:
        return False, "tag not found in Ollama (run `ollama pull` first)"
    result = await adapter.health_prompt(tag)
    if result.unclassified:
        return False, f"warm-up prompt did not return valid JSON ({result.error})"
    return True, "healthy"


async def warm_up_active():
    """Load the active model into Ollama memory at startup so the first event is not a cold start."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        m = active_model(db)
        tag = m.ollama_tag if m else None
    finally:
        db.close()
    if tag:
        ok, detail = await health_check(tag)
        log.info("warm-up %s: %s", tag, detail if not ok else "ok")
