import asyncio

import pytest

from app.config import runtime
from app.errors import AppError
from app.models import AiModel, Alert, AuditLog
from app.registry import deploy_model, evaluate_model, health_check, register_model


def _models(client, staff):
    return {m["id"]: m for m in client.get("/ai/models", headers=staff).json()}


def test_evaluate_mock_stores_accuracy_confusion_and_per_label(db):
    m = db.query(AiModel).filter(AiModel.id == "M-B").one()
    m = asyncio.run(evaluate_model(db, m))
    assert m.eval_progress == 100
    assert 0.5 < m.accuracy <= 1.0
    import json

    data = json.loads(m.eval_json)
    assert set(data["perLabel"]) >= {"glass_break", "fall", "normal"}
    assert data["confusion"]["glass_break"]["glass_break"] >= 2
    assert data["items"] == 40


def test_evaluate_endpoint_runs_in_background(client, staff):
    r = client.post("/ai/models/M-B/evaluate", headers=staff)
    assert r.status_code == 200
    assert r.json()["started"] is True
    import time

    for _ in range(50):
        m = _models(client, staff)["M-B"]
        if m["evalProgress"] == 100 and m["eval"]:
            break
        time.sleep(0.05)
    assert m["evalProgress"] == 100
    assert m["accuracy"] is not None


def test_deploy_b_then_rollback_to_a(client, staff, db):
    dep = client.post("/ai/models/M-B/deploy", json={"action": "deploy"}, headers=staff)
    assert dep.status_code == 200
    assert dep.json()["status"] == "Active" and dep.json()["isActive"] is True
    models = _models(client, staff)
    assert models["M-A"]["status"] == "Retired" and models["M-A"]["isActive"] is False

    # New traffic uses the new model.
    ev = client.post("/sim/scenario?name=glass", headers=staff).json()
    assert ev["modelVersion"] == "qwen2.5:3b"

    rb = client.post("/ai/models/M-B/deploy", json={"action": "rollback"}, headers=staff)
    assert rb.json()["id"] == "M-A" and rb.json()["isActive"] is True
    assert _models(client, staff)["M-B"]["status"] == "Retired"
    actions = [a.action for a in db.query(AuditLog).filter(AuditLog.entity == "ai_model")]
    assert "deploy" in actions and "rollback" in actions


def test_broken_tag_fails_and_old_model_keeps_serving(client, staff):
    reg = client.post("/ai/models", json={"tag": "llama9:70b", "name": "Broken"}, headers=staff)
    assert reg.status_code == 200 and reg.json()["status"] == "Registered"
    fail = client.post(f"/ai/models/{reg.json()['id']}/deploy", json={"action": "deploy"}, headers=staff)
    assert fail.json()["status"] == "Failed"
    models = _models(client, staff)
    assert models["M-A"]["isActive"] is True and models["M-A"]["status"] == "Active"
    ev = client.post("/sim/scenario?name=glass", headers=staff).json()
    assert ev["modelVersion"] == "llama3.2:3b"


def test_health_check_requires_valid_json_from_warmup(db):
    runtime["ai_outage"] = True
    ok, detail = asyncio.run(health_check("llama3.2:3b"))
    assert not ok and "unreachable" in detail.lower()
    runtime["ai_outage"] = False
    assert asyncio.run(health_check("llama3.2:3b"))[0]
    assert not asyncio.run(health_check("missing:1b"))[0]


def test_cannot_deploy_active_model_or_from_illegal_state(db):
    a = db.query(AiModel).filter(AiModel.id == "M-A").one()
    with pytest.raises(AppError) as exc:
        asyncio.run(deploy_model(db, a))
    assert exc.value.status_code == 409
    b = db.query(AiModel).filter(AiModel.id == "M-B").one()
    b.status = "Deploying"
    db.commit()
    with pytest.raises(AppError):
        asyncio.run(deploy_model(db, b))


def test_register_duplicate_tag_conflicts(db):
    with pytest.raises(AppError) as exc:
        register_model(db, "qwen2.5:3b")
    assert exc.value.status_code == 409


def test_sync_registers_local_ollama_tags(client, staff):
    new = client.post("/ai/models/sync", headers=staff).json()
    assert [m["ollamaTag"] for m in new] == ["qwen2:7b"]
    tags = client.get("/ai/ollama/tags", headers=staff).json()
    assert all(t["registered"] for t in tags)


def test_rollback_without_retired_model_404(client, staff):
    r = client.post("/ai/models/M-A/deploy", json={"action": "rollback"}, headers=staff)
    assert r.status_code == 404


def test_owner_cannot_manage_models(client, owner):
    assert client.post("/ai/models/M-B/deploy", json={"action": "deploy"}, headers=owner).status_code == 403
    assert client.post("/ai/models/M-B/evaluate", headers=owner).status_code == 403
    assert client.get("/ai/models", headers=owner).status_code == 200


def test_glass_scenario_end_to_end(client, staff, db):
    r = client.post("/sim/scenario?name=glass", headers=staff)
    alert_id = r.json()["alertId"]
    detail = client.get(f"/alerts/{alert_id}", headers=staff).json()
    ack = client.patch(
        f"/alerts/{alert_id}",
        json={"action": "acknowledge", "version": detail["version"]},
        headers=staff,
    )
    res = client.patch(
        f"/alerts/{alert_id}",
        json={"action": "resolve", "version": ack.json()["version"]},
        headers=staff,
    )
    assert res.json()["status"] == "resolved"
    a = db.query(Alert).filter(Alert.alert_id == alert_id).one()
    assert a.status == "resolved"
    history = client.get(f"/alerts/{alert_id}", headers=staff).json()["history"]
    assert [h["action"] for h in history][-2:] == ["acknowledge", "resolve"]
