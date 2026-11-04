"""Amostra de laboratório separada do histórico de insights ao vivo."""
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pyarrow.parquet as pq
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from app.core.datagen import ALL_SENSOR_FIELDS


def client():
    from app.config import settings
    return InfluxDBClient(url=settings.influx_url, token=settings.influx_token, org=settings.influx_org)


def write_lab_sample(path, dataset_id):
    # Leitura limitada: nunca carrega o dataset inteiro para exportar a amostra.
    batch = next(pq.ParquetFile(path).iter_batches(batch_size=1000))
    points = []
    origin = datetime.now(timezone.utc) - timedelta(minutes=1440)
    for row in batch.to_pylist():
        point = Point("synthetic").tag("pump", row["pump_id"]).tag("dataset", dataset_id)
        point.time(origin + timedelta(minutes=row["timestamp_minute"]))
        for field in ALL_SENSOR_FIELDS:
            value = row.get(field)
            if value is not None and np.isfinite(value):
                point.field(field, float(value))
        points.append(point)
    with client() as influx:
        with influx.write_api(write_options=SYNCHRONOUS) as writer:
            writer.write(bucket="lab", record=points)


def insight_history(period, device):
    from app.config import settings
    from app.influx_repo import RepositoryUnavailable
    durations = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
    if period not in durations or (device and not re.fullmatch(r"[a-z0-9-]{1,80}", device)):
        raise ValueError("Período ou dispositivo inválido")
    end = datetime.now(timezone.utc)
    step = max(10, durations[period] // 360)
    # Parâmetros do Flux: nenhum identificador do pedido é interpolado na query.
    flux = '''from(bucket: params.bucket)
      |> range(start: duration(v: params.period))
      |> filter(fn: (r) => r._measurement == "insight")
      |> filter(fn: (r) => r._field == "health" or r._field == "rul_h")
      |> filter(fn: (r) => params.device == "" or r.device == params.device)
      |> group(columns: ["device", "_field"])
    '''
    params = {"bucket": settings.influx_bucket, "period": "-" + period, "device": device or "", "step": f"{step}s"}
    try:
        with client() as influx:
            api = influx.query_api()
            tables = api.query(flux + '|> aggregateWindow(every: duration(v: params.step), fn: mean, createEmpty: false)', params=params)
            stats = {}
            for operation in ("count", "min", "max", "mean", "last"):
                for table in api.query(flux + f'|> {operation}()', params=params):
                    for record in table.records:
                        key = (record.values["device"], record.get_field())
                        stats.setdefault(key, {})["avg" if operation == "mean" else operation] = record.get_value()
    except Exception as error:
        raise RepositoryUnavailable("Histórico de insights indisponível") from error
    series = []
    for table in tables:
        if not table.records:
            continue
        first = table.records[0]
        key = (first.values["device"], first.get_field())
        series.append({"device": key[0], "field": key[1],
                       "points": [{"t": record.get_time().isoformat(), "v": record.get_value()} for record in table.records],
                       "stats": {**stats.get(key, {}), "out_of_range_s": 0}})
    return {"range": period, "from": (end - timedelta(seconds=durations[period])).isoformat(),
            "to": end.isoformat(), "step_s": step, "series": series}
