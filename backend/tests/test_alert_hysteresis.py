import pytest

from app.core.alert_hysteresis import AlertState, update_alert_state


def apply(state, health, rul_h):
    return update_alert_state(state, health, rul_h)


def test_opens_only_after_three_consecutive_bad_predictions():
    state = AlertState()
    state, event = apply(state, 39.9, 100)
    assert not state.is_open and event is None
    state, event = apply(state, 100, 23.9)
    assert not state.is_open and event is None
    state, event = apply(state, 20, 100)
    assert state.is_open and event == "open"


def test_a_good_prediction_resets_the_bad_streak():
    state, _ = apply(AlertState(), 39, 100)
    state, _ = apply(state, 39, 100)
    state, event = apply(state, 40, 24)

    assert state == AlertState()
    assert event is None


def test_closes_only_after_ten_consecutive_good_predictions():
    state = AlertState(is_open=True)
    for _ in range(9):
        state, event = apply(state, 40, 24)
        assert state.is_open and event is None

    state, event = apply(state, 100, 168)
    assert state == AlertState()
    assert event == "close"


def test_bad_prediction_resets_the_good_streak():
    state = AlertState(is_open=True)
    for _ in range(8):
        state, _ = apply(state, 100, 168)
    state, event = apply(state, 39, 168)
    assert state.is_open and state.good_streak == 0 and event is None

    for _ in range(9):
        state, event = apply(state, 100, 168)
        assert state.is_open and event is None
    state, event = apply(state, 100, 168)
    assert not state.is_open and event == "close"


@pytest.mark.parametrize("health,rul_h", [(-1, 100), (101, 100), (80, -1)])
def test_invalid_predictions_are_rejected(health, rul_h):
    with pytest.raises(ValueError):
        apply(AlertState(), health, rul_h)