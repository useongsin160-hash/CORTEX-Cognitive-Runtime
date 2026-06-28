from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_query_endpoint_returns_trace_id():
    resp = client.post("/query", json={"prompt": "테스트"})
    assert resp.status_code == 200
    body = resp.json()
    assert "trace_id" in body
    assert isinstance(body["trace_id"], str)
    assert body["trace_id"]


def test_trace_endpoint_accumulates_events():
    resp = client.post("/query", json={"prompt": "trace check"})
    assert resp.status_code == 200
    trace_id = resp.json()["trace_id"]

    trace_resp = client.get(f"/trace/{trace_id}")
    assert trace_resp.status_code == 200
    events = trace_resp.json()["events"]
    assert len(events) >= 2
    event_types = [e["event_type"] for e in events]
    assert "query.received" in event_types
    assert "query.completed" in event_types
