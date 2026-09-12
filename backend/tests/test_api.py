"""REST, lifespan e MQTT -> WebSocket com banco/broker substituídos."""
import json
from datetime import datetime, timezone
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.influx_repo import RepositoryUnavailable
from app import mqtt_ingestor


@pytest.fixture
def service(monkeypatch):
    repo = Mock()
    repo.ping.return_value = True
    repo.write_reading.return_value = True
    repo.get_latest.return_value = {"tank_id": "tanque-01", "device_id": "esp32",
        "time": datetime.now(timezone.utc).isoformat(), "temperature_c": 28.6, "ph": 7.21}
    repo.list_tanks.return_value = [{"tank_id": "tanque-01", "last_seen": "2026-08-25T13:45:00Z"}]
    repo.get_history.return_value = {"tank_id": "tanque-01", "range": "6h", "window": "1m", "series": []}
    repo.get_stats.return_value = {"tank_id": "tanque-01", "range": "24h", "count": 0, "metrics": {}}
    mqtt_client = Mock()
    mqtt_client.is_connected.return_value = True
    monkeypatch.setattr(mqtt_ingestor.mqtt, "Client", Mock(return_value=mqtt_client))
    app = create_app(repo_factory=lambda: repo)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, repo=repo, mqtt=mqtt_client)
    mqtt_client.disconnect.assert_called_once()
    mqtt_client.loop_stop.assert_called_once()
    repo.close.assert_called_once()


def deliver(service, suffix, payload, tank="tanque-01"):
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
    message = SimpleNamespace(topic=f"tankvitals/{tank}/{suffix}", payload=payload)
    # Mesma fronteira de threads usada pelo Paho em produção.
    thread = Thread(target=service.app.state.ingestor._on_message, args=(None, None, message))
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_health_and_lifecycle(service):
    assert service.client.get("/api/health").json() == {"status": "ok", "mqtt": True, "influxdb": True}
    service.mqtt.connect_async.assert_called_once()
    service.repo.ping.return_value = False
    response = service.client.get("/api/health")
    assert response.status_code == 503 and response.json()["status"] == "degraded"
    assert isinstance(response.json()["detail"], str)


def test_disconnected_broker_is_degraded(service):
    service.mqtt.is_connected.return_value = False
    assert service.client.get("/api/health").status_code == 503


def test_latest_contract_and_404(service):
    data = service.client.get("/api/readings/latest").json()
    assert set(data) == {"tank_id", "device_id", "time", "age_s", "online", "level", "metrics"}
    assert data["level"] == "atencao" and data["time"].endswith("Z")
    assert data["metrics"]["temperature_c"] == {"value": 28.6, "level": "atencao", "unit": "°C"}
    assert data["age_s"] >= 0
    service.repo.get_latest.return_value = None
    assert service.client.get("/api/readings/latest").status_code == 404


@pytest.mark.parametrize("path", [
    "/api/readings/history?range=abc", "/api/readings/history?window=abc",
    "/api/readings/history?metrics=bad", "/api/readings/history?metrics=",
    "/api/stats?range=abc", "/api/readings/latest?tank_id=",
])
def test_invalid_parameters_do_not_hit_database(service, path):
    response = service.client.get(path)
    assert response.status_code == 400 and isinstance(response.json()["detail"], str)
    service.repo.get_latest.assert_not_called()
    service.repo.get_history.assert_not_called()
    service.repo.get_stats.assert_not_called()


def test_history_stats_thresholds_tanks_and_cors(service):
    assert service.client.get("/api/readings/history?metrics=ph,level_pct").json()["series"] == []
    service.repo.get_history.assert_called_once_with("tanque-01", "6h", None, ["ph", "level_pct"])
    assert service.client.get("/api/stats").json()["count"] == 0
    assert service.client.get("/api/thresholds").json()["ph"]["ok_max"] == 8
    deliver(service, "status", b"offline")
    assert service.client.get("/api/tanks").json()[0]["online"] is False
    response = service.client.get("/api/thresholds", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert service.client.get("/docs").status_code == 200
    assert len(service.client.get("/openapi.json").json()["paths"]) == 6


@pytest.mark.parametrize("path,method", [
    ("/api/readings/latest", "get_latest"), ("/api/readings/history", "get_history"),
    ("/api/stats", "get_stats"), ("/api/tanks", "list_tanks"),
])
def test_database_failure_maps_to_503(service, path, method):
    getattr(service.repo, method).side_effect = RepositoryUnavailable("secret connection details")
    response = service.client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "InfluxDB indisponível"}


def test_two_websockets_receive_reading_and_remaining_client_receives_status(service):
    with service.client.websocket_connect("/ws/live") as first:
        with service.client.websocket_connect("/ws/live") as second:
            deliver(service, "telemetry", {"device_id": "esp32", "tank_id": "tanque-01", "ph": 5.5})
            a, b = first.receive_json(), second.receive_json()
            assert a == b and a["type"] == "reading"
            assert a["payload"]["level"] == "critico"
            rest = service.client.get("/api/readings/latest").json()
            assert rest["metrics"] == a["payload"]["metrics"]
            service.repo.get_latest.assert_not_called()
        deliver(service, "status", b"offline")
        assert first.receive_json() == {"type": "status", "payload": {"tank_id": "tanque-01", "online": False}}


def test_websocket_filters_tanks(service):
    with service.client.websocket_connect("/ws/live?tank_id=tanque-02") as socket:
        deliver(service, "status", b"online", tank="tanque-01")
        deliver(service, "status", b"offline", tank="tanque-02")
        assert socket.receive_json()["payload"] == {"tank_id": "tanque-02", "online": False}
