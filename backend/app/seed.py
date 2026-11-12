from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import ROOT, settings
from app.database import SessionLocal, engine, init_db
from app.models import (
    ActivityProfile,
    AiModel,
    AlertRule,
    Contact,
    Device,
    House,
    Room,
    User,
)
from app.rules import DEFAULT_RULES, to_local


def seed(db: Session | None = None):
    close = False
    if db is None:
        Path(ROOT / "data").mkdir(parents=True, exist_ok=True)
        init_db()
        db = SessionLocal()
        close = True
    try:
        if db.query(User).count():
            return
        _load(db)
        db.commit()
    finally:
        if close:
            db.close()


def reset_and_seed():
    Path(ROOT / "data").mkdir(parents=True, exist_ok=True)
    from app.database import Base
    from app import models  # noqa

    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    try:
        _load(db)
        db.commit()
    finally:
        db.close()


def _load(db: Session):
    h1 = House(
        id="H-1042",
        name="Lopez Home",
        subscription_active=True,
        quiet_start="22:00",
        quiet_end="07:00",
        quiet_hours_enabled=False,
        tz=settings.house_tz,
    )
    h2 = House(
        id="H-2001",
        name="Chen Residence",
        subscription_active=True,
        quiet_start="22:00",
        quiet_end="07:00",
        quiet_hours_enabled=False,
        tz=settings.house_tz,
    )
    db.add_all([h1, h2])
    db.flush()

    rooms = [
        Room(id="R-KIT", house_id="H-1042", name="Kitchen"),
        Room(id="R-BED", house_id="H-1042", name="Bedroom"),
        Room(id="R-BATH", house_id="H-1042", name="Bathroom"),
        Room(id="R-LIV", house_id="H-2001", name="Living Room"),
    ]
    db.add_all(rooms)
    db.flush()

    devices = [
        Device(id="D-210", house_id="H-1042", room_id="R-KIT", type="audio", active=True, last_seen=datetime.utcnow()),
        Device(id="D-211", house_id="H-1042", room_id="R-BED", type="audio", active=True, last_seen=datetime.utcnow()),
        Device(id="D-212", house_id="H-1042", room_id="R-BATH", type="audio", active=True, last_seen=datetime.utcnow()),
        Device(id="D-300", house_id="H-2001", room_id="R-LIV", type="audio", active=True, last_seen=datetime.utcnow()),
    ]
    db.add_all(devices)
    db.flush()

    contacts = [
        Contact(id="C-1", house_id="H-1042", name="Maria Lopez", priority=1, phone="+15550101", email="maria@example.com"),
        Contact(id="C-2", house_id="H-1042", name="Carlos Lopez", priority=2, phone="+15550102", email="carlos@example.com"),
        Contact(id="C-3", house_id="H-1042", name="SafeNest Care Center", priority=3, phone="+15550999", email="care@safenest.local", is_care_center=True),
        Contact(id="C-4", house_id="H-2001", name="Wei Chen", priority=1, phone="+15550201", email="wei@example.com"),
    ]
    db.add_all(contacts)
    db.flush()

    for hid in ("H-1042", "H-2001"):
        for r in DEFAULT_RULES:
            db.add(
                AlertRule(
                    house_id=hid,
                    label=r.label,
                    enabled=r.enabled,
                    severity=r.severity,
                    min_confidence=r.min_confidence,
                    high_mark=r.high_mark,
                    channels=r.channels,
                    ack_timeout_sec=r.ack_timeout_sec,
                )
            )

    db.add_all(
        [
            User(
                id="U-OWNER",
                email="owner@safenest.local",
                role="owner",
                house_ids="H-1042",
                token="owner-token",
                name="Ana Lopez",
            ),
            User(
                id="U-STAFF",
                email="staff@safenest.local",
                role="staff",
                house_ids="",
                token="staff-token",
                name="Jamie Staff",
            ),
        ]
    )

    m_a = AiModel(
        id="M-A",
        name="Llama 3.2 3B",
        ollama_tag="llama3.2:3b",
        prompt_version=settings.prompt_version,
        status="Active",
        is_active=True,
        accuracy=0.0,
        avg_latency_ms=0,
    )
    m_b = AiModel(
        id="M-B",
        name="Qwen 2.5 3B",
        ollama_tag="qwen2.5:3b",
        prompt_version=settings.prompt_version,
        status="Registered",
        is_active=False,
        accuracy=0.0,
    )
    db.add_all([m_a, m_b])

    # Activity profiles use the house-local calendar day.
    today = to_local(datetime.utcnow(), settings.house_tz).date()
    typical = [0, 0, 0, 0, 0, 1, 2, 4, 5, 4, 3, 4, 5, 3, 3, 4, 5, 6, 4, 3, 2, 1, 0, 0]
    # Baseline: 14 normal days (days 3..16 ago).
    for d in range(3, 17):
        day = today - timedelta(days=d)
        for hour, count in enumerate(typical):
            db.add(ActivityProfile(house_id="H-1042", day=day.isoformat(), hour=hour, count=count, is_baseline=True))
    # Yesterday and the day before: night-time activity, quiet days (routine changed, score > 0.8).
    inverted = [10 if c == 0 else 0 for c in typical]
    for d in (1, 2):
        day = today - timedelta(days=d)
        for hour, count in enumerate(inverted):
            db.add(ActivityProfile(house_id="H-1042", day=day.isoformat(), hour=hour, count=count, is_baseline=False))
    # Today starts empty; every ingested event adds to it.


if __name__ == "__main__":
    reset_and_seed()
    print("seeded", settings.database_url)
