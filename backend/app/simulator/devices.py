"""Os 9 dispositivos do cadastro inicial (docs/ARQUITETURA.md §15).

O simulador não consulta o banco: o que ele publica é o dispositivo, e o
cadastro é que precisa bater com ele. Se uma linha daqui mudar, muda o seed do
backend e o `SEED_DEVICES` do front junto.
"""

from __future__ import annotations

from dataclasses import dataclass

ZONE = "tanque-01"

PUMP_ID = "bomba-rec-01"
AERATOR_ID = "aerador-01"
REFILL_ID = "bomba-repo-01"

FAULT_MODES: tuple[str, ...] = ("rolamento", "entupimento", "superaquecimento", "desbalanceamento")


@dataclass(frozen=True)
class SimDevice:
    id: str
    kind: str  # sensor | equipment | actuator
    zone: str
    # (chave no payload, casas decimais). Atuador: só o estado, sem casas.
    fields: tuple[tuple[str, int], ...]
    # Comandos que o dispositivo aceita (§15). Sensor não aceita nenhum.
    commands: frozenset[str] = frozenset()


DEVICES: tuple[SimDevice, ...] = (
    SimDevice("temp-agua-01", "sensor", ZONE, (("temperature_c", 1),)),
    SimDevice("ph-01", "sensor", ZONE, (("ph", 2),)),
    SimDevice("nivel-01", "sensor", ZONE, (("level_pct", 1),)),
    SimDevice("turbidez-01", "sensor", ZONE, (("turbidity_ntu", 1),)),
    SimDevice("od-01", "sensor", ZONE, (("oxygen_mg_l", 2),)),
    SimDevice("luz-01", "sensor", ZONE, (("light_lux", 0),)),
    SimDevice(
        PUMP_ID,
        "equipment",
        ZONE,
        (("vibration_mm_s", 2), ("current_a", 2), ("motor_temp_c", 1), ("flow_l_min", 1)),
        frozenset({"on", "inject_fault", "repair"}),
    ),
    SimDevice(AERATOR_ID, "actuator", ZONE, (("on", 0),), frozenset({"on"})),
    SimDevice(REFILL_ID, "actuator", ZONE, (("on", 0),), frozenset({"on"})),
)

DEVICES_BY_ID: dict[str, SimDevice] = {device.id: device for device in DEVICES}
