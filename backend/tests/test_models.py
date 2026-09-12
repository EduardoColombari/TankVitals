"""Validação do contrato MQTT, inclusive sensores parciais."""
import json
from datetime import datetime, timezone
import pytest
from app.models import parse_reading

TOPIC = "tankvitals/tanque-01/telemetry"


def parse(**overrides):
    data = {"device_id": "esp32", "tank_id": "tanque-01", "temperature_c": 26,
            "ph": 7.2, "level_pct": 78, "turbidity_ntu": 12, "ts": 1756108800}
    data.update(overrides)
    return parse_reading(TOPIC, json.dumps(data).encode())


def test_complete_payload():
    reading = parse(seq=42, rssi=-58, distance_cm=13.6)
    assert reading.temperature_c == 26 and reading.ph == 7.2
    assert reading.seq == 42 and reading.rssi == -58
    assert reading.time == datetime.fromtimestamp(1756108800, tz=timezone.utc)


@pytest.mark.parametrize("payload", [b"lixo", b"[]", b"null", b"\xff", b"{}"])
def test_bad_json(payload):
    assert parse_reading(TOPIC, payload) is None


def test_invalid_metric_is_removed_alone():
    reading = parse(ph=99)
    assert reading.ph is None and reading.temperature_c == 26


def test_no_main_metric_is_rejected():
    assert parse(temperature_c=None, ph=None, level_pct=None, turbidity_ntu=None, distance_cm=20) is None


@pytest.mark.parametrize("ts", [None, 0, 1700000000, 10**30, float("inf")])
def test_timestamp_fallback(ts):
    before = datetime.now(timezone.utc)
    assert before <= parse(ts=ts).time <= datetime.now(timezone.utc)


def test_topic_takes_precedence(caplog):
    reading = parse(tank_id="outro")
    assert reading.tank_id == "tanque-01"
    assert "divergente" in caplog.text


@pytest.mark.parametrize("field", ["device_id", "tank_id"])
def test_required_identifiers(field):
    assert parse(**{field: None}) is None


def test_zero_is_a_valid_sensor_value():
    assert parse(level_pct=0, turbidity_ntu=0).level_pct == 0
