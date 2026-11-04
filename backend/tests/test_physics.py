import numpy as np
import pytest

from app.core.physics import (
    FAULT_MODES,
    NO_FAULT,
    advance_population,
    create_population,
    inject_fault,
    repair,
)


def test_population_is_vectorized_seeded_and_has_no_truth_labels():
    first = create_population(10, failure_fraction=0.5, seed=21)
    second = create_population(10, failure_fraction=0.5, seed=21)

    first_readings = advance_population(first)
    second_readings = advance_population(second)

    assert first.size == 10
    assert set(first.fault_mode) <= set(FAULT_MODES) | {NO_FAULT}
    assert set(first_readings) == {
        "vibration_mm_s", "current_a", "motor_temp_c", "flow_l_min", "running",
        "water_temp_c", "light_lux", "oxygen_mg_l",
    }
    for field in first_readings:
        np.testing.assert_array_equal(first_readings[field], second_readings[field])


@pytest.mark.parametrize(
    ("mode", "increased", "decreased"),
    [
        ("rolamento", ("vibration_mm_s", "motor_temp_c"), ()),
        ("entupimento", (), ("current_a", "flow_l_min")),
        ("superaquecimento", ("current_a", "motor_temp_c"), ()),
        ("desbalanceamento", ("vibration_mm_s",), ()),
    ],
)
def test_fault_modes_change_expected_signals(mode, increased, decreased):
    healthy = create_population(1, seed=7)
    failing = create_population(1, seed=7)
    healthy_initial = advance_population(healthy)
    failing_initial = advance_population(failing)
    inject_fault(failing, mode)

    for _ in range(360):
        healthy_readings = advance_population(healthy)
        failing_readings = advance_population(failing)

    for field in increased:
        assert failing_readings[field][0] > healthy_readings[field][0]
    for field in decreased:
        assert failing_readings[field][0] < healthy_readings[field][0]
    assert healthy_initial["running"][0] and failing_initial["running"][0]


def test_repair_clears_fault_and_wear():
    population = create_population(2, seed=3)
    inject_fault(population, "rolamento", np.array([1]))
    repair(population, np.array([1]))

    assert population.fault_mode[1] == NO_FAULT
    assert population.wear[1] == 0


def test_invalid_population_and_fault_inputs_are_rejected():
    with pytest.raises(ValueError):
        create_population(0)
    with pytest.raises(ValueError):
        create_population(1, failure_fraction=1.1)
    with pytest.raises(ValueError):
        inject_fault(create_population(1), "quebrada")