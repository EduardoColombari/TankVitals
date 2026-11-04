"""Histerese de alertas de IA, independente de banco e mensageria."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

OPEN_AFTER_BAD = 3
CLOSE_AFTER_GOOD = 10
LOW_HEALTH = 40.0
LOW_RUL_HOURS = 24.0


@dataclass(frozen=True)
class AlertState:
    is_open: bool = False
    bad_streak: int = 0
    good_streak: int = 0


def update_alert_state(
    state: AlertState,
    health: float,
    rul_h: float,
) -> tuple[AlertState, str | None]:
    """Aplica uma previsão e devolve o novo estado e uma transição opcional."""

    if not isfinite(health) or not isfinite(rul_h) or health < 0 or health > 100 or rul_h < 0:
        raise ValueError("health deve estar entre 0 e 100 e rul_h não pode ser negativo")

    is_bad = health < LOW_HEALTH or rul_h < LOW_RUL_HOURS
    if state.is_open:
        if is_bad:
            return AlertState(is_open=True), None
        good_streak = state.good_streak + 1
        if good_streak >= CLOSE_AFTER_GOOD:
            return AlertState(), "close"
        return AlertState(is_open=True, good_streak=good_streak), None

    if not is_bad:
        return AlertState(), None
    bad_streak = state.bad_streak + 1
    if bad_streak >= OPEN_AFTER_BAD:
        return AlertState(is_open=True), "open"
    return AlertState(bad_streak=bad_streak), None
