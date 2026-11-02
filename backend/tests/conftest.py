import os
import tempfile

os.environ.update({"RUN_BACKGROUND_WORKERS": "false", "GOOGLE_API_KEY": "", "LLM_FAKE": "false",
                   "SECRET_KEY": "test-secret-key-that-is-long-enough-123", "PYDANTIC_AI_NO_BANNER": "1",
                   "DATABASE_URL": f"sqlite:///{tempfile.gettempdir()}/opspilot-test-boot.db"})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import clock, db  # noqa: E402
from app.integrations import fake_llm, llm  # noqa: E402
from app.integrations.breaker import BREAKERS  # noqa: E402
from app.orchestrator import pipeline  # noqa: E402

H = {"x-requested-with": "opspilot"}
PW = "Demo@2026"


@pytest.fixture()
def client(tmp_path):
    db.rebind(f"sqlite:///{tmp_path / 'test.db'}")
    db.init_db()
    pipeline.SYNC.inline = True
    clock.set_offset(0)
    for b in BREAKERS.values():
        b.reset()
    llm.set_test_models(fake_llm.make(), fake_llm.make(name="fake-llm-fallback"))
    from app.main import app

    with TestClient(app) as c:
        yield c
    llm.set_test_models(None)


class Api:
    def __init__(self, client: TestClient):
        self.c = client

    def login(self, who: str):
        r = self.c.post("/api/auth/login", json={"email": f"{who}@opspilot.demo", "password": PW})
        assert r.status_code == 200, r.text
        return r.json()

    def ingest(self, key: str, as_: str = "analyst") -> dict:
        self.login(as_)
        r = self.c.post("/api/admin/scenarios/ingest", json={"key": key}, headers=H)
        assert r.status_code == 202, r.text
        return self.case(r.json()["id"])

    def case(self, cid: int) -> dict:
        r = self.c.get(f"/api/requests/{cid}")
        assert r.status_code == 200, r.text
        return r.json()

    def post(self, url: str, json: dict | None = None, **kw):
        return self.c.post(url, json=json or {}, headers={**H, **kw.pop("headers", {})}, **kw)

    def patch(self, url: str, json: dict):
        return self.c.patch(url, json=json, headers=H)

    def put(self, url: str, json: dict):
        return self.c.put(url, json=json, headers=H)

    def chaos(self, **flags):
        self.login("admin")
        r = self.put("/api/admin/settings/chaos", {"value": flags})
        assert r.status_code == 200, r.text

    def rec(self, case: dict, title_contains: str | None = None, recommended: bool = False) -> dict:
        live = [r for r in case["recommendations"] if r["status"] in ("proposed", "selected")]
        if recommended:
            return next(r for r in live if r["is_recommended"])
        return next(r for r in live if title_contains is None or title_contains.lower() in r["option"]["title"].lower())

    def submit(self, case: dict, rec: dict, note: str = "") -> dict:
        r = self.post(f"/api/requests/{case['id']}/recommendations/{rec['id']}/submit", {"note": note})
        assert r.status_code == 200, r.text
        return r.json()

    def fact(self, case: dict, key: str):
        return next((f for f in case["facts"] if f["key"] == key), None)

    def issues(self, case: dict, type_: str, status: str = "open") -> list[dict]:
        return [i for i in case["issues"] if i["type"] == type_ and i["status"] == status]


@pytest.fixture()
def api(client):
    return Api(client)
