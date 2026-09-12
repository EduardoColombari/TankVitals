"""Classificação das leituras em ok / atencao / critico.

Tarefa: BE-03
"""

from __future__ import annotations

from enum import Enum

from app.config import settings


class AlertLevel(str, Enum):
    OK = "ok"
    ATENCAO = "atencao"
    CRITICO = "critico"


SEVERITY = {
    AlertLevel.OK: 0,
    AlertLevel.ATENCAO: 1,
    AlertLevel.CRITICO: 2,
}


RANGES = {
    "temperature_c": {
        "ok_min": settings.temp_ok_min,
        "ok_max": settings.temp_ok_max,
        "crit_min": settings.temp_crit_min,
        "crit_max": settings.temp_crit_max,
    },

    "ph": {
        "ok_min": settings.ph_ok_min,
        "ok_max": settings.ph_ok_max,
        "crit_min": settings.ph_crit_min,
        "crit_max": settings.ph_crit_max,
    },

    "level_pct": {
        "ok_min": settings.level_ok_min,
        "ok_max": None,
        "crit_min": settings.level_crit_min,
        "crit_max": None,
    },

    "turbidity_ntu": {
        "ok_min": settings.turbidity_ok_min,
        "ok_max": settings.turbidity_ok_max,
        "crit_min": settings.turbidity_crit_min,
        "crit_max": settings.turbidity_crit_max,
    },
}


def classify_metric(metric: str, value: float) -> AlertLevel:
    """Classifica uma única grandeza."""

    if metric not in RANGES:
        raise ValueError(f"Grandeza desconhecida: {metric}")

    limits = RANGES[metric]

    if metric == "turbidity_ntu":
        if value < limits["ok_max"]:
            return AlertLevel.OK
        return AlertLevel.ATENCAO if value <= limits["crit_max"] else AlertLevel.CRITICO

    if metric == "level_pct":
        if value >= limits["ok_min"]:
            return AlertLevel.OK
        return AlertLevel.ATENCAO if value >= limits["crit_min"] else AlertLevel.CRITICO

    # Faixa OK
    if limits["ok_min"] <= value <= limits["ok_max"]:
        return AlertLevel.OK

    # Fora do limite crítico
    if value < limits["crit_min"] or value > limits["crit_max"]:
        return AlertLevel.CRITICO

    # Entre OK e crítico
    return AlertLevel.ATENCAO


def classify_reading(
    reading: object,
) -> tuple[AlertLevel, dict[str, AlertLevel]]:
    """Classifica todas as grandezas presentes."""

    metrics = [
        "temperature_c",
        "ph",
        "level_pct",
        "turbidity_ntu",
    ]

    result: dict[str, AlertLevel] = {}

    for metric in metrics:
        value = getattr(reading, metric, None)

        if value is None:
            continue

        result[metric] = classify_metric(metric, value)

    if not result:
        return AlertLevel.OK, {}

    general = max(
        result.values(),
        key=lambda level: SEVERITY[level],
    )

    return general, result
