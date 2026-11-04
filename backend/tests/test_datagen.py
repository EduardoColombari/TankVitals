import pyarrow.parquet as pq
import pytest

from app.core.datagen import DatasetConfig, generate_dataset


def test_dataset_is_reproducible_parquet_with_truth_labels_and_progress(tmp_path):
    config = DatasetConfig(
        num_pumps=2,
        days=1,
        sensor_count=5,
        failure_fraction=1.0,
        failure_modes=("desbalanceamento",),
        missing_fraction=0.1,
        absurd_fraction=0.1,
        seed=17,
        block_minutes=720,
    )
    progress = []

    first = generate_dataset(config, tmp_path / "first.parquet", lambda done, total: progress.append((done, total)))
    generate_dataset(config, tmp_path / "second.parquet")
    first_table = pq.read_table(tmp_path / "first.parquet")
    second_table = pq.read_table(tmp_path / "second.parquet")

    assert first["rows"] == 2 * 24 * 60
    assert first_table.equals(second_table)
    assert {"health_true", "rul_h_true", "fault_mode_true", "water_temp_c"} <= set(first_table.column_names)
    assert set(first_table["fault_mode_true"].to_pylist()) == {"desbalanceamento"}
    assert first_table["vibration_mm_s"].null_count > 0
    assert progress == [(1440, 2880), (2880, 2880)]


def test_volume_limit_is_checked_before_creating_output(tmp_path):
    output = tmp_path / "not-created" / "dataset.parquet"
    config = DatasetConfig(num_pumps=1390, days=10)

    with pytest.raises(ValueError, match="teto"):
        generate_dataset(config, output)

    assert not output.parent.exists()


@pytest.mark.parametrize(
    "config",
    [
        DatasetConfig(num_pumps=0),
        DatasetConfig(num_pumps=1, sensor_count=3),
        DatasetConfig(num_pumps=1, failure_fraction=1.1),
        DatasetConfig(num_pumps=1, failure_fraction=1.0, failure_modes=()),
        DatasetConfig(num_pumps=1, missing_fraction=-0.1),
        DatasetConfig(num_pumps=1, absurd_fraction=1.1),
        DatasetConfig(num_pumps=1, noise_scale=-1),
    ],
)
def test_invalid_dataset_config_is_rejected_before_output(config, tmp_path):
    output = tmp_path / "dataset.parquet"

    with pytest.raises(ValueError):
        generate_dataset(config, output)

    assert not output.exists()