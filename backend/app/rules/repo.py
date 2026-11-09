"""O `rules` conversando com o PostgreSQL pelas tabelas da §16.1.

Tudo síncrono: o serviço chama pelo `asyncio.to_thread`, para a consulta não
travar o laço de eventos que consome a fila.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update

from app.core.envelope import format_ts, parse_ts
from app.core.pg import Database, coerce
from app.core.ranges import Range

from .alerts import FieldSpec, OpenAlert
from .engine import RuleSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceRow:
    id: str
    zone: str
    kind: str
    active: bool


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


class RulesRepository:
    def __init__(self, db: Database, *, wait_s: float = 0.0):
        self._db = db
        self._wait_s = wait_s

    def _t(self, name: str):
        return self._db.table(name, wait_s=self._wait_s)

    # --- leitura do cadastro -----------------------------------------------------

    def load_rules(self) -> list[RuleSpec]:
        t = self._t("rules")
        with self._db.engine.connect() as conn:
            rows = conn.execute(select(t)).mappings().all()
        return [
            RuleSpec(
                id=str(row["id"]),
                name=str(row["name"]),
                device=str(row["device_id"]),
                field=str(row["field_key"]),
                actuator=str(row["actuator_id"]),
                on_below=float(row["on_below"]),
                off_at=float(row["off_at"]),
            )
            for row in rows
            if row.get('enabled', True)
        ]

    def load_devices(self) -> dict[str, DeviceRow]:
        t = self._t("devices")
        with self._db.engine.connect() as conn:
            rows = conn.execute(select(t.c.id, t.c.zone, t.c.kind, t.c.active)).all()
        return {str(r.id): DeviceRow(str(r.id), str(r.zone), str(r.kind), bool(r.active)) for r in rows}

    def load_fields(self) -> list[FieldSpec]:
        """As faixas de cada variável, só de dispositivo ativo (desativar fecha os alertas dele)."""
        devices = self._t("devices")
        fields = self._t("device_fields")
        with self._db.engine.connect() as conn:
            active = {str(r.id) for r in conn.execute(select(devices.c.id).where(devices.c.active.is_(True)))}
            rows = conn.execute(select(fields)).mappings().all()
        return [
            FieldSpec(
                device=str(row["device_id"]),
                field=str(row["key"]),
                label=str(row["label"]),
                rng=Range(
                    ok_min=_num(row["ok_min"]),
                    ok_max=_num(row["ok_max"]),
                    crit_min=_num(row["crit_min"]),
                    crit_max=_num(row["crit_max"]),
                ),
            )
            for row in rows
            if str(row["device_id"]) in active and row.get('active', True)
        ]

    # --- comandos ----------------------------------------------------------------

    def record_command(self, device: str, command: str, value: Any, *, origin: str, username: str | None, ts: str) -> None:
        """Grava na tabela `commands` o comando que o `rules` mandou (origem "regra")."""
        t = self._t("commands")
        with self._db.engine.begin() as conn:
            conn.execute(
                insert(t).values(
                    device_id=device,
                    command=command,
                    value=coerce(t, "value", value),
                    origin=origin,
                    username=username,
                    created_at=parse_ts(ts),
                )
            )

    # --- alertas -----------------------------------------------------------------

    def load_open_alerts(self) -> list[OpenAlert]:
        """Os alertas de faixa que seguem abertos no banco (os da IA, com `field_key` nulo, não são daqui)."""
        t = self._t("alerts")
        with self._db.engine.connect() as conn:
            rows = conn.execute(
                select(t).where(t.c.closed_at.is_(None), t.c.field_key.is_not(None))
            ).mappings().all()
        alerts = []
        for row in rows:
            opened = row["opened_at"]
            alerts.append(
                OpenAlert(
                    device=str(row["device_id"]),
                    field=str(row["field_key"]),
                    level=str(row["level"]),
                    value=float(row["value"]) if row["value"] is not None else 0.0,
                    message=str(row["message"]),
                    opened_at=format_ts(opened) if isinstance(opened, datetime) else str(opened),
                    db_id=row["id"],
                )
            )
        return alerts

    def open_alert(self, alert: OpenAlert) -> Any:
        t = self._t("alerts")
        with self._db.engine.begin() as conn:
            result = conn.execute(
                insert(t).values(
                    device_id=alert.device,
                    field_key=alert.field,
                    level=alert.level,
                    message=alert.message,
                    value=alert.value,
                    opened_at=parse_ts(alert.opened_at),
                    closed_at=None,
                )
            )
            return result.inserted_primary_key[0]

    def escalate_alert(self, alert: OpenAlert) -> None:
        t = self._t("alerts")
        with self._db.engine.begin() as conn:
            conn.execute(
                update(t).where(t.c.id == alert.db_id).values(level=alert.level, message=alert.message, value=alert.value)
            )

    def close_alert(self, alert: OpenAlert, ts: str) -> None:
        t = self._t("alerts")
        with self._db.engine.begin() as conn:
            conn.execute(update(t).where(t.c.id == alert.db_id).values(closed_at=parse_ts(ts)))
