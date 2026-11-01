"""Bomba de recirculação do simulador (IOT-01, IOT-06 e IOT-08).

>>> PROVISÓRIO. A física de verdade é o `core/physics.py` da IA-01 (IOT-06):
>>> quando ele existir, a troca é só neste arquivo, porque o resto do
>>> simulador conversa com a bomba por `PumpModel.step`, `inject_fault`,
>>> `repair` e `set_running`, e nada mais. Os números abaixo seguem a mesma
>>> ideia descrita na IA-01 (desgaste escondido de 0 a 1 que acelera perto da
>>> falha, quatro modos de falha, ruído), mas quem treina o modelo precisa
>>> conferir se as leituras ao vivo parecem com as do gerador.

Passo: 1 leitura = 1 minuto simulado (o mesmo do gerador). Com `SIM_SPEED=60`,
1 s real = 1 min simulado, e a falha dura `FAULT_SIM_MINUTES` = 360 passos, ou
seja, uns 6 minutos reais.

**O rótulo da simulação fica aqui dentro.** O desgaste (`_progress`), o modo de
falha e a "saúde de verdade" nunca saem de `step()`: a leitura devolvida tem só
os quatro valores físicos e o estado `running`. Se o rótulo vazasse na
telemetria, o modelo "colaria" na prova ao vivo (§12.2).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .devices import FAULT_MODES

# Quantos passos (minutos simulados) a bomba leva do estado de nova até o
# desgaste máximo, com a falha injetada. 360 passos = 6 min reais a SIM_SPEED=60.
FAULT_SIM_MINUTES = 360
# Cada falha anda num ritmo um pouco diferente (IA-01: "ritmo diferente por bomba").
FAULT_RATE_JITTER = 0.12

# Valores de uma bomba saudável (dentro das faixas do §15: vibração ≤ 2,8 mm/s,
# corrente ≤ 3,2 A, temperatura ≤ 65 °C, vazão ≥ 40 L/min).
BASE_VIBRATION = 1.1
BASE_CURRENT = 2.6
BASE_FLOW = 49.0
BASE_MOTOR_TEMP = 47.0
IDLE_MOTOR_TEMP = 27.0
# Fração do caminho até a temperatura-alvo que o motor percorre a cada passo (inércia térmica).
THERMAL_INERTIA = 0.05

# Desvio-padrão do ruído de cada variável. Bomba saudável não é uma reta.
NOISE_VIBRATION = 0.12
NOISE_CURRENT = 0.04
NOISE_MOTOR_TEMP = 0.3
NOISE_FLOW = 0.8

# O quanto cada modo de falha soma (ou tira) de cada variável com o desgaste no máximo.
#                      vibração, corrente, temperatura, vazão
_EFFECTS: dict[str, tuple[float, float, float, float]] = {
    "rolamento": (5.5, 0.3, 20.0, 0.0),  # rolamento gasto: vibração e temperatura sobem
    "entupimento": (0.4, -0.9, 4.0, -32.0),  # filtro entupido: vazão e corrente caem
    "superaquecimento": (0.4, 1.2, 38.0, 0.0),  # temperatura e corrente sobem
    "desbalanceamento": (5.0, 0.2, 3.0, 0.0),  # rotor desbalanceado: a vibração sobe
}


@dataclass(frozen=True)
class PumpSample:
    """O que a bomba mede. Só grandezas físicas: nenhum rótulo."""

    vibration_mm_s: float
    current_a: float
    motor_temp_c: float
    flow_l_min: float
    running: bool


class PumpModel:
    def __init__(self, rng: np.random.Generator):
        self._rng = rng
        self.running = True
        self._motor_temp = BASE_MOTOR_TEMP + float(rng.normal(0, 0.5))
        # A bomba nasce com pequenas diferenças das outras (IA-01).
        self._base_vibration = BASE_VIBRATION + float(rng.normal(0, 0.05))
        self._base_current = BASE_CURRENT + float(rng.normal(0, 0.03))
        self._base_flow = BASE_FLOW + float(rng.normal(0, 0.5))
        # --- rótulo escondido (nunca sai pela telemetria) ---
        self._mode: str | None = None
        self._progress = 0.0  # 0 = nova, 1 = no limite
        self._rate = 1.0 / FAULT_SIM_MINUTES

    # --- controle (comandos) -------------------------------------------------

    def set_running(self, running: bool) -> None:
        self.running = bool(running)

    def inject_fault(self, mode: str) -> None:
        if mode not in FAULT_MODES:
            raise ValueError(f"modo de falha desconhecido: {mode!r}")
        self._mode = mode
        self._progress = 0.0
        jitter = 1.0 + float(self._rng.uniform(-FAULT_RATE_JITTER, FAULT_RATE_JITTER))
        self._rate = jitter / FAULT_SIM_MINUTES

    def repair(self) -> None:
        """A bomba volta ao estado de nova (a temperatura esfria sozinha, com a inércia)."""
        self._mode = None
        self._progress = 0.0

    # --- simulação -----------------------------------------------------------

    @property
    def _wear(self) -> float:
        """Desgaste de 0 a 1: pior aos poucos e acelerando perto da falha (curva quadrática)."""
        return self._progress**2

    def step(self, daylight: float) -> PumpSample:
        """Avança 1 minuto simulado e devolve a leitura dos sensores da bomba."""
        rng = self._rng
        if not self.running:
            # Desligada: sem vazão nem corrente, o motor esfria e o desgaste não anda.
            self._motor_temp += (IDLE_MOTOR_TEMP - self._motor_temp) * THERMAL_INERTIA
            return PumpSample(
                vibration_mm_s=abs(float(rng.normal(0, 0.02))),
                current_a=0.0,
                motor_temp_c=self._motor_temp + float(rng.normal(0, NOISE_MOTOR_TEMP)),
                flow_l_min=0.0,
                running=False,
            )

        if self._mode is not None:
            self._progress = min(1.0, self._progress + self._rate)
        wear = self._wear
        d_vib, d_cur, d_temp, d_flow = _EFFECTS[self._mode] if self._mode else (0.0, 0.0, 0.0, 0.0)

        target_temp = BASE_MOTOR_TEMP + 5.0 * daylight + d_temp * wear
        self._motor_temp += (target_temp - self._motor_temp) * THERMAL_INERTIA

        return PumpSample(
            vibration_mm_s=max(0.0, self._base_vibration + d_vib * wear + float(rng.normal(0, NOISE_VIBRATION))),
            current_a=max(0.0, self._base_current + d_cur * wear + float(rng.normal(0, NOISE_CURRENT))),
            motor_temp_c=self._motor_temp + float(rng.normal(0, NOISE_MOTOR_TEMP)),
            flow_l_min=max(0.0, self._base_flow + d_flow * wear + float(rng.normal(0, NOISE_FLOW))),
            running=True,
        )
