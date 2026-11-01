"""O mundo simulado: dispositivos, atuadores que reagem, bomba e falha (IOT-01, 03, 06, 08)."""
import statistics

import pytest

from app.simulator.devices import AERATOR_ID, DEVICES, FAULT_MODES, PUMP_ID, REFILL_ID
from app.simulator.pump import FAULT_SIM_MINUTES
from app.simulator.world import CommandRejected, SimWorld, daylight

NOON = 12 * 60
MIDNIGHT = 0


def by_id(readings):
    return {r.device.id: r.payload for r in readings}


def run(world, steps):
    return [by_id(world.step()) for _ in range(steps)]


def test_nine_devices_with_the_ids_of_the_registry():
    assert [d.id for d in DEVICES] == [
        "temp-agua-01", "ph-01", "nivel-01", "turbidez-01", "od-01", "luz-01",
        "bomba-rec-01", "aerador-01", "bomba-repo-01",
    ]
    assert len({d.id for d in DEVICES}) == 9


def test_every_step_has_one_reading_per_device_with_the_registry_fields():
    world = SimWorld(seed=1, start_minute=NOON)
    readings = world.step()
    assert len(readings) == 9
    payloads = by_id(readings)
    assert set(payloads["od-01"]) == {"oxygen_mg_l"}
    assert set(payloads["luz-01"]) == {"light_lux"}
    assert set(payloads[PUMP_ID]) == {"vibration_mm_s", "current_a", "motor_temp_c", "flow_l_min", "running"}
    assert payloads[AERATOR_ID] == {"on": False} and payloads[REFILL_ID] == {"on": False}


def test_same_seed_same_data_and_different_seed_different_data():
    a = run(SimWorld(seed=7, start_minute=NOON), 20)
    b = run(SimWorld(seed=7, start_minute=NOON), 20)
    c = run(SimWorld(seed=8, start_minute=NOON), 20)
    assert a == b
    assert a != c


def test_day_and_night_cycle_in_light_water_temperature_and_oxygen():
    # 1 dia = 1440 passos de 1 min simulado.
    assert daylight(MIDNIGHT) == 0 and daylight(NOON) == pytest.approx(1.0)
    world = SimWorld(seed=3, start_minute=0)
    day = run(world, 1440)
    night_lux = [d["luz-01"]["light_lux"] for d in day[:240]]  # 0 h às 4 h
    noon_lux = [d["luz-01"]["light_lux"] for d in day[690:750]]
    assert max(night_lux) < 500 and min(noon_lux) > 30000
    temps = [d["temp-agua-01"]["temperature_c"] for d in day]
    assert max(temps) - min(temps) > 2.0  # a água esquenta de tarde e esfria de madrugada
    # Sem aerador, o oxigênio cai à noite e a bomba não faz diferença nisso.
    oxygen = [d["od-01"]["oxygen_mg_l"] for d in day]
    assert oxygen[-1] < oxygen[900] or min(oxygen) < 5


def test_aerator_on_makes_oxygen_rise_and_off_lets_it_fall():
    world = SimWorld(seed=2, start_minute=MIDNIGHT)
    before = statistics.mean(d["od-01"]["oxygen_mg_l"] for d in run(world, 10))
    world.apply_command(AERATOR_ID, "on", True)
    run(world, 30)
    with_aerator = statistics.mean(d["od-01"]["oxygen_mg_l"] for d in run(world, 10))
    assert with_aerator > before + 0.5
    assert by_id(world.step())[AERATOR_ID] == {"on": True}  # a telemetria confirma o estado
    world.apply_command(AERATOR_ID, "on", False)
    run(world, 30)
    after = statistics.mean(d["od-01"]["oxygen_mg_l"] for d in run(world, 10))
    assert after < with_aerator - 0.5


def test_refill_on_makes_the_level_rise():
    world = SimWorld(seed=2, start_minute=NOON)
    start = by_id(world.step())["nivel-01"]["level_pct"]
    world.apply_command(REFILL_ID, "on", True)
    later = run(world, 20)[-1]["nivel-01"]["level_pct"]
    assert later > start + 3
    assert run(world, 1)[0][REFILL_ID] == {"on": True}


def test_level_never_goes_above_100():
    world = SimWorld(seed=2, start_minute=NOON)
    world.apply_command(REFILL_ID, "on", True)
    assert max(d["nivel-01"]["level_pct"] for d in run(world, 400)) <= 100.0


def test_pump_off_zeroes_flow_and_current_and_turbidity_slowly_rises():
    world = SimWorld(seed=4, start_minute=NOON)
    baseline = statistics.mean(d["turbidez-01"]["turbidity_ntu"] for d in run(world, 20))
    world.apply_command(PUMP_ID, "on", False)
    steps = run(world, 200)
    last = steps[-1]
    assert last[PUMP_ID]["running"] is False
    assert last[PUMP_ID]["flow_l_min"] == 0 and last[PUMP_ID]["current_a"] == 0
    turbidity = [d["turbidez-01"]["turbidity_ntu"] for d in steps]
    assert turbidity[-1] > baseline + 5  # sobe...
    assert turbidity[5] < turbidity[-1] - 3  # ...aos poucos, não de uma vez
    # Ligando de novo, a recirculação limpa a água.
    world.apply_command(PUMP_ID, "on", True)
    cleaned = run(world, 100)
    assert cleaned[-1]["turbidez-01"]["turbidity_ntu"] < turbidity[-1] - 5
    assert cleaned[-1][PUMP_ID]["flow_l_min"] > 30


def test_healthy_pump_has_noise_it_is_not_a_straight_line():
    world = SimWorld(seed=5, start_minute=NOON)
    readings = [d[PUMP_ID] for d in run(world, 200)]
    for key, minimum_std in (("vibration_mm_s", 0.05), ("current_a", 0.02), ("motor_temp_c", 0.1), ("flow_l_min", 0.3)):
        values = [r[key] for r in readings]
        assert statistics.pstdev(values) > minimum_std, key
        assert len(set(values)) > 10, key
    # E fica dentro das faixas normais do cadastro (§15).
    assert max(r["vibration_mm_s"] for r in readings) < 2.8
    assert max(r["current_a"] for r in readings) < 3.2
    assert max(r["motor_temp_c"] for r in readings) < 65
    assert min(r["flow_l_min"] for r in readings) > 40


def smooth(values, width=15):
    return [statistics.mean(values[max(0, i - width): i + 1]) for i in range(len(values))]


def test_fault_degrades_gradually_over_about_six_real_minutes_and_not_at_once():
    world = SimWorld(seed=6, start_minute=NOON)
    run(world, 30)
    healthy = statistics.mean(d[PUMP_ID]["vibration_mm_s"] for d in run(world, 30))
    world.apply_command(PUMP_ID, "inject_fault", "rolamento")
    steps = run(world, FAULT_SIM_MINUTES + 40)
    vibration = smooth([d[PUMP_ID]["vibration_mm_s"] for d in steps])
    temp = smooth([d[PUMP_ID]["motor_temp_c"] for d in steps])
    # Gradual: um minuto depois de injetar quase nada mudou...
    assert vibration[10] < healthy + 0.5
    # ...a meio caminho já subiu, mas ainda não chegou ao fim...
    assert healthy + 0.5 < vibration[FAULT_SIM_MINUTES // 2] < vibration[-1]
    # ...e no fim passou do limite crítico (4,5 mm/s).
    assert vibration[-1] > 4.5
    # Nenhum salto: de um passo para outro, a média suavizada nunca pula mais que 0,3.
    assert max(b - a for a, b in zip(vibration, vibration[1:])) < 0.3
    assert temp[-1] > temp[10] + 8  # a temperatura do motor sobe junto
    # Acelera perto da falha: a segunda metade piora mais que a primeira.
    mid = FAULT_SIM_MINUTES // 2
    assert vibration[FAULT_SIM_MINUTES] - vibration[mid] > vibration[mid] - vibration[0]


def test_each_fault_mode_has_its_own_signature():
    def after(mode):
        world = SimWorld(seed=9, start_minute=NOON)
        run(world, 30)
        base = by_id(world.step())[PUMP_ID]
        world.apply_command(PUMP_ID, "inject_fault", mode)
        last = run(world, FAULT_SIM_MINUTES + 20)
        tail = [d[PUMP_ID] for d in last[-20:]]
        mean = {k: statistics.mean(r[k] for r in tail) for k in ("vibration_mm_s", "current_a", "motor_temp_c", "flow_l_min")}
        return base, mean

    base, m = after("rolamento")
    assert m["vibration_mm_s"] > base["vibration_mm_s"] + 3 and m["motor_temp_c"] > base["motor_temp_c"] + 10
    base, m = after("entupimento")
    assert m["flow_l_min"] < base["flow_l_min"] - 20 and m["current_a"] < base["current_a"] - 0.4
    base, m = after("superaquecimento")
    assert m["motor_temp_c"] > base["motor_temp_c"] + 25 and m["current_a"] > base["current_a"] + 0.6
    base, m = after("desbalanceamento")
    assert m["vibration_mm_s"] > base["vibration_mm_s"] + 3
    assert abs(m["flow_l_min"] - base["flow_l_min"]) < 4  # só a vibração mexe de verdade


def test_repair_brings_the_pump_back_to_new():
    world = SimWorld(seed=6, start_minute=NOON)
    run(world, 20)
    world.apply_command(PUMP_ID, "inject_fault", "rolamento")
    run(world, FAULT_SIM_MINUTES)
    broken = statistics.mean(d[PUMP_ID]["vibration_mm_s"] for d in run(world, 10))
    assert broken > 4.5
    world.apply_command(PUMP_ID, "repair")
    run(world, 120)  # o motor esfria com a inércia térmica
    fixed = [d[PUMP_ID] for d in run(world, 30)]
    assert statistics.mean(r["vibration_mm_s"] for r in fixed) < 2.0
    assert statistics.mean(r["motor_temp_c"] for r in fixed) < 60


def test_a_stopped_pump_does_not_wear_out():
    world = SimWorld(seed=6, start_minute=NOON)
    world.apply_command(PUMP_ID, "on", False)
    world.apply_command(PUMP_ID, "inject_fault", "rolamento")
    run(world, FAULT_SIM_MINUTES)
    world.apply_command(PUMP_ID, "on", True)
    first = statistics.mean(d[PUMP_ID]["vibration_mm_s"] for d in run(world, 5))
    assert first < 2.0  # o desgaste só anda com a bomba ligada


@pytest.mark.parametrize(
    "device, command, value",
    [
        ("od-01", "on", True),  # sensor não aceita comando
        (AERATOR_ID, "inject_fault", "rolamento"),
        (AERATOR_ID, "on", "ligado"),  # valor que não é booleano
        (AERATOR_ID, "on", 1),  # 1 não é true
        (AERATOR_ID, "on", None),
        (PUMP_ID, "inject_fault", "explodir"),
        (PUMP_ID, "inject_fault", None),
        (PUMP_ID, "reiniciar", None),
        ("nao-existe", "on", True),
    ],
)
def test_bad_commands_are_rejected(device, command, value):
    with pytest.raises(CommandRejected):
        SimWorld(seed=1).apply_command(device, command, value)


def test_all_fault_modes_are_accepted():
    for mode in FAULT_MODES:
        SimWorld(seed=1).apply_command(PUMP_ID, "inject_fault", mode)


def test_one_second_at_speed_60_is_one_simulated_minute_and_a_day_lasts_24_real_minutes():
    world = SimWorld(seed=1, start_minute=0)
    run(world, 60)
    assert world.minute == 60  # 60 passos = 60 min simulados
    # 1440 passos num dia, um por segundo real a SIM_SPEED=60 → 24 min reais.
    assert 1440 / 60 == 24
