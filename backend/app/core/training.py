"""Treinamento e avaliação dos modelos de saúde e vida útil restante."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import joblib
import numpy as np
import pyarrow.parquet as pq
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from app.core.features import extract_features
from app.core.physics import PUMP_FIELDS


TARGETS = ("health_true", "rul_h_true")
FEATURE_NAMES = tuple(
    f"{field}_{stat}"
    for field in PUMP_FIELDS
    for stat in ("mean", "std", "trend", "max")
)


@dataclass(frozen=True)
class TrainingConfig:
    window_size: int = 30
    sample_every: int = 10
    validation_fraction: float = 0.2
    test_fraction: float = 0.2
    seed: int = 42
    estimators: int = 80
    max_depth: int = 14
    min_samples_leaf: int = 6

    def validate(self) -> None:
        if self.window_size < 1 or self.sample_every < 1:
            raise ValueError("window_size e sample_every devem ser maiores que zero")
        if not 0 < self.validation_fraction < 1 or not 0 < self.test_fraction < 1:
            raise ValueError("as frações de validação e teste devem estar entre 0 e 1")
        if self.validation_fraction + self.test_fraction >= 1:
            raise ValueError("as frações de validação e teste devem somar menos que 1")
        if self.estimators < 1 or self.max_depth < 1 or self.min_samples_leaf < 1:
            raise ValueError("os parâmetros da floresta devem ser maiores que zero")


def _read_examples(dataset_path: str | Path, config: TrainingConfig) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray]:
    parquet = pq.ParquetFile(dataset_path)
    required = {"pump_id", "timestamp_minute", *PUMP_FIELDS, *TARGETS}
    missing_columns = required - set(parquet.schema_arrow.names)
    if missing_columns:
        raise ValueError(f"dataset sem colunas necessárias: {sorted(missing_columns)}")

    histories: dict[str, deque[dict[str, float]]] = defaultdict(
        lambda: deque(maxlen=config.window_size)
    )
    feature_rows: list[list[float]] = []
    targets: dict[str, list[float]] = {target: [] for target in TARGETS}
    groups: list[str] = []
    last_times: dict[str, float] = {}

    columns = ["pump_id", "timestamp_minute", *PUMP_FIELDS, *TARGETS]
    for batch in parquet.iter_batches(columns=columns, batch_size=65_536):
        for row in batch.to_pylist():
            pump_id = row["pump_id"]
            timestamp = row["timestamp_minute"]
            if not isinstance(timestamp, (int, float)) or not np.isfinite(timestamp):
                raise ValueError("timestamp inválido")
            if pump_id in last_times and timestamp <= last_times[pump_id]:
                raise ValueError("leituras devem estar em ordem cronológica por bomba")
            last_times[pump_id] = timestamp
            if any(row[target] is None or not np.isfinite(row[target]) for target in TARGETS):
                raise ValueError("rótulos inválidos")
            history = histories[pump_id]
            history.append(
                {
                    field: float(row[field]) if row[field] is not None else float("nan")
                    for field in PUMP_FIELDS
                }
            )
            if len(history) < config.window_size:
                continue
            if row["timestamp_minute"] % config.sample_every:
                continue
            try:
                features = extract_features(tuple(history), config.window_size)
            except ValueError:
                continue
            feature_rows.append([features[name] for name in FEATURE_NAMES])
            for target in TARGETS:
                targets[target].append(float(row[target]))
            groups.append(pump_id)

    if not feature_rows:
        raise ValueError("dataset não produziu janelas válidas para treinamento")
    return (
        np.asarray(feature_rows, dtype=float),
        {name: np.asarray(values, dtype=float) for name, values in targets.items()},
        np.asarray(groups),
    )


def _split_by_pump(
    groups: np.ndarray,
    config: TrainingConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, set[str]]]:
    unique_groups = np.unique(groups)
    if unique_groups.size < 3:
        raise ValueError("são necessárias pelo menos 3 bombas para treino, validação e teste")

    shuffled = np.random.default_rng(config.seed).permutation(unique_groups)
    test_count = max(1, int(round(unique_groups.size * config.test_fraction)))
    validation_count = max(1, int(round(unique_groups.size * config.validation_fraction)))
    while test_count + validation_count >= unique_groups.size:
        if test_count >= validation_count and test_count > 1:
            test_count -= 1
        elif validation_count > 1:
            validation_count -= 1
        else:
            raise ValueError("não há bombas suficientes para separar os três conjuntos")

    test_groups = set(shuffled[:test_count].tolist())
    validation_groups = set(shuffled[test_count : test_count + validation_count].tolist())
    train_groups = set(shuffled[test_count + validation_count :].tolist())
    train_indices = np.flatnonzero(np.isin(groups, list(train_groups)))
    validation_indices = np.flatnonzero(np.isin(groups, list(validation_groups)))
    test_indices = np.flatnonzero(np.isin(groups, list(test_groups)))
    return train_indices, validation_indices, test_indices, {
        "train": train_groups,
        "validation": validation_groups,
        "test": test_groups,
    }


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "r2": float(r2_score(actual, predicted)),
    }


def _make_model(config: TrainingConfig) -> RandomForestRegressor:
    return RandomForestRegressor(
        n_estimators=config.estimators,
        max_depth=config.max_depth,
        min_samples_leaf=config.min_samples_leaf,
        n_jobs=-1,
        random_state=config.seed,
    )


def train_models(
    dataset_path: str | Path,
    artifact_dir: str | Path,
    version: str,
    config: TrainingConfig = TrainingConfig(),
    progress_callback=None,
) -> dict[str, Any]:
    """Treina os dois regressores e só salva uma versão se ambos vencerem a base."""

    config.validate()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version):
        raise ValueError("version deve conter apenas letras, números, ponto, hífen ou sublinhado")

    if progress_callback:
        progress_callback(5, 100)
    features, targets, groups = _read_examples(dataset_path, config)
    if progress_callback:
        progress_callback(35, 100)
    train_idx, validation_idx, test_idx, group_split = _split_by_pump(groups, config)
    validation_models: dict[str, RandomForestRegressor] = {}
    validation_metrics: dict[str, dict[str, dict[str, float]]] = {}
    accepted = True

    for target in TARGETS:
        values = targets[target]
        model = _make_model(config)
        model.fit(features[train_idx], values[train_idx])
        predicted = model.predict(features[validation_idx])
        baseline_prediction = np.full(validation_idx.size, values[train_idx].mean())
        model_scores = _metrics(values[validation_idx], predicted)
        baseline_scores = _metrics(values[validation_idx], baseline_prediction)
        validation_metrics[target] = {"model": model_scores, "baseline": baseline_scores}
        accepted = accepted and (
            model_scores["mae"] < baseline_scores["mae"]
            and model_scores["rmse"] < baseline_scores["rmse"]
        )
        validation_models[target] = model
        if progress_callback:
            progress_callback(50 if target == TARGETS[0] else 65, 100)

    result: dict[str, Any] = {
        "version": version,
        "accepted": accepted,
        "sample_count": int(features.shape[0]),
        "split_counts": {
            "train": int(train_idx.size),
            "validation": int(validation_idx.size),
            "test": int(test_idx.size),
        },
        "pump_counts": {name: len(ids) for name, ids in group_split.items()},
        "validation": validation_metrics,
    }
    if not accepted:
        result["reason"] = "um ou ambos os modelos não venceram a baseline na validação"
        return result

    final_models: dict[str, RandomForestRegressor] = {}
    test_metrics: dict[str, dict[str, dict[str, float]]] = {}
    plots: dict[str, list[dict[str, float]]] = {}
    train_validation_idx = np.concatenate((train_idx, validation_idx))

    for target in TARGETS:
        values = targets[target]
        model = _make_model(config)
        model.fit(features[train_validation_idx], values[train_validation_idx])
        predicted = model.predict(features[test_idx])
        baseline_prediction = np.full(test_idx.size, values[train_validation_idx].mean())
        test_metrics[target] = {
            "model": _metrics(values[test_idx], predicted),
            "baseline": _metrics(values[test_idx], baseline_prediction),
        }
        final_models[target] = model
        if progress_callback:
            progress_callback(80 if target == TARGETS[0] else 95, 100)
        sample_count = min(500, test_idx.size)
        plot_indices = np.linspace(0, test_idx.size - 1, sample_count, dtype=int)
        plots[target] = [
            {"actual": float(values[test_idx[index]]), "predicted": float(predicted[index])}
            for index in plot_indices
        ]

    destination = Path(artifact_dir) / version
    if destination.exists():
        raise FileExistsError(f"a versão {version} já existe em {destination}")
    staging = Path(artifact_dir) / f".{version}.{uuid4().hex}.tmp"
    staging.mkdir(parents=True, exist_ok=False)
    model_path = staging / "models.joblib"
    temp_model_path = staging / ".models.joblib.tmp"
    manifest_path = staging / "manifest.json"
    temp_manifest_path = staging / ".manifest.json.tmp"

    try:
        joblib.dump(final_models, temp_model_path)
        temp_model_path.replace(model_path)
        manifest = {
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset": str(Path(dataset_path)),
            "features": list(FEATURE_NAMES),
            "config": asdict(config),
            "validation": validation_metrics,
            "predicted_vs_real": plots,
            "pump_split": {name: sorted(ids) for name, ids in group_split.items()},
            "warnings": [f"{target}: R² próximo de 1; revisar facilidade dos dados" for target in TARGETS if test_metrics[target]["model"]["r2"] > 0.98],
            "metrics": test_metrics,
            "feature_importance": {
                target: {
                    name: float(value)
                    for name, value in zip(FEATURE_NAMES, model.feature_importances_)
                }
                for target, model in final_models.items()
            },
        }
        temp_manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temp_manifest_path.replace(manifest_path)
        result.update(artifact_path=str(destination), metrics=test_metrics,
                      predicted_vs_real=plots, feature_importance=manifest["feature_importance"],
                      warnings=manifest["warnings"])
        (staging / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        staging.rename(destination)
    except Exception:
        # Apenas os arquivos conhecidos da pasta temporária deste treino.
        for name in (".models.joblib.tmp", "models.joblib", ".manifest.json.tmp", "manifest.json", "result.json"):
            (staging / name).unlink(missing_ok=True)
        staging.rmdir()
        raise

    result.update(
        {
            "artifact_path": str(destination),
            "metrics": test_metrics,
            "predicted_vs_real": plots,
            "feature_importance": manifest["feature_importance"],
            "warnings": manifest["warnings"],
        }
    )
    return result
