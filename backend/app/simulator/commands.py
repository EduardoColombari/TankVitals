"""Leitura dos comandos que chegam no `/cmd` (IOT-03).

Comando malformado vira log, sem derrubar o simulador: qualquer defeito aqui
levanta `InvalidCommand`, e quem chamou só registra o motivo e segue.

Fica só com a biblioteca padrão de propósito. O gateway já validou o envelope
com pydantic antes de publicar no MQTT, mas o simulador não confia em ninguém:
um `mosquitto_pub` à mão também chega aqui.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.envelope import ID_RE, MAX_MESSAGE_BYTES
from app.core.topics import TopicError, parse_topic


class InvalidCommand(ValueError):
    """Comando fora do formato; o texto é o motivo."""


@dataclass(frozen=True)
class Command:
    device: str
    command: str
    value: Any
    origin: str | None
    user: str | None


def parse_command(topic: str, raw: bytes | bytearray | str) -> Command:
    """Lê o envelope `command` publicado em `tankvitals/<zona>/<tipo>/<device>/cmd`."""
    try:
        parsed = parse_topic(topic)
    except TopicError as err:
        raise InvalidCommand(str(err)) from err
    if parsed.leaf != "cmd":
        raise InvalidCommand(f"não é um tópico de comando: {topic!r}")

    size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    if size > MAX_MESSAGE_BYTES:
        raise InvalidCommand(f"mensagem grande demais ({size} bytes)")
    try:
        message = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as err:
        raise InvalidCommand(f"JSON inválido: {err}") from err
    if not isinstance(message, dict):
        raise InvalidCommand("o envelope precisa ser um objeto JSON")

    for key in ("id", "ts", "type", "device", "payload"):
        if key not in message:
            raise InvalidCommand(f"falta o campo {key!r} no envelope")
    if message["type"] != "command":
        raise InvalidCommand(f"o tipo deveria ser 'command', veio {message['type']!r}")
    device = message["device"]
    if not isinstance(device, str) or not ID_RE.match(device):
        raise InvalidCommand(f"device inválido: {device!r}")
    if device != parsed.device:
        raise InvalidCommand(f"o device do envelope ({device!r}) não bate com o do tópico ({parsed.device!r})")

    payload = message["payload"]
    if not isinstance(payload, dict):
        raise InvalidCommand("o payload precisa ser um objeto")
    command = payload.get("command")
    if not isinstance(command, str) or not command:
        raise InvalidCommand("o payload precisa de 'command' (texto)")
    origin, user = payload.get("origin"), payload.get("user")
    return Command(
        device=device,
        command=command,
        value=payload.get("value"),
        origin=origin if isinstance(origin, str) else None,
        user=user if isinstance(user, str) else None,
    )


def _reject_constant(name: str) -> Any:
    raise ValueError(f"valor não permitido no JSON: {name}")
