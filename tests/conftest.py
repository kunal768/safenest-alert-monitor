import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["MODEL_ADAPTER"] = "mock"
os.environ["ESCALATION_TIMEOUT_SEC"] = "30"  # escalation tests shorten this per test
os.environ["DEPLOY_STEP_DELAY_SEC"] = "0"
os.environ["NOTIFY_FAIL_CHANNELS"] = ""
os.environ["MOCK_LATENCY_MS"] = "0"
_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.seed import reset_and_seed
from app.main import app


@pytest.fixture(autouse=True)
def _reset_runtime():
    from app.config import runtime

    runtime["ai_outage"] = False
    yield
    runtime["ai_outage"] = False


@pytest.fixture
def db():
    reset_and_seed()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def staff(client):
    return {"Authorization": "Bearer staff-token"}


@pytest.fixture
def owner(client):
    return {"Authorization": "Bearer owner-token"}
