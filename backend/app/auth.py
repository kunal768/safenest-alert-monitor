from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import User


def parse_token(
    authorization: str | None = Header(default=None),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def get_current_user(
    token: str | None = Depends(parse_token),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise AppError("unauthorized", "Missing bearer token", 401)
    user = db.query(User).filter(User.token == token).one_or_none()
    if not user:
        raise AppError("unauthorized", "Invalid token", 401)
    return user


def get_optional_user(
    token: str | None = Depends(parse_token),
    db: Session = Depends(get_db),
) -> User | None:
    """None when no bearer token was sent (care-center link access); 401 for a bad token."""
    if not token:
        return None
    return get_current_user(token, db)


def house_ids_for(user: User) -> list[str] | None:
    """None means all houses (staff)."""
    if user.role == "staff":
        return None
    return [h for h in user.house_ids.split(",") if h]


def assert_house_access(user: User, house_id: str):
    allowed = house_ids_for(user)
    if allowed is not None and house_id not in allowed:
        raise AppError("forbidden", "Owners may only access their own houses", 403)
