from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.audit import audit
from app.config import settings
from app.errors import AppError
from app.models import ActivityProfile, Alert, Device, House, Room
from app.pipeline import create_alert
from app.rules import deviation_score, pattern_decision, to_local


def local_today(house: House, now: datetime | None = None) -> datetime:
    return to_local(now or datetime.utcnow(), house.tz)


def hourly_counts(db: Session, house_id: str, day: str) -> list[int]:
    rows = (
        db.query(ActivityProfile)
        .filter(ActivityProfile.house_id == house_id, ActivityProfile.day == day)
        .all()
    )
    out = [0] * 24
    for r in rows:
        out[r.hour] = r.count
    return out


def baseline_hours(db: Session, house_id: str, before_day: str) -> tuple[list[float], int]:
    """Mean hourly activity over the baseline days (up to 14) before `before_day`."""
    rows = (
        db.query(ActivityProfile)
        .filter(
            ActivityProfile.house_id == house_id,
            ActivityProfile.day < before_day,
            ActivityProfile.is_baseline == True,  # noqa: E712
        )
        .all()
    )
    days = sorted({r.day for r in rows}, reverse=True)[:14]
    keep = set(days)
    sums = [0.0] * 24
    for r in rows:
        if r.day in keep:
            sums[r.hour] += r.count
    n = len(days)
    if n == 0:
        return [0.0] * 24, 0
    return [s / n for s in sums], n


def pattern_summary(db: Session, house: House, now: datetime | None = None) -> dict:
    local = local_today(house, now)
    today = local.date().isoformat()
    today_h = hourly_counts(db, house.id, today)
    base, days = baseline_hours(db, house.id, today)
    # Compare only the hours that have elapsed so far today, otherwise every morning looks abnormal.
    upto = local.hour + 1
    score = deviation_score(today_h[:upto] + [0] * (24 - upto), base[:upto] + [0.0] * (24 - upto))
    recent = _recent_scores(db, house.id, today, base)
    return {
        "houseId": house.id,
        "day": today,
        "localHour": local.hour,
        "baselineDays": days,
        "today": today_h,
        "baseline": base,
        "deviationScore": score,
        "recentScores": recent,
        "noonReached": local.hour >= 12,
        "noActivityByNoon": sum(today_h[:12]) == 0,
    }


def run_patterns(db: Session, house_id: str | None = None, now: datetime | None = None) -> list[Alert]:
    """F3.8/F3.9: learn today's profile vs the 14-day baseline and apply Table 46."""
    q = db.query(House)
    if house_id:
        q = q.filter(House.id == house_id)
    created: list[Alert] = []
    for house in q.all():
        s = pattern_summary(db, house, now)
        last_two = [r["score"] for r in s["recentScores"][:2]]
        decision = pattern_decision(
            baseline_days=s["baselineDays"],
            no_activity_by_noon=s["noonReached"] and s["noActivityByNoon"],
            high_score_two_days=len(last_two) == 2 and min(last_two) > 0.8,
            score=max([s["deviationScore"], *last_two]),
        )
        audit(db, "pattern_run", "house", house.id, f"decision={decision} score={s['deviationScore']:.2f} days={s['baselineDays']}")
        if decision in ("alert_noon", "alert_routine"):
            kind = "no_activity_by_noon" if decision == "alert_noon" else "routine_changed"
            reason = (
                "Pattern learner: no activity detected by noon (baseline expects morning activity)"
                if kind == "no_activity_by_noon"
                else f"Pattern learner: routine changed — deviation above 0.8 on the last two days (today {s['deviationScore']:.2f})"
            )
            room = db.query(Room).filter(Room.house_id == house.id).first()
            created.append(
                create_alert(
                    db,
                    house,
                    room_id=room.id,
                    alert_type=kind,
                    severity="safety",
                    confidence=round(s["deviationScore"], 2),
                    reason=reason,
                )
            )
        db.commit()
    return created


def _recent_scores(db: Session, house_id: str, today: str, base: list[float]) -> list[dict]:
    """Deviation of each of the last 3 days (most recent first); days with no data are skipped."""
    out = []
    d = datetime.fromisoformat(today).date()
    for i in range(1, 4):
        day = (d - timedelta(days=i)).isoformat()
        hours = hourly_counts(db, house_id, day)
        if sum(hours) == 0:
            continue
        out.append({"day": day, "score": round(deviation_score(hours, base), 3)})
    return out


def mark_device_missed(
    db: Session, device_id: str, *, fast_forward: bool = True, offline_since: datetime | None = None
) -> Alert | None:
    """F3.10: the gateway reports a missed heartbeat. Offline > OFFLINE_MINUTES raises a warning."""
    device = db.query(Device).filter(Device.id == device_id).one_or_none()
    if not device:
        raise AppError("not_found", "Unknown device", 404)
    now = datetime.utcnow()
    if offline_since is not None:
        device.missed_since = offline_since
    elif device.missed_since is None:
        device.missed_since = now
    if fast_forward:
        # Demo: pretend the device has been silent for longer than the threshold.
        device.missed_since = min(device.missed_since, now - timedelta(minutes=settings.offline_minutes + 1))
    elapsed = (now - device.missed_since).total_seconds() / 60
    audit(db, "device_missed", "device", device.id, f"offline {elapsed:.0f} min")
    if elapsed < settings.offline_minutes:
        db.commit()
        return None
    device.active = False
    existing = (
        db.query(Alert)
        .filter(Alert.room_id == device.room_id, Alert.type == "device_offline", Alert.status.in_(["new", "notified", "escalated", "acknowledged"]))
        .first()
    )
    if existing:
        db.commit()
        return existing
    house = db.query(House).filter(House.id == device.house_id).one()
    return create_alert(
        db,
        house,
        room_id=device.room_id,
        alert_type="device_offline",
        severity="info",
        confidence=1.0,
        reason=f"Warning: device {device.id} has been offline for {elapsed:.0f} min (threshold {settings.offline_minutes} min)",
    )
