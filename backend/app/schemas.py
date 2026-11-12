from pydantic import BaseModel


class EventIn(BaseModel):
    eventId: str
    houseId: str
    deviceId: str
    ts: str | None = None
    description: str
    levelDb: float = 0
    durationSec: float = 0


class PatchAlert(BaseModel):
    action: str
    version: int
    note: str | None = None


class EscalateBody(BaseModel):
    target: str = "family"  # family | careCenter | emergency
    version: int | None = None
    note: str | None = None


class DeviceMissedBody(BaseModel):
    """device.missed from the device manager (evt:device:missed)."""

    eventId: str | None = None
    lastSeen: str | None = None
    offlineSince: str | None = None


class DeployBody(BaseModel):
    action: str = "deploy"  # deploy | rollback


class RegisterModelBody(BaseModel):
    tag: str
    name: str | None = None


class OutageBody(BaseModel):
    on: bool


class RulesBody(BaseModel):
    version: int
    quietStart: str | None = None
    quietEnd: str | None = None
    quietHoursEnabled: bool | None = None
    rules: list[dict]


class LoginBody(BaseModel):
    email: str


class CustomEventBody(BaseModel):
    description: str
    houseId: str = "H-1042"
    deviceId: str = "D-210"
    levelDb: float = 70
    eventId: str | None = None
