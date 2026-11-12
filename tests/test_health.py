def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["db"] is True


def test_features_flag(client):
    r = client.get("/config/features")
    assert r.json()["ENABLE_CLOUD_DEPLOY"] is False


def test_login(client):
    r = client.post("/auth/login", json={"email": "staff@safenest.local"})
    assert r.json()["token"] == "staff-token"
    assert r.json()["role"] == "staff"


def test_live_websocket_pushes_new_alerts(client, staff):
    with client.websocket_connect("/live") as ws:
        client.post("/sim/scenario?name=glass", headers=staff)
        msg = ws.receive_json()
        assert msg["event"] == "alert"
        assert msg["payload"]["type"] == "glass_break"


def test_health_reports_active_model_and_feature_flags(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["activeModel"] == "llama3.2:3b"
    assert h["deps"]["db"] == "up" and h["queueDepth"] == 0
    f = client.get("/config/features").json()
    assert f["MODEL_ADAPTER"] == "mock" and f["escalationTimeoutSec"] == 30
