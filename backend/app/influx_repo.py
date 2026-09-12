"""Acesso ao InfluxDB: escrita das leituras e consultas do dashboard.

Tarefas: BE-04 (escrita) e BE-06 (consultas)

Contrato: docs/ARQUITETURA.md §4 (schema e queries de referência) e §6 (formatos)

Este é o ÚNICO módulo que fala com o banco.
"""

from __future__ import annotations

import logging
import json

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from app.config import settings
from app.models import SensorReading
from app.presentation import UNITS, iso_time


logger = logging.getLogger(__name__)


VALID_RANGES = ("1h", "6h", "24h", "7d")
VALID_WINDOWS = ("10s", "1m", "5m", "1h")

DEFAULT_WINDOW = {
    "1h": "10s",
    "6h": "1m",
    "24h": "5m",
    "7d": "1h",
}


class RepositoryUnavailable(RuntimeError):
    """Falha de acesso ao banco, traduzida para HTTP 503 pela API."""


def validate_query(tank_id: str, range_: str = "6h", window: str | None = None,
                   metrics: list[str] | None = None) -> tuple[str, list[str]]:
    if not 1 <= len(tank_id.strip()) <= 64 or any(c in tank_id for c in "/+#\x00"):
        raise ValueError("tank_id inválido")
    if range_ not in VALID_RANGES:
        raise ValueError(f"range deve ser um de: {', '.join(VALID_RANGES)}")
    if window is not None and window not in VALID_WINDOWS:
        raise ValueError(f"window deve ser um de: {', '.join(VALID_WINDOWS)}")
    if metrics is not None and (not metrics or any(m not in UNITS for m in metrics)):
        raise ValueError(f"metrics deve conter apenas: {', '.join(UNITS)}")
    return window or DEFAULT_WINDOW[range_], list(dict.fromkeys(metrics if metrics is not None else UNITS))


class InfluxRepository:
    """Encapsula cliente, escrita e consultas do InfluxDB."""

    def __init__(self) -> None:
        """Cria o cliente do InfluxDB a partir do config."""

        self.client = InfluxDBClient(
            url=settings.influx_url,
            token=settings.influx_token,
            org=settings.influx_org,
        )

        self.write_api = self.client.write_api(
            write_options=SYNCHRONOUS
        )
        self.query_api = self.client.query_api()

    # ------------------------------------------------------------------ escrita

    def write_reading(self, reading: SensorReading) -> bool:
        """Grava uma leitura no measurement water_reading.

        Se o InfluxDB estiver indisponível, registra o erro e retorna False,
        sem derrubar a aplicação.
        """

        point = (
            Point("water_reading")
            .tag("tank_id", reading.tank_id)
            .tag("device_id", reading.device_id)
            .time(reading.time, WritePrecision.NS)
        )

        # fw é tag quando estiver presente
        if reading.fw is not None:
            point.tag("fw", reading.fw)
        for field in ("rssi", "seq"):
            value = getattr(reading, field)
            if value is not None:
                point.field(field, int(value))

        # Grandezas presentes viram fields.
        # Campo ausente NÃO vira zero.

        if reading.temperature_c is not None:
            point.field(
                "temperature_c",
                float(reading.temperature_c),
            )

        if reading.ph is not None:
            point.field(
                "ph",
                float(reading.ph),
            )

        if reading.level_pct is not None:
            point.field(
                "level_pct",
                float(reading.level_pct),
            )

        if reading.distance_cm is not None:
            point.field(
                "distance_cm",
                float(reading.distance_cm),
            )

        if reading.turbidity_ntu is not None:
            point.field(
                "turbidity_ntu",
                float(reading.turbidity_ntu),
            )

        try:
            self.write_api.write(
                bucket=settings.influx_bucket,
                org=settings.influx_org,
                record=point,
            )

            logger.debug(
                "Leitura gravada no InfluxDB: tank_id=%s",
                reading.tank_id,
            )

            return True

        except Exception as exc:
            logger.error(
                "Erro ao gravar no InfluxDB: tank_id=%s erro=%s",
                reading.tank_id,
                exc,
            )

            return False

    # ----------------------------------------------------------------- consultas

    def _query(self, query: str, tank_id: str | None = None) -> list[dict]:
        params = {"bucket": settings.influx_bucket}
        if tank_id is not None:
            params["tank"] = tank_id
        try:
            tables = self.query_api.query(query, org=settings.influx_org, params=params)
            return [record.values for table in tables for record in table.records]
        except Exception as exc:
            logger.warning("Falha na consulta ao InfluxDB: %s", exc)
            raise RepositoryUnavailable("InfluxDB indisponível") from exc

    @staticmethod
    def _source(range_: str) -> str:
        # range_ é validado antes; bucket/tank são parâmetros vinculados.
        return (f'from(bucket: bucket) |> range(start: -{range_}) '
                '|> filter(fn: (r) => r._measurement == "water_reading") '
                '|> filter(fn: (r) => r.tank_id == tank) ')

    def get_latest(self, tank_id: str) -> dict | None:
        validate_query(tank_id)
        rows = self._query(self._source("30d") + '''
            |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
            |> group()
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: 1)
        ''', tank_id)
        if not rows:
            return None
        row = rows[0]
        return {"tank_id": row["tank_id"], "device_id": row["device_id"],
                "time": iso_time(row["_time"]),
                **{m: row[m] for m in (*UNITS, "distance_cm", "rssi", "seq") if row.get(m) is not None}}

    def get_history(
        self,
        tank_id: str,
        range_: str = "6h",
        window: str | None = None,
        metrics: list[str] | None = None,
    ) -> dict:
        window, metrics = validate_query(tank_id, range_, window, metrics)
        fields = json.dumps(metrics)
        rows = self._query(self._source(range_) + f'''
            |> filter(fn: (r) => contains(value: r._field, set: {fields}))
            |> group(columns: ["_field"])
            |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
            |> sort(columns: ["_time"])
        ''', tank_id)
        points = {metric: [] for metric in metrics}
        for row in rows:
            if row.get("_value") is not None:
                points[row["_field"]].append({"t": iso_time(row["_time"]), "v": row["_value"]})
        return {"tank_id": tank_id, "range": range_, "window": window,
                "series": [{"metric": m, "unit": UNITS[m], "points": sorted(p, key=lambda x: x["t"])}
                           for m, p in points.items() if p]}

    def get_stats(
        self,
        tank_id: str,
        range_: str = "24h",
    ) -> dict:
        validate_query(tank_id, range_)
        source = self._source(range_)
        fields = json.dumps(list(UNITS))
        query = f'''base = {source}
            data = base |> filter(fn: (r) => contains(value: r._field, set: {fields}))
                |> group(columns: ["_field"])
            data |> min() |> yield(name: "min")
            data |> max() |> yield(name: "max")
            data |> mean() |> yield(name: "avg")
            data |> sort(columns: ["_time"]) |> last() |> yield(name: "last")
            base |> keep(columns: ["_time", "device_id"])
                |> group(columns: ["device_id"])
                |> unique(column: "_time")
                |> map(fn: (r) => ({{_value: 1}}))
                |> group() |> sum() |> yield(name: "count")
        '''
        result = {"tank_id": tank_id, "range": range_, "count": 0, "metrics": {}}
        for row in self._query(query, tank_id):
            if row["result"] == "count":
                result["count"] = int(row["_value"])
            else:
                result["metrics"].setdefault(row["_field"], {})[row["result"]] = row["_value"]
        return result

    def list_tanks(self) -> list[dict]:
        rows = self._query('''from(bucket: bucket) |> range(start: -30d)
            |> filter(fn: (r) => r._measurement == "water_reading")
            |> keep(columns: ["tank_id", "_time"])
            |> group(columns: ["tank_id"])
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: 1)
        ''')
        return sorted([{"tank_id": row["tank_id"], "last_seen": iso_time(row["_time"])}
                       for row in rows], key=lambda row: row["tank_id"])

    def ping(self) -> bool:
        """Verifica se o InfluxDB está respondendo."""

        try:
            return bool(self.client.ping())

        except Exception as exc:
            logger.warning(
                "InfluxDB indisponível: %s",
                exc,
            )
            return False

    def close(self) -> None:
        """Fecha a conexão com o InfluxDB."""

        try:
            self.write_api.close()
            self.client.close()

        except Exception as exc:
            logger.warning(
                "Erro ao fechar cliente do InfluxDB: %s",
                exc,
            )
