"""Repositório: validação, parâmetros Flux e formatos sem banco externo."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.influx_repo import InfluxRepository, RepositoryUnavailable
from app.models import SensorReading


@pytest.fixture
def repository(monkeypatch):
    from app import influx_repo
    client = Mock()
    monkeypatch.setattr(influx_repo, "InfluxDBClient", Mock(return_value=client))
    repo = InfluxRepository()
    repo.query_api.query.return_value = []
    return repo


def result(repository, rows):
    repository.query_api.query.return_value = [SimpleNamespace(records=[SimpleNamespace(values=r) for r in rows])]


def test_latest_keeps_partial_reading_and_timestamp(repository):
    result(repository, [{"tank_id": "tanque-01", "device_id": "esp32", "_time": datetime(2026, 8, 25, tzinfo=timezone.utc),
                         "temperature_c": 26.4, "ph": None, "seq": 42}])
    reading = repository.get_latest("tanque-01")
    assert reading == {"tank_id": "tanque-01", "device_id": "esp32", "time": "2026-08-25T00:00:00Z",
                       "temperature_c": 26.4, "seq": 42}
    query = repository.query_api.query.call_args.args[0]
    assert query.index("pivot(") < query.index("sort(") < query.index("limit(")


@pytest.mark.parametrize("range_,window", [("1h", "10s"), ("6h", "1m"), ("24h", "5m"), ("7d", "1h")])
def test_history_automatic_windows(repository, range_, window):
    data = repository.get_history("tanque-01", range_)
    assert data == {"tank_id": "tanque-01", "range": range_, "window": window, "series": []}
    assert f"aggregateWindow(every: {window}, fn: mean" in repository.query_api.query.call_args.args[0]


def test_history_groups_metrics_and_sorts_utc_points(repository):
    result(repository, [
        {"_field": "ph", "_value": 7.2, "_time": "2026-08-25T13:45:00Z"},
        {"_field": "ph", "_value": 7.1, "_time": "2026-08-25T13:44:00Z"},
    ])
    data = repository.get_history("tanque-01", metrics=["ph"])
    assert data["series"] == [{"metric": "ph", "unit": "pH", "points": [
        {"t": "2026-08-25T13:44:00Z", "v": 7.1}, {"t": "2026-08-25T13:45:00Z", "v": 7.2}]}]


@pytest.mark.parametrize("kwargs", [{"range_": "abc"}, {"window": "1m) |> die()"}, {"metrics": ["unknown"]}])
def test_invalid_parameters_never_query_database(repository, kwargs):
    with pytest.raises(ValueError):
        repository.get_history("tanque-01", **kwargs)
    repository.query_api.query.assert_not_called()


def test_tank_is_a_bound_parameter(repository):
    tank = 'tank" malicious'
    repository.get_latest(tank)
    call = repository.query_api.query.call_args
    assert tank not in call.args[0]
    assert call.kwargs["params"]["tank"] == tank


def test_stats_format_and_count(repository):
    result(repository, [
        {"result": "count", "_value": 2},
        *[{"result": name, "_field": "ph", "_value": value}
          for name, value in [("min", 7), ("max", 8), ("avg", 7.5), ("last", 8)]],
    ])
    assert repository.get_stats("tanque-01") == {"tank_id": "tanque-01", "range": "24h", "count": 2,
        "metrics": {"ph": {"min": 7, "max": 8, "avg": 7.5, "last": 8}}}


def test_empty_results(repository):
    assert repository.get_latest("missing") is None
    assert repository.list_tanks() == []
    assert repository.get_stats("missing")["metrics"] == {}
    assert repository.get_stats("missing")["count"] == 0


def test_tanks_return_last_seen(repository):
    result(repository, [{"tank_id": "tanque-01", "_time": "2026-08-25T10:00:00-03:00"}])
    assert repository.list_tanks() == [{"tank_id": "tanque-01", "last_seen": "2026-08-25T13:00:00Z"}]


def test_database_errors_are_translated(repository):
    repository.query_api.query.side_effect = TimeoutError()
    with pytest.raises(RepositoryUnavailable):
        repository.get_latest("tanque-01")
    repository.client.ping.side_effect = TimeoutError()
    assert repository.ping() is False


def test_write_serializes_present_fields_and_metadata(repository):
    reading = SensorReading(tank_id="tanque-01", device_id="esp32", time=datetime(2026, 8, 25, tzinfo=timezone.utc),
                            temperature_c=26.4, level_pct=0, seq=42, rssi=-58)
    assert repository.write_reading(reading)
    line = repository.write_api.write.call_args.kwargs["record"].to_line_protocol()
    assert "temperature_c=26.4" in line and "level_pct=0" in line
    assert "seq=42i" in line and "rssi=-58i" in line
    assert "ph=" not in line and "turbidity_ntu=" not in line
    repository.write_api.write.side_effect = TimeoutError()
    assert repository.write_reading(reading) is False
    repository.close()
    repository.write_api.close.assert_called_once()
    repository.client.close.assert_called_once()
