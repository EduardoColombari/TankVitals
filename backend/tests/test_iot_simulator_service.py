"""O laço do simulador e os comandos pelo MQTT (IOT-01, IOT-03) e o teste do IOT-08."""
import asyncio
import json

import pytest

from app.simulator.commands import InvalidCommand, parse_command
from app.simulator.devices import AERATOR_ID, DEVICES, FAULT_MODES, PUMP_ID, REFILL_ID
from app.simulator.pump import FAULT_SIM_MINUTES
from app.simulator.service import SimulatorService
from app.simulator.world import SimWorld

CMD = "tankvitals/tanque-01/{kind}/{device}/cmd"


class Bus:
    """Um MQTT de mentira: guarda o que o simulador publicou."""

    def __init__(self):
        self.sent = []  # (topic, envelope, qos, retain)

    async def publish(self, topic, body, qos, retain):
        self.sent.append((topic, json.loads(body), qos, retain))


def make(seed=1, speed=60.0, **kwargs):
    bus = Bus()
    world = SimWorld(seed=seed, start_minute=12 * 60)
    return SimulatorService(world, bus.publish, speed=speed, **kwargs), bus, world


def command_message(device, command, value=None, *, kind="actuator", device_in_topic=None):
    payload = {"command": command, "origin": "web", "user": "operador"}
    if value is not None:
        payload["value"] = value
    envelope = {
        "id": "0f6c1d0e-5b7a-4a43-9a52-7c3e2f1b9d10",
        "ts": "2026-11-16T13:45:00Z",
        "type": "command",
        "device": device,
        "payload": payload,
    }
    return CMD.format(kind=kind, device=device_in_topic or device), json.dumps(envelope).encode()


# --- IOT-01: publicação ------------------------------------------------------------------


def test_one_step_publishes_nine_messages_on_the_agreed_topics_in_the_agreed_format():
    service, bus, _ = make()
    asyncio.run(service.publish_step())
    assert len(bus.sent) == 9
    expected = {f"tankvitals/tanque-01/{d.kind}/{d.id}/telemetry" for d in DEVICES}
    assert {topic for topic, *_ in bus.sent} == expected
    for topic, message, qos, retain in bus.sent:
        assert set(message) == {"id", "ts", "type", "device", "payload"}
        assert message["type"] == "telemetry"
        assert topic.split("/")[3] == message["device"]
        assert (qos, retain) == (0, False)  # telemetria: QoS 0 e sem retained (§13)
    assert len({message["id"] for _, message, _, _ in bus.sent}) == 9  # um id por mensagem


def test_status_announcement_is_retained_qos1_on_the_status_topic():
    service, bus, _ = make()
    asyncio.run(service.announce(True))
    topic, message, qos, retain = bus.sent[0]
    assert topic == "tankvitals/sim/status"
    assert (message["type"], message["device"], message["payload"]) == ("status", "sim", {"online": True})
    assert (qos, retain) == (1, True)


def test_run_publishes_one_step_per_interval_without_drift():
    now = [0.0]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds
        if len(slept) >= 5:
            stop.set()

    stop = asyncio.Event()
    service, bus, world = make(speed=60.0, monotonic=lambda: now[0], sleep=sleep)
    asyncio.run(service.run(stop))
    assert service.interval_s == 1.0  # SIM_SPEED=60: 1 s real = 1 min simulado
    assert world.minute == 12 * 60 + 5
    assert all(abs(s - 1.0) < 1e-9 for s in slept)
    assert len(bus.sent) == 9 * 5


def test_speed_changes_the_interval_and_the_length_of_a_day():
    assert make(speed=60.0)[0].interval_s == 1.0
    assert make(speed=120.0)[0].interval_s == 0.5
    assert make(speed=6.0)[0].interval_s == 10.0
    with pytest.raises(ValueError):
        make(speed=0)
    # Um dia = 1440 passos; a 60 passos por minuto real, são 24 min reais.
    assert 1440 * make(speed=60.0)[0].interval_s / 60 == 24


def test_run_does_not_burst_to_catch_up_after_a_long_stall():
    now = [0.0]

    async def sleep(seconds):
        stop.set()

    stop = asyncio.Event()
    service, bus, world = make(monotonic=lambda: now[0], sleep=sleep)

    async def go():
        await service.publish_step()
        now[0] = 100.0  # a máquina travou por 100 s
        await service.run(stop)

    asyncio.run(go())
    assert world.minute == 12 * 60 + 2  # um passo antes do travamento e um depois: sem rajada de 100


# --- IOT-03: comandos ---------------------------------------------------------------------------


def test_command_turns_the_actuator_on_and_confirms_right_away_with_its_own_telemetry():
    service, bus, world = make()
    topic, raw = command_message(AERATOR_ID, "on", True)
    assert asyncio.run(service.handle_cmd(topic, raw)) is True
    assert world.aerator_on is True
    sent_topic, message, qos, retain = bus.sent[-1]
    assert sent_topic == "tankvitals/tanque-01/actuator/aerador-01/telemetry"
    assert message["payload"] == {"on": True}  # a confirmação de que o comando chegou


def test_command_for_the_pump_applies_and_the_next_step_shows_it():
    service, bus, world = make()
    topic, raw = command_message(PUMP_ID, "on", False, kind="equipment")
    assert asyncio.run(service.handle_cmd(topic, raw)) is True
    assert bus.sent == []  # a bomba confirma no passo seguinte, com leituras novas
    asyncio.run(service.publish_step())
    pump = next(m for t, m, *_ in bus.sent if m["device"] == PUMP_ID)
    assert pump["payload"]["running"] is False and pump["payload"]["flow_l_min"] == 0


def test_inject_fault_and_repair_commands():
    service, _, world = make()
    topic, raw = command_message(PUMP_ID, "inject_fault", "superaquecimento", kind="equipment")
    assert asyncio.run(service.handle_cmd(topic, raw)) is True
    topic, raw = command_message(PUMP_ID, "repair", kind="equipment")
    assert asyncio.run(service.handle_cmd(topic, raw)) is True


@pytest.mark.parametrize(
    "topic, raw",
    [
        ("tankvitals/tanque-01/actuator/aerador-01/cmd", b"{isto nao e json"),
        ("tankvitals/tanque-01/actuator/aerador-01/cmd", b""),
        ("tankvitals/tanque-01/actuator/aerador-01/cmd", b"[1, 2, 3]"),
        ("tankvitals/tanque-01/actuator/aerador-01/cmd", b'{"id": "x"}'),
        ("tankvitals/tanque-01/actuator/aerador-01/cmd", b'\xff\xfe\x00'),
        ("tankvitals/tanque-01/actuator/aerador-01/cmd", b'{"payload": {"command": "on", "value": NaN}}'),
        ("tankvitals/tanque-01/actuator/aerador-01/cmd", b"x" * 100_000),
        ("lixo/qualquer/coisa", b"{}"),
        ("tankvitals/tanque-01/actuator/aerador-01/telemetry", b"{}"),
    ],
)
def test_malformed_command_is_only_a_log_and_never_crashes_the_simulator(topic, raw):
    service, bus, world = make()
    assert asyncio.run(service.handle_cmd(topic, raw)) is False
    assert world.aerator_on is False and bus.sent == []


def test_command_rejected_by_the_device_is_ignored():
    service, bus, world = make()
    for device, command, value, kind in (
        ("od-01", "on", True, "sensor"),  # sensor não aceita comando
        (AERATOR_ID, "inject_fault", "rolamento", "actuator"),
        (AERATOR_ID, "on", "sim", "actuator"),
    ):
        topic, raw = command_message(device, command, value, kind=kind)
        assert asyncio.run(service.handle_cmd(topic, raw)) is False
    assert world.aerator_on is False and bus.sent == []


def test_envelope_device_must_match_the_topic():
    service, _, world = make()
    topic, raw = command_message(AERATOR_ID, "on", True, device_in_topic=REFILL_ID)
    assert asyncio.run(service.handle_cmd(topic, raw)) is False
    assert world.aerator_on is False and world.refill_on is False


def test_unexpected_error_in_a_command_does_not_escape():
    service, _, world = make()

    def boom(*args, **kwargs):
        raise RuntimeError("defeito qualquer")

    world.apply_command = boom
    topic, raw = command_message(AERATOR_ID, "on", True)
    assert asyncio.run(service.handle_cmd(topic, raw)) is False


def test_the_simulator_keeps_running_after_a_bad_command():
    service, bus, _ = make()
    asyncio.run(service.handle_cmd("tankvitals/tanque-01/actuator/aerador-01/cmd", b"{quebrado"))
    asyncio.run(service.publish_step())
    assert len(bus.sent) == 9


def test_parse_command_reads_origin_and_user():
    topic, raw = command_message(AERATOR_ID, "on", True)
    command = parse_command(topic, raw)
    assert (command.device, command.command, command.value, command.origin, command.user) == (
        AERATOR_ID, "on", True, "web", "operador",
    )
    with pytest.raises(InvalidCommand):
        parse_command(topic, b'{"id":"a","ts":"b","type":"telemetry","device":"aerador-01","payload":{}}')


# --- IOT-08: o rótulo nunca vai na telemetria ---------------------------------------------------

# Pedaços que só existiriam num rótulo: horas até a falha, saúde, modo de falha, desgaste.
FORBIDDEN = ("rul", "health", "saude", "fault", "falha", "failure", "mode", "wear", "progress", "label", "hours", "horas", "life")

ALLOWED_KEYS = {
    PUMP_ID: {"vibration_mm_s", "current_a", "motor_temp_c", "flow_l_min", "running"},
    "od-01": {"oxygen_mg_l"},
    "temp-agua-01": {"temperature_c"},
    "ph-01": {"ph"},
    "nivel-01": {"level_pct"},
    "turbidez-01": {"turbidity_ntu"},
    "luz-01": {"light_lux"},
    AERATOR_ID: {"on"},
    REFILL_ID: {"on"},
}


def walk_keys(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield f"{path}/{key}", key
            yield from walk_keys(item, f"{path}/{key}")
    elif isinstance(value, list):
        for item in value:
            yield from walk_keys(item, path)


def test_no_published_message_carries_the_simulation_label():
    """IOT-08: horas até a falha, saúde e modo de falha nunca vão na telemetria, senão o modelo "cola" na prova."""
    service, bus, _ = make(seed=11)

    async def scenario():
        for mode in FAULT_MODES:
            await service.handle_cmd(*command_message(PUMP_ID, "inject_fault", mode, kind="equipment"))
            for _ in range(FAULT_SIM_MINUTES + 30):  # a falha inteira, até o desgaste máximo
                await service.publish_step()
            await service.handle_cmd(*command_message(PUMP_ID, "repair", kind="equipment"))
            for _ in range(10):
                await service.publish_step()

    asyncio.run(scenario())
    assert len(bus.sent) > 9 * 4 * FAULT_SIM_MINUTES

    for topic, message, _, _ in bus.sent:
        device = message["device"]
        # A forma do envelope e as chaves do payload são exatamente as combinadas.
        assert set(message) == {"id", "ts", "type", "device", "payload"}
        assert set(message["payload"]) == ALLOWED_KEYS[device], (device, message["payload"])
        for path, key in walk_keys(message):
            assert not any(bad in key.lower() for bad in FORBIDDEN), f"campo suspeito na telemetria: {path}"
        # Só números e booleanos: nem o nome do modo de falha cabe num valor.
        for value in message["payload"].values():
            assert isinstance(value, (int, float, bool)), (device, value)
    raw = json.dumps([m for _, m, _, _ in bus.sent])
    for mode in FAULT_MODES:
        assert mode not in raw


def test_the_hidden_label_is_not_reachable_from_a_reading():
    service, bus, world = make()
    world.apply_command(PUMP_ID, "inject_fault", "rolamento")
    reading = next(r for r in world.step() if r.device.id == PUMP_ID)
    assert set(reading.payload) == ALLOWED_KEYS[PUMP_ID]
