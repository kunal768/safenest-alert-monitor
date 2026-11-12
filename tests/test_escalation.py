"""Phase 3 'Done when': glass break -> emergency -> SMS+push to priority-1 -> no ack ->
escalate to next contact -> care center -> ack -> resolve, with a short timeout."""

import time

import pytest

from app.config import settings


def _wait_for(client, headers, alert_id, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = client.get(f"/alerts/{alert_id}", headers=headers).json()
        if predicate(a):
            return a
        time.sleep(0.1)
    raise AssertionError(f"condition not met; last state {a['status']} step {a['escalateStep']}")


@pytest.fixture
def short_timeout(monkeypatch):
    monkeypatch.setattr(settings, "escalation_timeout_sec", 1)


def test_escalation_chain_then_ack_and_resolve(client, staff, short_timeout):
    r = client.post("/sim/scenario?name=glass", headers=staff).json()
    alert_id = r["alertId"]
    a = client.get(f"/alerts/{alert_id}", headers=staff).json()
    assert a["severity"] == "emergency" and a["status"] == "notified"
    assert a["nextContact"] == "Carlos Lopez"
    notes = client.get(f"/alerts/{alert_id}/notifications", headers=staff).json()
    assert {(n["channel"], n["contact"]) for n in notes} == {("push", "Maria Lopez"), ("sms", "Maria Lopez")}

    # No acknowledgement: escalates to the priority-2 contact...
    a = _wait_for(client, staff, alert_id, lambda x: x["escalateStep"] >= 1)
    assert a["status"] == "escalated"
    # ...then to the care center, after which the timer stops.
    a = _wait_for(client, staff, alert_id, lambda x: x["escalateStep"] >= 2)
    assert a["escalateRemainingSec"] is None
    contacts = [n["contact"] for n in client.get(f"/alerts/{alert_id}/notifications", headers=staff).json()]
    assert contacts.index("Carlos Lopez") < contacts.index("SafeNest Care Center")

    ack = client.patch(f"/alerts/{alert_id}", json={"action": "acknowledge", "version": a["version"]}, headers=staff)
    assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
    res = client.patch(f"/alerts/{alert_id}", json={"action": "resolve", "version": ack.json()["version"]}, headers=staff)
    assert res.json()["status"] == "resolved"
    actions = [h["action"] for h in client.get(f"/alerts/{alert_id}", headers=staff).json()["history"]]
    assert actions.count("auto_escalate") == 2


def test_acknowledge_stops_escalation(client, staff, short_timeout):
    alert_id = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    a = client.get(f"/alerts/{alert_id}", headers=staff).json()
    client.patch(f"/alerts/{alert_id}", json={"action": "acknowledge", "version": a["version"]}, headers=staff)
    time.sleep(1.5)
    a = client.get(f"/alerts/{alert_id}", headers=staff).json()
    assert a["status"] == "acknowledged" and a["escalateStep"] == 0


def test_manual_escalate_pages_next_contact(client, staff):
    alert_id = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    a = client.get(f"/alerts/{alert_id}", headers=staff).json()
    r = client.post(f"/alerts/{alert_id}/escalate", json={"target": "family", "version": a["version"]}, headers=staff)
    assert r.status_code == 202 and r.json()["alert"]["status"] == "escalated"
    assert r.json()["notificationIds"]
    notes = client.get(f"/alerts/{alert_id}/notifications", headers=staff).json()
    assert any(n["contact"] == "Carlos Lopez" and "escalation" in n["detail"] for n in notes)
    # Table 45: escalate is not allowed from escalated.
    again = client.post(f"/alerts/{alert_id}/escalate", json={"version": r.json()["alert"]["version"]}, headers=staff)
    assert again.status_code == 409


def test_info_alerts_do_not_start_escalation(client, staff):
    a = client.post("/devices/D-210/missed", headers=staff).json()["alert"]
    assert a["severity"] == "info" and a["escalateRemainingSec"] is None


def test_pending_escalations_are_rearmed_after_restart(db):
    from datetime import datetime, timedelta

    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import Alert

    db.add(Alert(alert_id="A-restart", house_id="H-1042", room_id="R-KIT", type="glass_break", severity="emergency",
                 status="notified", escalate_after=datetime.utcnow() - timedelta(seconds=1)))
    db.commit()
    with TestClient(app) as c:  # startup = server restart
        a = _wait_for(c, {"Authorization": "Bearer staff-token"}, "A-restart", lambda x: x["escalateStep"] >= 1)
    assert a["status"] == "escalated"


def test_escalate_targets_care_center_and_emergency(client, staff):
    """L4 target: careCenter jumps straight to the care center; emergency also requests dispatch."""
    a = client.post("/sim/scenario?name=glass", headers=staff).json()
    r = client.post(f"/alerts/{a['alertId']}/escalate", json={"target": "careCenter"}, headers=staff).json()
    assert r["alert"]["escalateStep"] == 2 and r["alert"]["escalateRemainingSec"] is None
    b = client.post("/sim/scenario?name=fall", headers=staff).json()
    r = client.post(f"/alerts/{b['alertId']}/escalate", json={"target": "emergency"}, headers=staff).json()
    notes = client.get(f"/alerts/{b['alertId']}/notifications", headers=staff).json()
    assert any(n["channel"] == "call" and "emergency services" in n["detail"] for n in notes)
    bad = client.post(f"/alerts/{b['alertId']}/escalate", json={"target": "police"}, headers=staff)
    assert bad.status_code == 400


def test_care_center_link_opens_only_that_alert_and_may_only_acknowledge(client, staff):
    a = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    other = client.post("/sim/scenario?name=fall", headers=staff).json()["alertId"]
    client.post(f"/alerts/{a}/escalate", json={"target": "careCenter"}, headers=staff)
    link = next(n["link"] for n in client.get(f"/alerts/{a}/notifications", headers=staff).json() if n["link"])
    token = link.split("t=")[1]
    detail = client.get(f"/alerts/{a}?link={token}")  # no bearer token
    assert detail.status_code == 200 and detail.json()["viewer"] == "link"
    assert client.get(f"/alerts/{other}?link={token}").status_code == 401
    assert client.get("/alerts", params={"link": token}).status_code == 401
    v = detail.json()["version"]
    assert client.patch(f"/alerts/{a}?link={token}", json={"action": "falseAlarm", "version": v}).status_code == 403
    ack = client.patch(f"/alerts/{a}?link={token}", json={"action": "acknowledge", "version": v, "note": "On our way"})
    assert ack.status_code == 200 and ack.json()["ackBy"] == "care-center (alert link)"


def test_ack_note_is_kept_in_history(client, staff):
    a = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    v = client.get(f"/alerts/{a}", headers=staff).json()["version"]
    client.patch(f"/alerts/{a}", json={"action": "acknowledge", "version": v, "note": "Called, she is fine"}, headers=staff)
    history = client.get(f"/alerts/{a}", headers=staff).json()["history"]
    assert "note: Called, she is fine" in history[-1]["detail"]


def test_house_ack_timeout_drives_escalation(client, staff, db, monkeypatch):
    """F3.6: the rule's ackTimeoutSec is used, capped by ESCALATION_TIMEOUT_SEC."""
    from app.models import AlertRule

    monkeypatch.setattr(settings, "escalation_timeout_sec", 600)
    a = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    assert 115 <= client.get(f"/alerts/{a}", headers=staff).json()["escalateRemainingSec"] <= 120
    rule = db.query(AlertRule).filter(AlertRule.house_id == "H-1042", AlertRule.label == "fall").one()
    assert rule.ack_timeout_sec == 120
