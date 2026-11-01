"""Tópicos, routing keys e envelope (contrato das §12, §13 e §14.1)."""
import json

import pytest

from app.core import envelope as env
from app.core import topics
from app.core.ranges import Range, classify


def test_telemetry_topic_and_routing_key_follow_the_contract():
    assert topics.telemetry_topic("tanque-01", "sensor", "od-01") == "tankvitals/tanque-01/sensor/od-01/telemetry"
    assert topics.cmd_topic("tanque-01", "actuator", "aerador-01") == "tankvitals/tanque-01/actuator/aerador-01/cmd"
    assert topics.telemetry_routing_key("tanque-01", "sensor", "od-01") == "telemetry.tanque-01.sensor.od-01"
    assert topics.command_routing_key("tanque-01", "actuator", "aerador-01") == "command.tanque-01.actuator.aerador-01"


def test_routing_key_is_not_a_plain_slash_to_dot_swap():
    # A troca direta daria "tankvitals.tanque-01.sensor.od-01.telemetry", que nenhum binding pega.
    topic = topics.telemetry_topic("tanque-01", "sensor", "od-01")
    assert topics.telemetry_routing_key("tanque-01", "sensor", "od-01") != topic.replace("/", ".")


def test_parse_topic_round_trip():
    parsed = topics.parse_topic("tankvitals/tanque-02/equipment/bomba-rec-01/cmd")
    assert (parsed.zone, parsed.kind, parsed.device, parsed.leaf) == ("tanque-02", "equipment", "bomba-rec-01", "cmd")


@pytest.mark.parametrize(
    "bad",
    [
        "tankvitals/tanque-01/sensor/od-01",  # falta a folha
        "tankvitals/tanque-01/sensor/od-01/telemetry/extra",
        "outro/tanque-01/sensor/od-01/telemetry",
        "tankvitals/tanque-01/robo/od-01/telemetry",  # tipo desconhecido
        "tankvitals/Tanque_01/sensor/od-01/telemetry",  # maiúscula e sublinhado
        "tankvitals/tanque-01/sensor/od.01/telemetry",  # ponto vira separador no RabbitMQ
        "tankvitals/tanque-01/sensor/od-01/status",
        "tankvitals/+/+/+/telemetry",  # curinga nunca é um tópico de verdade
    ],
)
def test_parse_topic_rejects_bad_topics(bad):
    with pytest.raises(topics.TopicError):
        topics.parse_topic(bad)


def test_command_routing_key_to_mqtt_topic():
    assert (
        topics.cmd_topic_from_routing_key("command.tanque-01.actuator.aerador-01")
        == "tankvitals/tanque-01/actuator/aerador-01/cmd"
    )
    for bad in ("command.tanque-01.actuator", "telemetry.tanque-01.sensor.od-01", "command.a.b.c.d"):
        with pytest.raises(topics.TopicError):
            topics.cmd_topic_from_routing_key(bad)


def test_make_envelope_has_exactly_the_five_fields():
    message = env.make_envelope("telemetry", "od-01", {"oxygen_mg_l": 6.82})
    assert set(message) == {"id", "ts", "type", "device", "payload"}
    assert message["ts"].endswith("Z")
    assert env.parse_ts(message["ts"]).tzinfo is not None


def test_make_envelope_rejects_unknown_type_and_bad_device():
    with pytest.raises(ValueError):
        env.make_envelope("telemetria", "od-01", {})
    with pytest.raises(ValueError):
        env.make_envelope("telemetry", "OD.01", {})


def test_dumps_is_compact_json_and_refuses_nan():
    text = env.dumps(env.make_envelope("telemetry", "od-01", {"oxygen_mg_l": 6.82}))
    assert " " not in text
    assert json.loads(text)["payload"] == {"oxygen_mg_l": 6.82}
    with pytest.raises(ValueError):
        env.dumps(env.make_envelope("telemetry", "od-01", {"oxygen_mg_l": float("nan")}))


def test_command_payload_matches_the_examples_of_the_contract():
    assert env.command_payload("on", True, origin="web", user="operador") == {
        "command": "on", "value": True, "origin": "web", "user": "operador",
    }
    assert env.command_payload("repair", origin="web", user="operador") == {
        "command": "repair", "origin": "web", "user": "operador",
    }
    assert env.command_payload("on", False, origin="regra", user=None)["user"] is None


def test_status_envelope_is_from_the_sim_device():
    message = env.status_envelope(False)
    assert message["type"] == "status" and message["device"] == "sim" and message["payload"] == {"online": False}


def test_classify_matches_the_frontend_rule_and_limits_count_as_inside():
    oxygen = Range(ok_min=5, ok_max=None, crit_min=3, crit_max=None)
    assert classify(5.0, oxygen) == "ok"  # o limite conta como dentro
    assert classify(4.99, oxygen) == "atencao"
    assert classify(2.99, oxygen) == "critico"
    temp = Range(24, 28, 22, 30)
    assert classify(28.0, temp) == "ok" and classify(28.1, temp) == "atencao" and classify(30.1, temp) == "critico"
    assert classify(999, None) == "ok" and classify(999, Range()) == "ok"
