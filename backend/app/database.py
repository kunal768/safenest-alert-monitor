from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _engine_url() -> str:
    return settings.database_url


engine = create_engine(
    _engine_url(),
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _fk_on(dbapi_conn, _rec):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns():
    """Tiny forward-only migration: add columns that newer code expects to an older SQLite file."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                if default is not None:
                    ddl += f" DEFAULT {default!r}" if isinstance(default, str) else f" DEFAULT {int(default)}"
                conn.execute(text(ddl))
