import pytest
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from app.core.features import extract_features
from app.core.physics import PUMP_FIELDS
from app.core.inference import InferenceEngine
from app.core.training import TrainingConfig, _read_examples


def reading(value):
    return {field: value for field in PUMP_FIELDS}


def test_features_have_expected_statistics():
    history = [reading(float(value)) for value in range(1, 5)]

    training_features = extract_features(history, window_size=4)
    inference_features = extract_features(history, window_size=4)

    assert training_features == inference_features
    assert training_features["vibration_mm_s_mean"] == 2.5
    assert training_features["vibration_mm_s_std"] == pytest.approx(1.11803398875)
    assert training_features["vibration_mm_s_trend"] == 1.0
    assert training_features["vibration_mm_s_max"] == 4.0


def test_training_parquet_and_live_engine_receive_identical_past_windows(tmp_path):
    rows = [{"pump_id": "p1", "timestamp_minute": i, **reading(float(i)),
             "health_true": 90, "rul_h_true": 168} for i in range(6)]
    path = tmp_path / "readings.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    train_rows, _, _ = _read_examples(path, TrainingConfig(window_size=3, sample_every=1))

    class CaptureModel:
        def __init__(self):
            self.rows = []

        def predict(self, features):
            self.rows.append(features[0].copy())
            return [90.0]

    model = CaptureModel()
    engine = InferenceEngine(window_size=3)
    engine._models = {"health_true": model, "rul_h_true": CaptureModel()}
    for row in rows:
        engine.update("p1", row)
    np.testing.assert_allclose(train_rows, model.rows)
    assert train_rows[0, 0] == 1.0  # A primeira janela não contém o futuro (3, 4, 5).


def test_features_use_only_the_latest_window():
    history = [reading(-100.0), reading(1.0), reading(2.0), reading(3.0)]

    features = extract_features(history, window_size=3)

    assert features["current_a_mean"] == 2.0
    assert features["current_a_trend"] == 1.0


@pytest.mark.parametrize("history,window_size", [([], 30), ([reading(1.0)], 0)])
def test_invalid_windows_are_rejected(history, window_size):
    with pytest.raises(ValueError):
        extract_features(history, window_size)


def test_missing_or_non_finite_signals_are_rejected():
    with pytest.raises(ValueError):
        extract_features([{"vibration_mm_s": 1.0}])
    with pytest.raises(ValueError):
        extract_features([{**reading(1.0), "current_a": float("nan")}])
