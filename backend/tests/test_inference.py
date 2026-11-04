import joblib
import numpy as np
import pytest

from app.core.inference import InferenceEngine
from app.core.physics import PUMP_FIELDS


class ConstantModel:
    def __init__(self, value):
        self.value = value

    def predict(self, rows):
        return np.full(len(rows), self.value)


def save_models(directory, health, rul):
    directory.mkdir()
    joblib.dump(
        {"health_true": ConstantModel(health), "rul_h_true": ConstantModel(rul)},
        directory / "models.joblib",
    )


def reading(value):
    return {field: value for field in PUMP_FIELDS}


def test_inference_waits_for_full_per_device_window(tmp_path):
    save_models(tmp_path / "v1", health=84, rul=96)
    engine = InferenceEngine(window_size=2)
    engine.load_version(tmp_path / "v1", "v1")

    assert engine.update("bomba-1", reading(1)) is None
    prediction = engine.update("bomba-1", reading(2))

    assert prediction == {
        "device": "bomba-1",
        "health": 84.0,
        "rul_h": 96.0,
        "model_version": "v1",
    }
    assert engine.update("bomba-2", reading(1)) is None


def test_new_version_is_loaded_without_clearing_history(tmp_path):
    save_models(tmp_path / "v1", health=80, rul=72)
    save_models(tmp_path / "v2", health=30, rul=12)
    engine = InferenceEngine(window_size=2)
    engine.load_version(tmp_path / "v1", "v1")
    engine.update("bomba-1", reading(1))
    assert engine.update("bomba-1", reading(2))["health"] == 80

    engine.load_version(tmp_path / "v2", "v2")
    prediction = engine.update("bomba-1", reading(3))

    assert prediction["health"] == 30
    assert prediction["rul_h"] == 12
    assert prediction["model_version"] == "v2"


def test_inference_clamps_outputs_and_rejects_bad_readings(tmp_path):
    save_models(tmp_path / "v1", health=120, rul=200)
    engine = InferenceEngine(window_size=1)
    engine.load_version(tmp_path / "v1", "v1")

    prediction = engine.update("bomba-1", reading(1))
    assert prediction["health"] == 100
    assert prediction["rul_h"] == 168

    with pytest.raises(ValueError):
        engine.update("bomba-2", {"vibration_mm_s": 1})
    with pytest.raises(ValueError):
        engine.update("bomba-2", {**reading(1), "flow_l_min": float("nan")})


def test_invalid_model_bundle_is_rejected(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    joblib.dump({"health_true": ConstantModel(80)}, tmp_path / "models.joblib")
    engine = InferenceEngine()

    with pytest.raises(ValueError, match="saúde e RUL"):
        engine.load_version(tmp_path, "bad")