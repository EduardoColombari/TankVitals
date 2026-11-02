"""Envelope único das mensagens (docs/ARQUITETURA.md §12).

Toda mensagem ao vivo, no MQTT, no RabbitMQ e no WebSocket, tem a mesma forma:

    {"id": "<uuid>", "ts": "<ISO 8601 UTC>", "type": "...", "device": "...", "payload": {...}}

Este módulo só monta e serializa; a validação de quem *recebe* está em
`core/schema.py` (pydantic). Fica só com a biblioteca padrão para o simulador
e os testes não dependerem de nada além do Python.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

MESSAGE_TYPES: tuple[str, ...] = (
    "telemetry",
    "command",
    "insight",
    "alert",
    "job_progress",
    "model_updated",
    "status",
)

# Ids e zonas: letras minúsculas, números e hífen (§13). Nunca ponto nem barra,
# porque os dois viram separador no MQTT e no RabbitMQ.
ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
ID_RE = re.compile(ID_PATTERN)

# Teto do corpo de uma mensagem. A maior (a bomba) tem uns 250 bytes; passar
# muito disso é lixo ou ataque, e vai para a DLQ.
MAX_MESSAGE_BYTES = 16 * 1024


def format_ts(moment: datetime | None = None) -> str:
    """ISO 8601 em UTC, com milissegundos: `2026-11-16T13:45:00.123Z`."""
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    """Lê o `ts` do envelope. Levanta `ValueError` se não for ISO 8601 com fuso."""
    # O Python 3.11+ já aceita o "Z"; o replace deixa claro e vale para 3.10 também.
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("o ts precisa ter fuso horário (use UTC, com Z no fim)")
    return moment.astimezone(timezone.utc)


def make_envelope(
    type_: str,
    device: str,
    payload: dict[str, Any],
    *,
    ts: str | None = None,
    id_: str | None = None,
) -> dict[str, Any]:
    """Monta um envelope. O `ts` é o de quando o fato aconteceu, não o de agora, se for passado."""
    if type_ not in MESSAGE_TYPES:
        raise ValueError(f"tipo de mensagem desconhecido: {type_!r}")
    if not ID_RE.match(device):
        raise ValueError(f"id de dispositivo inválido: {device!r}")
    return {
        "id": id_ or str(uuid.uuid4()),
        "ts": ts or format_ts(),
        "type": type_,
        "device": device,
        "payload": payload,
    }


def dumps(envelope: dict[str, Any]) -> str:
    """JSON compacto. Recusa NaN e infinito: o JSON deles não é válido e o front quebraria."""
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def command_payload(command: str, value: Any = None, *, origin: str, user: str | None) -> dict[str, Any]:
    """Payload de um `command` (§12.1). O `repair` não leva `value`."""
    payload: dict[str, Any] = {"command": command}
    if value is not None:
        payload["value"] = value
    payload["origin"] = origin
    payload["user"] = user
    return payload


def status_envelope(online: bool, *, ts: str | None = None) -> dict[str, Any]:
    """Status do simulador (o Last Will da §13): `{"online": false}`."""
    return make_envelope("status", "sim", {"online": online}, ts=ts)
