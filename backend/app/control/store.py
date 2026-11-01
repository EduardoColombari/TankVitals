"""A rota de comando conversando com o PostgreSQL (tabelas `devices`, `commands` e `audit`, §16.1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert, select

from app.core.pg import Database, coerce

from .service import DeviceInfo


def _as_list(value: Any) -> tuple[str, ...]:
    """`devices.commands` é uma lista; vem como lista (coluna JSON/ARRAY) ou como texto JSON."""
    if value is None:
        return ()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    return tuple(str(item) for item in value)


class PostgresCommandStore:
    def __init__(self, db: Database):
        self._db = db

    def get_device(self, device_id: str) -> DeviceInfo | None:
        t = self._db.table("devices")
        with self._db.engine.connect() as conn:
            row = conn.execute(select(t).where(t.c.id == device_id)).mappings().first()
        if row is None:
            return None
        return DeviceInfo(
            id=str(row["id"]),
            name=str(row["name"]),
            kind=str(row["kind"]),
            zone=str(row["zone"]),
            active=bool(row["active"]),
            commands=_as_list(row["commands"]),
        )

    def record_command(self, device_id: str, command: str, value: Any, *, username: str) -> None:
        t = self._db.table("commands")
        with self._db.engine.begin() as conn:
            conn.execute(
                insert(t).values(
                    device_id=device_id,
                    command=command,
                    value=coerce(t, "value", value),
                    origin="web",
                    username=username,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def record_audit(self, *, username: str, target: str, detail: str, ok: bool) -> None:
        t = self._db.table("audit")
        with self._db.engine.begin() as conn:
            conn.execute(
                insert(t).values(
                    ts=datetime.now(timezone.utc),
                    username=username,
                    type="command",
                    action="command",
                    target=target,
                    detail=detail,
                    ok=ok,
                )
            )
