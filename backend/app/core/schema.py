"""Validação do envelope com pydantic (docs/ARQUITETURA.md §12).

Quem *recebe* uma mensagem de fora (o gateway, vindo do MQTT; o `rules`, vindo
do RabbitMQ) passa por aqui. Quem *monta* usa `core/envelope.py`.
"""

from __future__ import annotations

import math
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .envelope import ID_PATTERN, MAX_MESSAGE_BYTES, parse_ts


class EnvelopeError(ValueError):
    """Mensagem fora do envelope combinado; o texto é o motivo, em português."""


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=64)
    ts: str = Field(min_length=1, max_length=40)
    type: Literal["telemetry", "command", "insight", "alert", "job_progress", "model_updated", "status"]
    device: str = Field(max_length=64, pattern=ID_PATTERN)
    payload: dict[str, Any]

    @field_validator("id")
    @classmethod
    def _id_is_uuid(cls, value: str) -> str:
        try:
            uuid.UUID(value)
        except ValueError as err:
            raise ValueError("o id precisa ser um UUID") from err
        return value

    @field_validator("ts")
    @classmethod
    def _ts_is_iso(cls, value: str) -> str:
        try:
            parse_ts(value)
        except ValueError as err:
            raise ValueError("o ts precisa ser ISO 8601 em UTC (ex.: 2026-11-16T13:45:00Z)") from err
        return value


def _all_finite(value: Any) -> bool:
    """NaN e infinito não existem no JSON de verdade: se vierem, o dado está quebrado."""
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_all_finite(item) for item in value)
    return True


def parse_envelope(raw: bytes | bytearray | str) -> Envelope:
    """Valida o corpo de uma mensagem. Levanta `EnvelopeError` com o motivo."""
    size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    if size == 0:
        raise EnvelopeError("mensagem vazia")
    if size > MAX_MESSAGE_BYTES:
        raise EnvelopeError(f"mensagem grande demais ({size} bytes; o teto é {MAX_MESSAGE_BYTES})")
    try:
        envelope = Envelope.model_validate_json(raw)
    except ValidationError as err:
        first = err.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "corpo"
        raise EnvelopeError(f"envelope inválido em {where}: {first['msg']}") from err
    if not _all_finite(envelope.payload):
        raise EnvelopeError("o payload tem NaN ou infinito")
    return envelope
