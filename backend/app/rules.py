"""Pure decision functions for Tables 43–46."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

LABELS = [
    "fall",
    "scream",
    "glass_break",
    "door_open",
    "cough",
    "call_for_help",
    "unknown_voice",
    "long_silence",
    "normal",
]

SEVERITIES = ["emergency", "security", "safety", "info"]
CLOSED_STATUSES = {"resolved", "false_alarm"}
OPEN_STATUSES = {"new", "notified", "escalated", "acknowledged"}

# Table 45: from_status -> allowed actions
TRANSITIONS = {
    "acknowledge": {"new", "notified", "escalated"},
    "resolve": {"acknowledged"},
    "falseAlarm": {"notified", "escalated", "acknowledged"},
    "escalate": {"new", "notified", "acknowledged"},
}

ACTION_TO_STATUS = {
    "acknowledge": "acknowledged",
    "resolve": "resolved",
    "falseAlarm": "false_alarm",
    "escalate": "escalated",
}


@dataclass(frozen=True)
class Rule:
    label: str
    enabled: bool
    severity: str
    min_confidence: float
    high_mark: float = 0.85
    channels: str = "push,sms,email"
    ack_timeout_sec: int = 300


@dataclass(frozen=True)
class Decision:
    action: Literal["create", "ignore", "suppress"]
    severity: str | None
    reason: str


def to_local(ts_utc: datetime, tz: str) -> datetime:
    """Naive UTC (as stored) -> naive house-local time for quiet hours and patterns."""
    aware = ts_utc.replace(tzinfo=timezone.utc) if ts_utc.tzinfo is None else ts_utc
    return aware.astimezone(ZoneInfo(tz)).replace(tzinfo=None)


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    t = now.time()
    s = parse_hhmm(start)
    e = parse_hhmm(end)
    if s <= e:
        return s <= t < e
    return t >= s or t < e


def decide_severity(
    label: str,
    confidence: float,
    rules: Iterable[Rule],
    *,
    now: datetime,
    quiet_start: str = "22:00",
    quiet_end: str = "07:00",
    quiet_enabled: bool = False,
) -> Decision:
    """Table 43 + quiet hours (info/safety only)."""
    if label == "normal":
        return Decision("ignore", None, "normal activity")
    rule_map = {r.label: r for r in rules}
    rule = rule_map.get(label)
    if rule is None or not rule.enabled:
        return Decision("ignore", None, "no matching rule")
    if confidence < rule.min_confidence:
        return Decision("ignore", None, "below min confidence")

    severity = rule.severity
    if severity == "emergency" and confidence < rule.high_mark:
        severity = "security"

    if quiet_enabled and in_quiet_hours(now, quiet_start, quiet_end) and severity in (
        "info",
        "safety",
    ):
        return Decision("suppress", severity, "quiet hours")

    return Decision("create", severity, "rule matched")


def notification_plan(
    severity: str,
    *,
    night: bool,
    subscription_active: bool,
) -> list[str]:
    """Table 44. Returns channels to attempt, empty = dashboard only."""
    if not subscription_active:
        return []
    if severity in ("emergency", "security"):
        return ["push", "sms"]
    if severity == "safety" and not night:
        return ["push"]
    return []  # safety at night and info → dashboard only


CHANNEL_FALLBACK = ["push", "sms", "email"]


def next_fallback_channel(failed: str) -> str | None:
    try:
        i = CHANNEL_FALLBACK.index(failed)
    except ValueError:
        return None
    if i + 1 < len(CHANNEL_FALLBACK):
        return CHANNEL_FALLBACK[i + 1]
    return None


def can_transition(status: str, action: str) -> bool:
    if status in CLOSED_STATUSES:
        return False
    allowed = TRANSITIONS.get(action, set())
    return status in allowed


def apply_transition(status: str, action: str) -> str:
    if not can_transition(status, action):
        raise ValueError(f"illegal transition {status} -> {action}")
    return ACTION_TO_STATUS[action]


def should_merge(
    *,
    existing_type: str,
    existing_room: str,
    existing_status: str,
    existing_created: datetime,
    new_type: str,
    new_room: str,
    now: datetime,
    window_min: int = 10,
) -> bool:
    if existing_status in CLOSED_STATUSES:
        return False
    if existing_type != new_type or existing_room != new_room:
        return False
    delta = now - existing_created
    return delta.total_seconds() <= window_min * 60


def pattern_decision(
    *,
    baseline_days: int,
    no_activity_by_noon: bool,
    high_score_two_days: bool,
    score: float,
) -> str:
    """Table 46. Returns 'alert_noon' | 'alert_routine' | 'learn' | 'skip'."""
    if baseline_days < 7:
        return "skip"
    if no_activity_by_noon:
        return "alert_noon"
    if high_score_two_days and score > 0.8:
        return "alert_routine"
    return "learn"


def deviation_score(today_hours: list[int], baseline_hours: list[float]) -> float:
    """Normalized L1 deviation vs baseline, clamped to [0, 1]."""
    if not baseline_hours or len(today_hours) != 24 or len(baseline_hours) != 24:
        return 0.0
    total_base = sum(baseline_hours) or 1.0
    diff = sum(abs(t - b) for t, b in zip(today_hours, baseline_hours))
    return min(1.0, diff / (2 * total_base))


DEFAULT_RULES = [
    # PDF Fig. 72 + Sec. IV-D 4.1: fall/scream/glass emergency, door/unknown voice security,
    # cough/call for help/long silence safety. Ack timeouts from Fig. 72 (120 s / 180 s / 600 s).
    Rule("fall", True, "emergency", 0.80, 0.85, ack_timeout_sec=120),
    Rule("scream", True, "emergency", 0.80, 0.85, ack_timeout_sec=120),
    Rule("glass_break", True, "emergency", 0.70, 0.85, ack_timeout_sec=120),
    Rule("door_open", True, "security", 0.75, 0.85, ack_timeout_sec=180),
    Rule("cough", True, "safety", 0.85, 0.85, ack_timeout_sec=600),
    Rule("call_for_help", True, "safety", 0.80, 0.85, ack_timeout_sec=300),
    Rule("unknown_voice", True, "security", 0.80, 0.85, ack_timeout_sec=180),
    Rule("long_silence", True, "safety", 0.80, 0.85, ack_timeout_sec=600),
]


def validate_rules_limits(rules: Iterable[Rule]) -> str | None:
    """Service limits: emergency rules cannot be disabled; min confidence floor 0.5."""
    for r in rules:
        if r.severity not in SEVERITIES:
            return f"unknown severity {r.severity}"
        if r.severity == "emergency" and not r.enabled:
            return "emergency rules cannot be disabled"
        if r.min_confidence < 0.5:
            return "min confidence floor is 0.5"
        if not 0 <= r.high_mark <= 1 or r.min_confidence > 1:
            return "confidence values must be between 0 and 1"
        if r.severity == "emergency" and not [c for c in r.channels.split(",") if c]:
            return "emergency rules need at least one channel"
        if not 10 <= r.ack_timeout_sec <= 3600:
            return "acknowledge timeout must be between 10 and 3600 seconds"
        if r.severity == "emergency" and r.ack_timeout_sec > 300:
            return "emergency acknowledge timeout cannot exceed 300 seconds"
    return None


MODEL_TRANSITIONS = {
    ("Registered", "deploy"): "Deploying",
    ("Deploying", "success"): "Active",
    ("Deploying", "fail"): "Failed",
    ("Active", "retire"): "Retired",
    ("Retired", "deploy"): "Deploying",
    ("Failed", "deploy"): "Deploying",
}


def next_model_status(current: str, action: str) -> str:
    key = (current, action)
    if key not in MODEL_TRANSITIONS:
        raise ValueError(f"illegal model transition {current} / {action}")
    return MODEL_TRANSITIONS[key]
