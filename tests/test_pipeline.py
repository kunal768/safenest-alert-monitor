
from app.models import Alert, Event


def test_dedupe_same_event_id(client, staff, db):
    payload = {
        "eventId": "E-1001",
        "houseId": "H-1042",
        "deviceId": "D-210",
        "description": "Sharp crash of shattering glass followed by two seconds of silence",
        "levelDb": 92,
        "durationSec": 3,
    }
    a = client.post("/events", json=payload, headers=staff)
    b = client.post("/events", json=payload, headers=staff)
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json()["alertId"] == b.json()["alertId"]
    assert db.query(Event).filter(Event.event_id == "E-1001").count() == 1
    alerts = db.query(Alert).filter(Alert.type == "glass_break").all()
    assert len(alerts) == 1
    assert alerts[0].count == 1


def test_merge_ten_coughs_one_alert(client, staff, db):
    r = client.post("/sim/coughs?count=10", headers=staff)
    assert r.status_code == 200
    alert_id = r.json()["alertId"]
    alert = db.query(Alert).filter(Alert.alert_id == alert_id).one()
    assert alert.count == 10
    assert db.query(Alert).filter(Alert.type == "cough").count() == 1


def test_glass_break_notifies_priority_one(client, staff, db):
    r = client.post("/sim/scenario?name=glass", headers=staff)
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "glass_break"
    alert_id = body["alertId"]
    notes = client.get(f"/alerts/{alert_id}/notifications", headers=staff).json()
    channels = {n["channel"] for n in notes if n["status"] == "sent"}
    assert "sms" in channels and "push" in channels
    assert any(n["contact"] == "Maria Lopez" for n in notes)


def test_lifecycle_ack_resolve_and_illegal_409(client, staff, db):
    r = client.post("/sim/scenario?name=glass", headers=staff)
    alert_id = r.json()["alertId"]
    detail = client.get(f"/alerts/{alert_id}", headers=staff).json()
    version = detail["version"]
    bad = client.patch(
        f"/alerts/{alert_id}",
        json={"action": "resolve", "version": version},
        headers=staff,
    )
    assert bad.status_code == 409
    ack = client.patch(
        f"/alerts/{alert_id}",
        json={"action": "acknowledge", "version": version},
        headers=staff,
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"
    resolved = client.patch(
        f"/alerts/{alert_id}",
        json={"action": "resolve", "version": ack.json()["version"]},
        headers=staff,
    )
    assert resolved.json()["status"] == "resolved"
    again = client.patch(
        f"/alerts/{alert_id}",
        json={"action": "acknowledge", "version": resolved.json()["version"]},
        headers=staff,
    )
    assert again.status_code == 409


def test_fail_loud_warning(client, staff, db):
    r = client.post("/sim/scenario?name=loud_unknown", headers=staff)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unclassified"
    assert body["alertId"]
    alert = db.query(Alert).filter(Alert.alert_id == body["alertId"]).one()
    assert alert.type == "unclassified"
    assert alert.severity in ("info", "safety")


def test_owner_house_scoping(client, owner, staff):
    client.post("/sim/scenario?name=glass", headers=staff)
    # create a chen-house event as staff
    r = client.post(
        "/events",
        json={
            "eventId": "E-chen-1",
            "houseId": "H-2001",
            "deviceId": "D-300",
            "description": "Sharp crash of shattering glass followed by two seconds of silence",
            "levelDb": 90,
        },
        headers=staff,
    )
    assert r.status_code == 200
    owner_list = client.get("/alerts", headers=owner).json()["items"]
    assert all(a["houseId"] == "H-1042" for a in owner_list)
    denied = client.get(f"/alerts/{r.json()['alertId']}", headers=owner)
    assert denied.status_code == 403


def test_quiet_hours_suppress_cough(client, staff, db):
    rules = client.get("/houses/H-1042/alert-rules", headers=staff).json()
    for row in rules["rules"]:
        if row["label"] == "cough":
            row["minConfidence"] = 0.85
    put = client.put(
        "/houses/H-1042/alert-rules",
        json={
            "version": rules["version"],
            "quietHoursEnabled": True,
            "quietStart": "22:00",
            "quietEnd": "07:00",
            "rules": rules["rules"],
        },
        headers=staff,
    )
    assert put.status_code == 200
    r = client.post("/sim/scenario?name=cough_night", headers=staff)
    assert r.status_code == 200
    assert r.json()["alertId"] is None


def test_cannot_disable_emergency_rule(client, staff):
    rules = client.get("/houses/H-1042/alert-rules", headers=staff).json()
    for row in rules["rules"]:
        if row["label"] == "fall":
            row["enabled"] = False
    put = client.put(
        "/houses/H-1042/alert-rules",
        json={"version": rules["version"], "rules": rules["rules"]},
        headers=staff,
    )
    assert put.status_code == 400
    assert put.json()["error"] == "service_limit"


def test_pattern_job_no_activity(client, staff, db):
    r = client.post("/patterns/run?houseId=H-1042", headers=staff)
    assert r.status_code == 200
    created = r.json()["created"]
    assert created
    assert any(a["type"] in ("no_activity_by_noon", "routine_changed") for a in created)


def test_device_missed_warning(client, staff, db):
    r = client.post("/devices/D-210/missed", headers=staff)
    assert r.status_code == 200
    assert r.json()["alert"]["type"] == "device_offline"


def test_audit_on_status_and_rules(client, staff, db):
    from app.models import AuditLog

    client.post("/sim/scenario?name=glass", headers=staff)
    rules = client.get("/houses/H-1042/alert-rules", headers=staff).json()
    client.put(
        "/houses/H-1042/alert-rules",
        json={"version": rules["version"], "rules": rules["rules"], "quietHoursEnabled": False},
        headers=staff,
    )
    actions = {a.action for a in db.query(AuditLog).all()}
    assert "create" in actions
    assert "rules_update" in actions


def test_duplicate_flag_and_no_double_count(client, staff, db):
    r = client.post("/sim/duplicate", headers=staff).json()
    assert r["first"]["duplicate"] is False
    assert r["second"]["duplicate"] is True
    assert r["first"]["alertId"] == r["second"]["alertId"]
    assert r["alertCount"] == 1


def test_merge_does_not_cross_rooms(client, staff, db):
    client.post("/sim/scenario?name=glass", headers=staff)  # kitchen
    client.post(
        "/events",
        json={"eventId": "E-bed-glass", "houseId": "H-1042", "deviceId": "D-211", "description": "glass shattering", "levelDb": 90},
        headers=staff,
    )
    assert db.query(Alert).filter(Alert.type == "glass_break").count() == 2


def test_merge_window_expired_creates_new_alert(client, staff, db):
    from datetime import timedelta

    first = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    a = db.query(Alert).filter(Alert.alert_id == first).one()
    a.created_at = a.created_at - timedelta(minutes=11)
    db.commit()
    second = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    assert second != first


def test_unknown_device_and_wrong_house_rejected(client, staff):
    bad = client.post("/events", json={"eventId": "E-x", "houseId": "H-1042", "deviceId": "D-999", "description": "x"}, headers=staff)
    assert bad.status_code == 404 and bad.json()["error"] == "not_found"
    wrong = client.post("/events", json={"eventId": "E-y", "houseId": "H-1042", "deviceId": "D-300", "description": "x"}, headers=staff)
    assert wrong.status_code == 422


def test_ambiguous_text_is_ignored_as_normal(client, staff, db):
    r = client.post("/sim/scenario?name=ambiguous", headers=staff).json()
    assert r["label"] == "normal" and r["alertId"] is None and r["reason"]


def test_fail_loud_with_simulated_outage(client, staff, db):
    r = client.post("/sim/scenario?name=fail_loud", headers=staff).json()
    assert r["status"] == "unclassified"
    alert = db.query(Alert).filter(Alert.alert_id == r["alertId"]).one()
    assert alert.type == "unclassified" and "92 dB" in alert.reason


def test_quiet_unclassified_event_raises_nothing(client, staff):
    client.post("/sim/outage", json={"on": True}, headers=staff)
    r = client.post("/sim/custom", json={"description": "soft rustle", "levelDb": 50}, headers=staff).json()
    assert r["status"] == "unclassified" and r["alertId"] is None
    client.post("/sim/outage", json={"on": False}, headers=staff)
    r = client.post("/sim/custom", json={"description": "glass shattering", "levelDb": 90}, headers=staff).json()
    assert r["status"] == "classified"


def test_quiet_hours_use_house_local_time(client, staff, db):
    """23:30 in Los Angeles is quiet hours even though it is 07:30 UTC."""
    rules = client.get("/houses/H-1042/alert-rules", headers=staff).json()
    client.put("/houses/H-1042/alert-rules", json={**rules, "quietHoursEnabled": True}, headers=staff)
    night = client.post(
        "/events",
        json={"eventId": "E-n1", "houseId": "H-1042", "deviceId": "D-211", "ts": "2026-11-11T07:30:00Z", "description": "dry cough", "levelDb": 60},
        headers=staff,
    ).json()
    day = client.post(
        "/events",
        json={"eventId": "E-d1", "houseId": "H-1042", "deviceId": "D-211", "ts": "2026-11-11T20:00:00Z", "description": "dry cough", "levelDb": 60},
        headers=staff,
    ).json()
    assert night["alertId"] is None
    assert day["alertId"] is not None


def test_rules_version_conflict_and_validation(client, staff):
    rules = client.get("/houses/H-1042/alert-rules", headers=staff).json()
    ok = client.put("/houses/H-1042/alert-rules", json=rules, headers=staff)
    assert ok.status_code == 200
    stale = client.put("/houses/H-1042/alert-rules", json=rules, headers=staff)
    assert stale.status_code == 409 and "Reload" in stale.json()["message"]
    fresh = ok.json()
    bad_time = client.put("/houses/H-1042/alert-rules", json={**fresh, "quietStart": "25:00"}, headers=staff)
    assert bad_time.status_code == 422
    low = [dict(r, minConfidence=0.3) if r["label"] == "cough" else r for r in fresh["rules"]]
    floor = client.put("/houses/H-1042/alert-rules", json={**fresh, "rules": low}, headers=staff)
    assert floor.status_code == 400 and "0.5" in floor.json()["message"]


def test_owner_cannot_touch_other_house_or_simulator(client, owner):
    assert client.get("/houses/H-2001/alert-rules", headers=owner).status_code == 403
    assert client.post("/sim/scenario?name=glass", headers=owner).status_code == 403
    assert client.get("/houses", headers=owner).json()[0]["id"] == "H-1042"
    assert client.get("/alerts", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_alert_paging(client, staff):
    for _ in range(3):
        client.post("/sim/scenario?name=glass", headers=staff)
        client.post("/devices/D-210/missed", headers=staff)
        client.post("/sim/scenario?name=fall", headers=staff)
    page = client.get("/alerts?size=2&page=1", headers=staff).json()
    assert page["size"] == 2 and len(page["items"]) == 2 and page["total"] >= 3


def test_clip_token_signed_and_expiring(client, staff):
    from app.pipeline import clip_token, verify_clip_token

    alert_id = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    a = client.get(f"/alerts/{alert_id}", headers=staff).json()
    r = client.get(f"/alerts/{alert_id}/clip?token={a['clipToken']}")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert client.get(f"/alerts/{alert_id}/clip?token=1.bad").status_code == 401
    assert not verify_clip_token(alert_id, clip_token(alert_id, exp=1))


def test_event_updates_today_activity_profile(client, staff):
    before = sum(client.get("/houses/H-1042/patterns", headers=staff).json()["today"])
    client.post("/sim/scenario?name=ambiguous", headers=staff)
    after = sum(client.get("/houses/H-1042/patterns", headers=staff).json()["today"])
    assert after == before + 1


def test_open_alerts_and_time_filters(client, staff, owner):
    client.post("/sim/scenario?name=glass", headers=staff)
    opened = client.get("/alerts/open?houseIds=H-1042", headers=staff).json()["items"]
    assert opened and all(a["status"] in ("new", "notified", "escalated", "acknowledged") for a in opened)
    assert client.get("/alerts/open?houseIds=H-2001", headers=owner).status_code == 403
    assert client.get("/alerts?from=2000-01-01T00:00:00Z", headers=staff).json()["total"] >= 1
    assert client.get("/alerts?to=2000-01-01T00:00:00Z", headers=staff).json()["total"] == 0
    assert client.get("/alerts?from=yesterday", headers=staff).status_code == 400


def test_device_missed_payload_from_device_manager(client, staff):
    from datetime import datetime, timedelta, timezone

    recent = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    r = client.post("/devices/D-212/missed", json={"eventId": "M-1", "offlineSince": recent}, headers=staff).json()
    assert r["alert"] is None  # only 3 minutes offline
    old = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
    r = client.post("/devices/D-212/missed", json={"eventId": "M-2", "offlineSince": old}, headers=staff).json()
    assert r["alert"]["type"] == "device_offline"


def test_rule_ack_timeout_limits(client, staff):
    rules = client.get("/houses/H-1042/alert-rules", headers=staff).json()
    assert {r["label"]: r["ackTimeoutSec"] for r in rules["rules"]}["cough"] == 600
    bad = [dict(r, ackTimeoutSec=900) if r["label"] == "fall" else r for r in rules["rules"]]
    put = client.put("/houses/H-1042/alert-rules", json={**rules, "rules": bad}, headers=staff)
    assert put.status_code == 400 and "300" in put.json()["message"]
