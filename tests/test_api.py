import time

import pytest
from fastapi.testclient import TestClient

from conftest import FakeSite, NullHttp, RecordingNotifier
from stockwatch.app import create_app
from stockwatch.config import Config
from stockwatch.storage import MemoryStore


@pytest.fixture
def client():
    site = FakeSite("shop", ("a", "b"))
    app = create_app(
        Config(_env_file=None, mongodb_uri="memory://"),
        store=MemoryStore(),
        notifier=RecordingNotifier(),
        adapters={"shop": site},
        engine_options={"tick_seconds": None, "http_factory": lambda a: NullHttp()},
    )
    with TestClient(app) as c:
        yield c


def wait_idle(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        engine = client.get("/api/status").json()["engine"]
        if not engine["running"] and not engine["queued"]:
            return
        time.sleep(0.01)
    raise AssertionError("engine did not go idle")


def test_dashboard_and_health(client):
    assert "Stockwatch" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/health").json() == {"ok": True}


def test_status(client):
    body = client.get("/api/status").json()
    assert body["engine"]["max_concurrent_runs"] == 5
    assert body["engine"]["paused"] is False
    assert body["products"] == {"total": 0, "in_stock": 0}


def test_settings(client):
    assert client.patch("/api/settings", json={"max_concurrent_runs": 3}).json() == {
        "max_concurrent_runs": 3,
        "paused": False,
    }
    assert client.patch("/api/settings", json={"paused": True}).json()["paused"] is True
    assert client.get("/api/settings").json() == {"max_concurrent_runs": 3, "paused": True}
    assert client.patch("/api/settings", json={"max_concurrent_runs": 0}).status_code == 422
    assert client.patch("/api/settings", json={"max_concurrent_runs": 51}).status_code == 422


def test_sites_and_category_edits(client):
    (site,) = client.get("/api/sites").json()
    assert site["key"] == "shop"
    assert [c["id"] for c in site["categories"]] == ["shop:a", "shop:b"]

    updated = client.patch("/api/categories/shop:a", json={"enabled": False, "interval_minutes": 45}).json()
    assert (updated["enabled"], updated["interval_minutes"]) == (False, 45)
    assert client.patch("/api/categories/shop:a", json={"interval_minutes": 0}).status_code == 422
    assert client.patch("/api/categories/nope:x", json={"enabled": True}).status_code == 404


def test_run_now_then_history_and_products(client):
    response = client.post("/api/categories/shop:a/run")
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    wait_idle(client)

    runs = client.get("/api/runs").json()
    assert runs[0]["id"] == run_id and runs[0]["status"] == "success" and runs[0]["baseline"] is True
    assert client.get("/api/runs", params={"status": "failed"}).json() == []

    products = client.get("/api/products", params={"site": "shop", "in_stock": True}).json()
    assert products["total"] == 1 and products["items"][0]["name"] == "Product 1"
    assert client.get("/api/products", params={"q": "nomatch"}).json()["total"] == 0
    assert client.get("/api/products", params={"sort": "bogus"}).status_code == 422

    sites = client.get("/api/sites").json()
    assert sites[0]["categories"][0]["last_status"] == "success"


def test_run_conflicts_and_cancel(client):
    client.patch("/api/settings", json={"paused": True})
    client.app.state.engine.adapters["shop"].gate_all()
    run_id = client.post("/api/categories/shop:a/run").json()["run_id"]
    assert client.post("/api/categories/shop:a/run").status_code == 409
    assert client.post("/api/categories/nope:x/run").status_code == 404

    assert client.get("/api/sites").json()[0]["categories"][0]["active"] == "running"
    assert client.post(f"/api/runs/{run_id}/cancel").json() == {"ok": True}
    wait_idle(client)
    assert client.get("/api/runs").json()[0]["status"] == "cancelled"
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 404


def test_status_stats_follow_runs(client):
    assert client.get("/api/status").json()["recent_runs"] == []
    run_id = client.post("/api/categories/shop:a/run").json()["run_id"]
    wait_idle(client)
    body = client.get("/api/status").json()
    assert body["products"] == {"total": 1, "in_stock": 1}
    assert body["runs_24h"] == {"success": 1}
    assert [r["id"] for r in body["recent_runs"]] == [run_id]
