"""Modelo vetorizado da bomba e do ambiente do tanque."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray


FAULT_MODES = (
    "rolamento",
    "entupimento",
    "superaquecimento",
    "desbalanceamento",
    "sensor_deriva",
    "sensor_travado",
)
NO_FAULT = "nenhuma"
PUMP_FIELDS = ("vibration_mm_s", "current_a", "motor_temp_c", "flow_l_min")


@dataclass
class PumpPopulation:
    """Estado verdadeiro de um conjunto de bombas; uma bomba também é um lote de tamanho 1."""

    rng: np.random.Generator
    elapsed_minutes: NDArray[np.float64]
    wear: NDArray[np.float64]
    fault_mode: NDArray[np.str_]
    degradation_start: NDArray[np.float64]
    degradation_rate: NDArray[np.float64]
    sensor_field: NDArray[np.int64]
    stuck_values: NDArray[np.float64]
    base_values: NDArray[np.float64]

    @property
    def size(self) -> int:
        return int(self.wear.size)


def create_population(
    count: int,
    *,
    failure_fraction: float = 0.0,
    failure_modes: Sequence[str] = FAULT_MODES,
    seed: int | None = None,
) -> PumpPopulation:
    """Inicializa bombas com desgaste, falhas e ritmos independentes."""

    if count < 1:
        raise ValueError("count deve ser maior que zero")
    if not 0.0 <= failure_fraction <= 1.0:
        raise ValueError("failure_fraction deve estar entre 0 e 1")
    if not failure_modes and failure_fraction > 0:
        raise ValueError("failure_modes não pode ser vazio quando há falhas")
    unknown_modes = set(failure_modes) - set(FAULT_MODES)
    if unknown_modes:
        raise ValueError(f"modos de falha desconhecidos: {sorted(unknown_modes)}")

    rng = np.random.default_rng(seed)
    fault_mode = np.full(count, NO_FAULT, dtype="U24")
    failing = rng.random(count) < failure_fraction
    selected_modes = rng.choice(failure_modes or FAULT_MODES, size=count)
    fault_mode[failing] = selected_modes[failing]

    degradation_start = rng.uniform(0.0, 7 * 24 * 60, count)
    degradation_rate = rng.uniform(1 / 720, 1 / 240, count)
    degradation_rate[~failing] = rng.uniform(0.0, 0.00002, (~failing).sum())
    wear = rng.uniform(0.0, 0.08, count)

    base_values = np.column_stack(
        (
            rng.uniform(0.7, 1.5, count),
            rng.uniform(2.2, 3.0, count),
            rng.uniform(38.0, 46.0, count),
            rng.uniform(45.0, 55.0, count),
        )
    )

    return PumpPopulation(
        rng=rng,
        elapsed_minutes=np.zeros(count, dtype=float),
        wear=wear,
        fault_mode=fault_mode,
        degradation_start=degradation_start,
        degradation_rate=degradation_rate,
        sensor_field=rng.integers(0, len(PUMP_FIELDS), count),
        stuck_values=base_values.copy(),
        base_values=base_values,
    )


def inject_fault(
    population: PumpPopulation,
    mode: str,
    indices: NDArray[np.integer] | None = None,
) -> None:
    """Inicia uma falha nos índices escolhidos, como no comando inject_fault."""

    if mode not in FAULT_MODES:
        raise ValueError(f"modo de falha desconhecido: {mode}")
    selected = np.arange(population.size) if indices is None else np.asarray(indices, dtype=int)
    if np.any((selected < 0) | (selected >= population.size)):
        raise IndexError("índice de bomba fora do lote")

    population.fault_mode[selected] = mode
    population.degradation_start[selected] = population.elapsed_minutes[selected]
    population.degradation_rate[selected] = population.rng.uniform(1 / 480, 1 / 240, selected.size)
    population.wear[selected] = np.maximum(population.wear[selected], 0.02)
    population.sensor_field[selected] = population.rng.integers(0, len(PUMP_FIELDS), selected.size)
    population.stuck_values[selected] = population.base_values[selected]


def repair(population: PumpPopulation, indices: NDArray[np.integer] | None = None) -> None:
    """Restaura as bombas selecionadas e remove a falha injetada."""

    selected = np.arange(population.size) if indices is None else np.asarray(indices, dtype=int)
    if np.any((selected < 0) | (selected >= population.size)):
        raise IndexError("índice de bomba fora do lote")
    population.fault_mode[selected] = NO_FAULT
    population.wear[selected] = 0.0
    population.degradation_start[selected] = np.inf
    population.degradation_rate[selected] = 0.0


def _environment(
    elapsed_minutes: NDArray[np.float64],
    rng: np.random.Generator,
    noise_scale: float,
) -> dict[str, NDArray[np.float64]]:
    phase = 2 * np.pi * (elapsed_minutes % 1440) / 1440
    daylight = np.clip(np.sin(phase - np.pi / 2), 0.0, 1.0)
    return {
        "water_temp_c": 25.5 + 1.6 * np.sin(phase - np.pi / 2) + rng.normal(0.0, 0.12 * noise_scale, elapsed_minutes.size),
        "light_lux": np.maximum(0.0, 850.0 * daylight + rng.normal(0.0, 18.0 * noise_scale, elapsed_minutes.size)),
        "oxygen_mg_l": 5.8 + 1.0 * daylight + rng.normal(0.0, 0.08 * noise_scale, elapsed_minutes.size),
    }


def advance_population(
    population: PumpPopulation,
    minutes: float = 1.0,
    *,
    noise_scale: float = 1.0,
) -> dict[str, NDArray[np.float64] | NDArray[np.bool_]]:
    """Avança todas as bombas em um passo e devolve apenas sinais observáveis."""

    if not np.isfinite(minutes) or minutes <= 0:
        raise ValueError("minutes deve ser maior que zero")
    if not np.isfinite(noise_scale) or noise_scale < 0:
        raise ValueError("noise_scale deve ser finito e não negativo")

    previous = population.elapsed_minutes.copy()
    population.elapsed_minutes += minutes
    active = population.elapsed_minutes > population.degradation_start
    active_wear = population.wear[active]
    duration = population.elapsed_minutes[active] - np.maximum(previous[active], population.degradation_start[active])
    # Solução exata de dw/dt = rate * (1 + 4w²), limitada na falha.
    angle = np.minimum(np.arctan(2 * active_wear) + 2 * population.degradation_rate[active] * duration, np.arctan(2.0))
    population.wear[active] = np.tan(angle) / 2

    return _read_population(population, noise_scale)


def _read_population(population: PumpPopulation, noise_scale: float):
    wear = population.wear
    mode = population.fault_mode
    values = population.base_values.copy()
    values += population.rng.normal(
        0.0,
        np.array([0.12, 0.08, 0.7, 0.8]) * noise_scale,
        values.shape,
    )

    bearing = mode == "rolamento"
    values[bearing, 0] += 5.0 * wear[bearing]
    values[bearing, 2] += 26.0 * wear[bearing]

    clogged_filter = mode == "entupimento"
    values[clogged_filter, 1] -= 0.9 * wear[clogged_filter]
    values[clogged_filter, 3] -= 30.0 * wear[clogged_filter]

    overheating = mode == "superaquecimento"
    values[overheating, 1] += 1.2 * wear[overheating]
    values[overheating, 2] += 44.0 * wear[overheating]

    imbalance = mode == "desbalanceamento"
    values[imbalance, 0] += 6.0 * wear[imbalance]

    sensor_drift = mode == "sensor_deriva"
    if np.any(sensor_drift):
        rows = np.flatnonzero(sensor_drift)
        columns = population.sensor_field[rows]
        values[rows, columns] += wear[rows] * np.array([3.0, 1.0, 24.0, 25.0])[columns]

    sensor_stuck = (mode == "sensor_travado") & (wear > 0.15)
    if np.any(sensor_stuck):
        rows = np.flatnonzero(sensor_stuck)
        values[rows, population.sensor_field[rows]] = population.stuck_values[
            rows, population.sensor_field[rows]
        ]

    values = np.maximum(values, 0.0)
    result: dict[str, NDArray[np.float64] | NDArray[np.bool_]] = {
        field: values[:, index].copy() for index, field in enumerate(PUMP_FIELDS)
    }
    result["running"] = np.ones(population.size, dtype=bool)
    result.update(_environment(population.elapsed_minutes, population.rng, noise_scale))
    return result


def advance_block(population: PumpPopulation, steps: int, *, noise_scale: float = 1.0):
    """Avança minutos × bombas de uma vez, usando a mesma física do simulador."""
    if not isinstance(steps, int) or steps < 1:
        raise ValueError("steps deve ser inteiro positivo")
    if not np.isfinite(noise_scale) or noise_scale < 0:
        raise ValueError("noise_scale inválido")
    times = population.elapsed_minutes[None, :] + np.arange(1, steps + 1)[:, None]
    duration = np.maximum(times - np.maximum(population.elapsed_minutes, population.degradation_start), 0)
    angle = np.minimum(np.arctan(2 * population.wear) + 2 * population.degradation_rate * duration, np.arctan(2.0))
    wear = np.tan(angle) / 2
    expanded = PumpPopulation(
        rng=population.rng, elapsed_minutes=times.ravel(), wear=wear.ravel(),
        fault_mode=np.tile(population.fault_mode, steps),
        degradation_start=np.tile(population.degradation_start, steps),
        degradation_rate=np.tile(population.degradation_rate, steps),
        sensor_field=np.tile(population.sensor_field, steps),
        stuck_values=np.tile(population.stuck_values, (steps, 1)),
        base_values=np.tile(population.base_values, (steps, 1)),
    )
    readings = _read_population(expanded, noise_scale)
    population.elapsed_minutes[:] = times[-1]
    population.wear[:] = wear[-1]
    return readings, expanded
