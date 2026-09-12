"""BE-05: consumo, persistência e recuperação sem serviços externos."""

import importlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest


@pytest.fixture
def context(monkeypatch):
    # Credencial fictícia: os testes nunca acessam o InfluxDB real.
    monkeypatch.setenv("INFLUX_TOKEN", "test-token")
    module = importlib.import_module("app.mqtt_ingestor")
    monkeypatch.setattr(module.settings, "mqtt_topic_prefix", "tankvitals")
    monkeypatch.setattr(module.settings, "mqtt_username", None)
    client = Mock()
    monkeypatch.setattr(module.mqtt, "Client", Mock(return_value=client))
    repo = Mock()
    repo.write_reading.return_value = True
    callback = Mock()
    ingestor = module.MqttIngestor(on_reading=callback, repository=repo)
    return SimpleNamespace(
        module=module, client=client, repo=repo, callback=callback, ingestor=ingestor,
    )


def send(context, payload, topic="tankvitals/tanque-01/telemetry"):
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
    context.ingestor._on_message(
        context.client, None, SimpleNamespace(topic=topic, payload=payload),
    )


def reading(**overrides):
    return dict(device_id="esp32-test", tank_id="tanque-01", temperature_c=26,
                **overrides)


def test_persists_classifies_caches_and_notifies(context, caplog):
    with caplog.at_level(logging.INFO):
        send(context, reading(ph=99, level_pct=0))
    saved = context.repo.write_reading.call_args.args[0]
    assert saved.ph is None
    assert saved.level_pct == 0
    cached = context.ingestor.last_reading["tanque-01"]
    assert cached["temperature_c"] == 26
    assert cached["alert"] == "critico"
    assert cached["time"].endswith("Z")
    context.callback.assert_called_once_with(cached)
    assert sum("Leitura gravada:" in record.message for record in caplog.records) == 1


@pytest.mark.parametrize("payload", [
    b"lixo", b"\xff", b"[]", b"{}",
    b'{"device_id":"esp32","tank_id":"tanque-01","distance_cm":12}',
    b'{"device_id":"esp32","tank_id":"tanque-01","temperature_c":999}',
])
def test_bad_payload_warns_and_next_reading_survives(context, caplog, payload):
    send(context, payload)
    context.repo.write_reading.assert_not_called()
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    send(context, reading())
    context.repo.write_reading.assert_called_once()


def test_extreme_metadata_does_not_stop_ingestion(context):
    send(context, reading(ts=float("inf"), seq=float("inf"),
                          uptime_s=float("inf"), rssi=float("inf")))
    saved = context.repo.write_reading.call_args.args[0]
    assert saved.seq is None and saved.rssi is None and saved.uptime_s is None
    assert saved.time.year >= 2026


def test_topic_tank_takes_precedence_with_custom_prefix(context, monkeypatch):
    monkeypatch.setattr(context.module.settings, "mqtt_topic_prefix", "grupo/tankvitals")
    send(context, reading(), "grupo/tankvitals/tanque-02/telemetry")
    assert context.repo.write_reading.call_args.args[0].tank_id == "tanque-02"


@pytest.mark.parametrize("topic", [
    "outro/tanque-01/telemetry", "tankvitals//telemetry",
    "tankvitals/tanque-01/extra/telemetry", "tankvitals/tanque-01/cmd",
])
def test_unexpected_topic_is_rejected(context, topic):
    send(context, reading(), topic)
    context.repo.write_reading.assert_not_called()


def test_status_is_kept_per_tank_and_invalid_status_is_ignored(context, caplog):
    send(context, b"online", "tankvitals/tanque-01/status")
    send(context, b"online", "tankvitals/tanque-02/status")
    send(context, b"offline", "tankvitals/tanque-01/status")
    send(context, b"lixo", "tankvitals/tanque-02/status")
    send(context, b"\xff", "tankvitals/tanque-02/status")
    assert context.ingestor.online == {"tanque-01": False, "tanque-02": True}
    assert "inválido" in caplog.text
    context.repo.write_reading.assert_not_called()


@pytest.mark.parametrize("failure", [False, RuntimeError("banco indisponível")])
def test_failed_write_does_not_replace_cache_or_notify(context, failure):
    send(context, reading())
    previous = context.ingestor.last_reading["tanque-01"]
    context.callback.reset_mock()
    if isinstance(failure, Exception):
        context.repo.write_reading.side_effect = failure
    else:
        context.repo.write_reading.return_value = failure
    send(context, reading(ph=7))
    assert context.ingestor.last_reading["tanque-01"] is previous
    context.callback.assert_not_called()
    context.repo.write_reading.side_effect = None
    context.repo.write_reading.return_value = True
    send(context, reading(ph=8))
    context.callback.assert_called_once()


def test_callback_failure_does_not_stop_following_readings(context, caplog):
    context.callback.side_effect = RuntimeError("websocket fechado")
    send(context, reading())
    send(context, reading(ph=7))
    assert context.repo.write_reading.call_count == 2
    assert context.ingestor.last_reading["tanque-01"]["ph"] == 7
    assert "Erro no callback" in caplog.text


def test_start_reconnect_subscriptions_and_shutdown(context):
    context.ingestor.start()
    context.client.connect_async.assert_called_once_with(
        context.module.settings.mqtt_host, context.module.settings.mqtt_port, keepalive=60,
    )
    context.client.loop_start.assert_called_once()
    context.client.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=30)
    context.ingestor._on_connect(context.client, None, None, 128)
    context.client.subscribe.assert_not_called()
    for _ in range(2):
        context.ingestor._on_connect(context.client, None, None, 0)
    assert context.client.subscribe.call_args_list == [
        call("tankvitals/+/telemetry", qos=0), call("tankvitals/+/status", qos=1),
    ] * 2
    context.ingestor.stop()
    assert context.client.method_calls[-2:] == [call.disconnect(), call.loop_stop()]


def test_command_uses_json_and_configured_topic(context):
    context.client.publish.return_value.rc = context.module.mqtt.MQTT_ERR_SUCCESS
    context.ingestor.publish_command("tanque-01", {"interval_s": 10})
    topic, payload = context.client.publish.call_args.args
    assert topic == "tankvitals/tanque-01/cmd"
    assert json.loads(payload) == {"interval_s": 10}
