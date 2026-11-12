from app.models import Alert, House
from app.notify import plan_and_notify
from app.rules import CHANNEL_FALLBACK, next_fallback_channel


def test_all_channels_fail_records_audit(db):
    house = db.query(House).filter(House.id == "H-1042").one()
    alert = Alert(
        alert_id="A-fail",
        house_id="H-1042",
        room_id="R-KIT",
        type="glass_break",
        severity="emergency",
        status="new",
    )
    db.add(alert)
    db.flush()
    notes = plan_and_notify(db, alert, house, force_fail=True)
    db.commit()
    assert notes
    assert all(n.status == "failed" for n in notes if n.channel != "dashboard")
    channels = [n.channel for n in notes]
    assert "push" in channels
    # fallback continues toward sms/email
    assert any(c in channels for c in CHANNEL_FALLBACK)
    from app.models import AuditLog

    assert db.query(AuditLog).filter(AuditLog.action == "notify_failed").count() >= 1


def test_fallback_chain_complete():
    assert next_fallback_channel("push") == "sms"
    assert next_fallback_channel("sms") == "email"
    assert next_fallback_channel("email") is None


def _glass_alert(db, alert_id="A-fb"):
    alert = Alert(
        alert_id=alert_id,
        house_id="H-1042",
        room_id="R-KIT",
        type="glass_break",
        severity="emergency",
        status="new",
    )
    db.add(alert)
    db.flush()
    return alert


def test_push_and_sms_fail_falls_back_to_email(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "notify_fail_channels", "push,sms")
    house = db.query(House).filter(House.id == "H-1042").one()
    notes = plan_and_notify(db, _glass_alert(db), house)
    assert [(n.channel, n.status) for n in notes] == [("push", "failed"), ("sms", "failed"), ("email", "sent")]


def test_safety_push_failure_falls_back_to_sms(db, monkeypatch):
    from datetime import datetime

    from app.config import settings

    monkeypatch.setattr(settings, "notify_fail_channels", "push")
    house = db.query(House).filter(House.id == "H-1042").one()
    alert = _glass_alert(db, "A-safety")
    alert.severity = "safety"
    noon_utc = datetime(2026, 11, 11, 20, 0)  # 12:00 in Los Angeles: daytime
    notes = plan_and_notify(db, alert, house, now=noon_utc)
    assert [(n.channel, n.status) for n in notes] == [("push", "failed"), ("sms", "sent")]


def test_all_channels_fail_escalates_immediately(client, staff, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "notify_fail_channels", "push,sms,email")
    alert_id = client.post("/sim/scenario?name=glass", headers=staff).json()["alertId"]
    a = client.get(f"/alerts/{alert_id}", headers=staff).json()
    assert a["status"] == "escalated"
    # Every contact failed, so the chain runs straight through to the care center.
    assert a["escalateStep"] == 2
    contacts = {n["contact"] for n in client.get(f"/alerts/{alert_id}/notifications", headers=staff).json()}
    assert contacts == {"Maria Lopez", "Carlos Lopez", "SafeNest Care Center"}


def test_inactive_subscription_is_dashboard_only(db):
    house = db.query(House).filter(House.id == "H-1042").one()
    house.subscription_active = False
    notes = plan_and_notify(db, _glass_alert(db, "A-sub"), house)
    assert [n.channel for n in notes] == ["dashboard"]


def test_rule_channels_limit_paging(db):
    from app.models import AlertRule

    rule = db.query(AlertRule).filter(AlertRule.house_id == "H-1042", AlertRule.label == "glass_break").one()
    rule.channels = "sms"
    house = db.query(House).filter(House.id == "H-1042").one()
    notes = plan_and_notify(db, _glass_alert(db, "A-ch"), house)
    assert [n.channel for n in notes] == ["sms"]
