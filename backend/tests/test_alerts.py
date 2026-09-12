"""Bordas da ARQUITETURA §5."""
from types import SimpleNamespace
import pytest
from app.alerts import classify_metric, classify_reading


@pytest.mark.parametrize("metric,value,expected", [
    ("temperature_c", 21.9, "critico"), ("temperature_c", 22, "atencao"),
    ("temperature_c", 23.9, "atencao"), ("temperature_c", 24, "ok"),
    ("temperature_c", 28, "ok"), ("temperature_c", 28.1, "atencao"),
    ("temperature_c", 30, "atencao"), ("temperature_c", 30.1, "critico"),
    ("ph", 5.5, "critico"), ("ph", 6, "atencao"), ("ph", 6.49, "atencao"),
    ("ph", 6.5, "ok"), ("ph", 8, "ok"), ("ph", 8.01, "atencao"),
    ("ph", 8.5, "atencao"), ("ph", 8.51, "critico"),
    ("level_pct", 14.9, "critico"), ("level_pct", 15, "atencao"),
    ("level_pct", 29.9, "atencao"), ("level_pct", 30, "ok"), ("level_pct", 100, "ok"),
    ("turbidity_ntu", 0, "ok"), ("turbidity_ntu", 39.9, "ok"),
    ("turbidity_ntu", 40, "atencao"), ("turbidity_ntu", 60, "atencao"),
    ("turbidity_ntu", 60.1, "critico"),
])
def test_boundaries(metric, value, expected):
    assert classify_metric(metric, value).value == expected


def test_overall_uses_worst_present_metric():
    overall, metrics = classify_reading(SimpleNamespace(temperature_c=29, ph=5.5, distance_cm=400))
    assert overall.value == "critico"
    assert metrics == {"temperature_c": "atencao", "ph": "critico"}


def test_missing_metrics_and_raw_distance_do_not_raise_alert():
    overall, metrics = classify_reading(SimpleNamespace(temperature_c=26, ph=None, distance_cm=400))
    assert overall.value == "ok" and list(metrics) == ["temperature_c"]
