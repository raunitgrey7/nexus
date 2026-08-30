import json

import pytest
from fastapi.testclient import TestClient

from nexus.api.app import create_app
from nexus.llm.client import NullLLM
from nexus.runtime.live import LiveRuntime


@pytest.fixture(scope="module")
def client():
    runtime = LiveRuntime(scale="tiny", seed=11, strategy="optimized", llm=NullLLM(), workers=1)
    app = create_app(runtime, autostart=False)
    with TestClient(app) as c:
        yield c
    runtime.close()


def test_health_status_and_control(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["llm"]["available"] is False
    s = client.get("/api/status").json()
    assert s["running"] is False and s["scale"] == "tiny" and s["strategy"] == "optimized"
    r = client.post("/api/sim/control", json={"action": "step", "ticks": 300})
    assert r.status_code == 200 and r.json()["tick"] >= 300
    assert (
        client.post("/api/sim/control", json={"action": "speed", "ticks_per_second": 50}).json()[
            "ticks_per_second"
        ]
        == 50
    )
    assert client.post("/api/sim/control", json={"action": "reset", "scale": "nope"}).status_code == 400
    r = client.post("/api/sim/control", json={"action": "start"})
    assert r.json()["running"] is True
    r = client.post("/api/sim/control", json={"action": "pause"})
    assert r.json()["running"] is False


def test_world_kpis_spatial_entities(client):
    w = client.get("/api/world").json()
    assert {"summary", "robots", "zones", "grid", "shelves", "kpis", "strategy"} <= set(w)
    assert len(w["grid"]["rows"]) == w["grid"]["height"]
    k = client.get("/api/kpis").json()
    assert "sla_breach_rate_projected" in k and k["tick"] >= 300
    sp = client.get("/api/spatial").json()
    assert sp["nodes"] and sp["edges"] and "A" in sp["zone_adjacency"]
    e = client.get("/api/world/entity/R01").json()
    assert e["kind"] == "robot" and e["relations"]["entity_id"] == "R01"
    assert client.get("/api/world/entity/NOPE").status_code == 404
    z = client.get("/api/world/entity/A").json()
    assert z["kind"] == "zone" and "shelves" in z
    assert client.get("/api/world/robots").json()[0]["id"] == "R01"
    assert isinstance(client.get("/api/world/orders?status=delivered&limit=5").json(), list)


def test_events_and_faults(client):
    ev = client.post(
        "/api/events/inject",
        json={
            "type": "ROBOT_FAILURE",
            "entity_id": "R01",
            "payload": {"cause": "motor_fault", "recovery_ticks": 600},
            "key": "t-fail-1",
        },
    )
    assert ev.status_code == 200 and ev.json()["origin"] == "user"
    dup = client.post(
        "/api/events/inject",
        json={"type": "ROBOT_FAILURE", "entity_id": "R01", "payload": {}, "key": "t-fail-1"},
    )
    assert dup.status_code == 409
    assert client.post("/api/events/inject", json={"type": "NOPE"}).status_code == 400
    recent = client.get("/api/events/recent?limit=20&notable=true").json()
    assert any(e["type"] == "ROBOT_FAILURE" for e in recent)
    since = client.get("/api/events?since_seq=0&limit=50&types=ROBOT_FAILURE").json()
    assert since and all(e["type"] == "ROBOT_FAILURE" for e in since)
    presets = client.get("/api/faults/presets").json()
    assert any(p["id"] == "demand-plus-40" for p in presets)
    fired = client.post("/api/faults/demand-plus-40")
    assert fired.status_code == 200 and fired.json()["type"] == "DEMAND_CHANGED"
    assert client.post("/api/faults/nope").status_code == 404
    assert client.post("/api/faults/recover-all").status_code == 200


def test_forecast_decisions_whatif_nlq(client):
    client.post("/api/sim/control", json={"action": "step", "ticks": 300})
    fc = client.get("/api/forecast?horizon_min=30").json()
    assert fc["demand"]["horizon_min"] == 30 and "summary" in fc and isinstance(fc["bottlenecks"], list)
    d = client.post(
        "/api/decisions",
        json={"goal": "min breach", "trigger": "test", "horizon_min": 5, "candidates": 3, "use_llm": False},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["candidates"] and body["baseline"] and body["explanation"]
    assert client.get("/api/decisions").json()[0]["id"] == body["id"]
    assert client.get(f"/api/decisions/{body['id']}").status_code == 200
    assert client.get("/api/decisions/DEC-nope").status_code == 404
    if body["status"] == "proposed":
        a = client.post(f"/api/decisions/{body['id']}/actions", json={"action": "approve", "actor": "qa"})
        assert a.json()["status"] == "approved"
    ex = client.post(f"/api/decisions/{body['id']}/actions", json={"action": "execute", "actor": "qa"})
    assert ex.status_code == 200 and ex.json()["status"] == "executed"
    assert client.post(f"/api/decisions/{body['id']}/actions", json={"action": "execute"}).status_code == 409
    presets = client.get("/api/whatif/presets").json()
    assert presets and presets[0]["scenario"]["mutations"]
    wi = client.post(
        "/api/whatif/run",
        json={"scenario": presets[0]["scenario"], "strategies": ["baseline"], "horizon_min": 5, "seeds": 1},
    )
    assert wi.status_code == 200 and wi.json()["status"] == "done" and wi.json()["runs"]
    assert (
        client.post(
            "/api/whatif", json={"scenario": presets[0]["scenario"], "strategies": ["nope"]}
        ).status_code
        == 400
    )
    queued = client.post(
        "/api/whatif",
        json={
            "scenario": presets[0]["scenario"],
            "strategies": ["baseline"],
            "horizon_min": 5,
            "include_current": False,
        },
    ).json()
    assert queued["status"] in ("queued", "running", "done")
    assert client.get(f"/api/whatif/{queued['id']}").status_code == 200
    assert client.get("/api/whatif/nope").status_code == 404
    n = client.post("/api/nlq", json={"question": "How many orders are open right now?"}).json()
    assert n["intent"] == "status" and n["llm_used"] is False


def test_timeline_snapshots_strategies_metrics(client):
    tl = client.get("/api/timeline").json()
    assert "points" in tl and "snapshots" in tl and tl["snapshots"]
    tick = tl["snapshots"][0]["tick"]
    snap = client.get(f"/api/snapshots/{tick}").json()
    assert snap["summary"]["tick"] == tick and "kpis" in snap
    assert client.get("/api/snapshots/999999").status_code == 404
    names = {s["name"] for s in client.get("/api/strategies").json()}
    assert {"baseline", "optimized", "nexus_full"} <= names
    assert client.get("/api/benchmarks").status_code in (200, 404)
    m = client.get("/metrics").text
    assert "nexus_sim_tick" in m and "nexus_events_total" in m
    assert client.get("/").json()["name"] == "NEXUS"


def test_websocket_hello_and_ping(client):
    with client.websocket_connect("/ws/live") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["world"]["robots"] and "status" in hello
        ws.send_text(json.dumps({"type": "ping"}))
        frame = json.loads(ws.receive_text())
        while frame["type"] != "pong":
            frame = json.loads(ws.receive_text())
        assert "tick" in frame
        ws.send_text(json.dumps({"type": "control", "action": "step", "ticks": 5}))
