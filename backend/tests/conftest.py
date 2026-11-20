import os
import tempfile
import time

import pytest

# Isolated DB and no LLM/Telegram regardless of the developer's .env.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LLM_BASE_URL"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.agents.llm import set_llm  # noqa: E402
from app.seed import DEMO_PASSWORD  # noqa: E402


@pytest.fixture()
def client():
    db.configure(f"sqlite:///{_tmp}/test-{time.time_ns()}.db")
    set_llm(None)
    from app.main import app

    with TestClient(app) as c:
        yield c


def login(client, user: str, password: str = DEMO_PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"login": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def wait_for(fn, timeout: float = 5.0):
    """Poll until fn() returns truthy — background agents run on the app's loop."""
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")
