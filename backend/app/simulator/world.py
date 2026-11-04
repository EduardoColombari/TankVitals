"""O mundo simulado: o tanque, a bomba e os atuadores (IOT-01, IOT-03, IOT-06).

Funciona por passos de 1 minuto simulado (`step`). Cada passo devolve a leitura
dos 9 dispositivos. O estado dos atuadores entra no cálculo do passo seguinte,
e é assim que o efeito de um comando aparece nas leituras:

* aerador ligado → o oxigênio sobe;
* reposição ligada → o nível sobe;
* bomba de recirculação desligada → vazão e corrente vão a zero e a turbidez
  sobe aos poucos (a água não passa pelo filtro).

Não tem rede, relógio nem MQTT: recebe o minuto simulado e uma semente, e dá o
mesmo resultado para a mesma semente. Por isso dá para testar sem broker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .devices import AERATOR_ID, DEVICES, DEVICES_BY_ID, FAULT_MODES, PUMP_ID, REFILL_ID, SimDevice
from .pump import PumpModel


class CommandRejected(ValueError):
    """O dispositivo não aceita aquele comando (ou o valor veio errado)."""


@dataclass(frozen=True)
class Reading:
    device: SimDevice
    payload: dict[str, float | bool | int]


# Dinâmica da água, por passo de 1 minuto simulado.
OXYGEN_PHOTOSYNTHESIS = 0.035  # mg/L por minuto, no pico do sol
OXYGEN_RESPIRATION = 0.02  # mg/L por minuto, sempre (consumo dos peixes e bactérias)
OXYGEN_AERATOR = 0.05  # mg/L por minuto, com o aerador ligado
OXYGEN_SATURATION = 8.0  # acima disso o excesso escapa para o ar
OXYGEN_MAX = 14.0

LEVEL_REFILL = 0.25  # pontos por minuto com a reposição ligada: ver a água subir em segundos
LEVEL_EVAPORATION = 0.003

DIRT_PER_MIN_NO_PUMP = 0.05  # NTU por minuto sem recirculação
DIRT_CLEAN_PER_MIN = 0.2  # com a recirculação, o filtro limpa mais rápido do que suja


def daylight(minute: float) -> float:
    """Luz do dia de 0 a 1: nasce às 6 h, pico ao meio-dia, zero à noite. 1 dia = 1440 min simulados."""
    hour = (minute / 60.0) % 24.0
    return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))


class SimWorld:
    def __init__(self, *, seed: int | None = None, start_minute: float = 0.0):
        self._rng = np.random.default_rng(seed)
        self.minute = float(start_minute)
        self.pump = PumpModel(self._rng)
        self.oxygen = 6.2
        self.level = 72.0
        self.dirt = 0.0
        self.aerator_on = False
        self.refill_on = False

    # --- comandos (IOT-03 e IOT-08) -------------------------------------------

    def apply_command(self, device_id: str, command: str, value: Any = None) -> None:
        """Aplica um comando. Levanta `CommandRejected` se não fizer sentido."""
        device = DEVICES_BY_ID.get(device_id)
        if device is None:
            raise CommandRejected(f"dispositivo desconhecido: {device_id!r}")
        if command not in device.commands:
            raise CommandRejected(f"{device_id} não aceita o comando {command!r}")

        if command == "on":
            if not isinstance(value, bool):
                raise CommandRejected(f"o comando 'on' pede true ou false, veio {value!r}")
            if device_id == AERATOR_ID:
                self.aerator_on = value
            elif device_id == REFILL_ID:
                self.refill_on = value
            elif device_id == PUMP_ID:
                self.pump.set_running(value)
        elif command == "inject_fault":
            if value not in FAULT_MODES:
                raise CommandRejected(f"modo de falha inválido: {value!r} (use {', '.join(FAULT_MODES)})")
            self.pump.inject_fault(value)
        elif command == "repair":
            self.pump.repair()

    def actuator_state(self, device_id: str) -> bool:
        if device_id == AERATOR_ID:
            return self.aerator_on
        if device_id == REFILL_ID:
            return self.refill_on
        raise KeyError(device_id)

    def actuator_reading(self, device_id: str) -> Reading:
        """A telemetria do atuador: só o estado. É a confirmação de que o comando chegou."""
        return Reading(DEVICES_BY_ID[device_id], {"on": self.actuator_state(device_id)})

    # --- um passo de 1 minuto simulado ------------------------------------------

    def step(self) -> list[Reading]:
        self.minute += 1.0
        rng = self._rng
        hour = (self.minute / 60.0) % 24.0
        sun = daylight(self.minute)

        # Oxigênio: sobe com a fotossíntese, cai com a respiração e o aerador empurra para cima.
        self.oxygen += (
            OXYGEN_PHOTOSYNTHESIS * sun
            - OXYGEN_RESPIRATION
            + (OXYGEN_AERATOR if self.aerator_on else 0.0)
            - max(0.0, self.oxygen - OXYGEN_SATURATION) * 0.1
        )
        self.oxygen = min(max(self.oxygen, 0.0), OXYGEN_MAX)

        self.level = min(max(self.level + (LEVEL_REFILL if self.refill_on else -LEVEL_EVAPORATION), 0.0), 100.0)

        # Sem recirculação a água não passa pelo filtro e turva aos poucos.
        if self.pump.running:
            self.dirt = max(0.0, self.dirt - DIRT_CLEAN_PER_MIN)
        else:
            self.dirt += DIRT_PER_MIN_NO_PUMP

        def noise(amplitude: float) -> float:
            return float(rng.uniform(-amplitude, amplitude))

        values: dict[str, float] = {
            "temp-agua-01": 26.0 + 1.4 * math.sin(2 * math.pi * (hour - 9.0) / 24.0) + noise(0.05),
            "ph-01": 7.2 + 0.12 * math.sin(self.minute / 90.0) + noise(0.02),
            "nivel-01": min(max(self.level + noise(0.2), 0.0), 100.0),
            "turbidez-01": max(0.0, 18.0 + 4.0 * math.sin(self.minute / 200.0) + self.dirt + noise(0.6)),
            "od-01": max(0.0, self.oxygen + noise(0.05)),
            "luz-01": max(0.0, 20.0 + 32000.0 * sun + noise(150.0)),
        }

        readings: list[Reading] = []
        for device in DEVICES:
            if device.id in values:
                key, decimals = device.fields[0]
                readings.append(Reading(device, {key: round(values[device.id], decimals)}))
            elif device.id == PUMP_ID:
                sample = self.pump.step(sun)
                payload: dict[str, float | bool | int] = {
                    key: round(getattr(sample, key), decimals) for key, decimals in device.fields
                }
                payload["running"] = sample.running
                readings.append(Reading(device, payload))
            else:
                readings.append(self.actuator_reading(device.id))
        return readings
