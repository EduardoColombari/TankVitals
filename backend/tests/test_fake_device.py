"""Simulador: payload válido, anomalias e publicação MQTT."""
import json
import sys
from unittest.mock import Mock
import pytest
from tools import fake_device
from app.models import parse_reading
from app.alerts import classify_reading


@pytest.mark.parametrize("anomaly", [None, "temperature_c", "ph", "level_pct", "turbidity_ntu"])
def test_generated_payload_is_valid_and_anomaly_is_critical(anomaly):
    for seq in (0, 12, 24, 80):
        payload = fake_device.build_payload(seq, anomaly)
        reading = parse_reading("tankvitals/tanque-01/telemetry", json.dumps(payload).encode())
        assert reading is not None
        if anomaly:
            assert classify_reading(reading)[0].value == "critico"
    assert fake_device.build_payload(0, None)["temperature_c"] != fake_device.build_payload(12, None)["temperature_c"]


def test_finite_run_publishes_telemetry_and_retained_status(monkeypatch):
    client = Mock()
    client.is_connected.return_value = True
    client.publish.return_value.rc = 0
    monkeypatch.setattr(fake_device.mqtt, "Client", Mock(return_value=client))
    monkeypatch.setattr(fake_device.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["fake_device.py", "--count", "2", "--tank-id", "tanque-02", "--anomalia", "ph"])
    fake_device.main()
    client.will_set.assert_called_once_with("tankvitals/tanque-02/status", "offline", qos=1, retain=True)
    published = client.publish.call_args_list
    assert len(published) == 3
    for call in published[:2]:
        assert call.args[0] == "tankvitals/tanque-02/telemetry"
        payload = json.loads(call.args[1])
        assert payload["ph"] == 5.5 and payload["tank_id"] == "tanque-02"
    assert published[-1].args == ("tankvitals/tanque-02/status", "offline")
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()
    client.on_connect(client, None, None, 0, None)
    assert client.publish.call_args.args == ("tankvitals/tanque-02/status", "online")


@pytest.mark.parametrize("args", [["--interval", "0"], ["--interval", "nan"], ["--count", "-1"], ["--tank-id", "a/b"]])
def test_invalid_cli_arguments_fail_before_connecting(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["fake_device.py", *args])
    client = Mock()
    monkeypatch.setattr(fake_device.mqtt, "Client", client)
    with pytest.raises(SystemExit) as exc:
        fake_device.main()
    assert exc.value.code == 2
    client.assert_not_called()
