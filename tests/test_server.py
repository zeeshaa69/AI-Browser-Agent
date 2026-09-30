import time

import pytest
from fastapi.testclient import TestClient

from browser_agent.server import create_app

pytestmark = [pytest.mark.browser, pytest.mark.e2e]


@pytest.fixture()
def client(demo, tmp_path):
    with TestClient(create_app(runs_dir=tmp_path, demo_url=demo.url)) as c:
        yield c


def wait_for(client, task_id, pred, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/tasks/{task_id}").json()
        if pred(st):
            return st
        time.sleep(0.1)
    raise AssertionError(f"timed out; last state: {st['status']} / {st['phase']}")


def test_health_and_config(client, demo):
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/config").json()["demo_url"] == demo.url


def test_full_task_lifecycle(client):
    tid = client.post("/api/tasks", json={"goal": "Extract the pricing table from the local demo website"}).json()["id"]
    st = wait_for(client, tid, lambda s: s["finished"])
    assert st["status"] == "completed", st["error"]
    assert [s["state"] for s in st["steps"]] == ["done"] * 3
    assert st["extracted"]["pricing"][0]["Plan"] == "Starter"
    assert st["browser"]["url"].endswith("pricing.html") and st["browser"]["state"] == "closed"
    assert [e["action"] for e in st["log"]][:3] == ["navigate", "click", "extract"]
    assert st["screenshots"]
    png = client.get(f"/api/tasks/{tid}/screenshots/{st['screenshots'][0]}")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert client.get(f"/api/tasks/{tid}/export?format=csv").text.splitlines()[0] == "Plan,Price,Seats,Support"
    assert client.get(f"/api/tasks/{tid}/export?format=xml").status_code == 400


def test_confirmation_flow_approve(client):
    goal = 'On the local demo site open the contact page and fill the form with name=Ada, message="hi"'
    tid = client.post("/api/tasks", json={"goal": goal}).json()["id"]
    st = wait_for(client, tid, lambda s: s["status"] == "awaiting_confirmation")
    assert "submits data" in st["pending_confirmation"]["description"]
    assert client.post(f"/api/tasks/{tid}/confirm", json={"approve": True}).status_code == 200
    st = wait_for(client, tid, lambda s: s["finished"])
    assert st["status"] == "completed", st["error"]


def test_confirmation_flow_deny(client):
    goal = 'On the local demo site open the contact page and fill the form with name=Ada, message="hi"'
    tid = client.post("/api/tasks", json={"goal": goal}).json()["id"]
    wait_for(client, tid, lambda s: s["status"] == "awaiting_confirmation")
    client.post(f"/api/tasks/{tid}/confirm", json={"approve": False})
    st = wait_for(client, tid, lambda s: s["finished"])
    assert st["status"] == "blocked" and "confirmation denied" in st["error"]
    assert client.post(f"/api/tasks/{tid}/confirm", json={"approve": True}).status_code == 409


def test_only_one_task_at_a_time_and_cancel(client):
    goal = 'On the local demo site open the contact page and fill the form with name=Ada, message="hi"'
    tid = client.post("/api/tasks", json={"goal": goal}).json()["id"]
    wait_for(client, tid, lambda s: s["status"] == "awaiting_confirmation")
    assert client.post("/api/tasks", json={"goal": goal}).status_code == 409
    client.post(f"/api/tasks/{tid}/cancel")
    st = wait_for(client, tid, lambda s: s["finished"])
    assert st["status"] in ("blocked", "aborted")


def test_cross_origin_posts_refused(client):
    r = client.post("/api/tasks", json={"goal": "x"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_unknown_task_and_bad_screenshot_name(client):
    assert client.get("/api/tasks/nope").status_code == 404
    tid = client.post("/api/tasks", json={"goal": "Extract the pricing table from the local demo website"}).json()["id"]
    wait_for(client, tid, lambda s: s["finished"])
    assert client.get(f"/api/tasks/{tid}/screenshots/..%2Fresult.json").status_code == 404


def test_planning_error_surfaces(client):
    tid = client.post("/api/tasks", json={"goal": "Extract the pricing table"}).json()["id"]
    st = wait_for(client, tid, lambda s: s["finished"])
    assert st["status"] == "failed" and "does not name a website" in st["error"]
