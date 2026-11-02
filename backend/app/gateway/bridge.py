"""As decisões da ponte, sem rede: para onde vai cada mensagem (IOT-02).

* telemetria do MQTT → `tv.telemetry`, com a routing key tirada do tópico;
* mensagem inválida → `tv.dlx`, com o corpo original intacto e o motivo no
  header `x-reason` (o que chegou é o que a DLQ guarda, para dar para
  investigar);
* comando do RabbitMQ (`q.commands`) → o `/cmd` do dispositivo no MQTT.

O gateway republica os **bytes originais** da mensagem válida, sem reescrever o
JSON: o que o simulador mandou é o que o writer e a API recebem.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.schema import Envelope, EnvelopeError, parse_envelope
from app.core.topics import (
    EX_TELEMETRY,
    SIM_STATUS_ROUTING_KEY,
    SIM_STATUS_TOPIC,
    TopicError,
    cmd_topic_from_routing_key,
    parse_command_routing_key,
    parse_topic,
    telemetry_routing_key,
)

Validator = Callable[[bytes | bytearray | str], Envelope]


@dataclass(frozen=True)
class Forward:
    """Mensagem válida: publicar no RabbitMQ."""

    exchange: str
    routing_key: str
    body: bytes
    message_id: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DeadLetter:
    """Mensagem inválida: vai para a DLQ com o corpo original e o motivo."""

    body: bytes
    reason: str
    source: str  # tópico MQTT ou routing key de onde veio

    @property
    def headers(self) -> dict[str, str]:
        return {"x-reason": self.reason, "x-source": self.source}


@dataclass(frozen=True)
class McuCommand:
    """Comando válido: publicar no `/cmd` do dispositivo (QoS 1)."""

    topic: str
    body: bytes


class BadCommand(ValueError):
    """Comando do RabbitMQ que não dá para entregar; vai para a DLQ (nack sem requeue)."""


def route_mqtt_message(topic: str, payload: bytes | bytearray, validate: Validator = parse_envelope) -> Forward | DeadLetter:
    """Decide o destino de uma mensagem vinda do MQTT."""
    body = bytes(payload)

    if topic == SIM_STATUS_TOPIC:
        try:
            envelope = validate(body)
        except EnvelopeError as err:
            return DeadLetter(body, str(err), topic)
        if envelope.type != "status" or envelope.device != "sim":
            return DeadLetter(body, "o status do simulador precisa ser type 'status' do device 'sim'", topic)
        if not isinstance(envelope.payload.get("online"), bool):
            return DeadLetter(body, "o status precisa de 'online' (true ou false)", topic)
        return Forward(EX_TELEMETRY, SIM_STATUS_ROUTING_KEY, body, envelope.id)

    try:
        parsed = parse_topic(topic)
    except TopicError as err:
        return DeadLetter(body, str(err), topic)
    if parsed.leaf != "telemetry":
        return DeadLetter(body, f"o gateway só repassa telemetria, veio {parsed.leaf!r}", topic)

    try:
        envelope = validate(body)
    except EnvelopeError as err:
        return DeadLetter(body, str(err), topic)
    if envelope.type != "telemetry":
        return DeadLetter(body, f"o tipo deveria ser 'telemetry', veio {envelope.type!r}", topic)
    if envelope.device != parsed.device:
        return DeadLetter(
            body, f"o device do envelope ({envelope.device!r}) não bate com o do tópico ({parsed.device!r})", topic
        )
    return Forward(EX_TELEMETRY, telemetry_routing_key(parsed.zone, parsed.kind, parsed.device), body, envelope.id)


def route_command(routing_key: str, body: bytes | bytearray, validate: Validator = parse_envelope) -> McuCommand:
    """Decide o destino de um comando vindo do RabbitMQ. Levanta `BadCommand` se for inválido."""
    raw = bytes(body)
    try:
        parsed = parse_command_routing_key(routing_key)
        envelope = validate(raw)
    except (TopicError, EnvelopeError) as err:
        raise BadCommand(str(err)) from err
    if envelope.type != "command":
        raise BadCommand(f"o tipo deveria ser 'command', veio {envelope.type!r}")
    if envelope.device != parsed.device:
        raise BadCommand(f"o device do envelope ({envelope.device!r}) não bate com o da routing key ({parsed.device!r})")
    if not isinstance(envelope.payload.get("command"), str):
        raise BadCommand("o comando precisa de 'command' (texto) no payload")
    return McuCommand(cmd_topic_from_routing_key(routing_key), raw)
