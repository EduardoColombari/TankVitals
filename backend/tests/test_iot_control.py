"""POST /api/devices/{id}/command (IOT-04): a lógica da rota e, se o FastAPI estiver instalado, a rota em si."""
import asyncio
import json

import pytest

from app.control.service import CommandError, CommandService, DeviceInfo, validate_command

PUMP = DeviceInfo("bomba-rec-01", "Bomba de recirculação", "equipment", "tanque-01", True, ("on", "inject_fault", "repair"))
REFILL = DeviceInfo("bomba-repo-01", "Reposição de água", "actuator", "tanque-01", True, ("on",))
SENSOR = DeviceInfo("od-01", "Sonda de oxigênio", "sensor", "tanque-01", True, ())
OFF = DeviceInfo("aerador-01", "Aerador", "actuator", "tanque-01", False, ("on",))


class FakeStore:
    def __init__(self, *devices):
        self.devices = {d.id: d for d in devices}
        self.commands, self.audit = [], []
        self.fail_audit = False

    def get_device(self, device_id): return self.devices.get(device_id)

    def record_command(self, device_id, command, value, *, username):
        self.commands.append((device_id, command, value, username))

    def record_audit(self, *, username, target, detail, ok):
        if self.fail_audit:
            raise RuntimeError("banco fora")
        self.audit.append((username, target, detail, ok))


class FakeBus:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def publish(self, exchange, routing_key, body, **options):
        if self.fail:
            raise ConnectionError("RabbitMQ fora")
        self.sent.append((exchange, routing_key, json.loads(body), options))


async def inline(fn, *args):
    return fn(*args)


def make(*devices, bus=None):
    store, bus = FakeStore(*devices), bus or FakeBus()
    return CommandService(store, bus.publish, run_blocking=inline), store, bus


def send(service, device, command, value=None, user="operador"):
    return asyncio.run(service.send(username=user, device_id=device, command=command, value=value))


def error_of(service, *args, **kwargs):
    with pytest.raises(CommandError) as info:
        send(service, *args, **kwargs)
    return info.value


# --- validação -----------------------------------------------------------------------------------


def test_validate_accepts_the_three_commands_where_the_device_supports_them():
    assert validate_command(PUMP, "on", True) is True
    assert validate_command(PUMP, "on", False) is False
    assert validate_command(PUMP, "inject_fault", "rolamento") == "rolamento"
    assert validate_command(PUMP, "repair", "qualquer coisa") is None
    assert validate_command(REFILL, "on", True) is True


@pytest.mark.parametrize(
    "device, command, value",
    [
        (SENSOR, "on", True),  # sensor não aceita comando
        (REFILL, "inject_fault", "rolamento"),  # atuador não aceita falha
        (REFILL, "repair", None),
        (PUMP, "reiniciar", None),
        (PUMP, "on", "true"),
        (PUMP, "on", 1),
        (PUMP, "on", None),
        (PUMP, "inject_fault", "explodir"),
        (PUMP, "inject_fault", None),
    ],
)
def test_validate_rejects_with_422(device, command, value):
    with pytest.raises(CommandError) as info:
        validate_command(device, command, value)
    assert info.value.status == 422 and info.value.detail


# --- o fluxo ---------------------------------------------------------------------------------------


def test_success_publishes_in_tv_commands_with_the_device_routing_key_and_the_web_origin():
    service, store, bus = make(REFILL)
    ack = send(service, "bomba-repo-01", "on", True)
    exchange, routing_key, message, options = bus.sent[0]
    assert (exchange, routing_key) == ("tv.commands", "command.tanque-01.actuator.bomba-repo-01")
    assert message["type"] == "command" and message["device"] == "bomba-repo-01"
    assert message["payload"] == {"command": "on", "value": True, "origin": "web", "user": "operador"}
    assert options["persistent"] is True and options["message_id"] == message["id"]
    # A resposta só diz que mandou: nada de "novo estado". Quem confirma é a telemetria.
    assert ack == {"id": message["id"], "status": "sent"}


def test_equipment_routing_key_uses_its_kind():
    service, _, bus = make(PUMP)
    send(service, "bomba-rec-01", "inject_fault", "superaquecimento")
    assert bus.sent[0][1] == "command.tanque-01.equipment.bomba-rec-01"
    send(service, "bomba-rec-01", "repair")
    assert bus.sent[1][2]["payload"] == {"command": "repair", "origin": "web", "user": "operador"}  # sem value


def test_command_and_audit_are_recorded_with_who_and_what():
    service, store, _ = make(PUMP)
    send(service, "bomba-rec-01", "inject_fault", "rolamento", user="ana")
    assert store.commands == [("bomba-rec-01", "inject_fault", "rolamento", "ana")]
    assert store.audit == [("ana", "bomba-rec-01", "inject_fault = rolamento", True)]
    send(service, "bomba-rec-01", "on", False, user="ana")
    assert store.audit[-1][2] == "on = false"  # o mesmo texto que a tela de auditoria mostra
    send(service, "bomba-rec-01", "repair", user="ana")
    assert store.audit[-1][2] == "repair"


def test_unknown_device_is_404_and_nothing_is_published():
    service, store, bus = make(REFILL)
    err = error_of(service, "nao-existe", "on", True)
    assert err.status == 404 and "nao-existe" in err.detail
    assert bus.sent == [] and store.commands == []
    assert store.audit[-1][3] is False  # a tentativa fica na auditoria


def test_device_that_does_not_accept_the_command_is_422_and_audited_as_refused():
    service, store, bus = make(SENSOR)
    err = error_of(service, "od-01", "on", True)
    assert err.status == 422
    assert bus.sent == [] and store.commands == [] and store.audit[-1][3] is False


def test_deactivated_device_is_409():
    service, _, bus = make(OFF)
    assert error_of(service, "aerador-01", "on", True).status == 409
    assert bus.sent == []


def test_bus_failure_is_503_and_the_audit_says_it_did_not_arrive():
    service, store, _ = make(REFILL, bus=FakeBus(fail=True))
    err = error_of(service, "bomba-repo-01", "on", True)
    assert err.status == 503
    assert store.audit[-1][3] is False and "barramento" in store.audit[-1][2]


def test_audit_failure_never_breaks_the_response():
    service, store, bus = make(REFILL)
    store.fail_audit = True
    assert send(service, "bomba-repo-01", "on", True)["status"] == "sent"
    assert len(bus.sent) == 1


# --- a rota (só roda com FastAPI instalado) ---------------------------------------------------------


def build_client(role_ok=True):
    pytest.importorskip("pydantic")
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.control.router import create_command_router

    service, store, bus = make(REFILL, PUMP)

    def require_operator():
        if not role_ok:
            raise fastapi.HTTPException(status_code=403, detail="Seu papel não pode fazer isso.")
        return {"username": "operador", "role": "operator"}

    app = fastapi.FastAPI()
    app.include_router(create_command_router(service, require_operator=require_operator), prefix="/api")
    return TestClient(app), store, bus


def test_route_sends_the_command_and_returns_id_and_sent():
    client, store, bus = build_client()
    response = client.post("/api/devices/bomba-repo-01/command", json={"command": "on", "value": True})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "sent" and set(body) == {"id", "status"}
    assert bus.sent[0][2]["payload"]["user"] == "operador"


def test_route_maps_errors_to_http_status_and_detail():
    client, _, _ = build_client()
    missing = client.post("/api/devices/nao-existe/command", json={"command": "on", "value": True})
    assert missing.status_code == 404 and "detail" in missing.json()
    refused = client.post("/api/devices/bomba-repo-01/command", json={"command": "repair"})
    assert refused.status_code == 422
    strict = client.post("/api/devices/bomba-repo-01/command", json={"command": "on", "value": 1})
    assert strict.status_code == 422
    extra = client.post("/api/devices/bomba-repo-01/command", json={"command": "on", "value": True, "x": 1})
    assert extra.status_code == 422


def test_route_leaves_the_authorization_to_the_dependency_viewer_gets_403():
    client, store, bus = build_client(role_ok=False)
    response = client.post("/api/devices/bomba-repo-01/command", json={"command": "on", "value": True})
    assert response.status_code == 403
    assert bus.sent == [] and store.commands == []
