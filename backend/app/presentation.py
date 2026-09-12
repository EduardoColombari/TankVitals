"""Formato compartilhado por REST e WebSocket (ARQUITETURA §6)."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.alerts import classify_reading

UNITS = {"temperature_c": "°C", "ph": "pH", "level_pct": "%", "turbidity_ntu": "NTU"}


def utc_time(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_time(value: datetime | str) -> str:
    return utc_time(value).isoformat().replace("+00:00", "Z")


def present_reading(reading: dict, online: bool) -> dict:
    level, levels = classify_reading(SimpleNamespace(**reading))
    return {
        "tank_id": reading["tank_id"], "device_id": reading["device_id"],
        "time": iso_time(reading["time"]),
        "age_s": max(0, int((datetime.now(timezone.utc) - utc_time(reading["time"])).total_seconds())),
        "online": online, "level": level.value,
        "metrics": {metric: {"value": reading[metric], "level": levels[metric].value, "unit": unit}
                    for metric, unit in UNITS.items() if reading.get(metric) is not None},
    }
