"""Modelos e validação do payload que chega do MQTT.

Tarefa: BE-02

Contrato: docs/ARQUITETURA.md §2.2 (payload) e §3 (campos e regras)

Esta é a fronteira de confiança do sistema: dado de IoT chega sujo,
e nada passa daqui para dentro sem ser validado.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class SensorReading(BaseModel):
    """Uma leitura validada, pronta para ser gravada no InfluxDB."""

    device_id: str = Field(min_length=1, max_length=64)
    tank_id: str = Field(min_length=1, max_length=64)

    fw: str | None = None

    seq: int | None = Field(default=None, ge=0)
    uptime_s: int | None = Field(default=None, ge=0)
    rssi: int | None = Field(default=None, ge=-100, le=0)

    time: datetime

    temperature_c: float | None = None
    ph: float | None = None
    level_pct: float | None = None
    distance_cm: float | None = None
    turbidity_ntu: float | None = None


# Faixas VÁLIDAS do sensor.
# Não são as faixas SEGURAS dos alertas da BE-03.
VALID_RANGES = {
    "temperature_c": (-10.0, 60.0),
    "ph": (0.0, 14.0),
    "level_pct": (0.0, 100.0),
    "distance_cm": (0.0, 400.0),
    "turbidity_ntu": (0.0, 1000.0),
}


def tank_id_from_topic(topic: str) -> str | None:
    """Extrai <tank_id> de <prefixo>/<tank_id>/<sufixo>.

    Exemplo:
        tankvitals/tanque-01/telemetry
        -> tanque-01

    Devolve None se o tópico não tiver esse formato.
    """

    parts = topic.rsplit("/", 2)

    if len(parts) != 3:
        return None

    prefix, tank_id, suffix = parts

    if not prefix or not tank_id or not suffix:
        return None

    return tank_id


def _valid_measurement(field: str, value) -> float | None:
    """Valida uma grandeza individual.

    Se o valor estiver ausente, inválido ou fora da faixa válida,
    devolve None.
    """

    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        logger.warning(
            "Grandeza %s descartada: valor inválido (%r)",
            field,
            value,
        )
        return None

    minimum, maximum = VALID_RANGES[field]

    if not minimum <= number <= maximum:
        logger.warning(
            "Grandeza %s descartada: %.2f fora da faixa válida %.2f..%.2f",
            field,
            number,
            minimum,
            maximum,
        )
        return None

    return number


def _parse_timestamp(ts) -> datetime:
    """Converte timestamp Unix em datetime UTC.

    Conforme a arquitetura:
    ts ausente ou <= 1700000000 usa horário do servidor.
    """

    try:
        timestamp = int(ts)
    except (TypeError, ValueError, OverflowError):
        timestamp = 0

    if timestamp <= 1700000000:
        return datetime.now(timezone.utc)

    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        logger.warning(
            "Timestamp inválido (%r). Usando horário do servidor.",
            ts,
        )
        return datetime.now(timezone.utc)


def parse_reading(topic: str, payload: bytes) -> SensorReading | None:
    """Converte a mensagem MQTT crua em uma leitura validada.

    Regras:

    1. JSON inválido -> None + WARNING
    2. sem device_id ou tank_id -> None
    3. grandeza fora da faixa válida -> remove apenas o campo
    4. nenhuma grandeza sobrou -> None
    5. ts ausente ou <= 1700000000 -> horário do servidor
    6. tank_id divergente do tópico -> usa o tópico + WARNING
    """

    # ---------------------------------------------------------
    # 1. JSON inválido
    # ---------------------------------------------------------

    try:
        data = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Payload MQTT descartado: JSON inválido.")
        return None

    if not isinstance(data, dict):
        logger.warning(
            "Payload MQTT descartado: JSON deve ser um objeto."
        )
        return None

    # ---------------------------------------------------------
    # 2. device_id e tank_id obrigatórios
    # ---------------------------------------------------------

    device_id = data.get("device_id")
    payload_tank_id = data.get("tank_id")

    if not isinstance(device_id, str) or not device_id.strip():
        logger.warning(
            "Payload MQTT descartado: device_id ausente ou inválido."
        )
        return None

    if not isinstance(payload_tank_id, str) or not payload_tank_id.strip():
        logger.warning(
            "Payload MQTT descartado: tank_id ausente ou inválido."
        )
        return None

    device_id = device_id.strip()
    payload_tank_id = payload_tank_id.strip()

    if len(device_id) > 64 or len(payload_tank_id) > 64:
        logger.warning(
            "Payload MQTT descartado: device_id ou tank_id maior que 64 caracteres."
        )
        return None

    # ---------------------------------------------------------
    # 6. tank_id do tópico tem prioridade
    # ---------------------------------------------------------

    topic_tank_id = tank_id_from_topic(topic)

    tank_id = payload_tank_id

    if topic_tank_id is not None:
        if topic_tank_id != payload_tank_id:
            logger.warning(
                "tank_id divergente: payload=%s, topico=%s. "
                "Usando tank_id do tópico.",
                payload_tank_id,
                topic_tank_id,
            )

        tank_id = topic_tank_id

    # ---------------------------------------------------------
    # 3. Validar grandezas individualmente
    # ---------------------------------------------------------

    temperature_c = _valid_measurement(
        "temperature_c",
        data.get("temperature_c"),
    )

    ph = _valid_measurement(
        "ph",
        data.get("ph"),
    )

    level_pct = _valid_measurement(
        "level_pct",
        data.get("level_pct"),
    )

    distance_cm = _valid_measurement(
        "distance_cm",
        data.get("distance_cm"),
    )

    turbidity_ntu = _valid_measurement(
        "turbidity_ntu",
        data.get("turbidity_ntu"),
    )

    # ---------------------------------------------------------
    # 4. Pelo menos uma grandeza deve existir
    # ---------------------------------------------------------

    measurements = [
        temperature_c,
        ph,
        level_pct,
        turbidity_ntu,
    ]

    if all(value is None for value in measurements):
        logger.warning(
            "Payload MQTT descartado: nenhuma grandeza válida."
        )
        return None

    # ---------------------------------------------------------
    # 5. Timestamp
    # ---------------------------------------------------------

    reading_time = _parse_timestamp(data.get("ts"))

    # ---------------------------------------------------------
    # Campos auxiliares
    # ---------------------------------------------------------

    fw = data.get("fw")

    if fw is not None:
        fw = str(fw)

    seq = data.get("seq")
    uptime_s = data.get("uptime_s")
    rssi = data.get("rssi")

    try:
        seq = int(seq) if seq is not None and int(seq) >= 0 else None
    except (TypeError, ValueError, OverflowError):
        seq = None

    try:
        uptime_s = (
            int(uptime_s)
            if uptime_s is not None and int(uptime_s) >= 0
            else None
        )
    except (TypeError, ValueError, OverflowError):
        uptime_s = None

    try:
        rssi = (
            int(rssi)
            if rssi is not None and -100 <= int(rssi) <= 0
            else None
        )
    except (TypeError, ValueError, OverflowError):
        rssi = None

    # ---------------------------------------------------------
    # Criar objeto final
    # ---------------------------------------------------------

    try:
        return SensorReading(
            device_id=device_id,
            tank_id=tank_id,
            fw=fw,
            seq=seq,
            uptime_s=uptime_s,
            rssi=rssi,
            time=reading_time,
            temperature_c=temperature_c,
            ph=ph,
            level_pct=level_pct,
            distance_cm=distance_cm,
            turbidity_ntu=turbidity_ntu,
        )

    except Exception as exc:
        logger.warning(
            "Payload MQTT descartado durante validação: %s",
            exc,
        )
        return None
