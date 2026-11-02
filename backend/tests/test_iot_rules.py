"""Aerador automático e alertas de faixa (IOT-07)."""
import asyncio
import json
from types import SimpleNamespace

from app.core.ranges import Range
from app.rules.alerts import FieldSpec, OpenAlert, RangeAlertTracker
from app.rules.engine import RuleEngine, RuleSpec
from app.rules.service import RulesService

RULE = RuleSpec("aerador-od", "Aerador pelo oxigênio", "od-01", "oxygen_mg_l", "aerador-01", 5.0, 6.5)


def engine(grace_s=20.0):
    e = RuleEngine(grace_s=grace_s)
    e.set_rules([RULE])
    return e


def feed(e, values, start=0.0, step=1.0):
    """Manda as leituras e devolve os comandos (True/False) na ordem."""
    out = []
    for i, v in enumerate(values):
        out += [d.on for d in e.evaluate("od-01", {"oxygen_mg_l": v}, start + i * step)]
    return out


# --- o motor -------------------------------------------------------------------------------


def test_turns_on_below_5_and_off_from_6_5():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    assert feed(e, [6.0, 5.2, 4.99]) == [True]
    assert feed(e, [5.5, 6.0, 6.4], start=10) == []  # dentro da histerese: não mexe
    assert feed(e, [6.5], start=20) == [False]  # 6,5 já desliga (o limite conta)


def test_hysteresis_prevents_on_off_chatter_around_the_limit():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    noisy = [5.05, 4.95, 5.05, 4.97, 5.1, 4.9, 5.2, 5.0, 4.99, 5.3]  # balançando em torno de 5
    assert feed(e, noisy) == [True]  # um liga só, nenhum desliga


def test_one_command_per_change_not_one_per_reading():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    assert feed(e, [4.0] * 50) == [True]
    assert feed(e, [7.0] * 50, start=100) == [False]


def test_full_night_cycle_on_then_off():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    oxygen = [6.0 - 0.02 * i for i in range(80)]  # cai à noite
    oxygen += [oxygen[-1] + 0.03 * i for i in range(1, 80)]  # sobe com o aerador ligado
    assert feed(e, oxygen) == [True, False]


def test_boot_with_aerator_already_off_and_oxygen_high_sends_nothing():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    assert feed(e, [7.0, 7.1, 7.2]) == []


def test_unknown_state_and_high_oxygen_sends_no_blind_off_but_low_oxygen_turns_on():
    e = engine()
    assert feed(e, [7.0]) == []
    assert feed(e, [4.0], start=5) == [True]


def test_forgets_a_command_the_actuator_did_not_follow_after_the_grace_time():
    """O simulador reiniciou: o aerador voltou desligado, mas o último comando foi 'ligar'."""
    e = engine(grace_s=20)
    e.observe_state("aerador-01", False, 0)
    assert feed(e, [4.0], start=0) == [True]
    e.observe_state("aerador-01", False, 5)  # ainda dentro da tolerância: o comando pode estar a caminho
    assert feed(e, [4.0], start=6) == []
    e.observe_state("aerador-01", False, 30)  # passou da tolerância e continua desligado
    assert feed(e, [4.0], start=31) == [True]  # a regra manda de novo


def test_actuator_following_the_command_keeps_it():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    feed(e, [4.0])
    e.observe_state("aerador-01", True, 2)
    assert feed(e, [4.0, 4.1], start=100) == []


def test_rollback_lets_the_next_reading_try_again():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    assert feed(e, [4.0]) == [True]
    e.rollback("aerador-01")
    assert feed(e, [4.0], start=1) == [True]


def test_ignores_other_devices_other_fields_and_non_numeric_values():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    assert e.evaluate("ph-01", {"oxygen_mg_l": 1.0}, 0) == []
    assert e.evaluate("od-01", {"outro": 1.0}, 0) == []
    for bad in (True, "4.0", None, float("nan"), float("inf")):
        assert e.evaluate("od-01", {"oxygen_mg_l": bad}, 0) == []


def test_invalid_rule_is_dropped_not_fatal():
    e = RuleEngine()
    e.set_rules([RuleSpec("x", "x", "od-01", "oxygen_mg_l", "aerador-01", 6.5, 5.0), RULE])
    assert [r.id for r in e.rules] == ["aerador-od"]


def test_new_limits_apply_after_set_rules():
    e = engine()
    e.observe_state("aerador-01", False, 0)
    e.set_rules([RuleSpec("aerador-od", "x", "od-01", "oxygen_mg_l", "aerador-01", 4.0, 5.0)])
    assert feed(e, [4.5]) == []  # com o limite antigo (5) ligaria
    assert feed(e, [3.9], start=5) == [True]


# --- alertas de faixa -----------------------------------------------------------------------------

OXYGEN = FieldSpec("od-01", "oxygen_mg_l", "Oxigênio dissolvido", Range(ok_min=5, crit_min=3))


def tracker(close_after=3):
    t = RangeAlertTracker(close_after=close_after)
    t.set_fields([OXYGEN, FieldSpec("luz-01", "light_lux", "Luz", Range())], "t0")
    return t


def test_opens_on_first_reading_out_of_range_and_only_once():
    t = tracker()
    assert t.update("od-01", "oxygen_mg_l", 6.0, "t1") is None
    change = t.update("od-01", "oxygen_mg_l", 4.5, "t2")
    assert change.kind == "open" and change.alert.level == "atencao"
    assert change.alert.message == "Oxigênio dissolvido fora da faixa normal"
    assert t.update("od-01", "oxygen_mg_l", 4.4, "t3") is None
    assert len(t.open_alerts) == 1


def test_escalates_to_critical_and_does_not_go_back_down_while_open():
    t = tracker()
    t.update("od-01", "oxygen_mg_l", 4.5, "t1")
    change = t.update("od-01", "oxygen_mg_l", 2.0, "t2")
    assert change.kind == "escalate" and change.alert.level == "critico"
    assert change.alert.message == "Oxigênio dissolvido em nível crítico"
    assert t.update("od-01", "oxygen_mg_l", 4.5, "t3") is None  # melhorar até 'atenção' não rebaixa


def test_closes_only_after_n_readings_in_a_row_inside_the_range():
    t = tracker(close_after=3)
    t.update("od-01", "oxygen_mg_l", 4.5, "t1")
    assert t.update("od-01", "oxygen_mg_l", 6.0, "t2") is None
    assert t.update("od-01", "oxygen_mg_l", 6.0, "t3") is None
    t.update("od-01", "oxygen_mg_l", 4.9, "t4")  # saiu de novo: a contagem zera
    assert t.update("od-01", "oxygen_mg_l", 6.0, "t5") is None
    assert t.update("od-01", "oxygen_mg_l", 6.0, "t6") is None
    assert t.update("od-01", "oxygen_mg_l", 6.0, "t7").kind == "close"
    assert t.open_alerts == []


def test_variable_without_range_never_alerts():
    t = tracker()
    assert t.update("luz-01", "light_lux", 0.0, "t") is None
    assert t.update("desconhecido", "x", 1.0, "t") is None


def test_removing_the_field_or_the_range_closes_its_alert():
    t = tracker()
    t.update("od-01", "oxygen_mg_l", 4.5, "t1")
    changes = t.set_fields([], "t2")  # desativado ou removido do cadastro
    assert [c.kind for c in changes] == ["close"] and t.open_alerts == []


def test_restore_resumes_open_alerts_without_duplicating():
    t = tracker()
    t.restore([OpenAlert("od-01", "oxygen_mg_l", "atencao", 4.5, "m", "t0", db_id=7)])
    assert t.update("od-01", "oxygen_mg_l", 4.4, "t1") is None
    assert t.open_alerts[0].public_id == "a-7"


# --- o serviço, com repositório e RabbitMQ de mentira ------------------------------------------------


class FakeRepo:
    def __init__(self):
        self.commands, self.opened, self.closed, self.escalated = [], [], [], []
        self.rules = [RULE]
        self.devices = {
            "od-01": SimpleNamespace(zone="tanque-01", kind="sensor", active=True),
            "aerador-01": SimpleNamespace(zone="tanque-01", kind="actuator", active=True),
        }
        self.fields = [OXYGEN]
        self.open_in_db = []
        self.fail_commands = False
        self.fail_alerts = False
        self.seq = 100

    def load_rules(self): return self.rules
    def load_devices(self): return self.devices
    def load_fields(self): return self.fields
    def load_open_alerts(self): return self.open_in_db

    def record_command(self, device, command, value, *, origin, username, ts):
        if self.fail_commands:
            raise RuntimeError("banco fora")
        self.commands.append((device, command, value, origin, username))

    def open_alert(self, alert):
        if self.fail_alerts:
            raise RuntimeError("banco fora")
        self.seq += 1
        self.opened.append(alert.message)
        return self.seq

    def escalate_alert(self, alert): self.escalated.append(alert.level)
    def close_alert(self, alert, ts): self.closed.append(alert.public_id)


class Bus:
    def __init__(self):
        self.sent = []
        self.fail = False

    async def publish(self, exchange, routing_key, body, **options):
        if self.fail:
            raise ConnectionError("RabbitMQ fora")
        self.sent.append((exchange, routing_key, json.loads(body), options))


async def inline(fn, *args):
    return fn(*args)


def make_service(repo=None, bus=None, close_after=2):
    repo, bus = repo or FakeRepo(), bus or Bus()
    clock = [0.0]
    service = RulesService(
        repo, bus.publish,
        engine=RuleEngine(grace_s=20), tracker=RangeAlertTracker(close_after=close_after),
        monotonic=lambda: clock[0], run_blocking=inline,
    )
    service.clock = clock
    return service, repo, bus


_n = [0]


def telemetry(device, payload, kind="sensor"):
    _n[0] += 1
    env = {"id": f"00000000-0000-4000-8000-{_n[0]:012d}", "ts": f"2026-11-16T13:45:{_n[0] % 60:02d}Z",
           "type": "telemetry", "device": device, "payload": payload}
    return f"telemetry.tanque-01.{kind}.{device}", env


def push(service, device, payload, kind="sensor"):
    service.clock[0] += 1
    asyncio.run(service.handle_telemetry(*telemetry(device, payload, kind)))


def commands(bus):
    return [(rk, m["payload"]) for ex, rk, m, _ in bus.sent if ex == "tv.commands"]


def alerts(bus):
    return [(rk, m["payload"]) for ex, rk, m, _ in bus.sent if ex == "tv.events"]


def test_night_cycle_aerator_turns_on_by_itself_and_off_when_oxygen_recovers():
    service, repo, bus = make_service()
    asyncio.run(service.reload())
    push(service, "aerador-01", {"on": False}, "actuator")
    for oxygen in (6.0, 5.4, 4.9, 4.7, 4.8, 5.5, 6.2, 6.6, 6.8):
        push(service, "od-01", {"oxygen_mg_l": oxygen})
    sent = commands(bus)
    assert [rk for rk, _ in sent] == ["command.tanque-01.actuator.aerador-01"] * 2
    assert [p["value"] for _, p in sent] == [True, False]
    for _, payload in sent:
        assert payload["command"] == "on" and payload["origin"] == "regra" and payload["user"] is None
    # Registrado na tabela de comandos, com origem "regra" e sem usuário.
    assert repo.commands == [("aerador-01", "on", True, "regra", None), ("aerador-01", "on", False, "regra", None)]
    # Comando vai persistente, com o id do envelope como message_id.
    command_options = [o for ex, _, _, o in bus.sent if ex == "tv.commands"]
    assert all(o["persistent"] is True and o["message_id"] for o in command_options)


def test_range_alert_opens_escalates_and_closes_and_is_stored_and_published():
    service, repo, bus = make_service(close_after=2)
    asyncio.run(service.reload())
    for oxygen in (6.0, 4.5, 2.5, 6.0, 6.0):
        push(service, "od-01", {"oxygen_mg_l": oxygen})
    sent = alerts(bus)
    assert [rk for rk, _ in sent] == ["alert.od-01"] * 3
    opened, escalated, closed = (p for _, p in sent)
    assert opened["id"] == "a-101" and opened["level"] == "atencao" and opened["closed_at"] is None
    assert escalated["id"] == "a-101" and escalated["level"] == "critico"
    assert closed["id"] == "a-101" and closed["closed_at"] is not None
    assert opened["field"] == "oxygen_mg_l" and opened["device"] == "od-01" and opened["value"] == 4.5
    assert (repo.escalated, repo.closed) == (["critico"], ["a-101"])
    assert set(opened) == {"id", "device", "field", "level", "message", "value", "opened_at", "closed_at"}


def test_duplicate_message_id_is_dropped():
    service, _, bus = make_service()
    asyncio.run(service.reload())
    push(service, "aerador-01", {"on": False}, "actuator")
    rk, env = telemetry("od-01", {"oxygen_mg_l": 4.0})
    asyncio.run(service.handle_telemetry(rk, env))
    asyncio.run(service.handle_telemetry(rk, env))  # o mesmo id de novo
    assert len(commands(bus)) == 1 and len(alerts(bus)) == 1


def test_command_publish_failure_is_retried_on_the_next_reading():
    service, repo, bus = make_service()
    asyncio.run(service.reload())
    push(service, "aerador-01", {"on": False}, "actuator")
    bus.fail = True
    push(service, "od-01", {"oxygen_mg_l": 4.0})
    assert commands(bus) == [] and repo.commands == []
    bus.fail = False
    push(service, "od-01", {"oxygen_mg_l": 4.0})
    assert len(commands(bus)) == 1


def test_database_failure_recording_a_command_does_not_stop_the_control():
    repo = FakeRepo()
    repo.fail_commands = True
    service, _, bus = make_service(repo)
    asyncio.run(service.reload())
    push(service, "aerador-01", {"on": False}, "actuator")
    push(service, "od-01", {"oxygen_mg_l": 4.0})
    assert len(commands(bus)) == 1  # o aerador liga mesmo sem o registro


def test_alert_not_stored_is_retried_and_not_published_half_done():
    repo = FakeRepo()
    repo.fail_alerts = True
    service, _, bus = make_service(repo)
    asyncio.run(service.reload())
    push(service, "od-01", {"oxygen_mg_l": 4.5})
    assert alerts(bus) == []
    repo.fail_alerts = False
    push(service, "od-01", {"oxygen_mg_l": 4.5})
    assert len(alerts(bus)) == 1


def test_open_alerts_in_the_database_are_resumed_on_restart():
    repo = FakeRepo()
    repo.open_in_db = [OpenAlert("od-01", "oxygen_mg_l", "atencao", 4.5, "m", "t0", db_id=55)]
    service, _, bus = make_service(repo)
    asyncio.run(service.reload())
    push(service, "od-01", {"oxygen_mg_l": 4.4})
    assert alerts(bus) == [] and repo.opened == []


def test_reload_picks_up_new_limits_and_survives_a_database_failure():
    service, repo, bus = make_service()
    asyncio.run(service.reload())
    push(service, "aerador-01", {"on": False}, "actuator")
    repo.rules = [RuleSpec("aerador-od", "x", "od-01", "oxygen_mg_l", "aerador-01", 4.0, 5.0)]
    asyncio.run(service.reload())
    push(service, "od-01", {"oxygen_mg_l": 4.5})
    assert commands(bus) == []  # o limite novo (4,0) vale, sem reiniciar

    def boom():
        raise RuntimeError("banco reiniciando")

    repo.load_rules = boom
    asyncio.run(service.reload())  # não levanta e segue com o que tinha
    push(service, "od-01", {"oxygen_mg_l": 3.5})
    assert len(commands(bus)) == 1


def test_changing_a_range_in_the_registry_changes_alerts_without_restart():
    service, repo, bus = make_service()
    asyncio.run(service.reload())
    push(service, "od-01", {"oxygen_mg_l": 5.5})
    assert alerts(bus) == []
    repo.fields = [FieldSpec("od-01", "oxygen_mg_l", "Oxigênio dissolvido", Range(ok_min=6, crit_min=3))]
    asyncio.run(service.reload())
    push(service, "od-01", {"oxygen_mg_l": 5.5})
    assert len(alerts(bus)) == 1


def test_actuator_zone_and_kind_come_from_the_registry():
    service, _, bus = make_service()
    asyncio.run(service.reload())
    push(service, "aerador-01", {"on": False}, "actuator")
    push(service, "od-01", {"oxygen_mg_l": 4.0})
    assert commands(bus)[0][0] == "command.tanque-01.actuator.aerador-01"
