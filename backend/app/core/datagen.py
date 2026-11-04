"""Geração em blocos de datasets rotulados para o laboratório de IA."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from app.core.physics import (
    FAULT_MODES,
    PUMP_FIELDS,
    PumpPopulation,
    advance_block,
    create_population,
)


MAX_DATASET_ROWS = 20_000_000
MIN_RUL_HOURS = 168.0
ENVIRONMENT_FIELDS = ("water_temp_c", "light_lux", "oxygen_mg_l")
ALL_SENSOR_FIELDS = PUMP_FIELDS + ENVIRONMENT_FIELDS
ABSURD_RANGES = {
    "vibration_mm_s": (20.0, 50.0),
    "current_a": (12.0, 30.0),
    "motor_temp_c": (150.0, 300.0),
    "flow_l_min": (150.0, 400.0),
    "water_temp_c": (80.0, 120.0),
    "light_lux": (20_000.0, 100_000.0),
    "oxygen_mg_l": (20.0, 50.0),
}
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class DatasetConfig:
    num_pumps: int = 100
    days: int = 7
    sensor_count: int = 4
    failure_fraction: float = 0.3
    failure_modes: tuple[str, ...] = FAULT_MODES
    noise_scale: float = 1.0
    missing_fraction: float = 0.0
    absurd_fraction: float = 0.0
    seed: int = 42
    block_minutes: int = 60

    @property
    def total_rows(self) -> int:
        return self.num_pumps * self.days * 24 * 60

    @property
    def sensor_fields(self) -> tuple[str, ...]:
        return PUMP_FIELDS + ENVIRONMENT_FIELDS[: self.sensor_count - len(PUMP_FIELDS)]

    def validate(self) -> None:
        for name in ("num_pumps", "days", "sensor_count", "seed", "block_minutes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} deve ser inteiro não negativo")
        if self.num_pumps < 1:
            raise ValueError("num_pumps deve ser maior que zero")
        if self.days < 1:
            raise ValueError("days deve ser maior que zero")
        if not len(PUMP_FIELDS) <= self.sensor_count <= len(ALL_SENSOR_FIELDS):
            raise ValueError(f"sensor_count deve estar entre {len(PUMP_FIELDS)} e {len(ALL_SENSOR_FIELDS)}")
        if self.total_rows > MAX_DATASET_ROWS:
            raise ValueError(
                f"dataset excede o teto de {MAX_DATASET_ROWS:,} leituras ({self.total_rows:,} solicitadas)"
            )
        if not 0.0 <= self.failure_fraction <= 1.0:
            raise ValueError("failure_fraction deve estar entre 0 e 1")
        if self.failure_fraction > 0 and not self.failure_modes:
            raise ValueError("failure_modes não pode ser vazio quando há falhas")
        if set(self.failure_modes) - set(FAULT_MODES):
            raise ValueError("failure_modes contém um modo desconhecido")
        for name, value in (
            ("missing_fraction", self.missing_fraction),
            ("absurd_fraction", self.absurd_fraction),
        ):
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} deve estar entre 0 e 1")
        if not np.isfinite(self.noise_scale) or self.noise_scale < 0:
            raise ValueError("noise_scale deve ser finito e não negativo")
        if self.num_pumps * min(self.block_minutes, 1440) > 500_000:
            raise ValueError("bloco excede 500.000 leituras; reduza block_minutes")
        if self.block_minutes < 1:
            raise ValueError("block_minutes deve ser maior que zero")


def _labels(population: PumpPopulation) -> tuple[np.ndarray, np.ndarray]:
    failing = population.fault_mode != "nenhuma"
    remaining_minutes = np.maximum(
        population.degradation_start - population.elapsed_minutes,
        0.0,
    )
    time_to_failure_minutes = remaining_minutes + (np.arctan(2.0) - np.arctan(2 * population.wear)) / np.maximum(
        2 * population.degradation_rate,
        np.finfo(float).tiny,
    )
    rul_hours = np.where(failing, np.minimum(time_to_failure_minutes / 60.0, MIN_RUL_HOURS), MIN_RUL_HOURS)
    health = np.clip(100.0 * (1.0 - population.wear), 0.0, 100.0)
    return health, rul_hours


def _corrupt_sensors(
    columns: dict[str, np.ndarray],
    fields: tuple[str, ...],
    rng: np.random.Generator,
    missing_fraction: float,
    absurd_fraction: float,
) -> None:
    for field in fields:
        values = columns[field]
        shape = values.shape
        values = values.copy()
        if missing_fraction:
            values[rng.random(shape) < missing_fraction] = np.nan
        if absurd_fraction:
            outliers = rng.random(shape) < absurd_fraction
            low, high = ABSURD_RANGES[field]
            values[outliers] = rng.uniform(low, high, int(outliers.sum()))
        columns[field] = values


def _make_block(
    population: PumpPopulation,
    config: DatasetConfig,
    pump_ids: np.ndarray,
    start_minute: int,
    minute_count: int,
) -> pa.Table:
    pump_count = population.size
    readings, states = advance_block(population, minute_count, noise_scale=config.noise_scale)
    health, rul = _labels(states)
    columns = {
        "pump_id": np.tile(pump_ids, minute_count),
        "timestamp_minute": np.repeat(np.arange(start_minute + 1, start_minute + minute_count + 1), pump_count),
        **{field: readings[field] for field in config.sensor_fields},
        "health_true": health,
        "rul_h_true": rul,
        "fault_mode_true": states.fault_mode,
    }

    _corrupt_sensors(
        columns,
        config.sensor_fields,
        population.rng,
        config.missing_fraction,
        config.absurd_fraction,
    )
    arrow_columns = {
        name: pa.array(values, mask=np.isnan(values) if name in config.sensor_fields else None)
        for name, values in columns.items()
    }
    return pa.table(arrow_columns)


def generate_dataset(
    config: DatasetConfig,
    output_path: str | Path,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, str | int]:
    """Gera e grava um dataset Parquet; chama progresso uma vez por bloco."""

    config.validate()
    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"o arquivo de saída já existe: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)

    population = create_population(
        config.num_pumps,
        failure_fraction=config.failure_fraction,
        failure_modes=config.failure_modes,
        seed=config.seed,
    )
    pump_ids = np.asarray([f"bomba-{index + 1:05d}" for index in range(config.num_pumps)])
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    completed_rows = 0
    writer: pq.ParquetWriter | None = None

    try:
        minutes_per_day = 24 * 60
        for day_start in range(0, config.days * minutes_per_day, minutes_per_day):
            day_end = min(day_start + minutes_per_day, config.days * minutes_per_day)
            for block_start in range(day_start, day_end, config.block_minutes):
                block_end = min(block_start + config.block_minutes, day_end)
                table = _make_block(
                    population,
                    config,
                    pump_ids,
                    block_start,
                    block_end - block_start,
                )
                if writer is None:
                    writer = pq.ParquetWriter(temp_path, table.schema, compression="zstd")
                writer.write_table(table)
                completed_rows += table.num_rows
                if progress_callback is not None:
                    progress_callback(completed_rows, config.total_rows)

        if writer is None:
            raise RuntimeError("nenhum bloco foi gerado")
        writer.close()
        writer = None
        temp_path.replace(path)
    except Exception:
        if writer is not None:
            writer.close()
        temp_path.unlink(missing_ok=True)
        raise

    return {"path": str(path), "rows": completed_rows, "seed": config.seed}
