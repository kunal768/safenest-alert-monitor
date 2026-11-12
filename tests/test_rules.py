from datetime import datetime, timedelta

from app.rules import (
    CLOSED_STATUSES,
    DEFAULT_RULES,
    Rule,
    apply_transition,
    can_transition,
    decide_severity,
    deviation_score,
    in_quiet_hours,
    next_fallback_channel,
    next_model_status,
    notification_plan,
    pattern_decision,
    should_merge,
    validate_rules_limits,
)


def test_table43_emergency_above_high_mark():
    d = decide_severity("glass_break", 0.90, DEFAULT_RULES, now=datetime(2026, 1, 1, 12, 0))
    assert d.action == "create" and d.severity == "emergency"


def test_table43_emergency_below_high_mark_becomes_security():
    d = decide_severity("glass_break", 0.75, DEFAULT_RULES, now=datetime(2026, 1, 1, 12, 0))
    assert d.action == "create" and d.severity == "security"


def test_table43_below_min_confidence_ignored():
    d = decide_severity("cough", 0.50, DEFAULT_RULES, now=datetime(2026, 1, 1, 12, 0))
    assert d.action == "ignore"


def test_table43_no_rule_ignored():
    d = decide_severity("glass_break", 0.9, [], now=datetime(2026, 1, 1, 12, 0))
    assert d.action == "ignore"


def test_table43_normal_ignored():
    d = decide_severity("normal", 0.99, DEFAULT_RULES, now=datetime(2026, 1, 1, 12, 0))
    assert d.action == "ignore"


def test_quiet_hours_suppress_safety_only():
    night = datetime(2026, 1, 1, 2, 0)
    d = decide_severity(
        "cough",
        0.95,
        DEFAULT_RULES,
        now=night,
        quiet_enabled=True,
    )
    assert d.action == "suppress"
    d2 = decide_severity(
        "glass_break",
        0.95,
        DEFAULT_RULES,
        now=night,
        quiet_enabled=True,
    )
    assert d2.action == "create" and d2.severity == "emergency"


def test_in_quiet_hours_wraps_midnight():
    assert in_quiet_hours(datetime(2026, 1, 1, 23, 0), "22:00", "07:00")
    assert in_quiet_hours(datetime(2026, 1, 1, 6, 0), "22:00", "07:00")
    assert not in_quiet_hours(datetime(2026, 1, 1, 12, 0), "22:00", "07:00")


def test_table44_channels():
    assert notification_plan("emergency", night=False, subscription_active=True) == ["push", "sms"]
    assert notification_plan("security", night=True, subscription_active=True) == ["push", "sms"]
    assert notification_plan("safety", night=False, subscription_active=True) == ["push"]
    assert notification_plan("safety", night=True, subscription_active=True) == []
    assert notification_plan("info", night=False, subscription_active=True) == []
    assert notification_plan("emergency", night=False, subscription_active=False) == []


def test_channel_fallback_order():
    assert next_fallback_channel("push") == "sms"
    assert next_fallback_channel("sms") == "email"
    assert next_fallback_channel("email") is None


def test_table45_legal_and_illegal():
    assert can_transition("new", "acknowledge")
    assert can_transition("notified", "acknowledge")
    assert can_transition("escalated", "acknowledge")
    assert can_transition("acknowledged", "resolve")
    assert not can_transition("new", "resolve")
    assert can_transition("notified", "falseAlarm")
    assert can_transition("escalated", "falseAlarm")
    assert can_transition("acknowledged", "falseAlarm")
    assert not can_transition("new", "falseAlarm")
    assert can_transition("new", "escalate")
    assert can_transition("notified", "escalate")
    assert can_transition("acknowledged", "escalate")
    assert not can_transition("escalated", "escalate")
    for closed in CLOSED_STATUSES:
        assert not can_transition(closed, "acknowledge")
        assert not can_transition(closed, "resolve")
    assert apply_transition("acknowledged", "resolve") == "resolved"


def test_merge_window_same_type_room():
    now = datetime(2026, 1, 1, 12, 10)
    created = datetime(2026, 1, 1, 12, 5)
    assert should_merge(
        existing_type="cough",
        existing_room="R-BED",
        existing_status="notified",
        existing_created=created,
        new_type="cough",
        new_room="R-BED",
        now=now,
        window_min=10,
    )
    assert not should_merge(
        existing_type="cough",
        existing_room="R-BED",
        existing_status="resolved",
        existing_created=created,
        new_type="cough",
        new_room="R-BED",
        now=now,
    )
    assert not should_merge(
        existing_type="cough",
        existing_room="R-BED",
        existing_status="new",
        existing_created=now - timedelta(minutes=11),
        new_type="cough",
        new_room="R-BED",
        now=now,
    )
    assert not should_merge(
        existing_type="cough",
        existing_room="R-BED",
        existing_status="new",
        existing_created=created,
        new_type="fall",
        new_room="R-BED",
        now=now,
    )


def test_pattern_table46():
    assert pattern_decision(baseline_days=6, no_activity_by_noon=True, high_score_two_days=True, score=0.9) == "skip"
    assert pattern_decision(baseline_days=7, no_activity_by_noon=True, high_score_two_days=False, score=0.1) == "alert_noon"
    assert pattern_decision(baseline_days=7, no_activity_by_noon=False, high_score_two_days=True, score=0.9) == "alert_routine"
    assert pattern_decision(baseline_days=14, no_activity_by_noon=False, high_score_two_days=False, score=0.9) == "learn"


def test_deviation_score_range():
    base = [1.0] * 24
    today = [1] * 24
    assert deviation_score(today, base) == 0
    today2 = [0] * 24
    s = deviation_score(today2, base)
    assert 0 <= s <= 1


def test_rules_limits():
    bad = [Rule("fall", False, "emergency", 0.8)]
    assert validate_rules_limits(bad)
    low = [Rule("cough", True, "safety", 0.4)]
    assert validate_rules_limits(low)
    ok = DEFAULT_RULES
    assert validate_rules_limits(ok) is None


def test_model_state_machine():
    assert next_model_status("Registered", "deploy") == "Deploying"
    assert next_model_status("Deploying", "success") == "Active"
    assert next_model_status("Deploying", "fail") == "Failed"
    assert next_model_status("Active", "retire") == "Retired"
    try:
        next_model_status("Active", "fail")
        assert False
    except ValueError:
        pass


def test_rules_limits_channels_and_severity():
    assert validate_rules_limits([Rule("fall", True, "emergency", 0.8, 0.85, "")]) == "emergency rules need at least one channel"
    assert "unknown severity" in validate_rules_limits([Rule("fall", True, "critical", 0.8)])
    assert validate_rules_limits([Rule("cough", True, "safety", 0.8, 0.85, "")]) is None


def test_model_state_machine_rejects_illegal():
    import pytest

    for current, action in [("Registered", "success"), ("Active", "deploy"), ("Deploying", "deploy"), ("Retired", "fail")]:
        with pytest.raises(ValueError):
            next_model_status(current, action)
    assert next_model_status("Retired", "deploy") == "Deploying"
    assert next_model_status("Failed", "deploy") == "Deploying"


def test_to_local_converts_utc_to_house_time():
    from app.rules import to_local

    assert to_local(datetime(2026, 11, 11, 7, 30), "America/Los_Angeles") == datetime(2026, 11, 10, 23, 30)
