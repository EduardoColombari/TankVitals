"""Features temporais compartilhadas por treino e inferência."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from app.core.physics import PUMP_FIELDS


FEATURE_STATS = ("mean", "std", "trend", "max")


def extract_features(
    readings: Sequence[Mapping[str, float]],
    window_size: int = 30,
) -> dict[str, float]:
    """Extrai estatísticas da janela cronológica mais recente, sem olhar além dela."""

    if window_size < 1:
        raise ValueError("window_size deve ser maior que zero")
    if not readings:
        raise ValueError("é necessária ao menos uma leitura")

    window = readings[-window_size:]
    try:
        values = np.asarray([[reading[field] for field in PUMP_FIELDS] for reading in window], dtype=float)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("cada leitura deve conter os quatro sinais da bomba") from error
    if not np.isfinite(values).all():
        raise ValueError("as leituras devem conter apenas valores finitos")

    x = np.arange(values.shape[0], dtype=float)
    centered_x = x - x.mean()
    denominator = float(centered_x @ centered_x)
    trends = (centered_x @ (values - values.mean(axis=0))) / denominator if denominator else np.zeros(len(PUMP_FIELDS))

    features: dict[str, float] = {}
    for index, field in enumerate(PUMP_FIELDS):
        series = values[:, index]
        features[f"{field}_mean"] = float(series.mean())
        features[f"{field}_std"] = float(series.std())
        features[f"{field}_trend"] = float(trends[index])
        features[f"{field}_max"] = float(series.max())
    return features