"""Os repositórios do PostgreSQL, testados em SQLite com tabelas no formato da §16.1 (BACK-02)."""
import json
from datetime import datetime, timezone

import pytest

sa = pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic")

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, insert, select  # noqa: E402

from app.control.service import DeviceInfo  # noqa: E402,F401
from app.control.store import PostgresCommandStore  # noqa: E402
from app.core.pg import Database  # noqa: E402
from app.rules.alerts import OpenAlert  # noqa: E402
from app.rules.repo import RulesRepository  # noqa: E402


def make_db(json_columns=True):
    engine = create_engine("sqlite://")
    meta = MetaData()
    text_or_json = JSON if json_columns else Text
    Table("devices", meta, Column("id", String, primary_key=True), Column("name", String), Column("kind", String),
          Column("zone", String), Column("active", Boolean), Column("state_field", String),
          Column("commands", text_or_json), Column("control", String))
    Table("device_fields", meta, Column("device_id", String, primary_key=True), Column("key", String, primary_key=True),
          Column("label", String), Column("short", String), Column("unit", String), Column("decimals", Integer),
          Column("ok_min", Float), Column("ok_max", Float), Column("crit_min", Float), Column("crit_max", Float))
    Table("rules", meta, Column("id", String, primary_key=True), Column("name", String), Column("device_id", String),
          Column("field_key", String), Column("actuator_id", String), Column("on_below", Float), Column("off_at", Float))
    Table("alerts", meta, Column("id", Integer, primary_key=True, autoincrement=True), Column("device_id", String),
          Column("field_key", String), Column("level", String), Column("message", String), Column("value", Float),
          Column("opened_at", DateTime), Column("closed_at", DateTime))
    Table("commands", meta, Column("id", Integer, primary_key=True, autoincrement=True), Column("device_id", String),
          Column("command", String), Column("value", text_or_json), Column("origin", String), Column("username", String),
          Column("created_at", DateTime))
    Table("audit", meta, Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", DateTime),
          Column("username", String), Column("type", String), Column("action", String), Column("target", String),
          Column("detail", Text), Column("ok", Boolean))
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(meta.tables["devices"]), [
            {"id": "od-01", "name": "Sonda de oxigênio", "kind": "sensor", "zone": "tanque-01", "active": True,
             "state_field": None, "commands": [] if json_columns else "[]", "control": None},
            {"id": "aerador-01", "name": "Aerador", "kind": "actuator", "zone": "tanque-01", "active": True,
             "state_field": "on", "commands": ["on"] if json_columns else '["on"]', "control": "rule"},
            {"id": "ph-02", "name": "pH inativo", "kind": "sensor", "zone": "tanque-01", "active": False,
             "state_field": None, "commands": [] if json_columns else "[]", "control": None},
        ])
        conn.execute(insert(meta.tables["device_fields"]), [
            {"device_id": "od-01", "key": "oxygen_mg_l", "label": "Oxigênio dissolvido", "short": "Oxigênio", "unit": "mg/L",
             "decimals": 2, "ok_min": 5.0, "ok_max": None, "crit_min": 3.0, "crit_max": None},
            {"device_id": "ph-02", "key": "ph", "label": "pH", "short": "pH", "unit": "", "decimals": 2,
             "ok_min": 6.5, "ok_max": 8.0, "crit_min": 6.0, "crit_max": 8.5},
        ])
        conn.execute(insert(meta.tables["rules"]), [
            {"id": "aerador-od", "name": "Aerador pelo oxigênio", "device_id": "od-01", "field_key": "oxygen_mg_l",
             "actuator_id": "aerador-01", "on_below": 5.0, "off_at": 6.5},
        ])
    return Database(engine=engine), engine, meta


def test_rules_repository_reads_rules_devices_and_ranges_of_active_devices_only():
    db, _, _ = make_db()
    repo = RulesRepository(db)
    rule = repo.load_rules()[0]
    assert (rule.id, rule.device, rule.field, rule.actuator, rule.on_below, rule.off_at) == (
        "aerador-od", "od-01", "oxygen_mg_l", "aerador-01", 5.0, 6.5)
    assert repo.load_devices()["aerador-01"].kind == "actuator"
    fields = repo.load_fields()
    assert [(f.device, f.field) for f in fields] == [("od-01", "oxygen_mg_l")]  # o dispositivo desativado fica de fora
    assert fields[0].rng.ok_min == 5.0 and fields[0].rng.ok_max is None and fields[0].rng.crit_min == 3.0


def test_rules_repository_records_commands_for_json_and_text_columns():
    for json_columns in (True, False):
        db, engine, meta = make_db(json_columns)
        RulesRepository(db).record_command("aerador-01", "on", True, origin="regra", username=None,
                                           ts="2026-11-16T13:45:00.000Z")
        with engine.connect() as conn:
            row = conn.execute(select(meta.tables["commands"])).mappings().one()
        assert row["origin"] == "regra" and row["username"] is None and row["device_id"] == "aerador-01"
        assert (row["value"] is True) if json_columns else (json.loads(row["value"]) is True)


def test_rules_repository_alert_lifecycle():
    db, _, _ = make_db()
    repo = RulesRepository(db)
    alert = OpenAlert("od-01", "oxygen_mg_l", "atencao", 4.5, "Oxigênio fora da faixa normal", "2026-11-16T13:45:00.000Z")
    alert.db_id = repo.open_alert(alert)
    assert alert.public_id == "a-1"
    assert [a.db_id for a in repo.load_open_alerts()] == [1]
    alert.level, alert.value = "critico", 2.5
    repo.escalate_alert(alert)
    assert repo.load_open_alerts()[0].level == "critico"
    repo.close_alert(alert, "2026-11-16T13:50:00.000Z")
    assert repo.load_open_alerts() == []


def test_ai_alerts_with_null_field_are_not_loaded_by_rules():
    db, engine, meta = make_db()
    with engine.begin() as conn:
        conn.execute(insert(meta.tables["alerts"]).values(device_id="bomba-rec-01", field_key=None, level="critico",
                                                         message="IA", value=None, opened_at=datetime.now(timezone.utc)))
    assert RulesRepository(db).load_open_alerts() == []


def test_command_store_reads_device_and_writes_command_and_audit():
    for json_columns in (True, False):
        db, engine, meta = make_db(json_columns)
        store = PostgresCommandStore(db)
        device = store.get_device("aerador-01")
        assert (device.kind, device.zone, device.active, device.commands) == ("actuator", "tanque-01", True, ("on",))
        assert store.get_device("nao-existe") is None
        assert store.get_device("ph-02").active is False
        store.record_command("aerador-01", "on", True, username="operador")
        store.record_audit(username="operador", target="aerador-01", detail="on = true", ok=True)
        with engine.connect() as conn:
            command = conn.execute(select(meta.tables["commands"])).mappings().one()
            audit = conn.execute(select(meta.tables["audit"])).mappings().one()
        assert command["origin"] == "web" and command["username"] == "operador"
        assert (audit["type"], audit["action"], audit["target"], audit["detail"], audit["ok"]) == (
            "command", "command", "aerador-01", "on = true", True)
