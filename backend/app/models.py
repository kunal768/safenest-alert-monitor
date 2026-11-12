from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # owner | staff
    house_ids: Mapped[str] = mapped_column(String, nullable=False, default="")  # csv
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")


class House(Base):
    __tablename__ = "houses"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    subscription_active: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_start: Mapped[str] = mapped_column(String, default="22:00")
    quiet_end: Mapped[str] = mapped_column(String, default="07:00")
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    tz: Mapped[str] = mapped_column(String, default="America/Los_Angeles")
    rules_version: Mapped[int] = mapped_column(Integer, default=1)

    rooms = relationship("Room", back_populates="house")
    contacts = relationship("Contact", back_populates="house")
    devices = relationship("Device", back_populates="house")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    house_id: Mapped[str] = mapped_column(ForeignKey("houses.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    house = relationship("House", back_populates="rooms")
    devices = relationship("Device", back_populates="room")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    house_id: Mapped[str] = mapped_column(ForeignKey("houses.id"), nullable=False)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    missed_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    house = relationship("House", back_populates="devices")
    room = relationship("Room", back_populates="devices")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    house_id: Mapped[str] = mapped_column(ForeignKey("houses.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    phone: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    is_care_center: Mapped[bool] = mapped_column(Boolean, default=False)

    house = relationship("House", back_populates="contacts")


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    house_id: Mapped[str] = mapped_column(ForeignKey("houses.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    level_db: Mapped[float] = mapped_column(Float, default=0)
    duration_sec: Mapped[float] = mapped_column(Float, default=0)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|classified|unclassified
    alert_id: Mapped[str | None] = mapped_column(String, ForeignKey("alerts.alert_id"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(String, primary_key=True)
    house_id: Mapped[str] = mapped_column(ForeignKey("houses.id"), nullable=False)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="new")
    count: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    ack_by: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    escalate_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    escalate_step: Mapped[int] = mapped_column(Integer, default=0)
    clip_name: Mapped[str | None] = mapped_column(String, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (UniqueConstraint("house_id", "label", name="uq_house_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    house_id: Mapped[str] = mapped_column(ForeignKey("houses.id"), nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    min_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    high_mark: Mapped[float] = mapped_column(Float, default=0.85)
    channels: Mapped[str] = mapped_column(String, default="push,sms,email")
    ack_timeout_sec: Mapped[int] = mapped_column(Integer, default=300)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.alert_id"), nullable=False)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="sent")  # sent|failed|dashboard
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    detail: Mapped[str] = mapped_column(Text, default="")


class AiModel(Base):
    __tablename__ = "ai_models"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    ollama_tag: Mapped[str] = mapped_column(String, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String, default="v1")
    status: Mapped[str] = mapped_column(String, default="Registered")
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0)
    p95_latency_ms: Mapped[float] = mapped_column(Float, default=0)
    error_rate: Mapped[float] = mapped_column(Float, default=0)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    eval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_progress: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    previous_active_id: Mapped[str | None] = mapped_column(String, nullable=True)


class ActivityProfile(Base):
    __tablename__ = "activity_profiles"
    __table_args__ = (UniqueConstraint("house_id", "day", "hour", name="uq_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    house_id: Mapped[str] = mapped_column(ForeignKey("houses.id"), nullable=False)
    day: Mapped[str] = mapped_column(String, nullable=False)  # YYYY-MM-DD
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String, default="system")
    action: Mapped[str] = mapped_column(String, nullable=False)
    entity: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
