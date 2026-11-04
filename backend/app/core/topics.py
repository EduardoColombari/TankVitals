"""Tópicos MQTT e routing keys do RabbitMQ (docs/ARQUITETURA.md §13 e §14.1).

    MQTT      tankvitals/<zona>/<tipo>/<device>/telemetry
              tankvitals/<zona>/<tipo>/<device>/cmd
              tankvitals/sim/status
    RabbitMQ  telemetry.<zona>.<tipo>.<device>      (exchange tv.telemetry)
              status.sim                            (exchange tv.telemetry)
              command.<zona>.<tipo>.<device>        (exchange tv.commands)

A routing key NÃO é a troca direta de "/" por ".": o prefixo `tankvitals` sai e
o sufixo (`telemetry`, `cmd`) vira o começo. É isso que faz os bindings das
filas (`telemetry.#`, `telemetry.*.equipment.*`) baterem.
"""

from __future__ import annotations

from dataclasses import dataclass

from .envelope import ID_RE

TOPIC_ROOT = "tankvitals"
KINDS: tuple[str, ...] = ("sensor", "equipment", "actuator")

SIM_STATUS_TOPIC = f"{TOPIC_ROOT}/sim/status"
SIM_STATUS_ROUTING_KEY = "status.sim"

TELEMETRY_SUBSCRIPTION = f"{TOPIC_ROOT}/+/+/+/telemetry"
COMMAND_SUBSCRIPTION = f"{TOPIC_ROOT}/+/+/+/cmd"

EX_TELEMETRY = "tv.telemetry"
EX_COMMANDS = "tv.commands"
EX_EVENTS = "tv.events"
EX_DLX = "tv.dlx"
Q_COMMANDS = "q.commands"
Q_RULES = "q.rules"


class TopicError(ValueError):
    """Tópico ou routing key fora do formato combinado."""


@dataclass(frozen=True)
class DeviceTopic:
    zone: str
    kind: str
    device: str
    leaf: str  # "telemetry" ou "cmd"


def _check(zone: str, kind: str, device: str) -> None:
    if not ID_RE.match(zone):
        raise TopicError(f"zona inválida: {zone!r}")
    if kind not in KINDS:
        raise TopicError(f"tipo inválido: {kind!r} (use {', '.join(KINDS)})")
    if not ID_RE.match(device):
        raise TopicError(f"id de dispositivo inválido: {device!r}")


def telemetry_topic(zone: str, kind: str, device: str) -> str:
    _check(zone, kind, device)
    return f"{TOPIC_ROOT}/{zone}/{kind}/{device}/telemetry"


def cmd_topic(zone: str, kind: str, device: str) -> str:
    _check(zone, kind, device)
    return f"{TOPIC_ROOT}/{zone}/{kind}/{device}/cmd"


def parse_topic(topic: str) -> DeviceTopic:
    """Separa `tankvitals/<zona>/<tipo>/<device>/<telemetry|cmd>` em partes validadas."""
    parts = topic.split("/")
    if len(parts) != 5 or parts[0] != TOPIC_ROOT:
        raise TopicError(f"tópico fora do formato tankvitals/<zona>/<tipo>/<device>/<telemetry|cmd>: {topic!r}")
    _, zone, kind, device, leaf = parts
    if leaf not in ("telemetry", "cmd"):
        raise TopicError(f"o tópico deve terminar em telemetry ou cmd: {topic!r}")
    _check(zone, kind, device)
    return DeviceTopic(zone, kind, device, leaf)


def telemetry_routing_key(zone: str, kind: str, device: str) -> str:
    _check(zone, kind, device)
    return f"telemetry.{zone}.{kind}.{device}"


def command_routing_key(zone: str, kind: str, device: str) -> str:
    _check(zone, kind, device)
    return f"command.{zone}.{kind}.{device}"


def parse_command_routing_key(routing_key: str) -> DeviceTopic:
    """`command.<zona>.<tipo>.<device>` → partes validadas (a leaf é sempre "cmd")."""
    parts = routing_key.split(".")
    if len(parts) != 4 or parts[0] != "command":
        raise TopicError(f"routing key fora do formato command.<zona>.<tipo>.<device>: {routing_key!r}")
    _, zone, kind, device = parts
    _check(zone, kind, device)
    return DeviceTopic(zone, kind, device, "cmd")


def parse_telemetry_routing_key(routing_key: str) -> DeviceTopic:
    """`telemetry.<zona>.<tipo>.<device>` → partes validadas (a leaf é sempre "telemetry")."""
    parts = routing_key.split(".")
    if len(parts) != 4 or parts[0] != "telemetry":
        raise TopicError(f"routing key fora do formato telemetry.<zona>.<tipo>.<device>: {routing_key!r}")
    _, zone, kind, device = parts
    _check(zone, kind, device)
    return DeviceTopic(zone, kind, device, "telemetry")


def cmd_topic_from_routing_key(routing_key: str) -> str:
    """O caminho de volta do gateway: da routing key do comando para o `/cmd` do MQTT."""
    parsed = parse_command_routing_key(routing_key)
    return cmd_topic(parsed.zone, parsed.kind, parsed.device)
