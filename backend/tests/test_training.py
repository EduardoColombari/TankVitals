import numpy as np
import pytest

from app.core.datagen import DatasetConfig, generate_dataset
from app.core.training import FEATURE_NAMES, TrainingConfig, _split_by_pump, train_models


def test_group_split_never_puts_a_pump_in_two_sets():
    groups = np.repeat([f"pump-{index}" for index in range(10)], 12)

    train, validation, test, group_split = _split_by_pump(groups, TrainingConfig(seed=13))

    assert not group_split["train"] & group_split["validation"]
    assert not group_split["train"] & group_split["test"]
    assert not group_split["validation"] & group_split["test"]
    assert set(groups[train]) == group_split["train"]
    assert set(groups[validation]) == group_split["validation"]
    assert set(groups[test]) == group_split["test"]


def test_group_split_is_reproducible():
    groups = np.repeat([f"pump-{index}" for index in range(8)], 3)

    first = _split_by_pump(groups, TrainingConfig(seed=9))
    second = _split_by_pump(groups, TrainingConfig(seed=9))

    assert first[3] == second[3]


def test_group_split_requires_three_pumps():
    with pytest.raises(ValueError, match="3 bombas"):
        _split_by_pump(np.array(["pump-1", "pump-2"]), TrainingConfig())


def test_training_saves_models_metrics_and_importance_after_beating_baseline(tmp_path):
    dataset_path = tmp_path / "training.parquet"
    generate_dataset(
        DatasetConfig(num_pumps=30, days=14, failure_fraction=0.7, seed=31),
        dataset_path,
    )

    result = train_models(
        dataset_path,
        tmp_path / "models",
        "test-v1",
        TrainingConfig(
            window_size=15,
            sample_every=10,
            seed=31,
            estimators=60,
            max_depth=14,
            min_samples_leaf=4,
        ),
    )

    assert result["accepted"] is True
    assert result["pump_counts"]["train"] + result["pump_counts"]["validation"] + result["pump_counts"]["test"] == 30
    for target in ("health_true", "rul_h_true"):
        assert result["validation"][target]["model"]["mae"] < result["validation"][target]["baseline"]["mae"]
        assert result["metrics"][target]["model"]["mae"] < result["metrics"][target]["baseline"]["mae"]
        assert len(result["feature_importance"][target]) == len(FEATURE_NAMES)
        assert result["predicted_vs_real"][target]
    assert (tmp_path / "models" / "test-v1" / "models.joblib").exists()
    assert (tmp_path / "models" / "test-v1" / "manifest.json").exists()


@pytest.mark.parametrize(
    "config",
    [
        TrainingConfig(window_size=0),
        TrainingConfig(sample_every=0),
        TrainingConfig(validation_fraction=0.5, test_fraction=0.5),
        TrainingConfig(estimators=0),
    ],
)
def test_invalid_training_config_is_rejected(config):
    with pytest.raises(ValueError):
        config.validate()