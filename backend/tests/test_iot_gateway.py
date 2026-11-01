"""O gateway: o que vai para o RabbitMQ, o que vai para a DLQ e como o comando volta (IOT-02)."""
import asyncio
import json

import pytest

pytest.importorskip("pydantic")

from app.gateway.bridge import BadCommand, DeadLetter, Forward, route_command, route_mqtt_message  # noqa: E402
from app.gateway.service import CommandOutcome, GatewayService  # noqa: E402

TELEMETRY_TOPIC = "tankvitals/tanque-01/sensor/od-01/telemetry"
ID = "0f6c1d0e-5b7a-4a43-9a52-7c3e2f1b9d10"


def envelope(type_="telemetry", device="od-01", payload=None, **overrides):
    message = {
        "id": ID,
        "ts": "2026-11-16T13:45:00Z",
        "type": type_,
        "device": device,
        "payload": {"oxygen_mg_l": 6.82} if payload is None else payload,
    }
    message.update(overrides)
    return json.dumps(message).encode()


# --- MQTT → RabbitMQ ----------------------------------------------------------------------


def test_valid_telemetry_goes_to_tv_telemetry_with_the_routing_key_from_the_topic():
    body = envelope()
    decision = route_mqtt_message(TELEMETRY_TOPIC, body)
    assert isinstance(decision, Forward)
    assert decision.exchange == "tv.telemetry"
    assert decision.routing_key == "telemetry.tanque-01.sensor.od-01"
    assert decision.body == body  # os bytes originais, sem reescrever o JSON
    assert decision.message_id == ID


def test_routing_key_uses_zone_kind_and_device_of_the_topic():
    topic = "tankvitals/tanque-02/equipment/bomba-rec-01/telemetry"
    body = envelope(device="bomba-rec-01", payload={"vibration_mm_s": 1.1, "running": True})
    assert route_mqtt_message(topic, body).routing_key == "telemetry.tanque-02.equipment.bomba-rec-01"


def test_sim_status_goes_to_tv_telemetry_as_status_sim():
    body = envelope("status", "sim", {"online": False})
    decision = route_mqtt_message("tankvitals/sim/status", body)
    assert isinstance(decision, Forward)
    assert (decision.exchange, decision.routing_key) == ("tv.telemetry", "status.sim")


@pytest.mark.parametrize(
    "body",
    [
        b"{quebrado",  # JSON quebrado (o caso da apresentação)
        b"",
        b"[]",
        b"null",
        b'{"id": "1"}',  # faltam campos
        envelope(id="nao-e-uuid"),
        envelope(ts="ontem"),
        envelope(ts="2026-11-16T13:45:00"),  # sem fuso
        envelope(type="telemetria"),
        envelope(device="OD 01"),
        envelope(payload="texto"),
        envelope(extra="campo a mais"),
        b'{"id":"0f6c1d0e-5b7a-4a43-9a52-7c3e2f1b9d10","ts":"2026-11-16T13:45:00Z","type":"telemetry","device":"od-01","payload":{"x":NaN}}',
        b"x" * 100_000,
    ],
)
def test_invalid_message_goes_to_the_dlq_with_the_original_body_and_the_reason(body):
    decision = route_mqtt_message(TELEMETRY_TOPIC, body)
    assert isinstance(decision, DeadLetter)
    assert decision.body == body  # o corpo original, intacto
    assert decision.reason
    assert decision.headers["x-reason"] == decision.reason
    assert decision.headers["x-source"] == TELEMETRY_TOPIC


def test_envelope_must_agree_with_the_topic():
    assert isinstance(route_mqtt_message(TELEMETRY_TOPIC, envelope(device="ph-01")), DeadLetter)
    assert isinstance(route_mqtt_message(TELEMETRY_TOPIC, envelope(type_="command")), DeadLetter)
    assert isinstance(route_mqtt_message("tankvitals/sim/status", envelope()), DeadLetter)
    assert isinstance(route_mqtt_message("tankvitals/sim/status", envelope("status", "sim", {"online": "sim"})), DeadLetter)


def test_unexpected_topics_go_to_the_dlq():
    for topic in ("lixo", "tankvitals/tanque-01/sensor/od-01/cmd", "tankvitals/tanque-01/robo/od-01/telemetry"):
        assert isinstance(route_mqtt_message(topic, envelope()), DeadLetter), topic


def test_validator_is_injectable():
    seen = []

    def fake(raw):
        seen.append(raw)
        raise __import__("app.core.schema", fromlist=["EnvelopeError"]).EnvelopeError("sempre recusa")

    assert isinstance(route_mqtt_message(TELEMETRY_TOPIC, envelope(), validate=fake), DeadLetter)
    assert seen


# --- RabbitMQ → MQTT ----------------------------------------------------------------------


COMMAND = envelope("command", "aerador-01", {"command": "on", "value": True, "origin": "web", "user": "operador"})


def test_command_goes_to_the_cmd_topic_of_the_device():
    command = route_command("command.tanque-01.actuator.aerador-01", COMMAND)
    assert command.topic == "tankvitals/tanque-01/actuator/aerador-01/cmd"
    assert command.body == COMMAND


@pytest.mark.parametrize(
    "routing_key, body",
    [
        ("command.tanque-01.actuator", COMMAND),
        ("telemetry.tanque-01.sensor.od-01", COMMAND),
        ("command.tanque-01.actuator.aerador-01", b"{quebrado"),
        ("command.tanque-01.actuator.aerador-01", envelope()),  # é telemetria, não comando
        ("command.tanque-01.actuator.bomba-repo-01", COMMAND),  # device diferente da routing key
        ("command.tanque-01.actuator.aerador-01", envelope("command", "aerador-01", {"value": True})),
    ],
)
def test_bad_command_is_rejected(routing_key, body):
    with pytest.raises(BadCommand):
        route_command(routing_key, body)


# --- o serviço: portas falsas -------------------------------------------------------------------


class Ports:
    def __init__(self, mqtt_fails=False):
        self.rabbit = []
        self.mqtt = []
        self.mqtt_fails = mqtt_fails

    async def rabbit_publish(self, exchange, routing_key, body, **options):
        self.rabbit.append((exchange, routing_key, body, options))

    async def mqtt_publish(self, topic, body, qos):
        if self.mqtt_fails:
            raise ConnectionError("Mosquitto fora do ar")
        self.mqtt.append((topic, body, qos))


def test_service_forwards_valid_telemetry_to_rabbitmq():
    ports = Ports()
    service = GatewayService(ports.rabbit_publish, ports.mqtt_publish)
    body = envelope()
    asyncio.run(service.on_mqtt_message(TELEMETRY_TOPIC, body))
    exchange, routing_key, sent, options = ports.rabbit[0]
    assert (exchange, routing_key, sent) == ("tv.telemetry", "telemetry.tanque-01.sensor.od-01", body)
    assert options["message_id"] == ID


def test_a_broken_json_published_on_mqtt_shows_up_in_the_dlq():
    ports = Ports()
    service = GatewayService(ports.rabbit_publish, ports.mqtt_publish)
    asyncio.run(service.on_mqtt_message(TELEMETRY_TOPIC, b'{"id": "quebrado'))
    exchange, routing_key, sent, options = ports.rabbit[0]
    assert exchange == "tv.dlx"
    assert sent == b'{"id": "quebrado'  # corpo original intacto
    assert "x-reason" in options["headers"]
    assert options["persistent"] is True


def test_command_is_acked_only_after_the_mqtt_publish_worked():
    ports = Ports()
    service = GatewayService(ports.rabbit_publish, ports.mqtt_publish)
    outcome = asyncio.run(service.on_command("command.tanque-01.actuator.aerador-01", COMMAND))
    assert outcome is CommandOutcome.ACK
    assert ports.mqtt == [("tankvitals/tanque-01/actuator/aerador-01/cmd", COMMAND, 1)]  # QoS 1


def test_command_goes_back_to_the_queue_if_mqtt_fails_or_is_down():
    ports = Ports(mqtt_fails=True)
    service = GatewayService(ports.rabbit_publish, ports.mqtt_publish)
    assert asyncio.run(service.on_command("command.tanque-01.actuator.aerador-01", COMMAND)) is CommandOutcome.REQUEUE
    service.bind_mqtt(None)  # enquanto o Mosquitto está fora
    assert asyncio.run(service.on_command("command.tanque-01.actuator.aerador-01", COMMAND)) is CommandOutcome.REQUEUE


def test_invalid_command_is_rejected_without_requeue():
    ports = Ports()
    service = GatewayService(ports.rabbit_publish, ports.mqtt_publish)
    assert asyncio.run(service.on_command("command.tanque-01.actuator.aerador-01", b"lixo")) is CommandOutcome.REJECT
    assert ports.mqtt == []
