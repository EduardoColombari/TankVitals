"""Inferência em janelas por equipamento, independente do transporte."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import json

import joblib
import numpy as np

from app.core.features import extract_features
from app.core.physics import PUMP_FIELDS
from app.core.training import FEATURE_NAMES, TARGETS


class InferenceEngine:
    def __init__(self, window_size: int = 30) -> None:
        if window_size < 1:
            raise ValueError("window_size deve ser maior que zero")
        self.window_size = window_size
        self._histories: dict[str, deque[dict[str, float]]] = {}
        self._models: dict[str, Any] | None = None
        self.model_version: str | None = None

    @property
    def device_count(self) -> int:
        return len(self._histories)

    def load_version(self, artifact_dir: str | Path, version: str) -> None:
        """Troca os modelos após carregar e validar o pacote da nova versão."""

        artifact_path = Path(artifact_dir) / "models.joblib"
        models = joblib.load(artifact_path)
        if not isinstance(models, dict) or set(models) != set(TARGETS):
            raise ValueError("artefato deve conter os modelos de saúde e RUL")
        if any(not callable(getattr(models[target], "predict", None)) for target in TARGETS):
            raise ValueError("cada modelo deve implementar predict")
        manifest_path = Path(artifact_dir) / "manifest.json"
        window_size = self.window_size
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["version"] != version or manifest["features"] != list(FEATURE_NAMES):
                raise ValueError("manifesto incompatível com a inferência")
            window_size = manifest["config"]["window_size"]
            if not isinstance(window_size, int) or window_size < 1:
                raise ValueError("janela inválida no manifesto")
        if window_size != self.window_size:
            self._histories.clear()
            self.window_size = window_size
        self._models = models
        self.model_version = version

    def update(self, device_id: str, reading: dict[str, float]) -> dict[str, float | str] | None:
        """Adiciona uma leitura e prevê quando a janela estiver completa."""

        if not device_id:
            raise ValueError("device_id não pode ser vazio")
        try:
            normalized = {field: float(reading[field]) for field in PUMP_FIELDS}
        except (KeyError, TypeError, ValueError) as error:
            self._histories.pop(device_id, None)
            raise ValueError("a leitura precisa conter os quatro sinais da bomba") from error
        if not np.isfinite(list(normalized.values())).all():
            self._histories.pop(device_id, None)
            raise ValueError("os sinais da bomba devem ser finitos")

        history = self._histories.setdefault(device_id, deque(maxlen=self.window_size))
        history.append(normalized)
        if len(history) < self.window_size or self._models is None:
            return None

        features = extract_features(tuple(history), self.window_size)
        row = np.asarray([[features[name] for name in FEATURE_NAMES]], dtype=float)
        health = float(self._models["health_true"].predict(row)[0])
        rul_h = float(self._models["rul_h_true"].predict(row)[0])
        if not np.isfinite([health, rul_h]).all():
            raise ValueError("modelo produziu previsão não finita")
        return {
            "device": device_id,
            "health": float(np.clip(health, 0.0, 100.0)),
            "rul_h": float(np.clip(rul_h, 0.0, 168.0)),
            "model_version": self.model_version or "unknown",
        }
