"""Table 46 against seeded data: 14 baseline days, yesterday and the day before abnormal."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import ActivityProfile
from app.patterns import run_patterns


def _utc_at_local(hour: int) -> datetime:
    tz = ZoneInfo("America/Los_Angeles")
    local = datetime.now(timezone.utc).astimezone(tz).replace(hour=hour, minute=30)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def test_no_activity_by_noon_after_noon(db):
    alerts = run_patterns(db, "H-1042", now=_utc_at_local(13))
    assert [a.type for a in alerts] == ["no_activity_by_noon"]
    assert alerts[0].severity == "safety"


def test_routine_changed_in_the_morning(db):
    # Before noon the noon rule does not apply; two high-deviation days do.
    alerts = run_patterns(db, "H-1042", now=_utc_at_local(9))
    assert [a.type for a in alerts] == ["routine_changed"]


def test_no_pattern_alert_with_fewer_than_7_baseline_days(db):
    days = sorted({r.day for r in db.query(ActivityProfile).filter(ActivityProfile.is_baseline == True)})  # noqa: E712
    for day in days[:-6]:
        db.query(ActivityProfile).filter(ActivityProfile.day == day).delete()
    db.commit()
    assert run_patterns(db, "H-1042", now=_utc_at_local(13)) == []


def test_normal_day_only_learns(db):
    typical = [0, 0, 0, 0, 0, 1, 2, 4, 5, 4, 3, 4, 5, 3, 3, 4, 5, 6, 4, 3, 2, 1, 0, 0]
    now = _utc_at_local(13)
    today = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).date()
    for d in (0, 1, 2):
        day = (today - timedelta(days=d)).isoformat()
        db.query(ActivityProfile).filter(ActivityProfile.day == day).delete()
        for hour, count in enumerate(typical):
            db.add(ActivityProfile(house_id="H-1042", day=day, hour=hour, count=count, is_baseline=False))
    db.commit()
    assert run_patterns(db, "H-1042", now=now) == []


def test_pattern_endpoint_shape(client, staff):
    p = client.get("/houses/H-1042/patterns", headers=staff).json()
    assert p["baselineDays"] == 14
    assert len(p["today"]) == 24 and len(p["baseline"]) == 24
    assert all(s["score"] > 0.8 for s in p["recentScores"][:2])
