"""End-to-end ticket lifecycle through the HTTP API (heuristic agent mode)."""

from tests.conftest import login, wait_for


def _widget(client, name="Пётр"):
    tok = client.post("/api/widget/session", json={"name": name}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_full_lifecycle(client):
    anna = login(client, "anna")  # billing, returns
    igor = login(client, "igor")  # technical, account
    for h in (anna, igor):
        assert client.post("/api/me/status", json={"online": True}, headers=h).json()["online"]

    w = _widget(client)
    r = client.post("/api/widget/messages", json={"text": "Деньги списались дважды за заказ, срочно!"}, headers=w)
    assert r.status_code == 200 and r.json()["new_ticket"]
    tid = r.json()["ticket_id"]

    # triage + routing run in the background
    t = wait_for(lambda: (x := client.get(f"/api/tickets/{tid}", headers=anna).json())["status"] == "assigned" and x)
    assert t["category"] == "billing" and t["priority"] == "high"
    assert t["operator_name"] == "Анна Смирнова"
    assert t["routing"]["strategy"] == "skills"

    # wrong operator cannot answer
    assert client.post(f"/api/tickets/{tid}/messages", json={"text": "hi"}, headers=igor).status_code == 409

    sug = client.post(f"/api/tickets/{tid}/suggest", headers=anna).json()
    assert sug["mode"] == "heuristic" and "3 рабочих дней" in sug["reply"]

    assert client.post(f"/api/tickets/{tid}/messages", json={"text": sug["reply"]}, headers=anna).status_code == 200
    widget_view = client.get("/api/widget/ticket", headers=w).json()["ticket"]
    assert [m["sender"] for m in widget_view["messages"]] == ["customer", "system", "operator"]
    assert widget_view["first_response_at"]

    client.post("/api/widget/messages", json={"text": "Спасибо, помогли!"}, headers=w)
    assert client.post(f"/api/tickets/{tid}/close", json={"resolved": True}, headers=anna).status_code == 200
    assert client.post("/api/widget/rate", json={"ticket_id": tid, "rating": 5}, headers=w).status_code == 200

    q = wait_for(lambda: client.get(f"/api/tickets/{tid}", headers=anna).json()["quality"])
    assert q["resolved"] is True

    admin = login(client, "admin", "admin1234")
    dash = client.get("/api/admin/dashboard", headers=admin).json()
    assert dash["overview"]["total"] >= 1
    runs = client.get(f"/api/admin/agent-runs?ticket_id={tid}", headers=admin).json()
    assert {r["agent"] for r in runs} >= {"triage", "router", "copilot", "quality"}


def test_queue_waits_until_operator_online(client):
    w = _widget(client)
    tid = client.post("/api/widget/messages", json={"text": "Не приходит смс код для входа"}, headers=w).json()["ticket_id"]
    igor = login(client, "igor")
    wait_for(lambda: client.get(f"/api/tickets/{tid}", headers=igor).json()["status"] == "queued")
    client.post("/api/me/status", json={"online": True}, headers=igor)
    t = wait_for(lambda: (x := client.get(f"/api/tickets/{tid}", headers=igor).json())["status"] == "assigned" and x)
    assert t["operator_name"] == "Игорь Петров"


def test_strategy_switch_and_permissions(client):
    anna = login(client, "anna")
    assert client.get("/api/admin/settings", headers=anna).status_code == 403
    admin = login(client, "admin", "admin1234")
    r = client.put("/api/admin/settings", json={"routing_strategy": "queue"}, headers=admin)
    assert r.json()["routing_strategy"] == "queue"
    assert client.put("/api/admin/settings", json={"routing_strategy": "magic"}, headers=admin).status_code == 422


def test_customer_cannot_rate_foreign_ticket(client):
    anna = login(client, "anna")
    client.post("/api/me/status", json={"online": True}, headers=anna)
    w1, w2 = _widget(client, "A"), _widget(client, "B")
    tid = client.post("/api/widget/messages", json={"text": "оплата не проходит"}, headers=w1).json()["ticket_id"]
    wait_for(lambda: client.get(f"/api/tickets/{tid}", headers=anna).json()["status"] == "assigned")
    client.post(f"/api/tickets/{tid}/close", json={}, headers=anna)
    assert client.post("/api/widget/rate", json={"ticket_id": tid, "rating": 1}, headers=w2).status_code == 404


def test_auth_required(client):
    assert client.get("/api/tickets").status_code == 401
    assert client.post("/api/widget/messages", json={"text": "x"}).status_code == 401
    assert client.post("/api/auth/login", json={"login": "anna", "password": "nope"}).status_code == 401


def test_writes_are_committed_before_response(client):
    """Regression: the browser uses a fresh widget token immediately. If an endpoint relied on the
    get_db teardown to commit, the record could be missing when the next request arrives (401/403).
    Simulate a teardown that never commits: endpoints must commit before returning."""
    from app.db import SessionLocal, get_db
    from app.main import app

    def no_commit_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.rollback()
            s.close()

    app.dependency_overrides[get_db] = no_commit_db
    try:
        w = _widget(client)
        assert client.get("/api/widget/ticket", headers=w).status_code == 200

        admin = login(client, "admin", "admin1234")
        r = client.post("/api/admin/kb", json={"category": "general", "title": "T", "body": "B"}, headers=admin)
        assert any(a["id"] == r.json()["id"] for a in client.get("/api/admin/kb", headers=admin).json())
        op = client.post(
            "/api/admin/operators", json={"login": "newop", "name": "Новый", "password": "pass1234"}, headers=admin
        ).json()
        client.put(
            f"/api/admin/operators/{op['id']}",
            json={"login": "newop", "name": "Переименован", "skills": ["billing"]},
            headers=admin,
        )
        names = {o["name"] for o in client.get("/api/admin/operators", headers=admin).json()}
        assert "Переименован" in names
        client.delete(f"/api/admin/kb/{r.json()['id']}", headers=admin)
        assert all(a["id"] != r.json()["id"] for a in client.get("/api/admin/kb", headers=admin).json())
    finally:
        app.dependency_overrides.clear()
